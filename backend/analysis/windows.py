"""时间窗口指标（设计文档 §10.2：默认 30s 窗口 + 10s 滑动步长）。

每个窗口计算：accuracy 代理、Pre/Center/Post、miss/bad 率、左右失衡、
saber 速度、note 密度、timing 偏差。
"""
from __future__ import annotations

from ..bsor.models import Replay, GOOD, BAD, MISS, BOMB
from .scoring import cut_scores


def build_windows(replay: Replay, window_sec: float = 30.0,
                  step_sec: float = 10.0) -> list[dict]:
    duration = float(replay.frames["time"][-1]) if replay.frame_count else 0.0
    if duration <= 0 and replay.notes:
        duration = max(n.event_time for n in replay.notes)
    if duration <= 0:
        return []

    notes = sorted(replay.notes, key=lambda n: n.event_time)
    windows: list[dict] = []
    wi = 0
    t = 0.0
    while t < duration:
        t_end = min(t + window_sec, duration)
        w = _window_metrics(notes, t, t_end)
        w.update({"window_idx": wi, "t_start": round(t, 2), "t_end": round(t_end, 2)})
        windows.append(w)
        wi += 1
        t += step_sec
        if t_end >= duration:
            break
    return windows


def _window_metrics(notes, t0: float, t1: float) -> dict:
    in_win = [n for n in notes if t0 <= n.event_time < t1]
    good = [n for n in in_win if n.event_type == GOOD and n.cut is not None]
    bad = [n for n in in_win if n.event_type == BAD]
    miss = [n for n in in_win if n.event_type == MISS]
    bombs = [n for n in in_win if n.event_type == BOMB]

    m: dict = {
        "note_events": len(in_win),
        "good": len(good), "bad": len(bad), "miss": len(miss), "bomb": len(bombs),
        "note_density": round(len(in_win) / max(1e-6, t1 - t0), 3),
    }
    scored = [n for n in in_win if n.event_type != BOMB]
    if scored:
        m["miss_rate"] = round(len(miss) / len(scored), 4)
        m["bad_rate"] = round(len(bad) / len(scored), 4)
    if good:
        total_score = 0
        pre = center = post = 0.0
        speed = 0.0
        tdev = []
        left_n = right_n = 0
        left_score = right_score = 0
        for n in good:
            b, c, a = cut_scores(n)
            s = b + c + a
            total_score += s
            pre += b
            center += c
            post += a
            speed += n.cut.saber_speed
            tdev.append(n.cut.time_deviation)
            if n.saber == "left":
                left_n += 1
                left_score += s
            else:
                right_n += 1
                right_score += s
        n = len(good)
        m["accuracy_local"] = round(total_score / (115 * n), 4)
        m["pre_avg"] = round(pre / n, 3)
        m["center_avg"] = round(center / n, 3)
        m["post_avg"] = round(post / n, 3)
        m["saber_speed_avg"] = round(speed / n, 3)
        m["time_dev_avg_ms"] = round(sum(tdev) / n * 1000, 2)
        m["left_notes"] = left_n
        m["right_notes"] = right_n
        denom = left_score + right_score
        # >0 偏右手，<0 偏左手
        m["lr_imbalance"] = round((right_score - left_score) / denom, 4) if denom else 0.0
    return {"metrics": m}
