"""per-note 时间序列回归测试（v1.4.1 方案 A）。

背景：acc/center 曾用 30s 窗口聚合画在窗口中心，note 稀疏段错位 ±15s
（Hatatagami 实证：曲线 7s 开始/423s 结束，实际 note 21.61~408.36s）。
修复：get_note_series 返回每个 note 一个点（x=event_time），
彻底消除窗口聚合错位；bomb 排除（非玩家失误）；miss/bad 刀速为 null。
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
            # good(115分) / bad(0分,有刀速) / miss(0分,无刀速) / bomb(排除) / good
            rows = [
                ("r1", 0, 0, 21.61, 20.0, 0, 115, 15, 24.0),
                ("r1", 1, 1, 22.0, 21.0, 1, 0, 0, 18.5),
                ("r1", 2, 2, 23.0, 22.0, 2, 0, 0, None),
                ("r1", 3, 3, 24.0, 23.0, 3, 0, 0, None),      # bomb 应排除
                ("r1", 4, 4, 408.36, 407.0, 0, 102, 2, 32.0),
            ]
            c.executemany(
                "INSERT INTO notes(replay_id, idx, note_id, event_time, spawn_time,"
                " event_type, note_score, center_score, saber_speed)"
                " VALUES(?,?,?,?,?,?,?,?,?)", rows)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_note_series_alignment(self):
        """per-note 点：x=事件时间，首末 note 精确对齐（无窗口错位）。"""
        s = self.repo.get_note_series("r1")
        self.assertEqual(s["t"], [21.61, 22.0, 23.0, 408.36])   # bomb 排除
        self.assertEqual(len(s["acc"]), 4)
        self.assertAlmostEqual(s["acc"][0], 115 / 115.0)        # good = 1.0
        self.assertEqual(s["acc"][1], 0.0)                      # bad = 0
        self.assertEqual(s["acc"][2], 0.0)                      # miss = 0
        self.assertAlmostEqual(s["acc"][3], 102 / 115.0, delta=0.0001)  # 后端 round 4 位
        self.assertEqual(s["center"], [15, 0, 0, 2])
        # 刀速：good 有值、bad/miss 为 null
        self.assertEqual(s["speed"], [24.0, 18.5, None, 32.0])

    def test_no_notes_empty(self):
        s = self.repo.get_note_series("no_such_id")
        self.assertEqual(s, {"t": [], "acc": [], "center": [], "speed": []})


if __name__ == "__main__":
    unittest.main()
