"""Database schema self-bootstrap regression tests.

Background (architecture review P0-2.1): historically the v2/v4/v5 table structure
changes were scattered across _tools/migrate_db*.py, and models.py SCHEMA was
missing beatmap_key / nps_json / map_ranked_cache / scoresaber_leaderboards — so a
brand-new database crashed on the very first query after instantiating Repository.
With the migration history collapsed into SCHEMA + _migrate(), this test
guarantees:

1. Fresh database: create it → the whole normal read/write path raises nothing (the
   first-startup path of the self-contained build)
2. Legacy upgrade: simulate the pre-v2 structure (no beatmap_key/nps_json, neither
   cache table, replays without analysis_status) → opening it completes the
   migration, the data survives and beatmap_key is backfilled
"""
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from backend.db.repository import Repository

# pre-v2 legacy structure (the minimal subset matching the v1-era models.py, just
# enough to trigger the migration)
LEGACY_SCHEMA = """
CREATE TABLE maps (
    map_hash        TEXT PRIMARY KEY,
    folder_name     TEXT,
    path            TEXT,
    song_name       TEXT,
    song_author     TEXT,
    mapper          TEXT,
    bpm             REAL,
    song_length     REAL,
    version         TEXT,
    difficulties    TEXT,
    info_json        TEXT,
    hash_source     TEXT,
    ranked_difficulty TEXT,
    stars           REAL,
    scoresaber_updated TEXT,
    last_scanned    TEXT
);
CREATE TABLE replays (
    replay_id       TEXT PRIMARY KEY,
    file_path       TEXT,
    timestamp       INTEGER,
    map_hash        TEXT,
    song_name       TEXT,
    difficulty      TEXT,
    mode            TEXT,
    status          TEXT DEFAULT 'parsed',
    analysis_version INTEGER DEFAULT 1
);
CREATE TABLE notes (
    replay_id       TEXT NOT NULL,
    idx             INTEGER NOT NULL,
    PRIMARY KEY (replay_id, idx)
);
"""


def _close_repos(case) -> None:
    """Close every Repository a test left open, then drop the temp dir.

    Repository caches one long-lived connection per thread (2026-09 perf work),
    so a test that holds a Repository also holds the sqlite file handle and
    Windows refuses to delete the temp directory until it is closed.
    """
    for obj in list(vars(case).values()):
        if hasattr(obj, "close") and obj.__class__.__name__ == "Repository":
            try:
                obj.close()
            except Exception:                        # noqa: BLE001 — best effort
                pass
    case.tmp.cleanup()


class TestFreshDatabase(unittest.TestCase):
    """Fresh database: after creation the whole normal read/write path works."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.tmp.name) / "fresh.sqlite"

    def tearDown(self):
        _close_repos(self)

    def test_fresh_db_full_workflow(self):
        repo = Repository(self.db_path)
        # maps: upsert (with nps_json) + list (with the beatmap_key column)
        repo.upsert_map({
            "map_hash": "AA" * 20, "folder_name": "16633 (song - mapper)",
            "path": "X:/levels/16633", "song_name": "Test Song",
            "song_author": "author", "mapper": "mapper", "bpm": 120.0,
            "song_length": 90.0, "version": "2.1.0", "difficulties": "[]",
            "info_json": "{}", "hash_source": "computed",
        })
        maps = repo.list_maps()
        self.assertEqual(len(maps), 1)
        self.assertEqual(maps[0]["beatmap_key"], "")   # the new column is readable
        # nps_json defaults to None: a scan does not overwrite an already computed NPS
        # (upsert_map behaviour change)
        self.assertIsNone(maps[0]["nps_json"])
        # scoresaber_leaderboards: upsert + list
        repo.upsert_ss_leaderboard({
            "leaderboard_id": 1, "map_hash": "AA" * 20, "difficulty_rank": 7,
            "difficulty_name": "Expert", "game_mode": "SoloStandard",
            "difficulty_raw": "_Expert_SoloStandard", "song_name": "Test Song",
            "level_author": "mapper", "stars": 5.0, "ranked": 1, "qualified": 1,
            "loved": 0, "max_pp": 100.0, "plays": 1, "last_synced": "now",
        })
        self.assertEqual(repo.count_ss_leaderboards(), 1)
        self.assertEqual(len(repo.list_ss_leaderboards()), 1)
        # map_ranked_cache: upsert + list + get
        repo.upsert_ranked_cache("AA" * 20, "Expert", 5.0, 123.4, "now")
        rc = repo.list_ranked_cache()
        self.assertEqual(len(rc), 1)
        got = repo.get_ranked_cache("aa" * 20, "Expert")  # case-insensitive
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got["pp"], 123.4)
        # replays: upsert + count + list
        repo.upsert_replay({
            "replay_id": "r1", "file_path": "x.bsor", "timestamp": 1000,
            "map_hash": "AA" * 20, "song_name": "Test Song",
            "difficulty": "Expert", "mode": "Standard", "status": "parsed",
            "analysis_status": "pending",
        })
        self.assertEqual(repo.count_replays(), 1)
        self.assertEqual(len(repo.list_replays()), 1)
        # clear_analysis_cache (internally DELETEs from the two new tables)
        result = repo.clear_analysis_cache()
        self.assertTrue(result["cleared"])
        self.assertEqual(repo.count_replays(), 0)
        self.assertEqual(repo.count_ss_leaderboards(), 0)


class TestLegacyDatabaseUpgrade(unittest.TestCase):
    """Legacy database: opening it migrates in place, no data is lost and beatmap_key is backfilled."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.tmp.name) / "legacy.sqlite"

    def tearDown(self):
        _close_repos(self)

    def _create_legacy_db(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(LEGACY_SCHEMA)
            conn.execute(
                "INSERT INTO maps(map_hash, folder_name, path, song_name,"
                " hash_source, last_scanned) VALUES(?,?,?,?,?,?)",
                ("BB" * 20, "16633 (song - mapper)", "X:/old", "Old Song",
                 "computed", "2026-01-01"))
            conn.execute(
                "INSERT INTO replays(replay_id, file_path, timestamp, map_hash,"
                " song_name, difficulty, mode, status, analysis_version)"
                " VALUES('r1','x.bsor',1000,?, 'Old Song','Expert','Standard',"
                " 'analyzed', 1)", ("BB" * 20,))
            conn.commit()
        finally:
            conn.close()

    def test_upgrade_from_pre_v2(self):
        self._create_legacy_db()
        repo = Repository(self.db_path)  # opening it performs the migration
        # the columns are added and the data is kept
        m = repo.get_map("bb" * 20)
        self.assertIsNotNone(m)
        self.assertEqual(m["song_name"], "Old Song")
        # beatmap_key is backfilled from folder_name by the v2 rule
        self.assertEqual(m["beatmap_key"], "16633")
        self.assertEqual(m["nps_json"], "{}")   # a legacy database migration keeps the default value
        # replays.analysis_status is added and backfilled from status
        r = repo.get_replay("r1")
        self.assertEqual(r["analysis_status"], "analyzed")
        # the new tables are usable
        repo.upsert_ss_leaderboard({
            "leaderboard_id": 9, "map_hash": "BB" * 20, "difficulty_rank": 9,
            "difficulty_name": "ExpertPlus", "game_mode": "SoloStandard",
            "difficulty_raw": "_ExpertPlus_SoloStandard", "song_name": "Old Song",
            "level_author": "mapper", "stars": None, "ranked": 0, "qualified": 0,
            "loved": 0, "max_pp": None, "plays": 0, "last_synced": "now",
        })
        repo.upsert_ranked_cache("BB" * 20, "ExpertPlus", 6.0, 200.0, "now")
        self.assertEqual(len(repo.list_ranked_cache()), 1)
        # ACC-weighted skill columns are added to the palette cache (2026-09):
        # additive only, so an old cache row survives with NULL ratings
        repo.save_player_palette("scoresaber", "p1", {
            "yellow_stars": 8.75, "r80": 8.75, "r94": None, "r96": None,
            "r80_direct": 40, "r94_direct": 1, "r96_direct": 0,
            "r94_lower_bound": None, "r80_confidence": "high",
            "skill_params": {"lambda": 0.09},
        })
        cached = repo.get_player_palette("scoresaber", "p1")
        self.assertEqual(cached["r80"], 8.75)
        self.assertIsNone(cached["r94"])
        self.assertEqual(cached["r80_direct"], 40)
        # idempotent: opening it again raises nothing
        repo2 = Repository(self.db_path)
        self.assertEqual(repo2.get_map("bb" * 20)["beatmap_key"], "16633")


if __name__ == "__main__":
    unittest.main()
