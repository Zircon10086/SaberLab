"""增量刷新 + 分析缓存重置回归测试（v1.4.1 任务 2/3）。

任务 2：一键刷新本质是寻找新数据——
- sync_maps_batch(only_missing=True)：跳过已有 leaderboard 缓存的谱面
- nps_update 跳过"已计算且文件夹未变"的谱面

任务 3：分析参数变更 → reset_analysis_cache：
- 清 metrics/windows/motion_series，保留 replays 行/notes/maps
- replay 重置为 pending，详情页懒分析按新参数重算
"""
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import backend.scoresaber as ss  # noqa: E402
from backend.db.repository import Repository  # noqa: E402

import backend.main as m  # noqa: E402


class FakeConfig:
    network_timeout_seconds = 30.0
    network_proxy = ""


class _FakeRepo:
    """sync_maps_batch 的 repo 桩：记录查询，返回可控缓存状态。"""

    def __init__(self, cached_hashes=()):
        self.cached_hashes = set(cached_hashes)
        self.queried = []

    def get_ss_leaderboards_by_hash(self, mh):
        self.queried.append(mh)
        return [{"leaderboard_id": 1}] if mh in self.cached_hashes else []

    def get_map(self, mh):
        return {"song_name": f"song-{mh[:8]}"}


class TestOnlyMissingSync(unittest.TestCase):
    def test_skip_cached_hashes(self):
        """增量模式：已有缓存的谱面不联网（不进入同步队列）。"""
        repo = _FakeRepo(cached_hashes=("HASHAAA", "HASHBBB"))
        hashes = ["HASHAAA", "HASHBBB", "HASHCCC"]
        with mock.patch.object(ss, "sync_map_leaderboards",
                               return_value={"fetched": 1, "cached": 0,
                                             "failed": 0}) as sync:
            stats = ss.sync_maps_batch(FakeConfig(), repo, hashes,
                                       only_missing=True)
            self.assertEqual(sync.call_count, 1)          # 只同步 HASHCCC
            self.assertEqual(sync.call_args[0][2], "HASHCCC")
            self.assertEqual(stats["cached"], 2)          # 2 个跳过计入 cached
            self.assertEqual(stats["fetched"], 1)
            self.assertEqual(stats["maps"], 3)

    def test_force_syncs_all(self):
        """强制模式（联网重新更新数据）：全部谱面重新同步。"""
        repo = _FakeRepo(cached_hashes=("HASHAAA",))
        hashes = ["HASHAAA", "HASHBBB"]
        with mock.patch.object(ss, "sync_map_leaderboards",
                               return_value={"fetched": 1, "cached": 0,
                                             "failed": 0}) as sync:
            stats = ss.sync_maps_batch(FakeConfig(), repo, hashes,
                                       only_missing=False)
            self.assertEqual(sync.call_count, 2)
            self.assertEqual(stats["cached"], 0)


class TestResetAnalysisCache(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.db = self.tmpdir / "reset.sqlite"
        self.repo = Repository(self.db)
        with self.repo._conn() as c:
            c.execute(
                "INSERT INTO replays(replay_id, timestamp, song_name, status,"
                " analysis_status, analysis_version, analyzed_at)"
                " VALUES('r1', 1700000000, 's1', 'analyzed', 'analyzed', 3, '2026-01-01')")
            c.execute("INSERT INTO notes(replay_id, idx) VALUES('r1', 0)")
            c.execute(
                "INSERT INTO metrics(replay_id, scope, name, value)"
                " VALUES('r1', 'overall', 'accuracy', 0.9)")
            c.execute(
                "INSERT INTO windows(replay_id, window_idx, t_start, t_end, metrics_json)"
                " VALUES('r1', 0, 0.0, 30.0, '{}')")
            c.execute(
                "INSERT INTO motion_series(replay_id, series_json) VALUES('r1', '{}')")
            c.execute(
                "INSERT INTO maps(map_hash, song_name) VALUES('H1', 'm1')")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_reset_clears_analysis_and_keeps_replays(self):
        res = self.repo.reset_analysis_cache()
        self.assertTrue(res["cleared"])
        with self.repo._conn() as c:
            for t in ("metrics", "windows", "motion_series"):
                self.assertEqual(
                    c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], 0,
                    f"{t} 应清空")
            # replays 行保留，重置为 pending
            row = c.execute("SELECT status, analysis_status, analysis_version,"
                            " analyzed_at FROM replays WHERE replay_id='r1'"
                            ).fetchone()
            self.assertEqual(row["status"], "parsed")
            self.assertEqual(row["analysis_status"], "pending")
            self.assertIsNone(row["analysis_version"])
            self.assertIsNone(row["analyzed_at"])
            # notes（判定统计）与 maps（谱面库）保留
            self.assertEqual(c.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM maps").fetchone()[0], 1)

    def test_settings_save_analysis_clears_cache(self):
        """实际变更的 analysis.* 配置保存 → 触发 reset_analysis_cache。

        2026-08 修复后，端点按 save_values 返回的 changed（真实变更键）判定，
        不再按"提交了 analysis.* 键"判定——表单每次保存都全量提交字段。
        """
        m.repo = self.repo
        orig_svc = m.config_svc
        orig_reload = m.reload_runtime_config
        m.config_svc = mock.Mock()
        m.config_svc.save_values.return_value = {
            "saved": True, "changed": ["analysis.window_step_seconds"]}
        m.reload_runtime_config = mock.Mock()
        try:
            res = m.api_settings_save(m.SettingsSaveBody(
                values={"analysis.window_step_seconds": "2.0"}))
            self.assertTrue(res["saved"])
            self.assertIn("重新计算", res.get("message", ""))
            with self.repo._conn() as c:
                self.assertEqual(
                    c.execute("SELECT COUNT(*) FROM metrics").fetchone()[0], 0)
        finally:
            m.repo = Repository(m._orig_db) if hasattr(m, "_orig_db") else m.repo
            m.config_svc = orig_svc
            m.reload_runtime_config = orig_reload

    def test_settings_save_unchanged_analysis_keeps_cache(self):
        """提交未变更的 analysis.* 值不得清空分析缓存（2026-08 bug 修复）。

        设置表单每次保存都会提交全部分析参数；旧逻辑按键名判定导致
        "保存任意设置 → 分析数据被清空"。
        """
        self.repo.reset_analysis_cache()
        with self.repo._conn() as c:
            c.execute("INSERT INTO metrics(replay_id, scope, name, value)"
                      " VALUES('r1', 'overall', 'score', 100.0)")
        m.repo = self.repo
        orig_svc = m.config_svc
        orig_reload = m.reload_runtime_config
        m.config_svc = mock.Mock()
        # save_values 判定无真实变更（changed 为空）
        m.config_svc.save_values.return_value = {"saved": True, "changed": []}
        m.reload_runtime_config = mock.Mock()
        try:
            res = m.api_settings_save(m.SettingsSaveBody(
                values={"analysis.slope_group_notes": "50",
                        "player.player_name_fallback": "Same"}))
            self.assertTrue(res["saved"])
            self.assertNotIn("重新计算", res.get("message", ""))
            with self.repo._conn() as c:
                self.assertEqual(
                    c.execute("SELECT COUNT(*) FROM metrics").fetchone()[0], 1)
        finally:
            m.repo = Repository(m._orig_db) if hasattr(m, "_orig_db") else m.repo
            m.config_svc = orig_svc
            m.reload_runtime_config = orig_reload

    def test_settings_save_non_analysis_no_clear(self):
        """保存非分析参数 → 不清缓存。"""
        m.repo = self.repo
        orig_svc = m.config_svc
        orig_reload = m.reload_runtime_config
        m.config_svc = mock.Mock()
        m.config_svc.save_values.return_value = {"saved": True}
        m.reload_runtime_config = mock.Mock()
        try:
            res = m.api_settings_save(m.SettingsSaveBody(
                values={"player.player_name_fallback": "ZiRCON"}))
            self.assertTrue(res["saved"])
            self.assertNotIn("message", res)
            with self.repo._conn() as c:
                self.assertEqual(
                    c.execute("SELECT COUNT(*) FROM metrics").fetchone()[0], 1)
        finally:
            m.config_svc = orig_svc
            m.reload_runtime_config = orig_reload


if __name__ == "__main__":
    unittest.main()
