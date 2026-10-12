"""Note-anchored time series tests (2026 fixed-window retirement).

Covers analysis/notes.py:
- build_note_groups: group boundaries / median x / short trailing group / input
  compatibility (objects vs dicts)
- density_series: uniform sequence, gap dip, edge shrink, single point
- moving_average: centered window, edge shrink, empty input
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.analysis.notes import (  # noqa: E402
    build_note_groups, density_series, moving_average,
)
from backend.bsor.models import GOOD, BAD, MISS, BOMB  # noqa: E402
from tests.test_metrics import good, make_replay  # noqa: E402


def _note(t, etype=GOOD, score=115, center=15, speed=20.0, saber="right"):
    return {"event_time": t, "event_type": etype, "note_score": score,
            "center_score": center, "saber_speed": speed, "saber": saber}


class TestBuildNoteGroups(unittest.TestCase):
    def test_group_boundaries_and_ref(self):
        """10 notes / 4 per group → 3 groups (4+4+2), x = t_ref = median time inside the group."""
        notes = [_note(float(i)) for i in range(10)]
        groups = build_note_groups(notes, group_notes=4)
        self.assertEqual(len(groups), 3)
        g0, g1, g2 = groups
        self.assertEqual(g0["t_first"], 0.0)
        self.assertEqual(g0["t_last"], 3.0)
        self.assertEqual(g1["t_first"], 4.0)
        self.assertEqual(g2["t_last"], 9.0)
        # group 0: times [0,1,2,3], an even count → median (1+2)/2 = 1.5
        self.assertAlmostEqual(g0["t_ref"], 1.5)
        # group 2: [8,9] → median 8.5
        self.assertAlmostEqual(g2["t_ref"], 8.5)
        # notes per group is constant (a short trailing group is kept as-is)
        self.assertEqual([g["metrics"]["note_events"] for g in groups], [4, 4, 2])

    def test_accepts_note_event_objects(self):
        """Accepts NoteEvent objects (the fatigue scenario)."""
        r = make_replay([good(1.0), good(2.0), good(3.0)], duration=5.0)
        groups = build_note_groups(r.notes, group_notes=50)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["metrics"]["good"], 3)
        self.assertAlmostEqual(groups[0]["metrics"]["accuracy_local"], 1.0, places=4)

    def test_empty_input(self):
        self.assertEqual(build_note_groups([], 50), [])

    def test_group_metrics_exclude_bombs_from_rates(self):
        """Bombs are excluded from the miss_rate denominator; bad/miss counts are correct."""
        notes = [_note(1.0, GOOD), _note(2.0, BAD), _note(3.0, MISS),
                 _note(4.0, BOMB)]
        groups = build_note_groups(notes, group_notes=50)
        m = groups[0]["metrics"]
        self.assertEqual(m["good"], 1)
        self.assertEqual(m["bad"], 1)
        self.assertEqual(m["miss"], 1)
        self.assertEqual(m["bomb"], 1)
        # scored = 3 (bombs excluded) → miss_rate 1/3
        self.assertAlmostEqual(m["miss_rate"], 1 / 3, places=4)

    def test_group_density_uses_real_span(self):
        """Density denominator = the group's real time span (no dilution by silence)."""
        # 6 notes in a dense run + the last note 2s later (no silence pollution: span
        # uses the real first/last note)
        notes = [_note(10.0 + i * 0.5) for i in range(5)] + [_note(20.0)]
        groups = build_note_groups(notes, group_notes=50)
        m = groups[0]["metrics"]
        # span = 20-10 = 10s, 6 notes → 0.6
        self.assertAlmostEqual(m["note_density"], 0.6, places=3)

    def test_slope_group_notes_guard(self):
        """group_notes < 1 → falls back to the default 50."""
        notes = [_note(float(i)) for i in range(3)]
        groups = build_note_groups(notes, 0)
        self.assertEqual(len(groups), 1)


class TestDensitySeries(unittest.TestCase):
    def test_uniform_sequence(self):
        """Uniform 0.5s spacing: local density = 10 notes / 5s = 2/s (n=5, centered window)."""
        ts = [i * 0.5 for i in range(21)]   # 0..10s
        d = density_series(ts, n=5)
        self.assertEqual(len(d), 21)
        # center point i=10: (hi-lo)=10, span=5.0 → 2.0
        self.assertAlmostEqual(d[10], 2.0, places=3)

    def test_gap_creates_natural_dip(self):
        """A long gap: notes right before the gap get a low density (their neighbourhood spans
        the silence) — a faithful picture of the beatmap structure."""
        ts = [0.0, 0.5, 1.0, 1.5, 2.0, 10.0, 10.5, 11.0, 11.5, 12.0]
        d = density_series(ts, n=2)
        # i=4 (t=2.0): window notes 2..6 (t=1.0~10.5, span 9.5) → 4/9.5≈0.421 (the dip)
        self.assertAlmostEqual(d[4], 4 / 9.5, places=3)
        # i=5 (t=10.0): window notes 3..7 (t=1.5~11.0, span 9.5) → 4/9.5≈0.421
        self.assertAlmostEqual(d[5], 4 / 9.5, places=3)
        # center of the dense end, i=2 (t=1.0): window notes 0..4 (t=0~2.0, span 2.0) → 4/2=2.0
        self.assertAlmostEqual(d[2], 2.0, places=3)

    def test_edge_shrinks(self):
        """Edge shrink: the first point's window is [0, n]."""
        ts = [i * 0.5 for i in range(21)]
        d = density_series(ts, n=5)
        # i=0: lo=0 hi=5 span=2.5 → 5/2.5=2.0 (matches the center, uniform sequence)
        self.assertAlmostEqual(d[0], 2.0, places=3)

    def test_empty_and_single(self):
        self.assertEqual(density_series([]), [])
        self.assertEqual(density_series([1.0]), [0.0])


class TestMovingAverage(unittest.TestCase):
    def test_centered_and_edge_shrink(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        out = moving_average(values, window=3)
        # i=0: [1,2] → 1.5; i=1: [1,2,3] → 2; i=2: [2,3,4] → 3; ...
        self.assertAlmostEqual(out[0], 1.5, places=4)
        self.assertAlmostEqual(out[1], 2.0, places=4)
        self.assertAlmostEqual(out[2], 3.0, places=4)
        self.assertAlmostEqual(out[4], 4.5, places=4)

    def test_empty(self):
        self.assertEqual(moving_average([]), [])


if __name__ == "__main__":
    unittest.main()
