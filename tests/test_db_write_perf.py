"""Tests for the performance changes on the database write path (2026-09).

Covers three things that a later change could easily break:
  1. Repository reuses its connection within a thread (no more open/close per statement)
  2. session() merges several writes into one transaction and rolls the whole thing back on failure (one replay stays atomic)
  3. the batch path does not trigger a full map-library rescan (trigger_scan=False / map_scan=False)
"""
import pathlib
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import load_config  # noqa: E402
from backend.db.repository import Repository  # noqa: E402
from backend.maps.resolver import MapResolver  # noqa: E402


class TestConnectionReuse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(pathlib.Path(self.tmp.name) / "t.sqlite")

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def test_same_connection_is_reused(self):
        """Two reads on one thread must share one connection (2026-09: each new
        connection cost ~18 ms on a large DB — the dominant write cost)."""
        ids = []
        for _ in range(3):
            with self.repo._conn() as c:
                ids.append(id(c))
        self.assertEqual(len(set(ids)), 1, "连接未被复用")

    def test_close_then_reopen(self):
        with self.repo._conn() as c:
            first = id(c)
        self.repo.close()
        with self.repo._conn() as c:
            self.assertNotEqual(first, id(c), "close() 后应新建连接")

    def test_writes_still_commit_without_session(self):
        """Standalone writes must still be visible (commit still happens)."""
        self.repo.save_player_cache("scoresaber", "p1", {"name": "x"}, [])
        self.assertIsNotNone(self.repo.get_player_cache("scoresaber", "p1"))


class TestSessionTransaction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(pathlib.Path(self.tmp.name) / "t.sqlite")

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def test_session_commits_everything(self):
        with self.repo.session():
            self.repo.save_player_cache("scoresaber", "p1", {"name": "a"}, [])
            self.repo.save_player_cache("scoresaber", "p2", {"name": "b"}, [])
        self.assertIsNotNone(self.repo.get_player_cache("scoresaber", "p1"))
        self.assertIsNotNone(self.repo.get_player_cache("scoresaber", "p2"))

    def test_session_rolls_back_on_error(self):
        """A replay's derived rows must be all-or-nothing: the old code spread a
        single replay over 4 independent transactions, so a failure could leave
        it half-written."""
        with self.assertRaises(RuntimeError):
            with self.repo.session():
                self.repo.save_player_cache("scoresaber", "p1", {"name": "a"}, [])
                raise RuntimeError("boom")
        self.assertIsNone(self.repo.get_player_cache("scoresaber", "p1"))

    def test_nested_session_reuses_outer(self):
        conns = []
        with self.repo.session() as outer:
            conns.append(id(outer))
            with self.repo.session() as inner:
                conns.append(id(inner))
                self.repo.save_player_cache("scoresaber", "p1", {"name": "a"}, [])
        self.assertEqual(len(set(conns)), 1, "嵌套 session 应复用同一连接")
        self.assertIsNotNone(self.repo.get_player_cache("scoresaber", "p1"))

    def test_conn_inside_session_is_not_closed(self):
        """_ConnCtx inside a session must not commit/close the shared connection."""
        with self.repo.session() as outer:
            with self.repo._conn() as inner:
                self.assertIs(inner, outer)
            # the outer connection is still usable after the inner block exits
            outer.execute("SELECT 1").fetchone()


class TestBatchSkipsMapScan(unittest.TestCase):
    """The batch path must not trigger a full map-library rescan (~14s each, see the 2026-09 incident)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(pathlib.Path(self.tmp.name) / "t.sqlite")
        # point it at an empty directory: a real scan would find nothing, so a counter shows whether it ran
        self.empty = pathlib.Path(self.tmp.name) / "levels"
        self.empty.mkdir()
        self.resolver = MapResolver(str(self.empty), self.repo, "")
        self.scans = 0
        orig = self.resolver.scan

        def counting_scan(*a, **kw):
            self.scans += 1
            return orig(*a, **kw)

        self.resolver.scan = counting_scan

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def test_trigger_scan_false_does_not_scan(self):
        self.assertIsNone(self.resolver.resolve("A" * 40, trigger_scan=False))
        self.assertEqual(self.scans, 0, "trigger_scan=False 仍触发了扫描")

    def test_trigger_scan_true_scans_on_miss(self):
        self.assertIsNone(self.resolver.resolve("B" * 40, trigger_scan=True))
        self.assertEqual(self.scans, 1, "默认路径应保留重扫行为（新下载谱面需要）")

    def test_pipeline_map_scan_flag_defaults_on(self):
        from backend.watcher import ReplayPipeline
        cfg = load_config()
        self.assertTrue(ReplayPipeline(cfg, self.repo, self.resolver).map_scan)
        self.assertFalse(ReplayPipeline(cfg, self.repo, self.resolver,
                                        map_scan=False).map_scan)


if __name__ == "__main__":
    unittest.main()
