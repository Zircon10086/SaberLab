"""Empty-database guard regression test (v1.4.1).

Requirement: while the database is empty (a brand-new first launch / after clearing
the analysis cache), every background task except the overview's "⚡ one-click refresh"
must refuse to run and return clear guidance (nearly every feature is built on data
that has already been scanned into the DB).

Implementation: the backend checks count_replays()==0 live (a millisecond-level COUNT,
so there is no global flag that could fall out of sync); refresh/all is let through
(it is the only correct entry point for an empty database).
"""
import pathlib
import sys
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import HTTPException  # noqa: E402

# module-level side effect: load_config + Repository (read-only connection to the real DB), as in the existing tests
import backend.main as m  # noqa: E402


class TestDbEmptyGuard(unittest.TestCase):
    def setUp(self):
        self._orig_count = m.repo.count_replays

    def tearDown(self):
        m.repo.count_replays = self._orig_count

    def test_not_empty_passes(self):
        """DB not empty: the guard lets it through."""
        m.repo.count_replays = lambda: 332
        m._require_db_populated()   # passing == not raising

    def test_empty_raises_400(self):
        """DB empty: raises 400 and the message points at the one-click refresh."""
        m.repo.count_replays = lambda: 0
        with self.assertRaises(HTTPException) as ctx:
            m._require_db_populated()
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("一键刷新", str(ctx.exception.detail))

    def test_task_apis_rejected_when_empty(self):
        """Empty DB: every task API except refresh/all is refused (the 400 comes before the task starts)."""
        m.repo.count_replays = lambda: 0
        apis = [
            ("api_analyze_latest", lambda: m.api_analyze_latest(None)),
            ("api_analyze_all", lambda: m.api_analyze_all(None)),
            ("api_ingest_all", lambda: m.api_ingest_all()),
            ("api_maps_rescan", lambda: m.api_maps_rescan()),
            ("api_scoresaber_update_ranked", lambda: m.api_scoresaber_update_ranked()),
            ("api_maps_update_nps", lambda: m.api_maps_update_nps()),
            ("api_refresh_online", lambda: m.api_refresh_online()),
        ]
        for name, fn in apis:
            with self.assertRaises(HTTPException, msg=f"{name} 未拦截") as ctx:
                fn()
            self.assertEqual(ctx.exception.status_code, 400,
                             f"{name} 应返回 400")

    def test_refresh_all_has_no_guard(self):
        """refresh/all is the only entry point on an empty DB: it must not call _require_db_populated."""
        import inspect
        src = inspect.getsource(m.api_refresh_all)
        self.assertNotIn("_require_db_populated", src)

    def test_refresh_all_includes_player_cloud_refresh(self):
        """One-click refresh asks ranked_update to refresh the player level too."""
        started = []
        with mock.patch.object(m, "_require_replay_dir"), \
             mock.patch.object(m, "_require_maps_dir"), \
             mock.patch.object(m, "_start_task",
                               side_effect=lambda kind, fn, args=():
                               started.append((kind, args))):
            result = m.api_refresh_all(None)
        self.assertIn(("ranked_update", (True, True)), started)
        self.assertIn("ranked_update", result["tasks"])

    def test_ranked_update_refreshes_active_player_data(self):
        """The online task performs the requested profile/palette refresh."""
        sync_stats = {"maps": 0, "fetched": 0, "cached": 0}
        with mock.patch.object(m, "_active_platform", return_value="scoresaber"), \
             mock.patch.object(m, "_wait_ingest_done"), \
             mock.patch.object(m.repo, "list_replays", return_value=[]), \
             mock.patch.object(m.repo, "count_ss_leaderboards", return_value=0), \
             mock.patch.object(m.scoresaber, "sync_maps_batch",
                               return_value=sync_stats.copy()), \
             mock.patch.object(m.scoresaber, "build_ranked_index", return_value={}), \
             mock.patch.object(m, "_scoresaber_id", return_value="player"), \
             mock.patch.object(m, "_cloud_page_refresh") as refresh, \
             mock.patch.object(m, "_set_task") as set_task:
            m._run_ranked_update(only_missing=True, refresh_player=True)
        refresh.assert_called_once_with("scoresaber")
        final = set_task.call_args_list[-1]
        self.assertFalse(final.kwargs["running"])
        self.assertTrue(final.kwargs["results"][0]["player_refreshed"])


if __name__ == "__main__":
    unittest.main()
