"""Same-map history regression test (2026-10).

The replay detail page lists earlier attempts on the same map, each rendered as a normal
replay row - including the "file missing" badge, which the backend decides through
main._attach_file_available(). That helper can only tell the truth when the row carries
file_path, and the same-map query did not project it, so every entry in that list claimed
"file missing" while the file was right there (user report, Bad Apple test case: the same
replays showed no badge in the overview and in history, where the full row is selected).

Pinned here:
  * previous_attempts_on_map projects file_path (and still everything else callers use);
  * a row that carries file_path is judged by the real file: existing -> available,
    missing -> unavailable, absent field -> unavailable (the safe default);
  * the same replay row therefore reports the same file_available in the list and in the
    same-map history, which is the user-visible contract.
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import backend.main as m  # noqa: E402
from backend.db.repository import Repository  # noqa: E402

MAP_HASH = "1189A9190321" + "0" * 28
NOW = 1786976654


def _row(replay_id: str, file_path: str, timestamp: int, difficulty: str = "ExpertPlus") -> dict:
    return {
        "replay_id": replay_id, "file_path": file_path, "file_name": f"{replay_id}.bsor",
        "file_size": 12, "file_mtime": 1.0, "timestamp": timestamp,
        "player_id": "76561190000000000", "player_name": "p", "platform": "pc",
        "tracking_system": "oculus", "hmd": "h", "controller": "c",
        "game_version": "1.40.8", "mod_version": "0.9.33",
        "map_hash": MAP_HASH, "song_name": "Bad Apple!!", "difficulty": difficulty,
        "mode": "Standard", "environment": "", "modifiers": "", "score": 100,
        "completion_status": "completed", "status": "analyzed",
        "analysis_status": "analyzed", "error_message": None,
        "parsed_at": "", "analyzed_at": None,
    }


class SameMapHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.repo = Repository(self.tmpdir / "t.sqlite")
        self.present = self.tmpdir / "present.bsor"
        self.present.write_bytes(b"bsor")
        self.gone = self.tmpdir / "gone.bsor"          # deliberately not created
        self.repo.upsert_replay(_row("older", str(self.present), NOW - 100))
        self.repo.upsert_replay(_row("gone", str(self.gone), NOW - 50))
        self.repo.upsert_replay(_row("newest", str(self.present), NOW))

    def test_history_rows_carry_file_path(self):
        """The projection must include file_path - the bug was its absence."""
        rows = self.repo.previous_attempts_on_map(MAP_HASH, "ExpertPlus", NOW + 1,
                                                  exclude_id="newest")
        self.assertEqual([r["replay_id"] for r in rows], ["gone", "older"])
        for row in rows:
            self.assertIn("file_path", row)
            self.assertTrue(row["file_path"])

    def test_file_available_follows_the_real_file(self):
        """With the path present, the flag reports reality instead of always 'missing'."""
        rows = self.repo.previous_attempts_on_map(MAP_HASH, "ExpertPlus", NOW + 1,
                                                  exclude_id="newest")
        m._attach_file_available(rows)
        by_id = {r["replay_id"]: r["file_available"] for r in rows}
        self.assertTrue(by_id["older"])            # file exists -> available
        self.assertFalse(by_id["gone"])            # file really gone -> unavailable

    def test_missing_field_stays_unavailable(self):
        """A row without file_path (any other projection) keeps the safe default."""
        rows = [{"replay_id": "x"}]
        m._attach_file_available(rows)
        self.assertFalse(rows[0]["file_available"])

    def test_list_and_history_agree(self):
        """The user-visible contract: one replay reports the same state everywhere."""
        listed = [r for r in self.repo.list_replays(limit=50) if r["replay_id"] == "older"]
        m._attach_file_available(listed)
        hist = self.repo.previous_attempts_on_map(MAP_HASH, "ExpertPlus", NOW + 1,
                                                  exclude_id="newest")
        m._attach_file_available(hist)
        same = next(r for r in hist if r["replay_id"] == "older")
        self.assertEqual(listed[0]["file_available"], same["file_available"])
        self.assertTrue(same["file_available"])

    def test_projection_matches_the_list(self):
        """Every replay-row projection must carry the same columns.

        Two user-visible bugs came from this query dropping one column: file_path (every entry
        showed "file missing") and analysis_status (every entry showed "pending"). The list and
        the same-map history must stay identical, so this pins their key sets.
        """
        listed = self.repo.list_replays(limit=50)
        hist = self.repo.previous_attempts_on_map(MAP_HASH, "ExpertPlus", NOW + 1,
                                                 exclude_id="newest")
        self.assertTrue(listed and hist)
        by_id = {r["replay_id"]: r for r in listed}
        for row in hist:
            self.assertEqual(set(row), set(by_id[row["replay_id"]]),
                             f"same-map row {row['replay_id']} has a different column set")

    def test_analysis_status_marks_an_analyzed_replay(self):
        """The badge depends on analysis_status: an analyzed replay must arrive as 'analyzed'."""
        hist = self.repo.previous_attempts_on_map(MAP_HASH, "ExpertPlus", NOW + 1,
                                                 exclude_id="newest")
        statuses = {r["replay_id"]: r.get("analysis_status") for r in hist}
        self.assertEqual(statuses.get("older"), "analyzed")

    def test_other_fields_still_present(self):
        """The added column must not drop what the row renderer needs."""
        row = self.repo.previous_attempts_on_map(MAP_HASH, "ExpertPlus", NOW + 1,
                                                 exclude_id="newest")[1]
        for key in ("replay_id", "file_name", "timestamp", "song_name", "difficulty", "mode",
                    "map_hash", "score", "accuracy", "status", "duration", "player_name",
                    "completion_status"):
            self.assertIn(key, row)


if __name__ == "__main__":
    unittest.main()
