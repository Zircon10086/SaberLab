"""Incremental refresh + analysis cache reset regression tests (v1.4.1 tasks 2/3).

Task 2: one-click refresh is essentially a hunt for new data —
- sync_maps_batch(only_missing=True): skips beatmaps that already have a leaderboard
  cache
- nps_update skips beatmaps that are "already computed and whose folder is
  unchanged"

Task 3: analysis parameter change → reset_analysis_cache:
- clears metrics/windows/motion_series, keeps the replays rows/notes/maps
- replays go back to pending, and the detail page recomputes lazily with the new
  parameters
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
    """repo stub for sync_maps_batch: records queries and returns a controllable cache state."""

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
        """Incremental mode: a beatmap that is already cached is not fetched over the network
        (it never enters the sync queue)."""
        repo = _FakeRepo(cached_hashes=("HASHAAA", "HASHBBB"))
        hashes = ["HASHAAA", "HASHBBB", "HASHCCC"]
        with mock.patch.object(ss, "sync_map_leaderboards",
                               return_value={"fetched": 1, "cached": 0,
                                             "failed": 0}) as sync:
            stats = ss.sync_maps_batch(FakeConfig(), repo, hashes,
                                       only_missing=True)
            self.assertEqual(sync.call_count, 1)          # only HASHCCC is synced
            self.assertEqual(sync.call_args[0][2], "HASHCCC")
            self.assertEqual(stats["cached"], 2)          # the 2 skipped ones count as cached
            self.assertEqual(stats["fetched"], 1)
            self.assertEqual(stats["maps"], 3)

    def test_force_syncs_all(self):
        """Force mode (go online and update the data again): every beatmap is re-synced."""
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
            # the replays row is kept and reset to pending
            row = c.execute("SELECT status, analysis_status, analysis_version,"
                            " analyzed_at FROM replays WHERE replay_id='r1'"
                            ).fetchone()
            self.assertEqual(row["status"], "parsed")
            self.assertEqual(row["analysis_status"], "pending")
            self.assertIsNone(row["analysis_version"])
            self.assertIsNone(row["analyzed_at"])
            # notes (judgement statistics) and maps (the beatmap library) are kept
            self.assertEqual(c.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM maps").fetchone()[0], 1)

    def test_settings_save_analysis_clears_cache(self):
        """Saving an analysis.* config value that actually changed → triggers
        reset_analysis_cache.

        After the 2026-08 fix the endpoint decides from the changed list returned by
        save_values (the keys that really changed) instead of "an analysis.* key was
        submitted" — the form submits every field in full on every save."""
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
        """Submitting unchanged analysis.* values must not wipe the analysis cache (2026-08
        bug fix).

        The settings form submits every analysis parameter on every save; the old logic
        decided by key name, which produced "save any setting → the analysis data gets
        wiped"."""
        self.repo.reset_analysis_cache()
        with self.repo._conn() as c:
            c.execute("INSERT INTO metrics(replay_id, scope, name, value)"
                      " VALUES('r1', 'overall', 'score', 100.0)")
        m.repo = self.repo
        orig_svc = m.config_svc
        orig_reload = m.reload_runtime_config
        m.config_svc = mock.Mock()
        # save_values reports no real change (changed is empty)
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
        """Saving non-analysis parameters → the cache is not cleared."""
        m.repo = self.repo
        orig_svc = m.config_svc
        orig_reload = m.reload_runtime_config
        m.config_svc = mock.Mock()
        m.config_svc.save_values.return_value = {"saved": True}
        m.reload_runtime_config = mock.Mock()
        try:
            res = m.api_settings_save(m.SettingsSaveBody(
                values={"player.player_name_fallback": "TestPlayer"}))
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
