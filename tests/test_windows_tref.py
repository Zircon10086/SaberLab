"""窗口时间轴锚点 t_ref 回归测试（v1.4.1 方案 E）。

背景：窗口 x 坐标曾用窗口中心 [t, t+30]/2，稀疏段与窗口内 note 实际
时间偏差 ±15s（Hatatagami 实测：首窗中心 15s vs 真实 note 21.61s）。
修复：t_ref = 窗口内 note 事件时间的中位数——密集段≈中心，
稀疏段/首尾锚定真实数据位置。
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from backend.bsor.models import Replay, ReplayInfo, NoteEvent, GOOD, MISS  # noqa: E402
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
        """稀疏段：t_ref 锚定窗口内 note 真实时间，而非窗口中心。"""
        # 模拟 Hatatagami：第一个 note 在 21.61s（长前奏），窗口宽 30 步长 10
        notes = [good(21.61 + i, speed=20.0) for i in range(5)]     # 21.61~25.61
        notes += [good(60.0 + i, speed=20.0) for i in range(5)]     # 60~64
        r = make_replay(notes, duration=120.0)
        wins = build_windows(r, window_sec=30.0, step_sec=10.0)
        # 首窗口 [0,30] 内 note 21.61~25.61 → 中位数 23.61
        w0 = wins[0]
        self.assertEqual(w0["t_start"], 0.0)
        self.assertEqual(w0["t_end"], 30.0)
        self.assertIsNotNone(w0["t_ref"])
        self.assertGreater(w0["t_ref"], 21.0)      # 锚定真实 note，而非中心 15
        self.assertLess(w0["t_ref"], 26.0)
        # 窗口中心是 15 → t_ref 必须显著偏离中心（稀疏段锚定生效）
        self.assertGreater(abs(w0["t_ref"] - 15.0), 5.0)

    def test_tref_empty_window_none(self):
        """无 note 的窗口：t_ref=None（前端跳过，无 acc 值）。"""
        notes = [good(10.0, speed=20.0)]
        r = make_replay(notes, duration=120.0)
        wins = build_windows(r, window_sec=30.0, step_sec=30.0)
        self.assertEqual(len(wins), 4)             # [0,30],[30,60],[60,90],[90,120]
        self.assertIsNotNone(wins[0]["t_ref"])     # [0,30] 含 10.0
        for w in wins[1:]:
            self.assertIsNone(w["t_ref"])          # 无 note 窗口

    def test_tref_dense_window_near_center(self):
        """密集段：note 均匀分布时 t_ref ≈ 窗口中心（观感与旧版一致）。"""
        notes = [good(5.0 + i, speed=20.0) for i in range(25)]   # 5~29 均匀
        r = make_replay(notes, duration=120.0)
        wins = build_windows(r, window_sec=30.0, step_sec=10.0)
        w0 = wins[0]                               # [0,30] 内 5~29 中位 17
        self.assertIsNotNone(w0["t_ref"])
        self.assertLess(abs(w0["t_ref"] - 15.0), 4.0)   # 接近窗口中心 15

    def test_save_get_windows_roundtrip(self):
        """t_ref 落库 round-trip（repository 层）。"""
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
