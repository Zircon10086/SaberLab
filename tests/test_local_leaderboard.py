"""LocalLeaderboard 第二扫描源回归测试（2026-09，HANDOFF §4.25 待办 2）。

- normalize_ll_replay_name：`_<tick>` 后缀归一（BL 风格名）
- get_replay_by_session / refresh_replay_file：会话键匹配 + 文件位置修复
- ingest_local_leaderboard：LL-only 入库 / BL 孪生跳过 / 缺文件修复 / 现有文件冗余跳过

使用真实 BSOR 夹具 tests/fixtures/secret-boss-expert.bsor（缺失时跳过，
与 test_bsor_parser 一致），临时目录搭建 BL/LL 双目录场景。
"""
import pathlib
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import Config  # noqa: E402
from backend.db.repository import Repository  # noqa: E402
from backend.maps.resolver import MapResolver  # noqa: E402
from backend.watcher import ReplayPipeline, normalize_ll_replay_name  # noqa: E402
from backend.bsor.parser import parse_metadata_only  # noqa: E402

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "secret-boss-expert.bsor"


def _ll_name(meta) -> str:
    """Build a LocalLeaderboard-style name from a replay's metadata."""
    info = meta.info
    return (f"{info.player_id}-{info.song_name}-{info.difficulty}-{info.mode}-"
            f"{info.map_hash.upper()}-{info.timestamp_int}_639999999999999999.bsor")


class TestNormalizeName(unittest.TestCase):
    def test_ll_tick_stripped(self):
        self.assertEqual(
            normalize_ll_replay_name(
                "76561199673091080-SECRET BOSS-Expert-Standard-"
                "807E71EB310B8AEBA98A643C3E8C390E24E89A80-1786976654_12345.bsor"),
            "76561199673091080-SECRET BOSS-Expert-Standard-"
            "807E71EB310B8AEBA98A643C3E8C390E24E89A80-1786976654.bsor")

    def test_bl_name_unchanged(self):
        name = ("76561199673091080-SECRET BOSS-Expert-Standard-"
                "807E71EB310B8AEBA98A643C3E8C390E24E89A80-1786976654.bsor")
        self.assertEqual(normalize_ll_replay_name(name), name)

    def test_song_with_underscore_digits(self):
        """歌名本身含下划线+数字（如 'Miku_' 类命名）不误伤。"""
        name = ("76561199673091080-Song_Miku_2024-Expert-Standard-"
                "807E71EB310B8AEBA98A643C3E8C390E24E89A80-1786976654"
                "_639999999999999999.bsor")
        self.assertEqual(
            normalize_ll_replay_name(name),
            "76561199673091080-Song_Miku_2024-Expert-Standard-"
            "807E71EB310B8AEBA98A643C3E8C390E24E89A80-1786976654.bsor")

    def test_non_standard_name(self):
        self.assertIsNone(normalize_ll_replay_name("replay.bsor"))
        self.assertIsNone(normalize_ll_replay_name("7656-abc-123.bsor"))


class TestSessionKeyRepo(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.repo = Repository(self.tmpdir / "t.sqlite")

    def test_get_by_session(self):
        self.repo.upsert_replay({
            "replay_id": "abc123", "file_path": "/x/a.bsor", "file_name": "a.bsor",
            "file_size": 1, "file_mtime": 1.0, "timestamp": 1786976654,
            "player_id": "76561199673091080", "player_name": "p", "platform": "pc",
            "tracking_system": "oculus", "hmd": "h", "controller": "c",
            "game_version": "1.40.8", "mod_version": "0.9.33",
            "map_hash": "807E71EB310B8AEBA98A643C3E8C390E24E89A80",
            "song_name": "SECRET BOSS", "difficulty": "Expert", "mode": "Standard",
            "environment": "", "modifiers": "", "score": 0,
            "completion_status": "completed", "status": "parsed",
            "analysis_status": "pending", "error_message": None,
            "parsed_at": "", "analyzed_at": None,
        })
        row = self.repo.get_replay_by_session(
            "76561199673091080",
            "807e71eb310b8aeba98a643c3e8c390e24e89a80",  # lowercase input accepted
            1786976654)
        self.assertEqual(row["replay_id"], "abc123")
        self.assertIsNone(self.repo.get_replay_by_session(
            "76561199673091080",
            "807E71EB310B8AEBA98A643C3E8C390E24E89A80", 9999999999))
        self.assertIsNone(self.repo.get_replay_by_session("", "", 0))

    def test_refresh_replay_file(self):
        p = self.tmpdir / "twin.bsor"
        p.write_bytes(b"fake-replay-bytes")
        self.repo.upsert_replay({
            "replay_id": "r1", "file_path": "/gone/a.bsor", "file_name": "a.bsor",
            "file_size": 0, "file_mtime": 0.0, "timestamp": 1,
            "player_id": "p1", "player_name": "p", "platform": "pc",
            "tracking_system": "", "hmd": "", "controller": "",
            "game_version": "", "mod_version": "", "map_hash": "MH",
            "song_name": "s", "difficulty": "Easy", "mode": "Standard",
            "environment": "", "modifiers": "", "score": 0,
            "completion_status": "completed", "status": "parsed",
            "analysis_status": "pending", "error_message": None,
            "parsed_at": "", "analyzed_at": None,
        })
        self.repo.refresh_replay_file("r1", str(p))
        row = self.repo.get_replay("r1")
        self.assertEqual(row["file_path"], str(p))
        self.assertEqual(row["file_name"], "twin.bsor")
        self.assertEqual(row["file_size"], len(b"fake-replay-bytes"))


@unittest.skipUnless(FIXTURE.exists(), f"fixture 不存在: {FIXTURE}")
class TestIngestLocalLeaderboard(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.bl_dir = self.tmpdir / "BeatLeader"
        self.ll_dir = self.tmpdir / "LocalLeaderboard"
        self.bl_dir.mkdir()
        self.ll_dir.mkdir()
        self.cfg = Config(
            instance_root=str(self.tmpdir),
            replay_dir=str(self.bl_dir),
            custom_levels_dir=str(self.tmpdir / "CustomLevels"),
            songcore_cache=str(self.tmpdir / "SongHashData.dat"),
            local_leaderboard_dir=str(self.ll_dir),
            db_path=self.tmpdir / "t.sqlite",
            parsed_dir=self.tmpdir / "parsed",
        )
        self.repo = Repository(self.cfg.db_path)
        self.resolver = MapResolver(self.cfg.custom_levels_dir, self.repo,
                                    self.cfg.songcore_cache)
        self.pipe = ReplayPipeline(self.cfg, self.repo, self.resolver)
        self.meta = parse_metadata_only(FIXTURE)
        self.ll_file = self.ll_dir / _ll_name(self.meta)
        shutil.copyfile(FIXTURE, self.ll_file)

    def test_ll_only_ingest(self):
        """BL 无孪生 → LL-only 正常入库（file_path 指向 LL 文件）。"""
        out = self.pipe.ingest_local_leaderboard()
        self.assertEqual(out["ingested"], 1)
        self.assertEqual(out["repaired"], 0)
        row = self.repo.get_replay_by_session(
            self.meta.info.player_id, self.meta.info.map_hash.upper(),
            self.meta.info.timestamp_int)
        self.assertIsNotNone(row)
        self.assertEqual(row["file_path"], str(self.ll_file))
        full = self.repo.get_replay(row["replay_id"])
        self.assertEqual(full["file_name"], _ll_name(self.meta))

    def test_bl_twin_exists_skips(self):
        """BL 孪生存在 → 跳过（归 BL 扫描处理），不重复入库。"""
        bl_twin = self.bl_dir / normalize_ll_replay_name(_ll_name(self.meta))
        shutil.copyfile(FIXTURE, bl_twin)
        out = self.pipe.ingest_local_leaderboard()
        self.assertEqual(out["ingested"], 0)
        self.assertEqual(out["duplicate"], 1)
        self.assertEqual(self.repo.count_replays(), 0)

    def test_repair_missing_file(self):
        """行内文件消失 + LL 孪生存活 → 修复 file_path 指向 LL 副本。"""
        bl_twin = self.bl_dir / normalize_ll_replay_name(_ll_name(self.meta))
        shutil.copyfile(FIXTURE, bl_twin)
        self.pipe.ingest_file(str(bl_twin))          # BL 先入库
        row = self.repo.get_replay_by_session(
            self.meta.info.player_id, self.meta.info.map_hash.upper(),
            self.meta.info.timestamp_int)
        self.assertEqual(row["file_path"], str(bl_twin))
        bl_twin.unlink()                             # 原始文件消失
        out = self.pipe.ingest_local_leaderboard()
        self.assertEqual(out["repaired"], 1)
        row = self.repo.get_replay(row["replay_id"])
        self.assertEqual(row["file_path"], str(self.ll_file))
        self.assertEqual(row["status"], "parsed")    # 分析数据不动
        self.assertEqual(row["analysis_status"], "pending")

    def test_row_file_present_redundant(self):
        """行内文件仍在 → LL 副本冗余，跳过且不修改行。"""
        bl_twin = self.bl_dir / normalize_ll_replay_name(_ll_name(self.meta))
        shutil.copyfile(FIXTURE, bl_twin)
        self.pipe.ingest_file(str(bl_twin))
        out = self.pipe.ingest_local_leaderboard()
        self.assertEqual(out["duplicate"], 1)
        row = self.repo.get_replay_by_session(
            self.meta.info.player_id, self.meta.info.map_hash.upper(),
            self.meta.info.timestamp_int)
        self.assertEqual(row["file_path"], str(bl_twin))

    def test_missing_ll_dir_disabled(self):
        """LL 目录不存在（零配置自动检测）→ 空结果，不报错。"""
        self.cfg.local_leaderboard_dir = str(self.tmpdir / "no-such-dir")
        out = self.pipe.ingest_local_leaderboard()
        self.assertFalse(out["exists"])
        self.assertEqual(out["files"], 0)

    def test_run_twice_idempotent(self):
        """重复运行幂等：第一次 LL-only 入库，第二次判定为重复/冗余。"""
        self.pipe.ingest_local_leaderboard()
        out2 = self.pipe.ingest_local_leaderboard()
        self.assertEqual(out2["ingested"], 0)
        self.assertEqual(self.repo.count_replays(), 1)


if __name__ == "__main__":
    unittest.main()
