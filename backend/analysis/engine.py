"""Analysis engine orchestration: parse result -> all metrics -> DB persistence format.

Principle (design doc §3.4 / Rule 7): all numbers are computed deterministically
in this layer; the AI layer only interprets and never produces data.

Timeline (2026 decision, fixed windows retired): all time-based analysis is anchored
to note events (backend/analysis/notes.py) — curves are per-note, slopes are grouped
per note; fixed [0, duration] time windows are no longer used
(backend/analysis/windows.py is deprecated).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..bsor.models import Replay
from ..db.repository import Repository
from .scoring import compute_score
from .accuracy import analyze_accuracy
from .notes import build_note_groups
from .motion import analyze_motion
from .fatigue import analyze_fatigue
from .energy import EnergyConfig, simulate_energy


def analyze_replay(replay: Replay, cfg, repo: Optional[Repository] = None,
                   save: bool = True, filename_exit: bool = False) -> dict:
    """Run the full analysis for a single parsed Replay. Returns structured results.

    filename_exit: the "-exit-" marker in BeatLeader filenames (mid-run exit).
    The filename marker is authoritative on the game side and takes highest priority.
    """
    # 1) Official-convention total score / accuracy / combo
    score_result = compute_score(replay)

    # 2) Accuracy / Pre-Center-Post / per-hand / grid
    acc = analyze_accuracy(replay)

    # 3) Note grouping (for slopes / AI summaries; fixed windows retired, no window aggregation)
    note_groups = build_note_groups(replay.notes, cfg.slope_group_notes)

    # 4) Kinematics
    motion = analyze_motion(replay)

    # 5) Fatigue (early/late segments + group-level slopes, all note-anchored)
    fatigue = analyze_fatigue(replay, note_groups, motion, cfg.fatigue_edge_seconds)

    duration = float(replay.frames["time"][-1]) if replay.frame_count else 0.0
    fps_median = float(np.median(replay.frames["fps"])) if replay.frame_count else 0.0

    counts = acc["counts"]
    # Full combo = no combo-breaker events. Bombs break the combo exactly like
    # bad cuts/misses do (compute_score treats them as penalty events, as the
    # real game does), and counts["bomb"] counts only bombs actually CUT —
    # untouched bombs produce no note event in the BSOR scoring stream
    # (bomb events were excluded from FC before 2026-08: a run that hit a bomb
    # used to be flagged FC while max_combo proved the combo broke).
    full_combo = (counts["bad"] == 0 and counts["miss"] == 0
                  and counts["bomb"] == 0)

    # 6) Completion status determination
    # Priority: filename exit (mid-run exit, authoritative) > modifiers contain NF (auto-enabled after Fail) > fail_time > duration
    # Fetch the map song_length (queried from the repo)
    song_length = 0.0
    if repo is not None and replay.info.map_hash:
        map_info = repo.get_map(replay.info.map_hash)
        if map_info:
            song_length = float(map_info.get("song_length") or 0.0)

    mods = (replay.info.modifiers or "").upper()
    has_nf = "NF" in mods          # No Fail: auto-enabled after energy depletion, i.e. actually failed

    # 5b) Energy / fail time (official algorithm ported from the decompiled
    # GameEnergyCounter, see .energy module docstring). The .bsor failTime field
    # is never written by the mod (498/498 local files are 0.0), so this computed
    # value is the only source for the fail moment.
    # NOTE: completion_status below still keys on the RECORDED fail_time — switching
    # it to the computed one changes existing classification behaviour and is a
    # separate product decision (the computed values are exposed in the summary and
    # in the metrics table meanwhile).
    energy_cfg = EnergyConfig.from_modifiers(replay.info.modifiers)
    energy_result = simulate_energy(replay.notes, replay.walls, energy_cfg)
    fail_time = float(replay.info.fail_time or 0.0)
    if filename_exit:
        # BeatLeader filename explicitly marks a mid-run exit -> incomplete
        completion_status = "incomplete"
    elif has_nf:
        # NF marker = actually failed (Beat Saber auto-enables No Fail to continue) -> completed but failed
        completion_status = "failed"
    elif fail_time > 0:
        completion_status = "failed"
    elif song_length > 0 and duration < song_length * 0.98:
        completion_status = "incomplete"
    else:
        completion_status = "completed"

    # NF (fail) score penalty: per Beat Saber's official convention, the actual score after failing is halved
    raw_score = int(replay.info.score or 0)
    effective_score = raw_score // 2 if has_nf else raw_score

    summary = {
        "score_recomputed": score_result.total_score,
        "score_effective": effective_score,
        "has_nf": has_nf,
        "accuracy": round(score_result.accuracy, 5),
        "max_combo": score_result.max_combo,
        "full_combo": full_combo,
        "duration": round(duration, 2),
        "fps_median": fps_median,
        "frame_count": replay.frame_count,
        "note_count": len(replay.notes),
        "completion_status": completion_status,
        "song_length": song_length,
        "filename_exit": filename_exit,
        "energy": energy_result.to_dict(),
        # convenience copies of the two most-used energy facts (the full structured
        # result above stays the source of truth for future features)
        "energy_fail_time": energy_result.fail_time,
        "energy_min": round(energy_result.min_energy, 6),
        **{f"{k}_count": v for k, v in counts.items()},
    }

    result = {
        "summary": summary,
        "score_graph": score_result.score_graph,
        "block_accuracy": score_result.block_accuracy,
        "accuracy": acc,
        "note_groups": note_groups,
        "motion": motion,
        "fatigue": fatigue,
        "energy": energy_result,
    }

    if save and repo is not None:
        _persist(replay, result, repo)
    return result


def _persist(replay: Replay, result: dict, repo: Repository) -> None:
    """Persist one replay's derived data in a SINGLE connection + transaction.

    Perf (2026-09): the four writes below used to run as four independent
    transactions (~48 ms/replay, 74% of the whole per-replay analysis cost —
    mostly commit round-trips against a ~100 MB WAL database). One session
    makes the same writes ~3x cheaper and makes a replay's rows ATOMIC: a
    failure now rolls the whole replay back instead of leaving it half-written.
    """
    with repo.session():
        rid = replay.file_sha256
        # notes table
        repo.insert_notes(rid, result["accuracy"]["note_rows"])

        # metrics table
        rows: list[tuple[str, str, float, str]] = []
        s = result["summary"]
        rows.append(("overall", "score", float(replay.info.score), ""))
        rows.append(("overall", "score_recomputed", float(s["score_recomputed"]), ""))
        rows.append(("overall", "accuracy", s["accuracy"], ""))
        rows.append(("overall", "max_combo", float(s["max_combo"]), ""))
        rows.append(("overall", "full_combo", 1.0 if s["full_combo"] else 0.0, ""))
        rows.append(("overall", "duration", s["duration"], ""))
        rows.append(("overall", "good_count", float(s["good_count"]), ""))
        rows.append(("overall", "bad_count", float(s["bad_count"]), ""))
        rows.append(("overall", "miss_count", float(s["miss_count"]), ""))
        rows.append(("overall", "bomb_count", float(s["bomb_count"]), ""))

        # energy / fail telemetry (official algorithm, see analysis/energy.py). Stored
        # as metrics so existing read paths (detail API, AI context, future features)
        # pick them up without new tables; the full event stream is kept in the
        # analysis result and only its summary aggregates are persisted.
        en = s.get("energy") or {}
        if en:
            if en.get("fail_time") is not None:
                rows.append(("energy", "fail_time", float(en["fail_time"]), ""))
            for name in ("start_energy", "end_energy", "min_energy", "max_energy",
                         "final_charge", "total_drain", "time_in_danger"):
                v = en.get(name)
                if isinstance(v, (int, float)):
                    rows.append(("energy", name, float(v), ""))
            rows.append(("energy", "did_reach_zero", 1.0 if en.get("did_reach_zero") else 0.0, ""))
            rows.append(("energy", "obstacle_hits", float(len(en.get("obstacle_hits") or [])), ""))
            for reason, v in (en.get("drain_by_reason") or {}).items():
                rows.append(("energy", f"drain_{reason}", float(v), ""))

        import json as _json
        for hand, hs in result["accuracy"]["hands"].items():
            for name in ("pre_score_avg", "center_score_avg", "post_score_avg",
                         "cut_distance_cm_avg", "saber_speed_avg", "time_dev_avg_ms",
                         "time_dev_abs_avg_ms", "time_dev_std_ms", "late_ratio",
                         "good", "bad", "miss"):
                v = hs.get(name)
                if isinstance(v, (int, float)):
                    rows.append((hand, name, float(v), ""))
            rows.append((hand, "grid_acc", float(sum(g for g in hs["grid_acc"] if g)),
                         _json.dumps(hs["grid_acc"])))

        rev = result["motion"].get("reversal", {})
        for hand in ("left", "right"):
            r = rev.get(hand) or {}
            for name in ("fast_ratio", "fast_pairs", "single_hand_reversal_score",
                         "hit_interval_avg_ms", "fast_saber_speed_avg",
                         "fast_fail_rate", "fast_fail_concentration",
                         "speed_retention"):
                if isinstance(r.get(name), (int, float)):
                    rows.append((hand, name, float(r[name]), ""))
            eco = (result["motion"].get("economy") or {}).get(hand) or {}
            if eco.get("economy_avg") is not None:
                rows.append((hand, "path_economy", float(eco["economy_avg"]), ""))
            mhand = result["motion"].get(hand) or {}
            for name in ("path_length_m", "speed_avg_mps", "speed_p95_mps",
                         "speed_peak_mps", "angular_velocity_avg_degps",
                         "angular_velocity_p95_degps", "angular_velocity_peak_degps",
                         "angular_velocity_std_degps"):
                if isinstance(mhand.get(name), (int, float)):
                    rows.append((hand, name, float(mhand[name]), ""))

        if result["fatigue"].get("available"):
            for k, v in result["fatigue"]["deltas"].items():
                if isinstance(v, (int, float)):
                    rows.append(("fatigue", f"delta_{k}", float(v), ""))
            for k, v in result["fatigue"]["slopes"].items():
                if isinstance(v, (int, float)):
                    rows.append(("fatigue", k, float(v), ""))

        repo.save_metrics(rid, rows)

        # Motion series (charts)
        if result["motion"].get("series"):
            repo.save_motion_series(rid, result["motion"]["series"])

        # Official-convention per-block accuracy curve (2026-08: curve aligned with replay records)
        if result.get("block_accuracy"):
            repo.save_accuracy_curve(rid, result["block_accuracy"])
