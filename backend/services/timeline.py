"""Timeline payload assembly (moved out of main.py, 2026-09).

Builds the chart data the detail page draws. The shaping is deterministic and
depends only on stored replay data plus the energy simulation, so it lives here
rather than in the route: `main.py` keeps validation and orchestration only.

The moving-average / density primitives stay in `backend/analysis/notes.py` —
they are the analysis layer's, and this module only arranges their output into
the payload the chart expects.
"""
from __future__ import annotations

from ..analysis.notes import density_series, moving_average
from ..bsor.models import BOMB, GOOD
from ..db.repository import Repository
from . import energy_curve

#: Good-cut local mean width (±7 notes). Raw per-note saber speeds jump a lot
#: (2026-08 user request for smoother viewing); the value is still the mean of the
#: same batch of real good cuts, so it stays traceable.
SPEED_WINDOW = 15
#: Density neighborhoods: ±5 notes for the local density, then a ±2 note centred
#: mean to smooth the sharp jumps at pause edges (valleys are kept — they are the
#: map's real gaps).
DENSITY_WINDOW = 5
DENSITY_SMOOTH = 5


def build_timeline(repo: Repository, replay_id: str) -> dict:
    """Chart data (note-anchored; the fixed time-window mode is retired, 2026):

    * ``notes``: per-note cumulative acc/center curves (good cuts, x = event time)
      + saber speed (±7 good-cut local mean)
      + local density (±5 note neighborhood, natural valleys in map gaps —
        faithful to the data, then ±2 note rounding)
      + ``energy_t`` / ``energy``: the simulated energy bar (2026-09)
    * ``events``: miss/bad event timestamps (event step lines)
    * ``note_range``: first/last note times (timeline trim bounds)
    * ``windows``: reserved field (legacy history / empty arrays, backward
      compatibility; the engine no longer writes it)

    Miss/bad notes contribute no speed or density points: they are never padded
    with 0 nor interpolated, since a fabricated point would read as real data.
    """
    events = repo.get_note_events(replay_id)
    notes = repo.get_accuracy_curve(replay_id)
    good_sp = [(e["event_time"], e["saber_speed"]) for e in events
               if e["event_type"] == GOOD and e["saber_speed"] is not None]
    notes["speed_t"] = [t for t, _ in good_sp]
    notes["speed"] = moving_average([s for _, s in good_sp], SPEED_WINDOW)
    ts_all = [e["event_time"] for e in events if e["event_type"] != BOMB]
    notes["density_t"] = ts_all
    notes["density"] = moving_average(
        density_series(ts_all, DENSITY_WINDOW), DENSITY_SMOOTH)
    # Energy bar (2026-09): rebuilt from the replay on demand rather than
    # persisted — the energy module is a pure function of the note/wall events,
    # so a lightweight parse (frames and the large section skipped) + simulate
    # costs ~1 ms and keeps the DB free of a redundant derived series. Failures
    # are non-fatal: a missing/broken file simply yields no energy series.
    en = energy_curve.curve_for(repo, replay_id)
    if en:
        notes["energy_t"], notes["energy"] = en
    return {"windows": repo.get_windows(replay_id),
            "events": repo.get_miss_bad_events(replay_id),
            "notes": notes,
            "note_range": repo.get_note_time_range(replay_id)}
