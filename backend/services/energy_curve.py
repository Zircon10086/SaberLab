"""Timeline energy-curve service (moved out of main.py, 2026-09).

Turns a replay's parsed energy simulation into the step-line samples the detail
page chart draws, with a small bounded cache in front of the file re-parse.

Bounded because the detail view is user-driven: toggling charts redraws the
curve, but the set of replays a session touches stays small. The cache is
cleared wholesale when it grows past the cap, and entries must be dropped when a
replay record is removed (a derived cache must not outlive its record).
"""
from __future__ import annotations

import pathlib

from ..db.repository import Repository

# replay_id -> (ts, vals), or [] meaning "known unavailable" (a negative cache:
# a replay whose file is gone must not be re-parsed on every redraw).
_CACHE: dict[str, tuple] = {}
_CACHE_CAP = 256


def clear(replay_id: str) -> None:
    """Drop the cached curve for one replay (call when its record disappears)."""
    _CACHE.pop(replay_id, None)


def curve_for(repo: Repository, replay_id: str):
    """[(t, energy)] step-line samples for the timeline, or None when unavailable."""
    cached = _CACHE.get(replay_id)
    if cached is not None:
        return cached or None
    row = repo.get_replay(replay_id)
    path = (row or {}).get("file_path")
    if not path or not pathlib.Path(path).exists():
        _CACHE[replay_id] = []
        return None
    try:
        from ..analysis.energy import EnergyConfig, simulate_energy
        from ..bsor import parser as bsor_parser
        rep = bsor_parser.parse_file_light(path)
        res = simulate_energy(rep.notes, rep.walls,
                              EnergyConfig.from_modifiers(rep.info.modifiers))
    except Exception as e:                             # noqa: BLE001 — never break the chart
        print(f"[timeline] energy curve unavailable for {replay_id[:12]}: {e}", flush=True)
        _CACHE[replay_id] = []
        return None
    # Step line: emit (t, value_before) then (t, value_after) per change so the
    # bar reads as flat-then-jump, exactly like the miss/bad event lines.
    ts: list[float] = [0.0]
    vals: list[float] = [res.start_energy]
    for ev in res.events:
        if ev.reason == "fail":
            ts.append(ev.t)
            vals.append(0.0)
            continue
        ts.append(ev.t)
        vals.append(round(ev.energy - ev.delta, 6))
        ts.append(ev.t)
        vals.append(round(ev.energy, 6))
    if len(ts) > 20000:                                # safety cap (chart readability)
        step = len(ts) // 20000 + 1
        ts, vals = ts[::step], vals[::step]
    result = (ts, vals)
    if len(_CACHE) > _CACHE_CAP:
        _CACHE.clear()
    _CACHE[replay_id] = result
    return result
