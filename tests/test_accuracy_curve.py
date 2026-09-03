"""per-note 累计准确率曲线回归测试（v1.4.1 修复时间序列错位）。

背景：acc/center 曾用 30s 窗口聚合、x 画在窗口中心或 t_ref（窗口内
note 时间中位数），稀疏段 x 与真实 note 偏差 ±15s（Hatatagami 实证：
首窗中心 15s vs 真实 21.61s；方案 E t_ref 26.1s 仍偏 +4.5s）。根因：
事件级指标用区间聚合 + 区间代表点作 x，无论取中心还是中位数都无法
对齐稀疏段真实事件。

修复（参考 BeatSaviorUI）：per-note 累计运行均值——
  x = good cut 事件时间（精确，彻底消除窗口错位）
  y = 累计得分 / (累计 good 数 × 115)   运行准确率 (0-1)
      center = 累计 center 分 / 累计 good 数  运行 Center 均分 (0-15)
只取 good cut（acc/center 仅对 good 有意义）；bad/miss/bomb 不计入，
由 miss_cum/bad_cum 台阶线单独呈现。叠加 5 点滑动平均柔化台阶转角。
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
            # good(115,center=15) / bad(0) / miss(0) / bomb(排除) / good(102,center=2)
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
        """ma_window=1 关闭平滑：x 仅 good cut 时间，y 为累计运行均值。"""
        s = self.repo.get_accuracy_curve("r1", ma_window=1)
        # bad/miss/bomb 不计入，仅两个 good cut 的事件时间
        self.assertEqual(s["t"], [21.61, 408.36])
        # acc: 115/115=1.0； (115+102)/(2*115)=217/230≈0.9435
        self.assertAlmostEqual(s["acc"][0], 1.0)
        self.assertAlmostEqual(s["acc"][1], 217 / 230.0, places=4)
        # center: 15/1=15.0； (15+2)/2=8.5
        self.assertAlmostEqual(s["center"][0], 15.0)
        self.assertAlmostEqual(s["center"][1], 8.5)

    def test_curve_default_ma_smoothing(self):
        """默认 5 点滑动平均：平滑后值仍在累计原值范围内（不越界）。"""
        raw = self.repo.get_accuracy_curve("r1", ma_window=1)
        s = self.repo.get_accuracy_curve("r1")   # ma_window=5
        self.assertEqual(len(s["acc"]), len(raw["acc"]))
        # 平滑是区间内加权平均，结果必落在 [min, max] 内
        for key, vals in (("acc", s["acc"]), ("center", s["center"])):
            lo, hi = min(raw[key]), max(raw[key])
            for v in vals:
                self.assertGreaterEqual(v, lo - 1e-9)
                self.assertLessEqual(v, hi + 1e-9)

    def test_curve_excludes_bomb_bad_miss(self):
        """bomb/bad/miss 的事件时间绝不出现在曲线上。"""
        s = self.repo.get_accuracy_curve("r1", ma_window=1)
        for t in s["t"]:
            self.assertNotIn(t, (22.0, 23.0, 24.0))   # bad/miss/bomb 时间

    def test_curve_empty(self):
        s = self.repo.get_accuracy_curve("no_such_id")
        self.assertEqual(s, {"t": [], "acc": [], "center_t": [], "center": []})

    def test_curve_official_style_from_table(self):
        """官方口径（2026-08 修正）：accuracy_curve 表数据优先于 good-only 回退。

        表数据 = 分析时 compute_score 的 running_accuracy（score/maxScore，
        含 miss/bad 惩罚）。此测试模拟含惩罚的官方曲线：
        good(115) + miss（maxScore 增加、score 不增 → acc 下降）。
        """
        curve = {"t": [21.61, 22.0, 408.36],
                 "acc": [1.0, 0.5, 0.75]}
        with self.repo._conn() as c:
            c.execute("INSERT INTO accuracy_curve(replay_id, curve_json) VALUES(?,?)",
                      ("r1", __import__("json").dumps(curve)))
        s = self.repo.get_accuracy_curve("r1", ma_window=1)
        # acc 走表数据（官方口径），center 仍从 notes 实时 good-only 累计
        self.assertEqual(s["t"], [21.61, 22.0, 408.36])
        self.assertEqual(s["acc"], [1.0, 0.5, 0.75])
        self.assertEqual(s["center_t"], [21.61, 408.36])
        self.assertAlmostEqual(s["center"][0], 15.0)

    def test_curve_missing_note_score_zero_center(self):
        """表数据存在但 notes 无 good → center 空、acc 保留表数据。"""
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
        """save_accuracy_curve 落库后可读回（roundtrip）。"""
        self.repo.save_accuracy_curve("r1", [(1.0, 0.5), (2.0, 0.75)])
        s = self.repo.get_accuracy_curve("r1", ma_window=1)
        self.assertEqual(s["t"], [1.0, 2.0])
        self.assertEqual(s["acc"], [0.5, 0.75])

    def test_moving_average_helper(self):
        """5 点居中滑动平均：中点 = 窗口内 5 个值的均值，边缘窗口收缩。"""
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        out = _moving_average(vals, window=5)
        self.assertEqual(len(out), 5)
        # 中点 i=2：窗口 [0,5) 全部 5 个值 → 均值 3.0
        self.assertAlmostEqual(out[2], 3.0)
        # 边缘 i=0：窗口收缩为 [0,3) = [1,2,3] → 均值 2.0
        self.assertAlmostEqual(out[0], 2.0)
        # 边缘 i=4：窗口收缩为 [2,5) = [3,4,5] → 均值 4.0
        self.assertAlmostEqual(out[4], 4.0)

    def test_moving_average_empty_and_single(self):
        self.assertEqual(_moving_average([], 5), [])
        self.assertEqual(_moving_average([7.0], 5), [7.0])


if __name__ == "__main__":
    unittest.main()
