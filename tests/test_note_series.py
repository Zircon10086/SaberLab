"""per-note time series regression test (v1.4.1 option A).

Background: acc/center used to be aggregated over 30s windows and plotted at the
window centre, which misplaced sparse note sections by ±15s (measured on Hatatagami:
the curve started at 7s and ended at 423s, while the real notes run 21.61~408.36s).
Fix: get_note_series returns one point per note (x=event_time), removing window
aggregation misalignment entirely; bombs are excluded (not a player mistake); miss/bad
carry a null saber speed.
"""
import pathlib
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.repository import Repository  # noqa: E402


class TestNoteSeries(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.repo = Repository(self.tmpdir / "ns.sqlite")
        with self.repo._conn() as c:
            c.execute(
                "INSERT INTO replays(replay_id, timestamp, status, analysis_status)"
                " VALUES('r1', 1700000000, 'analyzed', 'analyzed')")
            # good (115 pts) / bad (0 pts, has saber speed) / miss (0 pts, no saber speed) / bomb (excluded) / good
            rows = [
                ("r1", 0, 0, 21.61, 20.0, 0, 115, 15, 24.0),
                ("r1", 1, 1, 22.0, 21.0, 1, 0, 0, 18.5),
                ("r1", 2, 2, 23.0, 22.0, 2, 0, 0, None),
                ("r1", 3, 3, 24.0, 23.0, 3, 0, 0, None),      # bomb, must be excluded
                ("r1", 4, 4, 408.36, 407.0, 0, 102, 2, 32.0),
            ]
            c.executemany(
                "INSERT INTO notes(replay_id, idx, note_id, event_time, spawn_time,"
                " event_type, note_score, center_score, saber_speed)"
                " VALUES(?,?,?,?,?,?,?,?,?)", rows)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_note_series_alignment(self):
        """per-note points: x=event time, first and last note exactly aligned (no window misalignment)."""
        s = self.repo.get_note_series("r1")
        self.assertEqual(s["t"], [21.61, 22.0, 23.0, 408.36])   # bomb excluded
        self.assertEqual(len(s["acc"]), 4)
        self.assertAlmostEqual(s["acc"][0], 115 / 115.0)        # good = 1.0
        self.assertEqual(s["acc"][1], 0.0)                      # bad = 0
        self.assertEqual(s["acc"][2], 0.0)                      # miss = 0
        self.assertAlmostEqual(s["acc"][3], 102 / 115.0, delta=0.0001)  # backend rounds to 4 places
        self.assertEqual(s["center"], [15, 0, 0, 2])
        # saber speed: a value for good, null for bad/miss
        self.assertEqual(s["speed"], [24.0, 18.5, None, 32.0])

    def test_no_notes_empty(self):
        s = self.repo.get_note_series("no_such_id")
        self.assertEqual(s, {"t": [], "acc": [], "center": [], "speed": []})


if __name__ == "__main__":
    unittest.main()
