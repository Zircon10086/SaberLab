"""file_available degradation flag regression test (2026-09, HANDOFF §4.25 todo 1).

ingest only ever adds rows, never deletes: a DB row can outlive the original .bsor
file (deleted or moved from outside the app). The backend computes file_available
uniformly on the response rows so the frontend can label the degradation precisely
(detail banner / cut details / 3D replay / why hand motion is unavailable / list
badge) instead of showing a blanket "no data". This test exercises the decision logic
of main._attach_file_available directly.
"""
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import backend.main as m  # noqa: E402


class TestAttachFileAvailable(unittest.TestCase):
    def test_existing_file(self):
        """An existing file path -> file_available=True."""
        p = pathlib.Path(PROJECT_ROOT) / "README.md"
        rows = [{"replay_id": "a", "file_path": str(p)}]
        m._attach_file_available(rows)
        self.assertTrue(rows[0]["file_available"])

    def test_missing_file(self):
        """Invalid path / file does not exist -> file_available=False."""
        rows = [{"replay_id": "b",
                 "file_path": str(PROJECT_ROOT / "_tmp" / "definitely-not-here.bsor")}]
        m._attach_file_available(rows)
        self.assertFalse(rows[0]["file_available"])

    def test_empty_path(self):
        """Row with a NULL/empty file_path (abnormal data) -> file_available=False."""
        for fp in (None, "", "   "):
            rows = [{"replay_id": "c", "file_path": fp}]
            m._attach_file_available(rows)
            self.assertFalse(rows[0]["file_available"])

    def test_batch_rows(self):
        """Mark a batch row by row (list / history / same-map history all share this helper)."""
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
