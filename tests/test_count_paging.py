"""总览分页模式回归测试（v1.4.1）。

/api/replays 新增 mode=count（按数量分页，20 条/页）：
- 分页边界正确（首/中/末页条数）
- 越界页码钳制
- mode=day 默认行为不变（按天分组）
"""
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.repository import Repository  # noqa: E402

# 模块级副作用与现有测试一致（真实库只读连接）
import backend.main as m  # noqa: E402


def _seed_replays(db_path: pathlib.Path, n: int) -> Repository:
    """在临时库插入 n 条最小 replay 行（timestamp 递减），返回 Repository。"""
    repo = Repository(db_path)
    with repo._conn() as c:
        base = 1_700_000_000
        for i in range(n):
            c.execute(
                "INSERT INTO replays(replay_id, timestamp, song_name, difficulty,"
                " status, analysis_status) VALUES(?,?,?,?,?,?)",
                (f"r{i:04d}", base - i, f"song-{i}", "Expert", "analyzed", "analyzed"))
    return repo


class TestCountPaging(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.db = self.tmpdir / "paging.sqlite"
        self.repo = _seed_replays(self.db, 45)
        self._orig_repo = m.repo
        self._orig_enrich = m.enrichment
        m.repo = self.repo
        # enrichment 需要 repo 上的表；用真实 EnrichmentService 指向临时库
        from backend.services.enrichment import EnrichmentService
        m.enrichment = EnrichmentService(self.repo)

    def tearDown(self):
        m.repo = self._orig_repo
        m.enrichment = self._orig_enrich
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _page(self, page, mode):
        """直接调用路由函数（FastAPI Query 默认值需显式传参）。"""
        return m.api_replays(page=page, mode=mode, map_hash=None, days=None,
                             flat=0, limit=200)

    def test_first_middle_last_pages(self):
        r1 = self._page(1, "count")
        self.assertEqual(r1["total"], 45)
        self.assertEqual(r1["pages"], 3)
        self.assertEqual(len(r1["replays"]), 20)
        r2 = self._page(2, "count")
        self.assertEqual(len(r2["replays"]), 20)
        r3 = self._page(3, "count")
        self.assertEqual(len(r3["replays"]), 5)

    def test_page_clamped(self):
        r = self._page(99, "count")
        self.assertEqual(r["page"], 3)          # 越界钳制到末页

    def test_empty_db(self):
        empty_db = self.tmpdir / "empty.sqlite"
        repo0 = Repository(empty_db)
        m.repo = repo0
        from backend.services.enrichment import EnrichmentService
        m.enrichment = EnrichmentService(repo0)
        r = self._page(1, "count")
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["pages"], 0)
        self.assertEqual(r["replays"], [])

    def test_day_mode_unchanged(self):
        r = self._page(1, "day")
        self.assertIn("days", r)
        self.assertIn("total_days", r)
        self.assertEqual(r["pages"], 1)   # 45 条都在同一天（timestamp 相近）


if __name__ == "__main__":
    unittest.main()
