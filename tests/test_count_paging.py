"""Overview paging mode regression test (v1.4.1).

/api/replays gained mode=count (paging by number of rows, 20 per page):
- page boundaries are correct (first/middle/last page sizes)
- out-of-range page numbers are clamped
- the default mode=day behaviour is unchanged (grouping by day)
"""
import pathlib
import shutil
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.repository import Repository  # noqa: E402

# module-level side effects match the existing tests (read-only connection to the real DB)
import backend.main as m  # noqa: E402


def _seed_replays(db_path: pathlib.Path, n: int) -> Repository:
    """Insert n minimal replay rows into a temp DB (descending timestamp) and return the Repository."""
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
        # enrichment needs tables on the repo; point a real EnrichmentService at the temp DB
        from backend.services.enrichment import EnrichmentService
        m.enrichment = EnrichmentService(self.repo)

    def tearDown(self):
        m.repo = self._orig_repo
        m.enrichment = self._orig_enrich
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _page(self, page, mode):
        """Call the route function directly (FastAPI Query defaults must be passed explicitly)."""
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
        self.assertEqual(r["page"], 3)          # out of range -> clamped to the last page

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
        self.assertEqual(r["pages"], 1)   # all 45 rows fall on the same day (nearby timestamps)


if __name__ == "__main__":
    unittest.main()
