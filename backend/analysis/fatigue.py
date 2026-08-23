"""Fatigue curves (design doc §10.3).

Core question: "does action quality degrade systematically the longer a run goes?"

Wording constraint: the output is a "kinematic feature consistent with local fatigue"
(kinematic proxy), not a medical diagnosis (Rule 8).

Timeline (2026 decision, fixed windows retired): early/late segments and slopes are all
anchored to note events —
- Early/late: edge seconds of [first_note, last_note] (v1.4.1 fix, see §4.8)
- Slopes: every N notes form a group (analysis/notes.py build_note_groups),
  x = in-group median note time — free of empty-window / mixed-window /
  small-sample-endpoint pollution (the three main distortion sources of fixed windows)
"""
from __future__ import annotations

from ..bsor.models import Replay, GOOD, BAD, MISS
from .scoring import cut_scores


def analyze_fatigue(replay: Replay, note_groups: list[dict],
                    motion: dict, edge_seconds: float = 30.0) -> dict:
    # Note span (v1.4.1 fix): early/late anchored to [first_note, last_note],
    # no longer [0, duration] — long intro/outro silence would make [0,edge] contain
    # only sparse opening notes and [dur-edge,dur] fall entirely in the outro
    # (Hatatagami measured: duration=447s, first note 21.6s, last note 408.4s; the old
    # late=[417,447] had zero notes → all deltas=None, fatigue analysis silently
    # failed). The span convention matches the detail-page timeline trim boundary
    # (repository.get_note_time_range): MIN/MAX over all note events (including
    # miss/bad; bombs naturally fall within the note span).
    note_times = [n.event_time for n in replay.notes]
    if not note_times:
        return {"available": False, "reason": "无 note 事件，无法做前后段对比"}
    first_note = min(note_times)
    last_note = max(note_times)
    note_span = last_note - first_note
    if note_span < 2 * edge_seconds:
        # Note span too short: shrink the comparison window, only provide slopes
        edge_seconds = max(10.0, note_span / 3.0)
        if note_span < 20:
            return {"available": False,
                    "reason": f"note 跨度过短（{note_span:.0f}s），无法做前后段对比"}

    notes = sorted(replay.notes, key=lambda n: n.event_time)
    early = _segment_stats(notes, first_note, first_note + edge_seconds)
    late = _segment_stats(notes, last_note - edge_seconds, last_note)

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

    # Group-level slopes (linear fit of accuracy / center / saber speed / miss_rate over time)
    # Fixed windows retired (2026): x = in-group median note time, each group has a fixed N notes —
    # eliminates the leverage of empty/mixed windows and small-sample endpoints on the fit
    # (Hatatagami's saber-speed slope was once distorted 4.4x, and the accuracy slope
    # direction flipped across 16 songs).
    slopes = _group_slopes(note_groups)
    result["slopes"] = slopes

    # Early/late comparison of hand speed / angular velocity (from the motion series):
    # also anchored to the note span; otherwise intro/outro silence (zero movement)
    # would dilute the early/late hand-speed means — Hatatagami's old algorithm had
    # late frames ≥417s entirely in the outro → hand speed ≈0 → deltas systematically
    # negative-biased (the illusion of "player slowed down a lot", actually silence pollution).
    series = motion.get("series") if isinstance(motion, dict) else None
    if series and series.get("t"):
        ts = series["t"]
        t_cut_early = first_note + edge_seconds
        t_cut_late = last_note - edge_seconds
        for hand in ("left", "right"):
            sp = series[f"{hand}_speed"]
            e_sp = _mean_where(sp, ts, lambda x: first_note <= x < t_cut_early)
            l_sp = _mean_where(sp, ts, lambda x: t_cut_late <= x <= last_note)
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


def _group_slopes(note_groups: list[dict]) -> dict:
    """Group-level slope fitting (replacement after fixed windows retired, see module docstring).

    x = median note event time in the group (t_ref, always present, no legacy-data
    fallback issue); each group has a fixed group_notes notes (constant sample size),
    empty groups do not exist. Fewer than 3 groups → slope None (keeps the old guard).
    """
    import numpy as np
    pts = [(g["t_ref"], g["metrics"]) for g in note_groups]
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
