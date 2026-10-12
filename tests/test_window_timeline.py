"""Tests for the AI note-group summary (2026 fixed-window retirement adaptation).

Background: ai/context._window_timeline used to take a few windows from each end
of the `windows` series — but that series covers [0, duration] and contains empty
windows over the intro/outro silence (Hatatagami: last note 408s, duration 447s,
so the last few windows all fell in the outro → acc/center/speed all None and the
AI wrongly read it as "the replay ends with no data"). v1.4.1 once fixed this by
"skipping empty windows, then taking head and tail".

2026 decision: fixed time windows are retired outright — the summary is built from
note groups instead (build_note_groups, one group per N notes). A group IS a set
of notes: there is no such thing as an empty group, every group necessarily
carries data, and each group reports its real time range [t_first, t_last].
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.ai.context import _note_group_timeline  # noqa: E402
from backend.analysis.notes import build_note_groups  # noqa: E402
from backend.bsor.models import GOOD  # noqa: E402


def _note(t, score=115, center=15, speed=20.0, saber="right", etype=GOOD):
    return {"event_time": t, "event_type": etype, "note_score": score,
            "center_score": center, "saber_speed": speed, "saber": saber}


class TestNoteGroupTimeline(unittest.TestCase):
    def test_head_tail_take_active_groups(self):
        """Hatatagami-style: long intro (starting 21.6s) + silent outro → grouping
        starts naturally at the first note, so the first group's t start = the first
        note time and the last group's t end = the last note time."""
        # 20s of intro with no notes, then a dense run of 60 notes (from 21.6s), 20s of outro with no notes
        notes = [_note(21.6 + i * 0.3, speed=20.0 + i * 0.1) for i in range(60)]
        groups = build_note_groups(notes, group_notes=20)   # 3 groups
        s = _note_group_timeline(groups, include_groups=8)
        self.assertEqual(len(s), 3)
        # the first group starts its t at the real first note time (not at the 0s intro)
        self.assertAlmostEqual(s[0]["t"][0], 21.6, places=1)
        # the last group ends its t at the real last note time (not at the outro)
        self.assertAlmostEqual(s[-1]["t"][1], 21.6 + 59 * 0.3, places=1)
        # every group carries data (no empty groups)
        for item in s:
            self.assertGreater(item["notes"], 0)
            self.assertIsNotNone(item["acc"])

    def test_many_groups_truncated_with_marker(self):
        """More groups than include → head + omission marker + tail."""
        notes = [_note(10.0 + i * 0.5) for i in range(400)]   # 400 notes / 50 = 8 groups
        groups = build_note_groups(notes, group_notes=50)
        self.assertEqual(len(groups), 8)
        s = _note_group_timeline(groups, include_groups=6)
        self.assertEqual(len(s), 7)   # 3 + marker + 3
        self.assertEqual(s[3], {"note": "...intermediate groups omitted..."})
        # head/tail are not truncated: the first group starts at the first note and the
        # last group ends at the last note
        self.assertAlmostEqual(s[0]["t"][0], 10.0, places=1)
        self.assertAlmostEqual(s[-1]["t"][1], 10.0 + 399 * 0.5, places=1)

    def test_few_groups_no_truncation(self):
        notes = [_note(5.0), _note(6.0), _note(7.0)]   # 1 group
        groups = build_note_groups(notes, group_notes=50)
        s = _note_group_timeline(groups, include_groups=8)
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["notes"], 3)

    def test_empty_notes_returns_empty(self):
        groups = build_note_groups([], group_notes=50)
        self.assertEqual(groups, [])
        self.assertEqual(_note_group_timeline(groups), [])

    def test_group_metrics_and_density(self):
        """In-group aggregation convention: acc/center/saber speed/miss_rate/density/left-right imbalance."""
        notes = [_note(10.0 + i, score=115 if i % 2 == 0 else 100,
                       center=15 if i % 2 == 0 else 10, speed=22.0)
                 for i in range(6)]
        groups = build_note_groups(notes, group_notes=6)
        m = groups[0]["metrics"]
        self.assertEqual(m["good"], 6)
        self.assertAlmostEqual(m["accuracy_local"], (115 * 3 + 100 * 3) / (115 * 6), places=4)
        self.assertAlmostEqual(m["center_avg"], (15 * 3 + 10 * 3) / 6, places=3)
        self.assertAlmostEqual(m["saber_speed_avg"], 22.0, places=3)
        # group time span 10.0~15.0 = 5s → density 6/5
        self.assertAlmostEqual(m["note_density"], 6 / 5, places=3)


if __name__ == "__main__":
    unittest.main()
