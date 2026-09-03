"""MapResolver 并发扫描互斥回归测试（v1.4.1 bug 修复）。

背景：历史页渲染 300 条 replay（无 lazy 封面）→ 86 个孤儿 hash 的封面请求
并发触发 ensure_map_path → 每个都执行全量 scan（19.5s/轮，先 scan 后更新
_last_scan 的竞态）→ 多轮并发扫描占满 FastAPI 线程池 + SQLite 写锁，
所有 API 卡死数分钟。

修复语义：
1. DB 无行的 hash：ensure_map_path 绝不触发全量扫描（封面是高频只读路径）
2. resolve 并发缺失 hash：任意时刻至多一轮全量扫描，其余请求立即判负
   （不等待 scan 完成）
3. 防抖 30s 冷却结束后允许再扫描（新下载谱面仍可被发现）
4. 有行但路径失效：懒修复语义保留，并发仍只扫一次
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
    """最小 fake：get_map 永远无行；upsert_map 记录调用（scan 写库路径）。"""

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
    """get_map 返回"有行但路径失效"，模拟原懒修复场景。"""

    def get_map(self, map_hash):
        return {"map_hash": map_hash, "path": "/nonexistent/path"}


class TestConcurrentScan(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.repo = FakeRepo()
        self.resolver = MapResolver(str(self.tmp), self.repo, "")
        # 慢 scan：0.4s 且计数（模拟真实全量扫描 19.5s 的慢路径）
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
        """DB 无行的 hash：ensure_map_path 绝不触发全量扫描（封面高频路径）。"""
        r = self.resolver.ensure_map_path("GHOST")
        self.assertIsNone(r)
        self.assertEqual(self.scan_calls, 0)

    def test_resolve_concurrent_only_one_scan(self):
        """并发 resolve 缺失 hash：只触发一次全量扫描，其余立即判负不等待。"""
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
        """防抖 30s 过后（模拟），新的缺失 hash 允许再触发一次扫描。"""
        self.resolver.resolve("HASH00000000000000000000000000000000")
        self.assertEqual(self.scan_calls, 1)
        self.resolver._last_scan = 0.0   # 模拟 30s 冷却结束
        self.resolver.resolve("HASH11111111111111111111111111111111")
        self.assertEqual(self.scan_calls, 2)

    def test_ensure_map_path_row_path_invalid_triggers_once(self):
        """有行但路径失效：懒修复保留，并发也只扫一次、不等待。"""
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
