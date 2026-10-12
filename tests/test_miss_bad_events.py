"""miss/bad event timestamp regression test (v1.4.1).

Background: the cumulative-miss curve used to add up miss counts from overlapping
windows (30s wide / 1s step), so a single event was counted roughly 30 times
(GENTLEMAN showed 4 misses as 120).
Fix: get_miss_bad_events queries the notes table by event timestamp, so every event
is counted exactly once.
"""
import pathlib
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.bsor.models import GOOD, BAD, MISS  # noqa: E402
from backend.db.repository import Repository  # noqa: E402


class TestMissBadEvents(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.repo = Repository(self.tmpdir / "ev.sqlite")
        with self.repo._conn() as c:
            c.execute(
                "INSERT INTO replays(replay_id, timestamp, status, analysis_status)"
                " VALUES('r1', 1700000000, 'analyzed', 'analyzed')")
            # 4 miss + 3 bad + some good/bomb, deliberately unsorted in time (verifies ORDER BY)
            rows = [
                ("r1", 0, 0, 5.0, 0.0, GOOD),
                ("r1", 1, 1, 17.09, 16.0, MISS),
                ("r1", 2, 2, 131.85, 130.0, BAD),
                ("r1", 3, 3, 176.85, 175.0, MISS),
                ("r1", 4, 4, 177.57, 176.0, MISS),
                ("r1", 5, 5, 3.0, 2.0, GOOD),
                ("r1", 6, 6, 61.21, 60.0, BAD),
                ("r1", 7, 7, 9.0, 8.0, 3),   # BOMB (not counted)
                ("r1", 8, 8, 180.0, 179.0, MISS),
            ]
            c.executemany(
                "INSERT INTO notes(replay_id, idx, note_id, event_time, spawn_time,"
                " event_type) VALUES(?,?,?,?,?,?)", rows)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_events_match_counts_and_sorted(self):
        ev = self.repo.get_miss_bad_events("r1")
        self.assertEqual(ev["miss"], [17.09, 176.85, 177.57, 180.0])
        self.assertEqual(ev["bad"], [61.21, 131.85])
        # consistent with the replays summary counts (this fixture has miss=4, bad=2)
        self.assertEqual(len(ev["miss"]), 4)
        self.assertEqual(len(ev["bad"]), 2)

    def test_no_events_empty(self):
        ev = self.repo.get_miss_bad_events("no_such_id")
        self.assertEqual(ev, {"miss": [], "bad": []})

    def test_good_bomb_excluded(self):
        ev = self.repo.get_miss_bad_events("r1")
        for t in ev["miss"] + ev["bad"]:
            self.assertNotIn(t, (5.0, 3.0, 9.0))   # good/bomb times are not in the events

    def test_note_time_range(self):
        """First/last note time: the timeline clipping bounds (shared by two cards)."""
        nr = self.repo.get_note_time_range("r1")
        self.assertAlmostEqual(nr["first_note"], 3.0)    # earliest note (3.0, before the good at 5.0)
        self.assertAlmostEqual(nr["last_note"], 180.0)   # the last miss
        # both ends lie inside the events/notes range
        self.assertLess(nr["first_note"], nr["last_note"])

    def test_note_time_range_empty(self):
        nr = self.repo.get_note_time_range("no_such_id")
        self.assertEqual(nr, {"first_note": 0.0, "last_note": 0.0})


if __name__ == "__main__":
    unittest.main()
