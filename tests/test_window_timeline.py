"""AI note 分组摘要测试（2026 固定窗口退役适配）。

背景：ai/context._window_timeline 原取 windows 首尾各若干——但 windows
序列覆盖 [0, duration]，含前奏/尾奏静默的空窗口（Hatatagami 末 note 408s、
duration 447s，末若干窗口全在尾奏 → acc/center/speed 全 None，AI 误判
"回放以无数据收尾"）。v1.4.1 曾修复为"跳过空窗再取首尾"。

2026 决策：固定时间窗口整体退役——摘要改为 note 分组
（build_note_groups，每 N 个 note 一组）。分组=note 集合：不存在空组，
组内必有数据；每组输出真实时间范围 [t_first, t_last]。
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.ai.context import _note_group_timeline  # noqa: E402
from backend.analysis.notes import build_note_groups  # noqa: E402
from backend.bsor.models import GOOD  # noqa: E402


def _note(t, score=115, center=15, speed=20.0, saber="right", etype=GOOD):
    return {"event_time": t, "event_type": etype, "note_score": score,
            "center_score": center, "saber_speed": speed, "saber": saber}


class TestNoteGroupTimeline(unittest.TestCase):
    def test_head_tail_take_active_groups(self):
        """Hatatagami 式：长前奏（21.6s 起）+ 尾奏静默 → 分组天然从首 note 起，
        首组 t 起点 = 第一个 note 时间，尾组 t 终点 = 最后一个 note 时间。"""
        # 前奏 20s 无 note，然后 60 个 note 密集区（21.6s 起），尾奏 20s 无 note
        notes = [_note(21.6 + i * 0.3, speed=20.0 + i * 0.1) for i in range(60)]
        groups = build_note_groups(notes, group_notes=20)   # 3 组
        s = _note_group_timeline(groups, include_groups=8)
        self.assertEqual(len(s), 3)
        # 首组 t 从真实首 note 时间开始（不是 0s 前奏）
        self.assertAlmostEqual(s[0]["t"][0], 21.6, places=1)
        # 尾组 t 到真实末 note 时间结束（不是尾奏）
        self.assertAlmostEqual(s[-1]["t"][1], 21.6 + 59 * 0.3, places=1)
        # 每组都有数据（无空组）
        for item in s:
            self.assertGreater(item["notes"], 0)
            self.assertIsNotNone(item["acc"])

    def test_many_groups_truncated_with_marker(self):
        """组数 > include → head + 省略标记 + tail。"""
        notes = [_note(10.0 + i * 0.5) for i in range(400)]   # 400 notes / 50 = 8 组
        groups = build_note_groups(notes, group_notes=50)
        self.assertEqual(len(groups), 8)
        s = _note_group_timeline(groups, include_groups=6)
        self.assertEqual(len(s), 7)   # 3 + 省略 + 3
        self.assertEqual(s[3], {"note": "...intermediate groups omitted..."})
        # 首尾无截断：首组起点 = 首 note，尾组终点 = 末 note
        self.assertAlmostEqual(s[0]["t"][0], 10.0, places=1)
        self.assertAlmostEqual(s[-1]["t"][1], 10.0 + 399 * 0.5, places=1)

    def test_few_groups_no_truncation(self):
        notes = [_note(5.0), _note(6.0), _note(7.0)]   # 1 组
        groups = build_note_groups(notes, group_notes=50)
        s = _note_group_timeline(groups, include_groups=8)
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["notes"], 3)

    def test_empty_notes_returns_empty(self):
        groups = build_note_groups([], group_notes=50)
        self.assertEqual(groups, [])
        self.assertEqual(_note_group_timeline(groups), [])

    def test_group_metrics_and_density(self):
        """组聚合口径：acc/center/刀速/miss_rate/密度/失衡。"""
        notes = [_note(10.0 + i, score=115 if i % 2 == 0 else 100,
                       center=15 if i % 2 == 0 else 10, speed=22.0)
                 for i in range(6)]
        groups = build_note_groups(notes, group_notes=6)
        m = groups[0]["metrics"]
        self.assertEqual(m["good"], 6)
        self.assertAlmostEqual(m["accuracy_local"], (115 * 3 + 100 * 3) / (115 * 6), places=4)
        self.assertAlmostEqual(m["center_avg"], (15 * 3 + 10 * 3) / 6, places=3)
        self.assertAlmostEqual(m["saber_speed_avg"], 22.0, places=3)
        # 组时间跨度 10.0~15.0 = 5s → 密度 6/5
        self.assertAlmostEqual(m["note_density"], 6 / 5, places=3)


if __name__ == "__main__":
    unittest.main()
