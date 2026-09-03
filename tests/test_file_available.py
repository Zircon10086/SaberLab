"""file_available 降级标记回归测试（2026-09，HANDOFF §4.25 待办 1）。

ingest 只增不删：DB 行可能比原始 .bsor 文件长寿（外部删除/移动）。
后端统一在响应行上计算 file_available，前端据此降级标注
（详情横幅 / 切割细节 / 3D 回放 / 手部运动不可用原因 / 列表徽章），
而不是笼统显示"无数据"。此处直接测 main._attach_file_available 的判定逻辑。
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import backend.main as m  # noqa: E402


class TestAttachFileAvailable(unittest.TestCase):
    def test_existing_file(self):
        """存在的文件路径 → file_available=True。"""
        p = pathlib.Path(PROJECT_ROOT) / "README.md"
        rows = [{"replay_id": "a", "file_path": str(p)}]
        m._attach_file_available(rows)
        self.assertTrue(rows[0]["file_available"])

    def test_missing_file(self):
        """路径无效/文件不存在 → file_available=False。"""
        rows = [{"replay_id": "b",
                 "file_path": str(PROJECT_ROOT / "_tmp" / "definitely-not-here.bsor")}]
        m._attach_file_available(rows)
        self.assertFalse(rows[0]["file_available"])

    def test_empty_path(self):
        """NULL/空 file_path 行（异常数据）→ file_available=False。"""
        for fp in (None, "", "   "):
            rows = [{"replay_id": "c", "file_path": fp}]
            m._attach_file_available(rows)
            self.assertFalse(rows[0]["file_available"])

    def test_batch_rows(self):
        """批量行逐条标注（列表/历史/同谱历史共用同一 helper）。"""
        ok = pathlib.Path(PROJECT_ROOT) / "README.md"
        rows = [
            {"replay_id": "1", "file_path": str(ok)},
            {"replay_id": "2", "file_path": str(PROJECT_ROOT / "_tmp" / "nope.bsor")},
            {"replay_id": "3"},
        ]
        m._attach_file_available(rows)
        self.assertEqual([r["file_available"] for r in rows], [True, False, False])


if __name__ == "__main__":
    unittest.main()
