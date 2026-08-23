"""DEPRECATED: fixed time-window aggregation (marked deprecated per the 2026 decision).

Kept for: windows table structure / legacy database compatibility (t_ref column
migration, test_windows_tref regression), and historical data comparison.
**The engine no longer calls it** — all time-based analysis now uses note anchoring
(backend/analysis/notes.py):

- Curves: per-note cumulative/local mean (x = event time)
- Density: per-note local density (gaps naturally show valleys, faithful to the data)
- Slopes/AI summaries: every N notes grouped (x = in-group median time)

The three main distortions of fixed windows [0, duration] (proven on long intros/outros):
empty windows (no notes), mixed windows (silence + gameplay dilutes density; the first
window measured at only 18%), small-sample endpoints (2-3 notes weighted equally in slope
fitting; saber-speed slope distorted 4.4x).

This module, together with the windows table and the analysis.window_seconds /
window_step_seconds config options, is marked deprecated; do not use it in new code.
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
        in_win = [n.event_time for n in notes if t <= n.event_time < t_end]
        t_ref = None
        if in_win:
            times = sorted(in_win)
            mid = len(times) // 2
            t_ref = (times[mid] if len(times) % 2 == 1
                     else (times[mid - 1] + times[mid]) / 2.0)
        w.update({"window_idx": wi, "t_start": round(t, 2),
                  "t_end": round(t_end, 2),
                  "t_ref": round(t_ref, 3) if t_ref is not None else None})
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
        # >0 favors the right hand, <0 favors the left hand
        m["lr_imbalance"] = round((right_score - left_score) / denom, 4) if denom else 0.0
    return {"metrics": m}
