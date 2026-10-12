"""Fatigue early/late segment boundary-anchoring tests (v1.4.1 fix + 2026
fixed-window retirement adaptation).

Background: analyze_fatigue used to anchor its early/late segments to
[0, duration], so the silence of a long intro/outro left the late segment with
zero notes → every delta became None (fatigue analysis silently stopped working;
measured on Hatatagami: duration=447s, first note 21.6s, last note 408.4s, the old
late=[417,447] held zero notes). Fix: anchor to [first_note, last_note], matching
the detail page's timeline clipping boundary (get_note_time_range). This test
reproduces and verifies it with a synthetic replay (21.6s intro + 32.5s outro).

2026 adaptation: the slope data source moved from fixed time windows
(build_windows, deprecated) to note groups (build_note_groups) — the second
argument of analyze_fatigue is now note_groups.
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.analysis.fatigue import analyze_fatigue, _segment_stats  # noqa: E402
from backend.analysis.motion import analyze_motion  # noqa: E402
from backend.analysis.notes import build_note_groups  # noqa: E402
from tests.test_metrics import good, make_replay  # noqa: E402


def _fatigue(r, edge=30.0):
    """Build note_groups and then call analyze_fatigue (the standard entry point after the
    fixed-window retirement)."""
    return analyze_fatigue(r, build_note_groups(r.notes, 50),
                           analyze_motion(r), edge)


class TestFatigueNoteAnchor(unittest.TestCase):
    def _build(self, duration: float = 120.0):
        # long 21.6s intro + long 32.5s outro: first note 21.6, last note 87.5
        # the old algorithm's late=[duration-30, duration]=[90,120] fell entirely in the outro → 0 notes
        notes = [good(21.6), good(22.0)]          # opening
        notes += [good(50.0), good(50.5)]        # middle
        notes += [good(87.0), good(87.5)]        # closing
        return make_replay(notes, duration=duration)

    def test_late_segment_anchored_to_notes_not_empty(self):
        r = self._build()
        f = _fatigue(r)
        self.assertTrue(f["available"])
        # the point: the late segment no longer falls into the outro silence → it has
        # notes (the old algorithm gave 0 here → every delta was None)
        self.assertGreater(f["late"]["notes"], 0)
        self.assertGreater(f["late"]["good"], 0)
        self.assertGreater(f["early"]["notes"], 0)

    def test_deltas_not_none_with_intro_outro(self):
        """With a long intro/outro the deltas are no longer None just because the late segment is empty."""
        r = self._build()
        f = _fatigue(r)
        for k in ("accuracy", "center", "saber_speed"):
            self.assertIsNotNone(
                f["deltas"][k],
                msg=f"delta {k} 不应为 None（late 段应锚到 note 而非尾奏）")

    def test_motion_hand_speed_delta_not_none(self):
        """The before/after hand-speed comparison is anchored to the note span as well (no longer
        polluted by the outro silence)."""
        r = self._build()
        f = _fatigue(r)
        self.assertIsNotNone(f["deltas"]["left_hand_speed"])
        self.assertIsNotNone(f["deltas"]["right_hand_speed"])

    def test_old_boundary_would_be_empty(self):
        """Regression corroboration: the old boundary [duration-30, duration] really did give an
        empty late segment (the root cause before the fix)."""
        r = self._build(duration=120.0)
        notes = sorted(r.notes, key=lambda n: n.event_time)
        old_late = _segment_stats(notes, 120.0 - 30.0, 120.0)
        self.assertEqual(old_late["notes"], 0)

    def test_no_notes_unavailable(self):
        r = make_replay([], duration=60.0)
        f = _fatigue(r)
        self.assertFalse(f["available"])

    def test_short_note_span_shrinks_edge(self):
        """Note span < 2*edge: the comparison window shrinks automatically (instead of reporting unavailable)."""
        notes = [good(5.0 + i) for i in range(31)]   # 5.0~35.0, span 30s
        r = make_replay(notes, duration=40.0)
        f = _fatigue(r)
        self.assertTrue(f["available"])
        self.assertLess(f["edge_seconds"], 30.0)      # it shrank
        self.assertIsNotNone(f["deltas"]["accuracy"])

    def test_normal_replay_not_regressed(self):
        """An ordinary replay with no intro/outro is not regressed."""
        notes = [good(2.0 + i * 0.3) for i in range(300)]   # 2.0~91.7s
        r = make_replay(notes, duration=95.0)
        f = _fatigue(r)
        self.assertTrue(f["available"])
        self.assertIsNotNone(f["deltas"]["accuracy"])
        self.assertGreater(f["early"]["notes"], 0)
        self.assertGreater(f["late"]["notes"], 0)

    def test_group_slopes_available(self):
        """Note-group slopes: 300 notes / 50 = 6 groups ≥ 3 → the slopes have values
        (window-retirement adaptation)."""
        notes = [good(2.0 + i * 0.3) for i in range(300)]
        r = make_replay(notes, duration=95.0)
        f = _fatigue(r)
        for k in ("accuracy_local_slope_per_min", "center_avg_slope_per_min"):
            self.assertIsNotNone(f["slopes"][k], msg=f"{k} 不应为 None")

    def test_too_few_notes_no_slopes(self):
        """Too few notes (<3 groups) → slopes are None (the old guard still applies)."""
        notes = [good(2.0 + i) for i in range(10)]   # 10 notes → 1 group
        r = make_replay(notes, duration=20.0)
        f = _fatigue(r)
        self.assertFalse(f["available"])   # note span <20s → reported unavailable


if __name__ == "__main__":
    unittest.main()
