"""note 锚定时间序列测试（2026 固定窗口退役）。

覆盖 analysis/notes.py：
- build_note_groups：分组边界/中位 x/末组不足/输入兼容（对象 vs dict）
- density_series：均匀序列、间隙低谷、边缘收缩、单点
- moving_average：居中窗口、边缘收缩、空输入
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.analysis.notes import (  # noqa: E402
    build_note_groups, density_series, moving_average,
)
from backend.bsor.models import GOOD, BAD, MISS, BOMB  # noqa: E402
from tests.test_metrics import good, make_replay  # noqa: E402


def _note(t, etype=GOOD, score=115, center=15, speed=20.0, saber="right"):
    return {"event_time": t, "event_type": etype, "note_score": score,
            "center_score": center, "saber_speed": speed, "saber": saber}


class TestBuildNoteGroups(unittest.TestCase):
    def test_group_boundaries_and_ref(self):
        """10 notes / 4 一组 → 3 组（4+4+2），x=t_ref=组内中位时间。"""
        notes = [_note(float(i)) for i in range(10)]
        groups = build_note_groups(notes, group_notes=4)
        self.assertEqual(len(groups), 3)
        g0, g1, g2 = groups
        self.assertEqual(g0["t_first"], 0.0)
        self.assertEqual(g0["t_last"], 3.0)
        self.assertEqual(g1["t_first"], 4.0)
        self.assertEqual(g2["t_last"], 9.0)
        # 组 0: times [0,1,2,3]，偶数个 → 中位 (1+2)/2 = 1.5
        self.assertAlmostEqual(g0["t_ref"], 1.5)
        # 组 2: [8,9] → 中位 8.5
        self.assertAlmostEqual(g2["t_ref"], 8.5)
        # 组内 note 数恒定（末组不足也保留）
        self.assertEqual([g["metrics"]["note_events"] for g in groups], [4, 4, 2])

    def test_accepts_note_event_objects(self):
        """兼容 NoteEvent 对象（fatigue 场景）。"""
        r = make_replay([good(1.0), good(2.0), good(3.0)], duration=5.0)
        groups = build_note_groups(r.notes, group_notes=50)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["metrics"]["good"], 3)
        self.assertAlmostEqual(groups[0]["metrics"]["accuracy_local"], 1.0, places=4)

    def test_empty_input(self):
        self.assertEqual(build_note_groups([], 50), [])

    def test_group_metrics_exclude_bombs_from_rates(self):
        """bomb 不计入 miss_rate 分母；bad/miss 计数正确。"""
        notes = [_note(1.0, GOOD), _note(2.0, BAD), _note(3.0, MISS),
                 _note(4.0, BOMB)]
        groups = build_note_groups(notes, group_notes=50)
        m = groups[0]["metrics"]
        self.assertEqual(m["good"], 1)
        self.assertEqual(m["bad"], 1)
        self.assertEqual(m["miss"], 1)
        self.assertEqual(m["bomb"], 1)
        # scored = 3（bomb 排除）→ miss_rate 1/3
        self.assertAlmostEqual(m["miss_rate"], 1 / 3, places=4)

    def test_group_density_uses_real_span(self):
        """密度分母 = 组内真实时间跨度（无静默稀释）。"""
        # 6 notes 密集 + 末 note 2s 后（无静默污染：span 按真实首末 note 计）
        notes = [_note(10.0 + i * 0.5) for i in range(5)] + [_note(20.0)]
        groups = build_note_groups(notes, group_notes=50)
        m = groups[0]["metrics"]
        # span = 20-10 = 10s，6 notes → 0.6
        self.assertAlmostEqual(m["note_density"], 0.6, places=3)

    def test_slope_group_notes_guard(self):
        """group_notes < 1 → 回落默认 50。"""
        notes = [_note(float(i)) for i in range(3)]
        groups = build_note_groups(notes, 0)
        self.assertEqual(len(groups), 1)


class TestDensitySeries(unittest.TestCase):
    def test_uniform_sequence(self):
        """均匀 0.5s 间隔：局部密度 = 10 notes / 5s = 2/s（n=5，中心窗口）。"""
        ts = [i * 0.5 for i in range(21)]   # 0..10s
        d = density_series(ts, n=5)
        self.assertEqual(len(d), 21)
        # 中心点 i=10: (hi-lo)=10, span=5.0 → 2.0
        self.assertAlmostEqual(d[10], 2.0, places=3)

    def test_gap_creates_natural_dip(self):
        """长间隙：间隙前的 note 密度低（邻域跨过静默）——忠实呈现谱面结构。"""
        ts = [0.0, 0.5, 1.0, 1.5, 2.0, 10.0, 10.5, 11.0, 11.5, 12.0]
        d = density_series(ts, n=2)
        # i=4 (t=2.0)：窗口 note 2..6（t=1.0~10.5，span 9.5）→ 4/9.5≈0.421（低谷）
        self.assertAlmostEqual(d[4], 4 / 9.5, places=3)
        # i=5 (t=10.0)：窗口 note 3..7（t=1.5~11.0，span 9.5）→ 4/9.5≈0.421
        self.assertAlmostEqual(d[5], 4 / 9.5, places=3)
        # 密集端中心 i=2 (t=1.0)：窗口 note 0..4（t=0~2.0，span 2.0）→ 4/2=2.0
        self.assertAlmostEqual(d[2], 2.0, places=3)

    def test_edge_shrinks(self):
        """边缘收缩：首点窗口 [0, n]。"""
        ts = [i * 0.5 for i in range(21)]
        d = density_series(ts, n=5)
        # i=0: lo=0 hi=5 span=2.5 → 5/2.5=2.0（与中心一致，均匀序列）
        self.assertAlmostEqual(d[0], 2.0, places=3)

    def test_empty_and_single(self):
        self.assertEqual(density_series([]), [])
        self.assertEqual(density_series([1.0]), [0.0])


class TestMovingAverage(unittest.TestCase):
    def test_centered_and_edge_shrink(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        out = moving_average(values, window=3)
        # i=0: [1,2] → 1.5; i=1: [1,2,3] → 2; i=2: [2,3,4] → 3; ...
        self.assertAlmostEqual(out[0], 1.5, places=4)
        self.assertAlmostEqual(out[1], 2.0, places=4)
        self.assertAlmostEqual(out[2], 3.0, places=4)
        self.assertAlmostEqual(out[4], 4.5, places=4)

    def test_empty(self):
        self.assertEqual(moving_average([]), [])


if __name__ == "__main__":
    unittest.main()
