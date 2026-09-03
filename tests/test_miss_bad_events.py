"""miss/bad 事件时间戳回归测试（v1.4.1）。

背景：累计失误曲线曾用重叠窗口（30s 宽 / 1s 步进）的 miss 计数累加，
同一事件被重复计入约 30 次（GENTLEMAN 4 miss 显示 120）。
修复：get_miss_bad_events 从 notes 表按事件时间戳查询，事件唯一精确。
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
            # 4 miss + 3 bad + 若干 good/bomb，时间不排序（验证 ORDER BY）
            rows = [
                ("r1", 0, 0, 5.0, 0.0, GOOD),
                ("r1", 1, 1, 17.09, 16.0, MISS),
                ("r1", 2, 2, 131.85, 130.0, BAD),
                ("r1", 3, 3, 176.85, 175.0, MISS),
                ("r1", 4, 4, 177.57, 176.0, MISS),
                ("r1", 5, 5, 3.0, 2.0, GOOD),
                ("r1", 6, 6, 61.21, 60.0, BAD),
                ("r1", 7, 7, 9.0, 8.0, 3),   # BOMB（不计入）
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
        # 与 replays summary 计数一致（miss=4, bad=2 的构造数据）
        self.assertEqual(len(ev["miss"]), 4)
        self.assertEqual(len(ev["bad"]), 2)

    def test_no_events_empty(self):
        ev = self.repo.get_miss_bad_events("no_such_id")
        self.assertEqual(ev, {"miss": [], "bad": []})

    def test_good_bomb_excluded(self):
        ev = self.repo.get_miss_bad_events("r1")
        for t in ev["miss"] + ev["bad"]:
            self.assertNotIn(t, (5.0, 3.0, 9.0))   # good/bomb 时间不在事件里

    def test_note_time_range(self):
        """note 首末时间：时间轴裁剪边界（两卡片共享）。"""
        nr = self.repo.get_note_time_range("r1")
        self.assertAlmostEqual(nr["first_note"], 3.0)    # 最早 note（good 5.0 之前的 3.0）
        self.assertAlmostEqual(nr["last_note"], 180.0)   # 最晚 miss
        # 首末都在事件/notes 范围内
        self.assertLess(nr["first_note"], nr["last_note"])

    def test_note_time_range_empty(self):
        nr = self.repo.get_note_time_range("no_such_id")
        self.assertEqual(nr, {"first_note": 0.0, "last_note": 0.0})


if __name__ == "__main__":
    unittest.main()
