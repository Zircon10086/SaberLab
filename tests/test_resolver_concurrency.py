"""MapResolver concurrent-scan mutual-exclusion regression test (v1.4.1 bug fix).

Background: the history page rendered 300 replays (no lazy covers) -> 86 cover
requests for orphan hashes concurrently triggered ensure_map_path -> each one ran
a full scan (19.5s per round; _last_scan was updated only after the scan, which is
the race) -> several rounds of concurrent scanning saturated the FastAPI thread
pool + the SQLite write lock, and every API froze for minutes.

Fixed semantics:
1. hash with no DB row: ensure_map_path never triggers a full scan (covers are a
   high-frequency read-only path)
2. concurrent resolve of a missing hash: at most one full scan at any moment, the
   remaining requests fail immediately (without waiting for the scan to finish)
3. once the 30s debounce cooldown has expired a rescan is allowed again (newly
   downloaded maps can still be discovered)
4. row exists but its path is invalid: the lazy-repair semantics are kept, and
   concurrent callers still scan only once
"""
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.maps.resolver import MapResolver  # noqa: E402


class FakeRepo:
    """Minimal fake: get_map never finds a row; upsert_map records calls (the scan's DB write path)."""

    def __init__(self):
        self.upsert_count = 0
        self._lock = threading.Lock()

    def get_map(self, map_hash):
        return None

    def get_map_by_path(self, path):
        return None

    def upsert_map(self, m):
        with self._lock:
            self.upsert_count += 1


class RepoWithInvalidPathRow(FakeRepo):
    """get_map returns "row exists but path is invalid", the original lazy-repair scenario."""

    def get_map(self, map_hash):
        return {"map_hash": map_hash, "path": "/nonexistent/path"}


class TestConcurrentScan(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.repo = FakeRepo()
        self.resolver = MapResolver(str(self.tmp), self.repo, "")
        # slow scan: 0.4s and counted (stands in for the real 19.5s full-scan path)
        self.scan_calls = 0
        self.scan_lock = threading.Lock()
        real_scan = self.resolver.scan

        def slow_scan(*a, **kw):
            with self.scan_lock:
                self.scan_calls += 1
            time.sleep(0.4)
            return real_scan(*a, **kw)

        self.resolver.scan = slow_scan

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ensure_map_path_no_row_no_scan(self):
        """hash with no DB row: ensure_map_path never triggers a full scan (the high-frequency cover path)."""
        r = self.resolver.ensure_map_path("GHOST")
        self.assertIsNone(r)
        self.assertEqual(self.scan_calls, 0)

    def test_resolve_concurrent_only_one_scan(self):
        """concurrent resolve of a missing hash: one full scan only, the rest fail immediately without waiting."""
        results, errors = [], []

        def worker(h):
            try:
                results.append(self.resolver.resolve(h))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        hashes = [f"HASH{i:032X}" for i in range(20)]
        threads = [threading.Thread(target=worker, args=(h,)) for h in hashes]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        dt = time.time() - t0
        self.assertEqual(errors, [])
        self.assertEqual(self.scan_calls, 1, "并发触发只能有一次全量扫描")
        self.assertLess(dt, 2.0,
                        f"并发请求不应等待 scan 完成，实际 {dt:.2f}s")
        self.assertTrue(all(r is None for r in results))

    def test_rescan_after_cooldown_allowed(self):
        """after the 30s debounce has passed (simulated), a new missing hash may trigger another scan."""
        self.resolver.resolve("HASH00000000000000000000000000000000")
        self.assertEqual(self.scan_calls, 1)
        self.resolver._last_scan = 0.0   # simulate the 30s cooldown having expired
        self.resolver.resolve("HASH11111111111111111111111111111111")
        self.assertEqual(self.scan_calls, 2)

    def test_ensure_map_path_row_path_invalid_triggers_once(self):
        """row exists but path is invalid: lazy repair is kept; concurrent callers still scan once and do not wait."""
        self.resolver.repo = RepoWithInvalidPathRow()
        results, errors = [], []

        def worker():
            try:
                results.append(
                    self.resolver.ensure_map_path("HASH22222222222222222222222222222222"))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        dt = time.time() - t0
        self.assertEqual(errors, [])
        self.assertEqual(self.scan_calls, 1, "并发懒修复只允许一次全量扫描")
        self.assertLess(dt, 2.0)


if __name__ == "__main__":
    unittest.main()
