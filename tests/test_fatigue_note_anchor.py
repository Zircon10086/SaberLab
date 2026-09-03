"""疲劳分析早晚段边界锚定测试（v1.4.1 修复 + 2026 固定窗口退役适配）。

背景：analyze_fatigue 早晚段曾锚到 [0, duration]，长前奏/尾奏的静默致
late 段零 note → 所有 delta=None（疲劳分析静默失效；Hatatagami 实证：
duration=447s、首 note 21.6s、末 note 408.4s，旧 late=[417,447] 零 note）。
修复：锚到 [first_note, last_note]，与详情页时间轴裁剪边界（get_note_time_range）
口径一致。本测试用合成 replay（前奏 21.6s + 尾奏 32.5s）复现并验证。

2026 适配：斜率数据源从固定时间窗口（build_windows，已弃用）改为
note 分组（build_note_groups）——analyze_fatigue 第二参数为 note_groups。
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.analysis.fatigue import analyze_fatigue, _segment_stats  # noqa: E402
from backend.analysis.motion import analyze_motion  # noqa: E402
from backend.analysis.notes import build_note_groups  # noqa: E402
from tests.test_metrics import good, make_replay  # noqa: E402


def _fatigue(r, edge=30.0):
    """构造 note_groups 后调用 analyze_fatigue（固定窗口退役后的标准入口）。"""
    return analyze_fatigue(r, build_note_groups(r.notes, 50),
                           analyze_motion(r), edge)


class TestFatigueNoteAnchor(unittest.TestCase):
    def _build(self, duration: float = 120.0):
        # 长前奏 21.6s + 长尾奏 32.5s：首 note 21.6、末 note 87.5
        # 旧算法 late=[duration-30, duration]=[90,120] 全落尾奏 → 0 note
        notes = [good(21.6), good(22.0)]          # 起手
        notes += [good(50.0), good(50.5)]        # 中段
        notes += [good(87.0), good(87.5)]        # 收尾
        return make_replay(notes, duration=duration)

    def test_late_segment_anchored_to_notes_not_empty(self):
        r = self._build()
        f = _fatigue(r)
        self.assertTrue(f["available"])
        # 关键：late 段不再落进尾奏静默 → 有 note（旧算法此处=0 → delta 全 None）
        self.assertGreater(f["late"]["notes"], 0)
        self.assertGreater(f["late"]["good"], 0)
        self.assertGreater(f["early"]["notes"], 0)

    def test_deltas_not_none_with_intro_outro(self):
        """长前奏/尾奏下 delta 不再因 late 段空而 None。"""
        r = self._build()
        f = _fatigue(r)
        for k in ("accuracy", "center", "saber_speed"):
            self.assertIsNotNone(
                f["deltas"][k],
                msg=f"delta {k} 不应为 None（late 段应锚到 note 而非尾奏）")

    def test_motion_hand_speed_delta_not_none(self):
        """手速前后对比同样锚到 note 跨度（不再被尾奏静默污染）。"""
        r = self._build()
        f = _fatigue(r)
        self.assertIsNotNone(f["deltas"]["left_hand_speed"])
        self.assertIsNotNone(f["deltas"]["right_hand_speed"])

    def test_old_boundary_would_be_empty(self):
        """回归佐证：旧边界 [duration-30, duration] 取 late 段确为空（修复前根因）。"""
        r = self._build(duration=120.0)
        notes = sorted(r.notes, key=lambda n: n.event_time)
        old_late = _segment_stats(notes, 120.0 - 30.0, 120.0)
        self.assertEqual(old_late["notes"], 0)

    def test_no_notes_unavailable(self):
        r = make_replay([], duration=60.0)
        f = _fatigue(r)
        self.assertFalse(f["available"])

    def test_short_note_span_shrinks_edge(self):
        """note 跨度 < 2*edge：对比窗自动收缩（而非判 unavailable）。"""
        notes = [good(5.0 + i) for i in range(31)]   # 5.0~35.0，跨度 30s
        r = make_replay(notes, duration=40.0)
        f = _fatigue(r)
        self.assertTrue(f["available"])
        self.assertLess(f["edge_seconds"], 30.0)      # 收缩了
        self.assertIsNotNone(f["deltas"]["accuracy"])

    def test_normal_replay_not_regressed(self):
        """无前奏/尾奏的普通 replay 不被回归。"""
        notes = [good(2.0 + i * 0.3) for i in range(300)]   # 2.0~91.7s
        r = make_replay(notes, duration=95.0)
        f = _fatigue(r)
        self.assertTrue(f["available"])
        self.assertIsNotNone(f["deltas"]["accuracy"])
        self.assertGreater(f["early"]["notes"], 0)
        self.assertGreater(f["late"]["notes"], 0)

    def test_group_slopes_available(self):
        """note 分组斜率：300 notes / 50 = 6 组 ≥ 3 → 斜率有值（窗口退役适配）。"""
        notes = [good(2.0 + i * 0.3) for i in range(300)]
        r = make_replay(notes, duration=95.0)
        f = _fatigue(r)
        for k in ("accuracy_local_slope_per_min", "center_avg_slope_per_min"):
            self.assertIsNotNone(f["slopes"][k], msg=f"{k} 不应为 None")

    def test_too_few_notes_no_slopes(self):
        """note 过少（<3 组）→ 斜率 None（沿用旧守卫）。"""
        notes = [good(2.0 + i) for i in range(10)]   # 10 notes → 1 组
        r = make_replay(notes, duration=20.0)
        f = _fatigue(r)
        self.assertFalse(f["available"])   # note 跨度 <20s → 判不可用


if __name__ == "__main__":
    unittest.main()
