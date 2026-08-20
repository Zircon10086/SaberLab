"""疲劳曲线（设计文档 §10.3）。

核心问题：“时间越长，动作质量是否系统性下降？”

措辞约束：输出的是“与局部疲劳一致的运动学特征”（kinematic proxy），
不是医学诊断（Rule 8）。
"""
from __future__ import annotations

from ..bsor.models import Replay, GOOD, BAD, MISS
from .scoring import cut_scores


def analyze_fatigue(replay: Replay, windows: list[dict],
                    motion: dict, edge_seconds: float = 30.0) -> dict:
    duration = float(replay.frames["time"][-1]) if replay.frame_count else 0.0
    if duration < 2 * edge_seconds:
        # 太短的歌不做前后对比，只给斜率
        edge_seconds = max(10.0, duration / 3.0)
        if duration < 20:
            return {"available": False,
                    "reason": f"歌曲过短（{duration:.0f}s），无法做前后段对比"}

    notes = sorted(replay.notes, key=lambda n: n.event_time)
    early = _segment_stats(notes, 0.0, edge_seconds)
    late = _segment_stats(notes, duration - edge_seconds, duration)

    def delta(key, scale=1.0):
        e, l = early.get(key), late.get(key)
        if e is None or l is None:
            return None
        return round((l - e) * scale, 4)

    result = {
        "available": True,
        "edge_seconds": round(edge_seconds, 1),
        "early": early,
        "late": late,
        "deltas": {
            "accuracy": delta("accuracy_local"),
            "center": delta("center_avg"),
            "pre": delta("pre_avg"),
            "post": delta("post_avg"),
            "miss_rate": delta("miss_rate"),
            "bad_rate": delta("bad_rate"),
            "saber_speed": delta("saber_speed_avg"),
            "time_dev_abs_ms": delta("time_dev_abs_avg_ms"),
        },
        "interpretation_note": (
            "以下均为运动学推断（kinematic proxy）：与局部疲劳一致的特征表现为"
            "后段 Center/刀速下降、miss/bad 率上升；不构成医学诊断。"),
    }

    # 窗口级斜率（accuracy / center 随时间线性拟合）
    slopes = _window_slopes(windows)
    result["slopes"] = slopes

    # 手速/角速度的前后对比（来自 motion 序列）
    series = motion.get("series") if isinstance(motion, dict) else None
    if series and series.get("t"):
        ts = series["t"]
        t_cut_early = edge_seconds
        t_cut_late = duration - edge_seconds
        for hand in ("left", "right"):
            sp = series[f"{hand}_speed"]
            e_sp = _mean_where(sp, ts, lambda x: x < t_cut_early)
            l_sp = _mean_where(sp, ts, lambda x: x >= t_cut_late)
            result["deltas"][f"{hand}_hand_speed"] = (
                round(l_sp - e_sp, 3) if e_sp is not None and l_sp is not None else None)
    return result


def _mean_where(values, ts, cond):
    sel = [v for v, x in zip(values, ts) if cond(x)]
    return sum(sel) / len(sel) if sel else None


def _segment_stats(notes, t0: float, t1: float) -> dict:
    seg = [n for n in notes if t0 <= n.event_time < t1]
    good = [n for n in seg if n.event_type == GOOD and n.cut is not None]
    bad = sum(1 for n in seg if n.event_type == BAD)
    miss = sum(1 for n in seg if n.event_type == MISS)
    scored = [n for n in seg if n.event_type != 3]
    out: dict = {"notes": len(seg), "good": len(good), "bad": bad, "miss": miss}
    if scored:
        out["miss_rate"] = round(miss / len(scored), 4)
        out["bad_rate"] = round(bad / len(scored), 4)
    if good:
        total = pre = center = post = speed = 0.0
        tdev_abs = 0.0
        for n in good:
            b, c, a = cut_scores(n)
            total += b + c + a
            pre += b
            center += c
            post += a
            speed += n.cut.saber_speed
            tdev_abs += abs(n.cut.time_deviation)
        g = len(good)
        out.update({
            "accuracy_local": round(total / (115 * g), 4),
            "pre_avg": round(pre / g, 3),
            "center_avg": round(center / g, 3),
            "post_avg": round(post / g, 3),
            "saber_speed_avg": round(speed / g, 3),
            "time_dev_abs_avg_ms": round(tdev_abs / g * 1000, 2),
        })
    return out


def _window_slopes(windows: list[dict]) -> dict:
    import numpy as np
    pts = [( (w["t_start"] + w["t_end"]) / 2.0, w["metrics"]) for w in windows]
    slopes = {}
    for key in ("accuracy_local", "center_avg", "saber_speed_avg", "miss_rate"):
        xs = [t for t, m in pts if key in m]
        ys = [m[key] for t, m in pts if key in m]
        if len(xs) >= 3:
            k, _ = np.polyfit(xs, ys, 1)
            slopes[f"{key}_slope_per_min"] = round(float(k) * 60.0, 4)
        else:
            slopes[f"{key}_slope_per_min"] = None
    return slopes
