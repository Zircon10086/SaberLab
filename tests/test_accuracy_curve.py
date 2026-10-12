"""Per-note cumulative accuracy curve regression tests (v1.4.1 time-series
misalignment fix).

Background: acc/center used to be aggregated into 30s windows with x drawn at the
window center or at t_ref (the median note time inside the window), so on sparse
sections x drifted from the real note by ±15s (measured on Hatatagami: first window
center 15s vs the real 21.61s; option E t_ref 26.1s was still off by +4.5s). Root
cause: event-level metrics aggregated over an interval with an interval
representative point as x — neither the center nor the median can line up with the
real events of a sparse section.

Fix (following BeatSaviorUI): per-note cumulative running mean —
  x = good cut event time (exact, which removes the window misalignment entirely)
  y = cumulative score / (cumulative good count × 115)   running accuracy (0-1)
      center = cumulative center score / cumulative good count   running Center average (0-15)
Only good cuts are used (acc/center are only meaningful for good); bad/miss/bomb
are not counted and are shown separately by the miss_cum/bad_cum step lines. A
5-point moving average is overlaid to soften the step corners.
"""
import pathlib
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.bsor.models import GOOD, BAD, MISS, BOMB  # noqa: E402
from backend.db.repository import Repository, _moving_average  # noqa: E402


class TestAccuracyCurve(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.repo = Repository(self.tmpdir / "ac.sqlite")
        with self.repo._conn() as c:
            c.execute(
                "INSERT INTO replays(replay_id, timestamp, status, analysis_status)"
                " VALUES('r1', 1700000000, 'analyzed', 'analyzed')")
            # good(115,center=15) / bad(0) / miss(0) / bomb(excluded) / good(102,center=2)
            rows = [
                ("r1", 0, 0, 21.61, 20.0, GOOD, 115, 15),
                ("r1", 1, 1, 22.0, 21.0, BAD, 0, 0),
                ("r1", 2, 2, 23.0, 22.0, MISS, 0, 0),
                ("r1", 3, 3, 24.0, 23.0, BOMB, 0, 0),
                ("r1", 4, 4, 408.36, 407.0, GOOD, 102, 2),
            ]
            c.executemany(
                "INSERT INTO notes(replay_id, idx, note_id, event_time, spawn_time,"
                " event_type, note_score, center_score)"
                " VALUES(?,?,?,?,?,?,?,?)", rows)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_curve_good_only_and_cumulative(self):
        """ma_window=1 turns smoothing off: x holds only good cut times, y is the cumulative running mean."""
        s = self.repo.get_accuracy_curve("r1", ma_window=1)
        # bad/miss/bomb are not counted, only the event times of the two good cuts
        self.assertEqual(s["t"], [21.61, 408.36])
        # acc: 115/115=1.0;  (115+102)/(2*115)=217/230≈0.9435
        self.assertAlmostEqual(s["acc"][0], 1.0)
        self.assertAlmostEqual(s["acc"][1], 217 / 230.0, places=4)
        # center: 15/1=15.0;  (15+2)/2=8.5
        self.assertAlmostEqual(s["center"][0], 15.0)
        self.assertAlmostEqual(s["center"][1], 8.5)

    def test_curve_default_ma_smoothing(self):
        """Default 5-point moving average: the smoothed values stay inside the range of the raw
        cumulative values (no overshoot)."""
        raw = self.repo.get_accuracy_curve("r1", ma_window=1)
        s = self.repo.get_accuracy_curve("r1")   # ma_window=5
        self.assertEqual(len(s["acc"]), len(raw["acc"]))
        # smoothing is a weighted average inside the window, so the result must land within [min, max]
        for key, vals in (("acc", s["acc"]), ("center", s["center"])):
            lo, hi = min(raw[key]), max(raw[key])
            for v in vals:
                self.assertGreaterEqual(v, lo - 1e-9)
                self.assertLessEqual(v, hi + 1e-9)

    def test_curve_excludes_bomb_bad_miss(self):
        """bomb/bad/miss event times never appear on the curve."""
        s = self.repo.get_accuracy_curve("r1", ma_window=1)
        for t in s["t"]:
            self.assertNotIn(t, (22.0, 23.0, 24.0))   # bad/miss/bomb times

    def test_curve_empty(self):
        s = self.repo.get_accuracy_curve("no_such_id")
        self.assertEqual(s, {"t": [], "acc": [], "center_t": [], "center": []})

    def test_curve_official_style_from_table(self):
        """Official definition (2026-08 correction): accuracy_curve table data takes
        priority over the good-only fallback.

        Table data = the running_accuracy of compute_score at analysis time
        (score/maxScore, including miss/bad penalties). This test simulates an
        official penalty-carrying curve: good(115) + miss (maxScore grows while score
        does not → acc falls)."""
        curve = {"t": [21.61, 22.0, 408.36],
                 "acc": [1.0, 0.5, 0.75]}
        with self.repo._conn() as c:
            c.execute("INSERT INTO accuracy_curve(replay_id, curve_json) VALUES(?,?)",
                      ("r1", __import__("json").dumps(curve)))
        s = self.repo.get_accuracy_curve("r1", ma_window=1)
        # acc comes from the table data (official definition), center is still
        # accumulated live from notes with good-only
        self.assertEqual(s["t"], [21.61, 22.0, 408.36])
        self.assertEqual(s["acc"], [1.0, 0.5, 0.75])
        self.assertEqual(s["center_t"], [21.61, 408.36])
        self.assertAlmostEqual(s["center"][0], 15.0)

    def test_curve_missing_note_score_zero_center(self):
        """Table data exists but notes hold no good → center is empty and acc keeps the table data."""
        curve = {"t": [1.0], "acc": [0.9]}
        with self.repo._conn() as c:
            c.execute("INSERT INTO accuracy_curve(replay_id, curve_json) VALUES(?,?)",
                      ("r_empty", __import__("json").dumps(curve)))
        s = self.repo.get_accuracy_curve("r_empty", ma_window=1)
        self.assertEqual(s["t"], [1.0])
        self.assertEqual(s["acc"], [0.9])
        self.assertEqual(s["center_t"], [])
        self.assertEqual(s["center"], [])

    def test_save_accuracy_curve_roundtrip(self):
        """save_accuracy_curve can be read back after being written (roundtrip)."""
        self.repo.save_accuracy_curve("r1", [(1.0, 0.5), (2.0, 0.75)])
        s = self.repo.get_accuracy_curve("r1", ma_window=1)
        self.assertEqual(s["t"], [1.0, 2.0])
        self.assertEqual(s["acc"], [0.5, 0.75])

    def test_moving_average_helper(self):
        """5-point centered moving average: the midpoint is the mean of the 5 values in the
        window, and edge windows shrink."""
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        out = _moving_average(vals, window=5)
        self.assertEqual(len(out), 5)
        # midpoint i=2: window [0,5) holds all 5 values → mean 3.0
        self.assertAlmostEqual(out[2], 3.0)
        # edge i=0: the window shrinks to [0,3) = [1,2,3] → mean 2.0
        self.assertAlmostEqual(out[0], 2.0)
        # edge i=4: the window shrinks to [2,5) = [3,4,5] → mean 4.0
        self.assertAlmostEqual(out[4], 4.0)

    def test_moving_average_empty_and_single(self):
        self.assertEqual(_moving_average([], 5), [])
        self.assertEqual(_moving_average([7.0], 5), [7.0])


if __name__ == "__main__":
    unittest.main()
