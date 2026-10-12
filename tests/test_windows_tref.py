"""Window timeline anchor t_ref regression test (v1.4.1 option E).

Background: the window x coordinate used to be the window centre [t, t+30]/2, which
put sparse sections up to ±15s away from the real time of the notes inside the window
(measured on Hatatagami: first window centre 15s vs the real note at 21.61s).
Fix: t_ref = median of the note event times inside the window — in dense sections it is
≈ the centre, while sparse sections / the first and last windows anchor the real data
position.
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from backend.bsor.models import Replay, ReplayInfo  # noqa: E402
from backend.bsor.parser import FRAME_DTYPE  # noqa: E402
from backend.analysis.windows import build_windows  # noqa: E402
from tests.test_metrics import good  # noqa: E402


def make_replay(notes, duration=120.0):
    r = Replay()
    r.info = ReplayInfo(score=0, fail_time=-1.0)
    r.notes = notes
    n = int(duration * 120)
    frames = np.zeros(n, dtype=FRAME_DTYPE)
    frames["time"] = np.arange(n) / 120.0
    frames["fps"] = 120
    frames["pose"][:, 7] = np.arange(n) * 0.01
    frames["pose"][:, 14] = np.arange(n) * 0.012
    frames["pose"][:, 3:7] = [0, 0, 0, 1]
    frames["pose"][:, 10:14] = [0, 0, 0, 1]
    frames["pose"][:, 17:21] = [0, 0, 0, 1]
    r.frames = frames
    return r


class TestWindowTRef(unittest.TestCase):
    def test_tref_anchors_sparse_notes(self):
        """Sparse section: t_ref anchors the real note time inside the window, not the window centre."""
        # simulating Hatatagami: the first note is at 21.61s (long intro), window 30, step 10
        notes = [good(21.61 + i, speed=20.0) for i in range(5)]     # 21.61~25.61
        notes += [good(60.0 + i, speed=20.0) for i in range(5)]     # 60~64
        r = make_replay(notes, duration=120.0)
        wins = build_windows(r, window_sec=30.0, step_sec=10.0)
        # notes 21.61~25.61 inside the first window [0,30] -> median 23.61
        w0 = wins[0]
        self.assertEqual(w0["t_start"], 0.0)
        self.assertEqual(w0["t_end"], 30.0)
        self.assertIsNotNone(w0["t_ref"])
        self.assertGreater(w0["t_ref"], 21.0)      # anchors the real note, not the centre 15
        self.assertLess(w0["t_ref"], 26.0)
        # the window centre is 15 -> t_ref must clearly deviate from it (sparse anchoring works)
        self.assertGreater(abs(w0["t_ref"] - 15.0), 5.0)

    def test_tref_empty_window_none(self):
        """Window without notes: t_ref=None (the frontend skips it, there is no acc value)."""
        notes = [good(10.0, speed=20.0)]
        r = make_replay(notes, duration=120.0)
        wins = build_windows(r, window_sec=30.0, step_sec=30.0)
        self.assertEqual(len(wins), 4)             # [0,30],[30,60],[60,90],[90,120]
        self.assertIsNotNone(wins[0]["t_ref"])     # [0,30] contains 10.0
        for w in wins[1:]:
            self.assertIsNone(w["t_ref"])          # window without notes

    def test_tref_dense_window_near_center(self):
        """Dense section: with evenly spread notes t_ref ≈ the window centre (looks like the old version)."""
        notes = [good(5.0 + i, speed=20.0) for i in range(25)]   # 5~29, evenly spread
        r = make_replay(notes, duration=120.0)
        wins = build_windows(r, window_sec=30.0, step_sec=10.0)
        w0 = wins[0]                               # 5~29 inside [0,30], median 17
        self.assertIsNotNone(w0["t_ref"])
        self.assertLess(abs(w0["t_ref"] - 15.0), 4.0)   # close to the window centre 15

    def test_save_get_windows_roundtrip(self):
        """t_ref persisted and read back — round-trip at the repository layer."""
        import shutil
        import tempfile
        from backend.db.repository import Repository
        tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        try:
            repo = Repository(tmpdir / "tref.sqlite")
            with repo._conn() as c:
                c.execute(
                    "INSERT INTO replays(replay_id, timestamp, status, analysis_status)"
                    " VALUES('r1', 1700000000, 'analyzed', 'analyzed')")
            repo.save_windows("r1", [
                {"window_idx": 0, "t_start": 0.0, "t_end": 30.0,
                 "t_ref": 23.61, "metrics": {"note_events": 5}},
                {"window_idx": 1, "t_start": 30.0, "t_end": 60.0,
                 "t_ref": None, "metrics": {}},
            ])
            wins = repo.get_windows("r1")
            self.assertEqual(wins[0]["t_ref"], 23.61)
            self.assertIsNone(wins[1]["t_ref"])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
