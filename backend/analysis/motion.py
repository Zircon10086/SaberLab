"""Motion Analyzer (design doc §11/§12).

Important constraint (§11.2): BSOR records controller pose, not wrist joints.
This module outputs controller angular velocity / hand motion proxies and must
never name them "wrist joint angular velocity".

Data sources:
- frames (120Hz pose series): hand position velocity, path length, controller angular velocity
- note cut info: saber direction, cut points → single-hand consecutive reversal analysis
"""
from __future__ import annotations

import math

import numpy as np

from ..bsor.models import Replay, GOOD, BAD, MISS

# pose layout: head=pos[0:3],rot[3:7]; left=pos[7:10],rot[10:14]; right=pos[14:17],rot[17:21]
LEFT_POS = slice(7, 10)
LEFT_ROT = slice(10, 14)
RIGHT_POS = slice(14, 17)
RIGHT_ROT = slice(17, 21)
HEAD_POS = slice(0, 3)


def _quat_angles(q: np.ndarray) -> np.ndarray:
    """Rotation angle between adjacent quaternions (radians). q: (N,4)"""
    if len(q) < 2:
        return np.zeros(0, dtype=np.float64)
    a = q[:-1]
    b = q[1:]
    dot = np.abs(np.sum(a * b, axis=1))
    dot = np.clip(dot, 0.0, 1.0)
    return 2.0 * np.arccos(dot)


def analyze_motion(replay: Replay, max_series_points: int = 600,
                   warmup_sec: float = 1.0, max_speed: float = 15.0) -> dict:
    """Run hand kinematic analysis on a single Replay.

    Data cleaning (§11.2 engineering constraints):
    - warmup_sec: drop the first N seconds. The first BSOR frame pose is the device's
      initial default position (not the real hand position); the jump from the first
      frame to the real position produces fake speeds of tens to hundreds of m/s;
      also the first few frames have duplicated timestamps (all 0), further amplifying
      that fake value.
    - max_speed: physical cap. Frames whose speed exceeds this value (m/s) are treated
      as anomalies/tracking loss, excluded from stats, and interpolated smoothly in the
      series to avoid polluting the mean/P95/peak and the charts.
    """
    result: dict = {"available": replay.frame_count > 1}
    if replay.frame_count < 2:
        return result

    frames = replay.frames
    t = frames["time"].astype(np.float64)
    pose = frames["pose"].astype(np.float64)

    # ---- Warmup trim: drop the first warmup seconds (includes the first-frame initialization jump) ----
    keep = t >= warmup_sec
    if not np.any(keep):
        # Fallback: drop at least the first frame (it is necessarily an initialization garbage frame)
        keep = np.arange(len(t)) >= 1
    t = t[keep]
    pose = pose[keep]
    if len(t) < 2:
        return {"available": False, "reason": "warmup 裁切后帧数不足"}

    dt = np.diff(t)
    # Protection against duplicate/anomalous timestamps
    median_dt = float(np.median(dt[dt > 0])) if np.any(dt > 0) else 1 / 120
    dt_safe = np.where(dt > 1e-6, dt, median_dt)

    series = {}
    for name, ps, rs in (("left", LEFT_POS, LEFT_ROT),
                          ("right", RIGHT_POS, RIGHT_ROT)):
        pos = pose[:, ps]
        rot = pose[:, rs]
        dist = np.linalg.norm(np.diff(pos, axis=0), axis=1)
        speed = dist / dt_safe                      # m/s
        ang = _quat_angles(rot) / dt_safe           # rad/s

        # ---- Physical-cap filtering: anomalous frames excluded from stats + interpolated in the series ----
        valid = speed <= max_speed
        speed_clean = speed
        if not np.all(valid):
            good = np.where(valid)[0]
            if len(good) >= 2:
                speed_clean = np.interp(np.arange(len(speed)), good, speed[good])
            else:
                speed_clean = np.full_like(speed, float(np.median(speed[valid])))
        stat = speed[valid] if np.any(valid) else speed_clean
        ang_stat = ang[valid] if np.any(valid) else ang
        result[name] = {
            "warmup_sec": warmup_sec,
            "trimmed_frames": int(len(speed) - valid.sum()),
            "path_length_m": round(float(dist.sum()), 2),
            "speed_avg_mps": round(float(stat.mean()), 3),
            "speed_p95_mps": round(float(np.percentile(stat, 95)), 3),
            "speed_peak_mps": round(float(stat.max()), 3),
            "angular_velocity_avg_radps": round(float(ang_stat.mean()), 3),
            "angular_velocity_avg_degps": round(float(np.degrees(ang_stat.mean())), 1),
            "angular_velocity_p95_degps": round(float(np.degrees(np.percentile(ang_stat, 95))), 1),
            "angular_velocity_peak_degps": round(float(np.degrees(ang_stat.max())), 1),
            "angular_velocity_std_degps": round(float(np.degrees(ang_stat.std())), 1),
        }
        series[name] = {"speed": speed_clean, "ang_deg": np.degrees(ang)}

    # Head displacement (body movement proxy)
    head = pose[:, HEAD_POS]
    head_dist = np.linalg.norm(np.diff(head, axis=0), axis=1)
    head_speed = head_dist / dt_safe
    head_valid = head_speed <= max_speed
    result["head"] = {
        "path_length_m": round(float(head_dist.sum()), 2),
        "speed_avg_mps": round(float(head_speed[head_valid].mean()
                                    if np.any(head_valid) else 0.0), 3),
    }

    # ---- Single-hand consecutive reversal analysis (§12) ----
    result["reversal"] = _reversal_analysis(replay)

    # ---- Per-cut path efficiency (straight-line distance / actual path) ----
    result["economy"] = _path_economy(replay, t, pose, dt_safe)

    # ---- Downsampled series (frontend charts) ----
    n = len(t) - 1
    if n > max_series_points:
        idx = np.linspace(0, n - 1, max_series_points).astype(int)
    else:
        idx = np.arange(n)
    ts = t[:-1] if len(t) > 1 else t
    result["series"] = {
        "t": [round(float(x), 3) for x in ts[idx]],
        "left_speed": [round(float(x), 3) for x in series["left"]["speed"][idx]],
        "right_speed": [round(float(x), 3) for x in series["right"]["speed"][idx]],
        "left_ang_deg": [round(float(x), 1) for x in series["left"]["ang_deg"][idx]],
        "right_ang_deg": [round(float(x), 1) for x in series["right"]["ang_deg"][idx]],
    }
    return result


def _vec_angle_deg(a: tuple, b: tuple) -> float:
    ax, ay, az = a
    bx, by, bz = b
    la = math.sqrt(ax * ax + ay * ay + az * az)
    lb = math.sqrt(bx * bx + by * by + bz * bz)
    if la < 1e-9 or lb < 1e-9:
        return 0.0
    dot = max(-1.0, min(1.0, (ax * bx + ay * by + az * bz) / (la * lb)))
    return math.degrees(math.acos(dot))


def _hand_of(note) -> str | None:
    """Which hand a note belongs to: good/bad use the actual saberType, miss uses the color."""
    if note.cut is not None:
        return note.cut.saber
    return note.params.saber


def _reversal_analysis(replay: Replay, fast_dt: float = 0.35) -> dict:
    """Single-hand high-speed consecutive cutting (stream/reversal) capability analysis — design doc §12.

    Does not use the "angle between adjacent saber directions" as the reversal signal
    (consecutive cut directions naturally differ, so it has no discriminative power).
    Instead, a "fast segment" is defined by "same-hand adjacent note interval < fast_dt seconds", answering:
      - What proportion of the time is the player in this hand's high-speed consecutive segments?
      - Are failures (bad/miss) within fast segments notably concentrated? — direct evidence of "can't keep up"
      - Is saber speed maintained within fast segments? (fast-segment speed / slow-segment speed)
    All derived from actual Replay data; single_hand_reversal_score is an internal metric.
    """
    out = {}
    notes = sorted(replay.notes, key=lambda n: n.event_time)

    for hand in ("left", "right"):
        hn = [n for n in notes if _hand_of(n) == hand]
        intervals = []
        fast_notes = 0          # number of notes within fast segments
        fast_fails = 0          # bad/miss within fast segments
        fast_speeds = []        # saber speeds of good cuts within fast segments
        slow_speeds = []        # saber speeds of good cuts outside fast segments
        fails_total = 0
        for prev, cur in zip(hn, hn[1:]):
            dt = cur.event_time - prev.event_time
            if dt <= 0 or dt > 2.0:
                continue
            intervals.append(dt)
            in_fast = dt < fast_dt
            if in_fast:
                fast_notes += 1
            is_fail = cur.event_type in (BAD, MISS)
            if is_fail:
                fails_total += 1
                if in_fast:
                    fast_fails += 1
            elif cur.event_type == GOOD and cur.cut is not None:
                (fast_speeds if in_fast else slow_speeds).append(cur.cut.saber_speed)

        if not intervals:
            out[hand] = {"pairs": 0}
            continue

        n_pairs = len(intervals)
        fast_ratio = fast_notes / n_pairs
        fast_fail_rate = (fast_fails / fast_notes) if fast_notes else 0.0
        overall_fail_rate = fails_total / n_pairs
        fs = sum(fast_speeds) / len(fast_speeds) if fast_speeds else 0.0
        ss = sum(slow_speeds) / len(slow_speeds) if slow_speeds else 0.0
        speed_retention = (fs / ss) if ss > 1e-6 else None
        avg_dt = sum(intervals) / n_pairs

        out[hand] = {
            "pairs": n_pairs,
            "hit_interval_avg_ms": round(avg_dt * 1000, 1),
            "hit_interval_min_ms": round(min(intervals) * 1000, 1),
            "fast_pairs": fast_notes,
            "fast_ratio": round(fast_ratio, 4),
            "fast_fail_rate": round(fast_fail_rate, 4),
            "overall_fail_rate": round(overall_fail_rate, 4),
            # Fast-segment failure concentration: >1 means failures skew toward fast segments (a sign of not keeping up)
            "fast_fail_concentration": (round(fast_fail_rate / overall_fail_rate, 3)
                                         if overall_fail_rate > 1e-9 else None),
            "fast_saber_speed_avg": round(fs, 2),
            "slow_saber_speed_avg": round(ss, 2),
            "speed_retention": (round(speed_retention, 3)
                                 if speed_retention is not None else None),
            # Internal metric: fast-segment ratio × fast-segment speed retention (higher = stronger high-speed continuity)
            "single_hand_reversal_score": (round(fast_ratio * (speed_retention or 0.0) * 100, 2)
                                            if speed_retention is not None else None),
        }
    return out



def _path_economy(replay: Replay, t: np.ndarray, pose: np.ndarray,
                  dt_safe: np.ndarray) -> dict:
    """Between adjacent same-hand cuts: straight-line path vs actual hand path → motion economy (≤1).

    Uses a cumulative path-length array per hand for O(1) range queries, avoiding per-pair slicing.
    """
    out = {}
    good = [n for n in replay.notes if n.event_type == GOOD and n.cut is not None]
    good.sort(key=lambda n: n.event_time)

    # Per-hand cumulative path length cum[i] = path length from frame 0 to frame i
    cum_by_hand = {}
    for hand, ps in (("left", LEFT_POS), ("right", RIGHT_POS)):
        pos = pose[:, ps]
        seg_len = np.linalg.norm(np.diff(pos, axis=0), axis=1)
        cum_by_hand[hand] = np.concatenate([[0.0], np.cumsum(seg_len)])

    for hand, st in (("left", 0), ("right", 1)):
        cum = cum_by_hand[hand]
        hc = [n for n in good if n.cut.saber_type == st]
        ratios = []
        for a, b in zip(hc, hc[1:]):
            straight = math.dist(a.cut.cut_point, b.cut.cut_point)
            if straight < 0.02:
                continue
            i0 = int(np.searchsorted(t, a.event_time))
            i1 = int(np.searchsorted(t, b.event_time))
            i0 = min(max(i0, 0), len(cum) - 1)
            i1 = min(max(i1, 0), len(cum) - 1)
            if i1 - i0 < 1:
                continue
            actual = float(cum[i1] - cum[i0])
            if actual > 1e-6:
                ratios.append(min(1.0, straight / actual))
        out[hand] = {
            "samples": len(ratios),
            "economy_avg": round(sum(ratios) / len(ratios), 4) if ratios else None,
        }
    return out
