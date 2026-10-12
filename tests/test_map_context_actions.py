"""Map context-menu backend regression (2026-10).

The map card's two OS actions are covered here without touching any real data:

* `Repository.delete_map`: the map row plus the rating / leaderboard caches keyed by it go away,
  while replay rows and their analysis are deliberately kept (the user deletes the MAP, not the
  replays) — that boundary is the part most likely to be "tidied" into a cascade later;
* `main.api_map_reveal` / `main.api_map_recycle`: the level folder is the target (never the
  CustomLevels root), a missing row is a 404 and a missing folder a 410, and the folder must
  reach the recycle bin BEFORE the record is dropped — a failure there has to leave the row
  intact, never a record deleted while the folder is still on disk.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import backend.main as m  # noqa: E402
from backend.db.repository import Repository  # noqa: E402

MAP_HASH = "ABCDEF0123456789"


def _map_row(folder: pathlib.Path, name: str = "Folder Song") -> dict:
    return {
        "map_hash": MAP_HASH, "folder_name": folder.name, "path": str(folder),
        "song_name": name, "song_author": "Artist", "mapper": "Mapper",
        "bpm": 120, "song_length": 100, "version": "3.2.0",
        "difficulties": json.dumps([{"characteristic": "Standard", "difficulty": "Expert",
                                     "label": "", "filename": "Expert.dat", "njs": 16}]),
        "info_json": json.dumps({"song_sub_name": "sub"}),
        "hash_source": "computed", "last_scanned": "2026-10-01 00:00:00Z",
        "beatmap_key": "1a2b3", "nps_json": json.dumps({"Standard|Expert": 4.5}),
        "ranked_difficulty": "Expert", "stars": None,
    }


class MapDeleteRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.repo = Repository(self.tmpdir / "maps.sqlite")
        self.folder = self.tmpdir / "1a2b3 (Folder Song)"
        self.folder.mkdir()
        (self.folder / "info.dat").write_text("{}", encoding="utf-8")
        self.repo.upsert_map(_map_row(self.folder))
        self.repo.upsert_ss_leaderboard({
            "leaderboard_id": "lb-1", "map_hash": MAP_HASH, "difficulty_rank": 7,
            "difficulty_name": "Expert", "game_mode": "SoloStandard", "difficulty_raw": None,
            "song_name": "Folder Song", "level_author": "m", "stars": 5.0, "ranked": 1,
            "qualified": 0, "loved": None, "max_pp": None, "plays": None,
            "last_synced": "now",
        }, platform="scoresaber")

    def test_delete_map_removes_the_map_and_its_caches(self):
        counts = self.repo.delete_map(MAP_HASH)
        self.assertEqual(counts["maps"], 1)
        self.assertEqual(counts["scoresaber_leaderboards"], 1)
        self.assertIsNone(self.repo.get_map(MAP_HASH))
        self.assertFalse(self.repo.list_ss_leaderboards(platform="scoresaber"))

    def test_delete_map_is_case_insensitive_and_idempotent(self):
        self.assertEqual(self.repo.delete_map(MAP_HASH.lower())["maps"], 1)
        self.assertEqual(self.repo.delete_map(MAP_HASH)["maps"], 0)

    def test_delete_map_unknown_hash_removes_nothing(self):
        counts = self.repo.delete_map("NOPE")
        self.assertEqual(counts["maps"], 0)
        self.assertIsNotNone(self.repo.get_map(MAP_HASH))

    def test_delete_map_keeps_the_replays(self):
        """The .bsor rows and their analysis belong to the user, not to the map record."""
        self.repo.upsert_replay({
            "replay_id": "R1", "file_path": "/x/R1.bsor", "file_name": "R1.bsor",
            "timestamp": "2026-10-01T00:00:00Z", "song_name": "Folder Song",
            "difficulty": "Expert", "mode": "Standard", "map_hash": MAP_HASH,
            "score": 100, "score_effective": 100, "has_nf": 0, "accuracy": 0.9,
            "good_count": 1, "bad_count": 0, "miss_count": 0, "bomb_count": 0,
            "max_combo": 1, "full_combo": 0, "won": 1, "modifiers": "{}", "status": "completed",
            "analysis_status": "analyzed", "profile_id": None, "analysis_version": 1,
            "analyzed_at": "2026-10-01T00:00:00Z", "duration": 10.0, "fps_median": 90,
            "player_name": "P", "player_id": "1", "completion_status": "completed",
        })
        self.assertIsNotNone(self.repo.get_replay("R1"))
        self.repo.delete_map(MAP_HASH)
        self.assertIsNotNone(self.repo.get_replay("R1"))


class MapContextActionTest(unittest.TestCase):
    """The two routes, against a temporary map row whose folder really exists on disk."""

    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.repo = Repository(self.tmpdir / "maps.sqlite")
        self.folder = self.tmpdir / "1a2b3 (Folder Song)"
        self.folder.mkdir()
        self.repo.upsert_map(_map_row(self.folder))
        patches = mock.patch.multiple(m, repo=mock.DEFAULT, enrichment=mock.DEFAULT)
        mocked = patches.start()
        self.addCleanup(patches.stop)
        self.repo_patch = mocked["repo"]
        self.repo_patch.get_map.side_effect = self.repo.get_map
        self.repo_patch.delete_map.side_effect = self.repo.delete_map
        self.enrichment = mocked["enrichment"]

        self.recycled: list[pathlib.Path] = []
        recycle = mock.patch.object(m, "_send_to_recycle_bin",
                                    side_effect=lambda t: self.recycled.append(pathlib.Path(t)))
        recycle.start()
        self.addCleanup(recycle.stop)
        opened = mock.patch.object(m, "_run_hidden", return_value=0)
        self.opened = opened.start()
        self.addCleanup(opened.stop)

    def test_reveal_opens_the_level_folder_not_the_root(self):
        res = m.api_map_reveal(MAP_HASH)
        self.assertEqual(res["status"], "opened")
        self.assertEqual(res["path"], str(self.folder))
        self.assertEqual(self.opened.call_args[0][0], ["explorer.exe", str(self.folder)])

    def test_reveal_unknown_map_is_404(self):
        with self.assertRaises(m.HTTPException) as ctx:
            m.api_map_reveal("NOPE")
        self.assertEqual(ctx.exception.status_code, 404)
        self.opened.assert_not_called()

    def test_reveal_missing_folder_is_410_and_never_opens_explorer(self):
        shutil.rmtree(self.folder)
        with self.assertRaises(m.HTTPException) as ctx:
            m.api_map_reveal(MAP_HASH)
        self.assertEqual(ctx.exception.status_code, 410)
        self.opened.assert_not_called()

    def test_recycle_recycles_the_folder_then_drops_the_row(self):
        order: list[str] = []
        with mock.patch.object(m, "_send_to_recycle_bin",
                               side_effect=lambda t: order.append("folder")), \
             mock.patch.object(self.repo_patch, "delete_map",
                               side_effect=lambda h: (order.append("row"), {"maps": 1})[1]):
            res = m.api_map_recycle(MAP_HASH)
        self.assertEqual(order, ["folder", "row"])
        self.assertEqual(res["status"], "deleted")
        self.assertEqual(res["song_name"], "Folder Song")
        self.assertEqual(res["folder_name"], self.folder.name)
        self.enrichment.invalidate.assert_called()

    def test_recycle_failure_keeps_the_record(self):
        """A folder that cannot be recycled must not leave a deleted record behind."""
        with mock.patch.object(m, "_send_to_recycle_bin",
                               side_effect=m.HTTPException(500, "移动到回收站失败（退出码 1）")):
            with self.assertRaises(m.HTTPException) as ctx:
                m.api_map_recycle(MAP_HASH)
        self.assertEqual(ctx.exception.status_code, 500)
        self.repo_patch.delete_map.assert_not_called()

    def test_recycle_unknown_map_is_404_and_never_reaches_the_bin(self):
        with self.assertRaises(m.HTTPException) as ctx:
            m.api_map_recycle("NOPE")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(self.recycled, [])

    def test_recycle_missing_folder_is_410_and_never_reaches_the_bin(self):
        shutil.rmtree(self.folder)
        with self.assertRaises(m.HTTPException) as ctx:
            m.api_map_recycle(MAP_HASH)
        self.assertEqual(ctx.exception.status_code, 410)
        self.assertEqual(self.recycled, [])


class RecycleBinHelperTest(unittest.TestCase):
    """`_send_to_recycle_bin`: folder vs file method, one retry, permanent failure raises."""

    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.folder = self.tmpdir / "map"
        self.folder.mkdir()
        self.file = self.tmpdir / "a.bsor"
        self.file.write_bytes(b"x")

    def _run(self, target: pathlib.Path, codes: list[int]):
        """Call the helper with a scripted exit-code sequence; returns the mocked runner."""
        with mock.patch.object(m, "_run_hidden", side_effect=list(codes)) as run, \
             mock.patch.object(m.time, "sleep"):
            m._send_to_recycle_bin(target)
        return run

    def test_directory_uses_DeleteDirectory(self):
        run = self._run(self.folder, [0])
        self.assertIn("::DeleteDirectory(", run.call_args[0][0][4])

    def test_file_uses_DeleteFile(self):
        run = self._run(self.file, [0])
        self.assertIn("::DeleteFile(", run.call_args[0][0][4])

    def test_the_path_travels_as_base64(self):
        """Quotes and unicode must survive the command line: only the decoded copy is used."""
        run = self._run(self.folder, [0])
        script = run.call_args[0][0][4]
        self.assertIn("FromBase64String(", script)
        self.assertNotIn(str(self.folder), script)

    def test_a_transient_failure_is_retried_once(self):
        run = self._run(self.folder, [1, 0])            # must not raise
        self.assertEqual(run.call_count, 2)

    def test_a_permanent_failure_raises_500_after_two_tries(self):
        with mock.patch.object(m, "_run_hidden", side_effect=[1, 1]) as run, \
             mock.patch.object(m.time, "sleep"):
            with self.assertRaises(m.HTTPException) as ctx:
                m._send_to_recycle_bin(self.folder)
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
