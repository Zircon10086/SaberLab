"""数据库 schema 自举回归测试。

背景（架构审查 P0-2.1）：历史上 v2/v4/v5 的表结构变更散落在 _tools/migrate_db*.py，
models.py SCHEMA 缺 beatmap_key / nps_json / map_ranked_cache /
scoresaber_leaderboards —— 全新数据库实例化 Repository 后首个查询即崩。
迁移史收敛进 SCHEMA + _migrate() 后，此测试保证：

1. 全新库：建库 → 常规读写全链路不抛错（独立化打包后的首次启动路径）
2. 旧库升级：模拟 pre-v2 老结构（无 beatmap_key/nps_json、无两张缓存表、
   replays 无 analysis_status）→ 打开即完成迁移，数据保留、beatmap_key 回填
"""
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from backend.db.repository import Repository

# pre-v2 旧结构（与 v1 时代 models.py 一致的最小子集，够触发迁移即可）
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


class TestFreshDatabase(unittest.TestCase):
    """全新数据库：建库后常规读写全链路可用。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.tmp.name) / "fresh.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_db_full_workflow(self):
        repo = Repository(self.db_path)
        # maps：upsert（含 nps_json）+ list（含 beatmap_key 列）
        repo.upsert_map({
            "map_hash": "AA" * 20, "folder_name": "16633 (song - mapper)",
            "path": "X:/levels/16633", "song_name": "Test Song",
            "song_author": "author", "mapper": "mapper", "bpm": 120.0,
            "song_length": 90.0, "version": "2.1.0", "difficulties": "[]",
            "info_json": "{}", "hash_source": "computed",
        })
        maps = repo.list_maps()
        self.assertEqual(len(maps), 1)
        self.assertEqual(maps[0]["beatmap_key"], "")   # 新列可读
        # nps_json 缺省 None：scan 不覆盖已计算 NPS（upsert_map 行为变更）
        self.assertIsNone(maps[0]["nps_json"])
        # scoresaber_leaderboards：upsert + list
        repo.upsert_ss_leaderboard({
            "leaderboard_id": 1, "map_hash": "AA" * 20, "difficulty_rank": 7,
            "difficulty_name": "Expert", "game_mode": "SoloStandard",
            "difficulty_raw": "_Expert_SoloStandard", "song_name": "Test Song",
            "level_author": "mapper", "stars": 5.0, "ranked": 1, "qualified": 1,
            "loved": 0, "max_pp": 100.0, "plays": 1, "last_synced": "now",
        })
        self.assertEqual(repo.count_ss_leaderboards(), 1)
        self.assertEqual(len(repo.list_ss_leaderboards()), 1)
        # map_ranked_cache：upsert + list + get
        repo.upsert_ranked_cache("AA" * 20, "Expert", 5.0, 123.4, "now")
        rc = repo.list_ranked_cache()
        self.assertEqual(len(rc), 1)
        got = repo.get_ranked_cache("aa" * 20, "Expert")  # 大小写不敏感
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got["pp"], 123.4)
        # replays：upsert + count + list
        repo.upsert_replay({
            "replay_id": "r1", "file_path": "x.bsor", "timestamp": 1000,
            "map_hash": "AA" * 20, "song_name": "Test Song",
            "difficulty": "Expert", "mode": "Standard", "status": "parsed",
            "analysis_status": "pending",
        })
        self.assertEqual(repo.count_replays(), 1)
        self.assertEqual(len(repo.list_replays()), 1)
        # clear_analysis_cache（内部 DELETE 两张新表）
        result = repo.clear_analysis_cache()
        self.assertTrue(result["cleared"])
        self.assertEqual(repo.count_replays(), 0)
        self.assertEqual(repo.count_ss_leaderboards(), 0)


class TestLegacyDatabaseUpgrade(unittest.TestCase):
    """旧版数据库：打开即迁移，数据不丢、beatmap_key 回填。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.tmp.name) / "legacy.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

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
        repo = Repository(self.db_path)  # 打开即迁移
        # 列已补齐且数据保留
        m = repo.get_map("bb" * 20)
        self.assertIsNotNone(m)
        self.assertEqual(m["song_name"], "Old Song")
        # beatmap_key 按 v2 规则从 folder_name 回填
        self.assertEqual(m["beatmap_key"], "16633")
        self.assertEqual(m["nps_json"], "{}")   # 旧库迁移保持默认值
        # replays.analysis_status 已补齐并按 status 回填
        r = repo.get_replay("r1")
        self.assertEqual(r["analysis_status"], "analyzed")
        # 新表可用
        repo.upsert_ss_leaderboard({
            "leaderboard_id": 9, "map_hash": "BB" * 20, "difficulty_rank": 9,
            "difficulty_name": "ExpertPlus", "game_mode": "SoloStandard",
            "difficulty_raw": "_ExpertPlus_SoloStandard", "song_name": "Old Song",
            "level_author": "mapper", "stars": None, "ranked": 0, "qualified": 0,
            "loved": 0, "max_pp": None, "plays": 0, "last_synced": "now",
        })
        repo.upsert_ranked_cache("BB" * 20, "ExpertPlus", 6.0, 200.0, "now")
        self.assertEqual(len(repo.list_ranked_cache()), 1)
        # 幂等：再次打开不出错
        repo2 = Repository(self.db_path)
        self.assertEqual(repo2.get_map("bb" * 20)["beatmap_key"], "16633")


if __name__ == "__main__":
    unittest.main()
