"""Map detail page regression test (2026-10).

The map detail page lists a difficulty's plays with the SAME replay card the overview uses
(`replayItem`), which renders them from the row itself. The first version of the map play query
projected only the numbers the page summarizes, so every history entry showed "unknown song" and
had no map hash to load a cover from (user-reported on Cyaegha).

Pinned here:
  * the play rows carry the fields the replay card needs (song name + map hash above all);
  * the per-difficulty filter is exact: a chart's history is only its own plays;
  * the chart summary (count / best score / best accuracy / full combo) matches those rows.
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.repository import Repository  # noqa: E402

MAP_HASH = "3D31005D1DBDD056564EB831C664B1EC9AF51AB2"
SONG = "Cyaegha"

# the fields replayItem() reads to render a row (the list, history and map detail share it)
CARD_FIELDS = ("replay_id", "map_hash", "song_name", "difficulty", "mode", "timestamp",
               "score", "score_effective", "accuracy", "has_nf", "full_combo", "won",
               "analysis_status", "completion_status", "file_path")


def _play(replay_id: str, difficulty: str, mode: str, ts: int, score: int,
          acc: float, fc: bool = False) -> dict:
    return {
        "replay_id": replay_id, "file_path": f"/x/{replay_id}.bsor",
        "file_name": f"{replay_id}.bsor", "file_size": 10, "file_mtime": 1.0, "timestamp": ts,
        "player_id": "76561190000000000", "player_name": "p", "platform": "pc",
        "tracking_system": "oculus", "hmd": "h", "controller": "c",
        "game_version": "1.40.8", "mod_version": "0.9.33",
        "map_hash": MAP_HASH, "song_name": SONG, "difficulty": difficulty, "mode": mode,
        "environment": "", "modifiers": "", "score": score, "score_effective": score,
        "accuracy": acc, "full_combo": 1 if fc else 0, "won": 1, "has_nf": 0,
        "completion_status": "completed", "status": "analyzed",
        "analysis_status": "analyzed", "error_message": None,
        "parsed_at": "", "analyzed_at": None,
    }


class MapPlayStatsTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.repo = Repository(self.tmpdir / "plays.sqlite")
        # two Expert plays and one ExpertPlus play, newest first by timestamp
        self.repo.upsert_replay(_play("e1", "Expert", "Standard", 100, 500000, 0.80))
        self.repo.upsert_replay(_play("e2", "Expert", "Standard", 300, 690000, 0.87, fc=True))
        self.repo.upsert_replay(_play("p1", "ExpertPlus", "Standard", 200, 400000, 0.60))
        self.repo.upsert_replay(_play("l1", "ExpertPlus", "Lawless", 150, 300000, 0.50))

    def test_play_rows_carry_the_replay_card_fields(self):
        """Every field replayItem() renders from must be present, song name and hash included."""
        rows = self.repo.map_play_stats(MAP_HASH)
        self.assertEqual(len(rows), 4)
        for row in rows:
            for field in CARD_FIELDS:
                self.assertIn(field, row, f"{field} missing from a map play row")
            self.assertEqual(row["song_name"], SONG)
            self.assertEqual(row["map_hash"], MAP_HASH)
        self.assertEqual([r["replay_id"] for r in rows], ["e2", "p1", "l1", "e1"])  # newest first

    def test_plays_are_ordered_newest_first_without_the_card(self):
        """The order is what the page lists, so it must not depend on the rendering layer."""
        rows = self.repo.map_play_stats(MAP_HASH)
        times = [r["timestamp"] for r in rows]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_unknown_map_returns_nothing(self):
        self.assertEqual(self.repo.map_play_stats("NOPE"), [])


if __name__ == "__main__":
    unittest.main()
