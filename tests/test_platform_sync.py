"""Dual-platform cloud sync tests (2026-08: scoresaber | beatleader).

Covers:
1. Legacy DB migration: pre-platform cache tables rebuilt with the platform
   dimension, old rows preserved as platform='scoresaber'
2. Platform isolation: both platforms' rows coexist; reads are scoped
3. Enrichment routing: replay lists read the ACTIVE platform's stars/pp
4. BeatLeader parsing: fetch_scores / fetch_profile field mapping (real API
   JSON fixtures, network mocked)
"""
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.repository import Repository
from backend.analysis.pp_predict import predict_pp
from backend.services.enrichment import EnrichmentService
import backend.beatleader as beatleader

HASH_A = "AA" * 20
HASH_B = "BB" * 20

# Pre-platform cache table structure (2026-08-23 and earlier)
PRE_PLATFORM_CACHE_SCHEMA = """
CREATE TABLE scoresaber_cache (
    player_id   TEXT PRIMARY KEY,
    fetched_at  TEXT,
    profile_json TEXT,
    scores_json  TEXT
);
CREATE TABLE player_palette_cache (
    player_id     TEXT PRIMARY KEY,
    computed_at   TEXT,
    stage         TEXT,
    max_single_pp REAL,
    fallback_stars REAL,
    yellow_stars  REAL,
    sample_count  INTEGER,
    method        TEXT,
    valid_count   INTEGER,
    nf_excluded   INTEGER
);
CREATE TABLE map_ranked_cache (
    map_hash    TEXT NOT NULL,
    difficulty  TEXT NOT NULL,
    stars       REAL,
    pp          REAL,
    ranked      INTEGER DEFAULT 0,
    fetched_at  TEXT,
    PRIMARY KEY (map_hash, difficulty)
);
CREATE TABLE scoresaber_leaderboards (
    leaderboard_id  INTEGER PRIMARY KEY,
    map_hash       TEXT NOT NULL,
    difficulty_rank INTEGER,
    difficulty_name TEXT,
    game_mode      TEXT,
    difficulty_raw TEXT,
    song_name      TEXT,
    level_author   TEXT,
    stars          REAL,
    ranked         INTEGER,
    qualified      INTEGER,
    loved          INTEGER,
    max_pp         REAL,
    plays          INTEGER,
    last_synced    TEXT
);
CREATE INDEX idx_ssl_hash ON scoresaber_leaderboards(map_hash);
CREATE INDEX idx_ssl_hash_diff
    ON scoresaber_leaderboards(map_hash, difficulty_name);
"""


class TestPlatformMigration(unittest.TestCase):
    """旧库（无 platform 列）打开即迁移：数据保留、新结构可用、幂等。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.tmp.name) / "legacy.sqlite"

    def tearDown(self):
        self.tmp.cleanup()

    def _create_legacy_cache_db(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(PRE_PLATFORM_CACHE_SCHEMA)
            conn.execute(
                "INSERT INTO scoresaber_cache(player_id, fetched_at, profile_json, scores_json)"
                " VALUES('p1','2026-01-01','{}','[]')")
            conn.execute(
                "INSERT INTO player_palette_cache(player_id, computed_at, stage,"
                " max_single_pp, fallback_stars, yellow_stars, sample_count,"
                " method, valid_count, nf_excluded)"
                " VALUES('p1','2026-01-01','竞技向',500,8.75,9.0,20,'top20',40,10)")
            conn.execute(
                "INSERT INTO map_ranked_cache(map_hash, difficulty, stars, pp, ranked, fetched_at)"
                " VALUES(?, 'Expert', 5.0, 123.4, 1, '2026-01-01')", (HASH_A,))
            conn.execute(
                "INSERT INTO scoresaber_leaderboards(leaderboard_id, map_hash,"
                " difficulty_rank, difficulty_name, game_mode, difficulty_raw,"
                " song_name, level_author, stars, ranked, qualified, loved,"
                " max_pp, plays, last_synced)"
                " VALUES(1, ?, 7, 'Expert', 'SoloStandard', '_Expert_SoloStandard',"
                " 'Song', 'mapper', 5.0, 1, 0, 0, 100.0, 1, '2026-01-01')", (HASH_A,))
            conn.commit()
        finally:
            conn.close()

    def test_migration_preserves_data_as_scoresaber(self):
        self._create_legacy_cache_db()
        repo = Repository(self.db_path)   # open = migrate
        # player cache migrated
        cached = repo.get_player_cache("scoresaber", "p1")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["fetched_at"], "2026-01-01")
        self.assertIsNone(repo.get_player_cache("beatleader", "p1"))
        # palette migrated
        pal = repo.get_player_palette("scoresaber", "p1")
        self.assertEqual(pal["yellow_stars"], 9.0)
        self.assertEqual(pal["stage"], "竞技向")
        # ranked cache migrated
        self.assertEqual(repo.get_ranked_cache(HASH_A, "Expert",
                                               platform="scoresaber")["pp"], 123.4)
        # leaderboards migrated
        lbs = repo.list_ss_leaderboards(platform="scoresaber")
        self.assertEqual(len(lbs), 1)
        self.assertEqual(repo.count_ss_leaderboards(platform="scoresaber"), 1)
        self.assertEqual(repo.count_ss_leaderboards(platform="beatleader"), 0)
        # idempotent reopen
        repo2 = Repository(self.db_path)
        self.assertIsNotNone(repo2.get_player_cache("scoresaber", "p1"))

    def test_fresh_db_has_platform_columns(self):
        repo = Repository(self.db_path)
        repo.save_player_cache("beatleader", "p1", {"name": "x"}, [])
        self.assertIsNotNone(repo.get_player_cache("beatleader", "p1"))
        self.assertIsNone(repo.get_player_cache("scoresaber", "p1"))


class TestPlatformIsolation(unittest.TestCase):
    """双平台数据共存互不干扰（来回切换的核心保证）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(pathlib.Path(self.tmp.name) / "t.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def test_leaderboards_and_pp_isolated(self):
        # same map, two platforms, different stars/pp
        for platform, stars, pp in (("scoresaber", 5.0, 123.4),
                                    ("beatleader", 8.357, 296.6)):
            self.repo.upsert_ss_leaderboard({
                "leaderboard_id": f"lb-{platform}", "map_hash": HASH_A,
                "difficulty_rank": 7, "difficulty_name": "Expert",
                "game_mode": "Standard", "difficulty_raw": None,
                "song_name": "Song", "level_author": "m",
                "stars": stars, "ranked": 1, "qualified": 0, "loved": None,
                "max_pp": None, "plays": None, "last_synced": "now",
            }, platform=platform)
            self.repo.upsert_ranked_cache(HASH_A, "Expert", stars, pp, "now",
                                          platform=platform)
        ss = self.repo.list_ss_leaderboards(platform="scoresaber")
        bl = self.repo.list_ss_leaderboards(platform="beatleader")
        self.assertEqual(len(ss), 1)
        self.assertEqual(len(bl), 1)
        self.assertEqual(ss[0]["stars"], 5.0)
        self.assertEqual(bl[0]["stars"], 8.357)
        self.assertEqual(self.repo.get_ranked_cache(
            HASH_A, "Expert", platform="scoresaber")["pp"], 123.4)
        self.assertEqual(self.repo.get_ranked_cache(
            HASH_A, "Expert", platform="beatleader")["pp"], 296.6)

    def test_player_cache_and_palette_isolated(self):
        self.repo.save_player_cache("scoresaber", "p1", {"name": "ss"}, [{"i": 1}])
        self.repo.save_player_cache("beatleader", "p1", {"name": "bl"}, [{"i": 2}])
        self.assertEqual(self.repo.get_player_cache("scoresaber", "p1")["profile"]["name"], "ss")
        self.assertEqual(self.repo.get_player_cache("beatleader", "p1")["profile"]["name"], "bl")
        self.repo.save_player_palette("scoresaber", "p1",
                                      {"stage": "A", "yellow_stars": 7.5})
        self.repo.save_player_palette("beatleader", "p1",
                                      {"stage": "B", "yellow_stars": 9.0})
        self.assertEqual(self.repo.get_player_palette("scoresaber", "p1")["yellow_stars"], 7.5)
        self.assertEqual(self.repo.get_player_palette("beatleader", "p1")["yellow_stars"], 9.0)


class TestEnrichmentPlatform(unittest.TestCase):
    """enrichment 按当前平台读 stars/pp。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(pathlib.Path(self.tmp.name) / "t.sqlite")
        self.repo.upsert_map({
            "map_hash": HASH_A, "folder_name": "1 (a)", "path": "X:/1",
            "song_name": "Song", "song_author": "a", "mapper": "m",
            "bpm": 120.0, "song_length": 90.0, "version": "2.1.0",
            "difficulties": "[]", "info_json": "{}", "hash_source": "computed",
        })
        self.repo.upsert_replay({
            "replay_id": "r1", "file_path": "x.bsor", "timestamp": 1000,
            "map_hash": HASH_A, "song_name": "Song", "difficulty": "Expert",
            "mode": "Standard", "status": "analyzed", "analysis_status": "analyzed",
            "accuracy": 0.90, "completion_status": "completed",
        })
        self.repo.upsert_replay({
            "replay_id": "r2", "file_path": "y.bsor", "timestamp": 900,
            "map_hash": HASH_A, "song_name": "Song", "difficulty": "Expert",
            "mode": "Standard", "status": "analyzed", "analysis_status": "analyzed",
            "accuracy": 0.80, "completion_status": "completed",
        })
        self.repo.upsert_replay({
            "replay_id": "r3", "file_path": "nf.bsor", "timestamp": 800,
            "map_hash": HASH_A, "song_name": "Song", "difficulty": "Expert",
            "mode": "Standard", "status": "analyzed", "analysis_status": "analyzed",
            "accuracy": 0.80, "score": 100000, "score_effective": 50000,
            "has_nf": 1, "completion_status": "failed",
        })
        for platform, stars, pp in (("scoresaber", 5.0, 123.4),
                                    ("beatleader", 8.357, 296.6)):
            self.repo.upsert_ss_leaderboard({
                "leaderboard_id": f"lb-{platform}", "map_hash": HASH_A,
                "difficulty_rank": 7, "difficulty_name": "Expert",
                "game_mode": "Standard", "difficulty_raw": None,
                "song_name": "Song", "level_author": "m",
                "stars": stars, "ranked": 1, "qualified": 0, "loved": None,
                "max_pp": 200.0 if platform == "scoresaber" else None,
                "plays": None, "last_synced": "now",
            }, platform=platform)
            self.repo.upsert_ranked_cache(HASH_A, "Expert", stars, pp, "now",
                                          platform=platform)

    def tearDown(self):
        self.tmp.cleanup()

    def test_enrich_reads_active_platform(self):
        svc = EnrichmentService(self.repo)
        for platform, stars in (("scoresaber", 5.0),
                                ("beatleader", 8.357)):
            replays = self.repo.list_replays(limit=10)
            svc.enrich_flat(replays, platform)
            self.assertEqual(replays[0]["stars"], stars, platform)
            expected = (predict_pp(200.0, replays[0]["accuracy"])
                        if platform == "scoresaber" else 296.6)
            self.assertAlmostEqual(replays[0]["pp"], expected, places=6)

    def test_scoresaber_local_attempts_get_distinct_pp(self):
        """One cloud best must not overwrite every local attempt's PP."""
        replays = self.repo.list_replays(limit=10)
        EnrichmentService(self.repo).enrich_flat(replays, "scoresaber")
        by_id = {r["replay_id"]: r for r in replays}
        self.assertAlmostEqual(by_id["r1"]["pp"], predict_pp(200.0, 0.90))
        self.assertAlmostEqual(by_id["r2"]["pp"], predict_pp(200.0, 0.80))
        self.assertNotEqual(by_id["r1"]["pp"], by_id["r2"]["pp"])
        self.assertAlmostEqual(by_id["r3"]["pp"], predict_pp(200.0, 0.40))
        # The cloud-best cache remains source data; enrichment never mutates it.
        self.assertEqual(self.repo.get_ranked_cache(
            HASH_A, "Expert", platform="scoresaber")["pp"], 123.4)


# Real BeatLeader API fixtures (measured 2026-08, trimmed)
BL_SCORE_FIXTURE = {
    "id": 21099595,
    "pp": 296.6153,
    "modifiedScore": 941570,
    "baseScore": 941570,
    "accuracy": 0.90480334,
    "rank": 921,
    "weight": 1,
    "fullCombo": False,
    "badCuts": 3,
    "missedNotes": 1,
    "maxCombo": 728,
    "modifiers": "SF,NF",
    "timepost": 1738671636,
    "playerId": "76561199673091080",
    "leaderboard": {
        "id": "1232e71",
        "song": {
            "hash": "aa31dcd3f9da483793922d24f210944178b6cbc2",
            "name": "Chariot",
            "author": "USAO",
            "mapper": "Timbo",
        },
        "difficulty": {
            "difficultyName": "Expert",
            "value": 7,
            "modeName": "Standard",
            "status": 3,
            "stars": 8.357123,
        },
    },
}

BL_PROFILE_FIXTURE = {
    "id": "76561199673091080",
    "name": "ZiRCON",
    "country": "CN",
    "pp": 3217.388,
    "rank": 24505,
    "countryRank": 556,
    "scoreStats": {
        "averageRankedAccuracy": 0.82693607,
        "totalPlayCount": 65,
        "rankedPlayCount": 19,
    },
}


class TestBeatLeaderParsing(unittest.TestCase):
    """fetch_scores / fetch_profile 字段映射（mock _get，真实 API 样本）。"""

    def _cfg(self):
        return mock.Mock(timeout_seconds=30)

    def test_classify_record_rules(self):
        """用户规则（2026-08）：status==3 才 ranked；unranked 有星也不算；
        isUsableRankedScore = status==3 且 stars>0 且 pp>0。"""
        def rec(status, stars, pp):
            return {"difficulty": {"status": status, "stars": stars}, "pp": pp}

        # 正式 ranked + 完整数据 -> usable
        r = beatleader.classify_record(rec(3, 8.357, 296.6))
        self.assertEqual(r["category"], "ranked")
        self.assertTrue(r["usable"])
        # ranked 但数据不完整 -> 不可用
        r = beatleader.classify_record(rec(3, 8.357, 0))
        self.assertEqual(r["category"], "ranked")
        self.assertFalse(r["usable"])
        r = beatleader.classify_record(rec(3, None, 296.6))
        self.assertFalse(r["usable"])
        # unranked（0/1/2/5/7）即使有星也不算 ranked
        for status in (0, 1, 2, 5, 7):
            r = beatleader.classify_record(rec(status, 8.17, 0))
            self.assertEqual(r["category"], "unranked", status)
            self.assertFalse(r["usable"])
            self.assertIn("ranked", r["reason"])
        # 0 星/null 星
        r = beatleader.classify_record(rec(3, 0, 100))
        self.assertFalse(r["usable"])
        r = beatleader.classify_record(rec(7, None, 0))
        self.assertEqual(r["category"], "unranked")
        # 非数值
        r = beatleader.classify_record(rec(3, "x", "y"))
        self.assertFalse(r["usable"])
        r = beatleader.classify_record(rec(3, float("nan"), 1))
        self.assertFalse(r["usable"])

    def test_fetch_scores_mapping(self):
        with mock.patch.object(beatleader, "_get",
                               return_value={"data": [BL_SCORE_FIXTURE],
                                             "metadata": {"total": 1}}):
            scores = beatleader.fetch_scores(self._cfg(), "76561199673091080")
        self.assertEqual(len(scores), 1)
        s = scores[0]
        self.assertEqual(s["stars"], 8.357123)
        self.assertEqual(s["pp"], 296.6153)
        self.assertEqual(s["ranked"], 1)                       # status==3
        self.assertEqual(s["modifiers"], "SF,NF")
        self.assertEqual(s["song_hash"], "AA31DCD3F9DA483793922D24F210944178B6CBC2")
        self.assertEqual(s["difficulty"], "Expert")
        self.assertEqual(s["difficulty_rank"], 7)
        self.assertEqual(s["song_name"], "Chariot")
        # timepost unix -> ISO (1738671636 = 2025-02-04T12:20:36Z)
        self.assertTrue(s["time_set"].startswith("2025-02-04"))
        self.assertEqual(s["timepost"], 1738671636)

    def test_fetch_scores_ranked_non_ranked(self):
        non_ranked = json.loads(json.dumps(BL_SCORE_FIXTURE))
        non_ranked["leaderboard"]["difficulty"]["status"] = 7
        with mock.patch.object(beatleader, "_get",
                               return_value={"data": [BL_SCORE_FIXTURE, non_ranked]}):
            scores = beatleader.fetch_scores(self._cfg(), "p")
        self.assertEqual([s["ranked"] for s in scores], [1, 0])
        # build_ranked_index: pp only on ranked maps; stars kept either way
        with mock.patch.object(beatleader, "_get",
                               return_value={"data": [BL_SCORE_FIXTURE, non_ranked]}):
            idx = beatleader.build_ranked_index(self._cfg(), "p")
        key = ("AA31DCD3F9DA483793922D24F210944178B6CBC2", "Expert")
        # 两条同 key：后写覆盖（non-ranked 无 pp）；stars 保留
        self.assertEqual(idx[key]["stars"], 8.357123)
        self.assertIsNone(idx[key]["pp"])

    def test_fetch_profile_mapping(self):
        with mock.patch.object(beatleader, "_get", return_value=BL_PROFILE_FIXTURE):
            p = beatleader.fetch_profile(self._cfg(), "p")
        self.assertEqual(p["name"], "ZiRCON")
        self.assertEqual(p["rank"], 24505)
        self.assertEqual(p["pp"], 3217.388)
        # averageRankedAccuracy 0-1 -> percent (frontend divides by 100)
        self.assertAlmostEqual(p["scoreStats"]["averageRankedAccuracy"], 82.693607)
        self.assertEqual(p["scoreStats"]["totalPlayCount"], 65)
        self.assertEqual(p["scoreStats"]["rankedPlayCount"], 19)


if __name__ == "__main__":
    unittest.main()
