"""AI Context Builder (design docs §15.1).

The AI never computes raw metrics; this module packs the structured results
produced by the engine + historical scores on the same map + research
hypotheses into a compact JSON.

Timeline (2026 decision, fixed-window retirement): window summaries were
replaced with **note-group summaries** (backend/analysis/notes.py
build_note_groups) — one group per N notes, a group being a set of notes;
there are no empty or mixed windows, and every group outputs its real time
range [t_first, t_last]. This also eliminates the problems of the
fixed-window era: empty windows in the intro/outro (acc/center/speed all
None) and silently diluted mixed windows (distorted density, t starting at 0).
"""
from __future__ import annotations

from ..config import Config
from ..db.repository import Repository
from ..analysis.notes import build_note_groups

RESEARCH_HYPOTHESES = (
    "H1: Center is the main Accuracy technical bottleneck; "
    "H2: Center degrades at high difficulty (low-pressure vs high-pressure); "
    "H3: Local fatigue first shows up as direction-change failures and misses, "
    "not an immediate Center drop; "
    "H4: Saber Profile significantly affects movement economy; "
    "H5: Training effectiveness = keeping movement quality across time and "
    "difficulty, not a single PP score."
)


def _note_group_timeline(note_groups: list[dict],
                         include_groups: int = 8) -> list[dict]:
    """Compress the note-group sequence into an AI context summary (after fixed-window retirement).

    Each group holds a fixed N notes (no empty groups); take the first and
    last include_groups//2 groups each plus an ellipsis marker in the middle;
    every group outputs its real time range [t_first, t_last] and key metrics.
    """
    out: list[dict] = []
    if len(note_groups) > include_groups:
        head = note_groups[: include_groups // 2]
        tail = note_groups[-(include_groups // 2):]
        picked = head + [None] + tail
    else:
        picked = note_groups
    for g in picked:
        if g is None:
            out.append({"note": "...intermediate groups omitted..."})
            continue
        m = g["metrics"]
        out.append({
            "t": [round(g["t_first"], 1), round(g["t_last"], 1)],
            "acc": m.get("accuracy_local"),
            "center": m.get("center_avg"),
            "miss_rate": m.get("miss_rate"),
            "bad_rate": m.get("bad_rate"),
            "speed": m.get("saber_speed_avg"),
            "density": m.get("note_density"),
            "imbalance": m.get("lr_imbalance"),
            "notes": m.get("note_events"),
        })
    return out


def build_context(repo: Repository, cfg: Config, replay_id: str,
                  include_windows: int = 8) -> dict:
    replay = repo.get_replay(replay_id)
    if replay is None:
        raise KeyError(f"replay 不存在: {replay_id}")
    metrics = repo.get_metrics(replay_id)
    motion_series = repo.get_motion_series(replay_id)

    # Historical attempts on the same map (same difficulty)
    history = repo.previous_attempts_on_map(
        replay["map_hash"], replay["difficulty"], replay["timestamp"], limit=5)

    # Time-series compression: take note groups from the head/middle/tail
    # (fixed-window retirement, see module docstring)
    note_groups = build_note_groups(repo.get_note_events(replay_id),
                                    cfg.slope_group_notes)
    win_summary = _note_group_timeline(note_groups, include_windows)

    ctx = {
        "meta": {
            "analysis_scope": "Local deterministic Replay analysis result; the AI only interprets",
            "units": {"center_score": "0-15, higher is better", "pre_score": "0-70",
                      "post_score": "0-30", "cut_distance_cm": "lower is better",
                      "time_dev_ms": "cut timing deviation"},
            "fatigue_note": "Fatigue-related output is a kinematic proxy, not a medical diagnosis",
        },
        "replay": {
            "song_name": replay["song_name"],
            "difficulty": replay["difficulty"],
            "mode": replay["mode"],
            "modifiers": replay["modifiers"],
            "score": replay["score"],
            "score_recomputed": replay["score_recomputed"],
            "accuracy": replay["accuracy"],
            "duration": replay["duration"],
            "won": bool(replay["won"]),
            "good": replay["good_count"], "bad": replay["bad_count"],
            "miss": replay["miss_count"], "bomb": replay["bomb_count"],
            "max_combo": replay["max_combo"],
            "full_combo": bool(replay["full_combo"]),
            "hmd": replay["hmd"], "controller": replay["controller"],
            "profile_id": replay["profile_id"],
        },
        "hands": {
            "left": metrics.get("left", {}),
            "right": metrics.get("right", {}),
        },
        "overall_metrics": metrics.get("overall", {}),
        "fatigue": metrics.get("fatigue", {}),
        "windows_timeline": win_summary,
        "history_same_map": history,
        "research_hypotheses": RESEARCH_HYPOTHESES,
    }
    return ctx
