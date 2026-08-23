"""Note-anchored time series and grouped aggregation (replacement after fixed time windows were retired).

Background (2026 decision): fixed time windows [0, duration] severely distort maps with
long intros/outros — empty windows (no notes), mixed windows (silence + gameplay dilutes
density; Hatatagami's first window measured at only 18% of the true value), and
small-sample endpoints (means of 2-3 notes participate in slope fitting with equal
weight). Conclusion: **all time-based analysis is anchored to note events**:

- Curves: per-note raw value or ±N local mean (x = event time, no fixed-width window aggregation)
- Density: per-note local density (N-note neighborhood); long map gaps naturally show valleys — faithful to the data
- Grouping: every N notes form a group (x = in-group median time), for fatigue slopes / AI time summaries

Replaces build_windows in backend/analysis/windows.py (that module is kept as deprecated).
This layer is pure computation (analysis-layer responsibility); input accepts NoteEvent
objects or dicts (DB rows).
"""
from __future__ import annotations

GOOD, BAD, MISS, BOMB = 0, 1, 2, 3


def _attr(n, key):
    """Unified note attribute access: NoteEvent objects (fatigue scenario) or dicts (DB rows)."""
    return n[key] if isinstance(n, dict) else getattr(n, key)


def _note_stats(n):
    """Extract (note_score, center_score, saber_speed, saber).

    dict (DB row): use the column values directly; NoteEvent objects (fatigue scenario)
    lack these attributes, so compute them from cut — convention consistent with
    accuracy.py (note_score = before+center+after, good cuts only; miss/bad are 0).
    """
    if isinstance(n, dict):
        return (float(n.get("note_score") or 0),
                float(n.get("center_score") or 0),
                float(n.get("saber_speed") or 0.0), n.get("saber"))
    from .scoring import cut_scores
    b, c, a = cut_scores(n)
    sp = n.cut.saber_speed if n.cut else 0.0
    return (float(b + c + a), float(c), float(sp), n.saber)


def moving_average(values: list, window: int = 5) -> list:
    """Centered moving average (edge-shrinking window).

    Same semantics as repository._moving_average (that function is kept in the db layer
    for compatibility with old callers; this function serves pure computation in the
    analysis layer, avoiding a db -> analysis reverse dependency).
    """
    if not values:
        return []
    w = max(1, int(window))
    half = w // 2
    n = len(values)
    out: list = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        seg = values[lo:hi]
        out.append(round(sum(seg) / len(seg), 4))
    return out


def density_series(times: list, n: int = 5) -> list:
    """Per-note local density: x = note event time, y = neighborhood note count / time span.

    y_i = (hi - lo) / (times[hi] - times[lo]), lo=max(0,i-n), hi=min(m-1,i+n)
    (edge-shrinking, same idea as moving_average).

    Semantics: how dense the note stream is around this note (notes/second). **The two
    ends of long gaps naturally show valleys** — that is the map's real structure (e.g.
    34 pauses >2s in the middle of Hatatagami), rendered faithfully rather than smoothed
    away. times must be in ascending order.
    """
    m = len(times)
    if m == 0:
        return []
    if m == 1:
        return [0.0]
    out: list = []
    for i in range(m):
        lo = max(0, i - n)
        hi = min(m - 1, i + n)
        span = times[hi] - times[lo]
        if span <= 0:
            out.append(0.0)   # multiple notes at the same time (anomalous data): density is undefined
        else:
            out.append(round((hi - lo) / span, 3))
    return out


def build_note_groups(notes, group_notes: int = 50) -> list[dict]:
    """Group the note sequence by group_notes per group in time order, aggregating in-group metrics.

    Input: list[NoteEvent] or list[dict] (keys event_time / event_type /
           note_score / center_score / saber_speed / saber).
    Output: [{t_first, t_last, t_ref, metrics}]:
          - t_first/t_last: event time of the first/last note in the group (real time range)
          - t_ref: median note event time in the group (x anchor for slope fitting)
          - metrics convention matches fatigue._segment_stats + left/right imbalance/density

    Properties (vs fixed windows): no empty groups (group = note set), no silent
    denominator (density uses the real in-group span), constant sample size (group_notes
    per group -> statistically stable slope fitting). Empty input returns [].
    """
    if not notes:
        return []
    if group_notes < 1:
        group_notes = 50
    sorted_notes = sorted(notes, key=lambda n: _attr(n, "event_time"))
    groups: list[dict] = []
    for i in range(0, len(sorted_notes), group_notes):
        chunk = sorted_notes[i:i + group_notes]
        t_first = float(_attr(chunk[0], "event_time"))
        t_last = float(_attr(chunk[-1], "event_time"))
        ts = sorted(float(_attr(n, "event_time")) for n in chunk)
        mid = len(ts) // 2
        t_ref = ts[mid] if len(ts) % 2 == 1 else (ts[mid - 1] + ts[mid]) / 2.0
        metrics = _group_metrics(chunk)
        span = t_last - t_first
        if span > 0:
            metrics["note_density"] = round(len(chunk) / span, 3)
        groups.append({"t_first": t_first, "t_last": t_last,
                       "t_ref": t_ref, "metrics": metrics})
    return groups


def _group_metrics(chunk) -> dict:
    """In-group aggregation (convention consistent with fatigue._segment_stats / windows._window_metrics)."""
    good = [n for n in chunk if _attr(n, "event_type") == GOOD]
    bad = sum(1 for n in chunk if _attr(n, "event_type") == BAD)
    miss = sum(1 for n in chunk if _attr(n, "event_type") == MISS)
    bombs = sum(1 for n in chunk if _attr(n, "event_type") == BOMB)
    m = {"note_events": len(chunk), "good": len(good), "bad": bad,
         "miss": miss, "bomb": bombs}
    scored = [n for n in chunk if _attr(n, "event_type") != BOMB]
    if scored:
        m["miss_rate"] = round(miss / len(scored), 4)
        m["bad_rate"] = round(bad / len(scored), 4)
    if good:
        total = center = speed = 0.0
        left_n = left_score = right_n = right_score = 0
        for n in good:
            ns, cs, sp, saber = _note_stats(n)
            total += ns
            center += cs
            speed += sp
            if saber == "left":
                left_n += 1
                left_score += ns
            else:
                right_n += 1
                right_score += ns
        g = len(good)
        m["accuracy_local"] = round(total / (115 * g), 4)
        m["center_avg"] = round(center / g, 3)
        m["saber_speed_avg"] = round(speed / g, 3)
        denom = left_score + right_score
        # >0 favors the right hand, <0 favors the left hand (consistent with the windows convention)
        m["lr_imbalance"] = round((right_score - left_score) / denom, 4) if denom else 0.0
        m["left_notes"] = left_n
        m["right_notes"] = right_n
    return m
