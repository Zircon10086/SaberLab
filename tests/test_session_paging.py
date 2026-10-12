"""Overview "by play session" paging (2026-09).

Grouping is based on the time gap between adjacent replays (threshold
analysis.session_gap_minutes, default 60 minutes), rather than on calendar days —
precisely to fix the two inherent errors of "by day":
  1. Playing past midnight: 00:40 and 23:50 (the next day) belong to the same
     session, whereas by day they get cut into two days
  2. One block in the morning and one in the afternoon of the same day: by day
     they get merged into a single day
Covers: the pure grouping function, repository paging, and the
/api/replays?mode=session response contract.
"""
import pathlib
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.repository import Repository, group_replays_by_session  # noqa: E402
from backend.config.service import derive_paths  # noqa: E402
import backend.main as m  # noqa: E402

HOUR = 3600


def rows(*timestamps: int) -> list[dict]:
    """Build list_replays-shaped rows (DESC, like the DB query returns)."""
    return [{"replay_id": f"r{t}", "timestamp": t, "song_name": f"s{t}"}
            for t in sorted(timestamps, reverse=True)]


class TestGroupReplaysBySession(unittest.TestCase):
    """Pure function: it depends only on timestamp gaps and never looks at calendar days."""

    def test_cross_midnight_is_one_session(self):
        """Playing across midnight (the extreme scenario the user reported): local
        2026-09-11 00:20 / 00:05 and 2026-09-10 23:50 are less than 1 hour apart →
        one session, and its title carries both dates."""
        import datetime
        t0 = int(datetime.datetime(2026, 9, 11, 0, 20).timestamp())
        t1 = int(datetime.datetime(2026, 9, 11, 0, 5).timestamp())
        t2 = int(datetime.datetime(2026, 9, 10, 23, 50).timestamp())
        groups = group_replays_by_session(rows(t0, t1, t2), HOUR)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["replays"]), 3)
        self.assertEqual(groups[0]["start"], t2)
        self.assertEqual(groups[0]["end"], t0)
        # crosses midnight → both ends carry a date (under by-day these 3 would be cut into 2 days)
        self.assertIn("2026-09-10 23:50", groups[0]["date"])
        self.assertIn("2026-09-11 00:20", groups[0]["date"])
        self.assertIn("–", groups[0]["date"])

    def test_same_day_two_blocks_are_two_sessions(self):
        """One block in the morning and one in the afternoon of the same day (more than 1 hour
        apart) → two sessions, whereas by day they would merge into one."""
        import datetime
        morning = int(datetime.datetime(2026, 9, 10, 9, 0).timestamp())
        afternoon = int(datetime.datetime(2026, 9, 10, 15, 0).timestamp())
        groups = group_replays_by_session(rows(afternoon, morning), HOUR)
        self.assertEqual(len(groups), 2)
        self.assertEqual([len(g["replays"]) for g in groups], [1, 1])
        # the newest comes first
        self.assertGreater(groups[0]["start"], groups[1]["start"])
        # a single-day session must still carry the year/month/day (the time alone does
        # not tell the player which day they played = 2026-09 user request),
        # and when both ends fall on the same day → the date is kept once
        self.assertIn("2026-09-10 15:00", groups[0]["date"])
        self.assertEqual(groups[0]["date"], "2026-09-10 15:00 – 15:00")
        self.assertEqual(groups[1]["date"], "2026-09-10 09:00 – 09:00")

    def test_gap_threshold_is_strict(self):
        """A gap exactly equal to the threshold still counts as one session; one second more → split."""
        base = 1_800_000_000
        self.assertEqual(len(group_replays_by_session(rows(base, base - HOUR), HOUR)), 1)
        self.assertEqual(len(group_replays_by_session(rows(base, base - HOUR - 1), HOUR)), 2)

    def test_song_length_gaps_stay_one_session(self):
        """A song lasts 3-4 minutes: plays 4 minutes apart must be one session (threshold 60 minutes)."""
        base = 1_800_000_000
        ts = [base - i * 240 for i in range(10)]      # 10 consecutive plays, 4 minutes apart
        groups = group_replays_by_session(rows(*ts), HOUR)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["replays"]), 10)

    def test_empty_and_single(self):
        self.assertEqual(group_replays_by_session([], HOUR), [])
        one = group_replays_by_session(rows(1_800_000_000), HOUR)
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0]["start"], one[0]["end"])

    def test_rows_without_timestamp_do_not_create_bogus_session(self):
        """Rows without a timestamp join the current session instead of opening a new one
        because ts=0 looks like a "huge gap"."""
        base = 1_800_000_000
        data = [{"replay_id": "a", "timestamp": base, "song_name": "a"},
                {"replay_id": "b", "timestamp": 0, "song_name": "b"},
                {"replay_id": "c", "timestamp": base - 300, "song_name": "c"}]
        groups = group_replays_by_session(data, HOUR)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["replays"]), 3)

    def test_all_untimed_rows_use_placeholder_label(self):
        data = [{"replay_id": "a", "timestamp": 0, "song_name": "a"},
                {"replay_id": "b", "timestamp": None, "song_name": "b"}]
        groups = group_replays_by_session(data, HOUR)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["date"], "未知时间")
        self.assertEqual((groups[0]["start"], groups[0]["end"]), (0, 0))

    def test_session_boundaries_are_gap_based_not_pair_based(self):
        """The rule uses the gap between "two neighbouring plays", not the head-to-tail span of
        the session: 4 plays 50 minutes apart (2.5 hours end to end) are still one session."""
        base = 1_800_000_000
        ts = [base - i * 3000 for i in range(4)]
        self.assertEqual(len(group_replays_by_session(rows(*ts), HOUR)), 1)


class SessionPagingTestCase(unittest.TestCase):
    """Base case class that owns a temporary database."""

    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.db = self.tmpdir / "sessions.sqlite"
        self.repo = Repository(self.db)
        self._orig_repo, self._orig_enrich, self._orig_cfg = m.repo, m.enrichment, m.cfg
        from backend.services.enrichment import EnrichmentService
        m.repo = self.repo
        m.enrichment = EnrichmentService(self.repo)
        m.cfg = SimpleNamespace(session_gap_minutes=60, data_source="scoresaber")

    def tearDown(self):
        m.repo, m.enrichment, m.cfg = self._orig_repo, self._orig_enrich, self._orig_cfg
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def seed(self, timestamps: list[int]) -> None:
        with self.repo._conn() as c:
            for t in timestamps:
                c.execute(
                    "INSERT INTO replays(replay_id, timestamp, song_name, difficulty,"
                    " status, analysis_status) VALUES(?,?,?,?,?,?)",
                    (f"r{t}", t, f"song{t}", "Expert", "analyzed", "analyzed"))

    def page(self, page=1, mode="session"):
        return m.api_replays(page=page, mode=mode, map_hash=None, days=None,
                             flat=0, limit=200)


class TestSessionPaging(SessionPagingTestCase):
    def test_pagination_unit_is_one_session(self):
        base = 1_800_000_000
        # 3 sessions: the newest has 3 plays, the middle 2, the oldest 1 (2 hours between sessions)
        self.seed([base, base - 300, base - 600,                 # session 1
                   base - 7200, base - 7500,                     # session 2
                   base - 20000])                                # session 3
        r1 = self.page(1)
        self.assertEqual(r1["mode"], "session")
        self.assertEqual(r1["total_days"], 3)
        self.assertEqual(r1["pages"], 3)
        self.assertEqual(len(r1["days"][0]["replays"]), 3)
        self.assertIn("start", r1["days"][0])
        self.assertEqual(self.page(2)["days"][0]["date"], self.page(2)["days"][0]["date"])
        self.assertEqual(len(self.page(2)["days"][0]["replays"]), 2)
        self.assertEqual(len(self.page(3)["days"][0]["replays"]), 1)

    def test_page_clamped_and_newest_first(self):
        base = 1_800_000_000
        self.seed([base, base - 20000])
        last = self.page(99)
        self.assertEqual(last["page"], 2)
        newest = self.page(1)["days"][0]
        oldest = self.page(2)["days"][0]
        self.assertGreater(newest["start"], oldest["start"])

    def test_empty_db(self):
        r = self.page(1)
        self.assertEqual(r["days"], [])
        self.assertEqual(r["total_days"], 0)
        self.assertEqual(r["pages"], 0)

    def test_gap_setting_changes_grouping(self):
        """The setting really drives grouping: threshold 60 minutes → 2 sessions; changed to
        5 minutes → 4 sessions."""
        base = 1_800_000_000
        self.seed([base, base - 1200, base - 2400, base - 3600])   # 20 minutes apart
        self.assertEqual(self.page(1)["total_days"], 1)
        m.cfg = SimpleNamespace(session_gap_minutes=15, data_source="scoresaber")
        self.assertEqual(self.page(1)["total_days"], 4)

    def test_day_mode_unchanged_by_new_mode(self):
        base = 1_800_000_000
        self.seed([base, base - 7200])
        day = self.page(1, mode="day")
        self.assertIn("days", day)
        self.assertNotIn("mode", day)          # the day response contract is unchanged

    def test_config_default_and_clamp(self):
        """Config read: default 60, out-of-range values are clamped to [1, 1440] (so a broken
        config.yaml cannot merge the whole library into one session)."""
        import yaml
        from backend.config import load_config as lc
        self.assertEqual(lc(self.tmpdir / "no_such_config.yaml").session_gap_minutes, 60)
        path = self.tmpdir / "config.yaml"
        for raw, expect in ((0, 1), (-5, 1), (99999, 1440), (45, 45)):
            path.write_text(yaml.safe_dump({"ui": {"session_gap_minutes": raw}}),
                            encoding="utf-8")
            self.assertEqual(lc(path).session_gap_minutes, expect, raw)

    def test_legacy_analysis_key_still_read(self):
        """Compatibility: the setting briefly lived under analysis.* (where it wrongly cleared
        the analysis cache); old configs must still be readable."""
        import yaml
        from backend.config import load_config as lc
        path = self.tmpdir / "legacy.yaml"
        path.write_text(yaml.safe_dump({"analysis": {"session_gap_minutes": 25}}),
                        encoding="utf-8")
        self.assertEqual(lc(path).session_gap_minutes, 25)
        # ui.* takes priority over the legacy key
        path.write_text(yaml.safe_dump({"ui": {"session_gap_minutes": 90},
                                        "analysis": {"session_gap_minutes": 25}}),
                        encoding="utf-8")
        self.assertEqual(lc(path).session_gap_minutes, 90)

    def test_legacy_key_is_migrated_out_of_the_file(self):
        """One-shot migration: after reading the legacy key it must be moved from analysis.*
        to ui.* and written to disk, otherwise the config file keeps two keys of the
        same name (two sources of truth)."""
        import yaml
        from backend.config import load_config as lc
        path = self.tmpdir / "migrate.yaml"
        path.write_text(yaml.safe_dump({
            "analysis": {"slope_group_notes": 40, "session_gap_minutes": 45},
            "player": {"star_palette": "personal"},
        }, allow_unicode=True), encoding="utf-8")
        self.assertEqual(lc(path).session_gap_minutes, 45)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertNotIn("session_gap_minutes", raw.get("analysis") or {})
        self.assertEqual((raw.get("ui") or {}).get("session_gap_minutes"), 45)
        # every other key is left untouched, character for character
        self.assertEqual((raw.get("analysis") or {}).get("slope_group_notes"), 40)
        self.assertEqual((raw.get("player") or {}).get("star_palette"), "personal")
        # reading it again must not migrate twice and must not raise
        self.assertEqual(lc(path).session_gap_minutes, 45)

    def test_setting_lives_outside_analysis_namespace(self):
        """Key regression: it must stay under ui.* — living under analysis.* made the
        "any analysis-parameter change clears the cache" rule wipe the analysis data of
        the whole library on every adjustment (actually hit in 2026-09)."""
        from backend.config.schema import get_schema
        item = next((i for i in get_schema()
                     if i["key"].endswith("session_gap_minutes")), None)
        self.assertIsNotNone(item)
        self.assertEqual(item["key"], "ui.session_gap_minutes")
        self.assertFalse(item["key"].startswith("analysis."))
        self.assertEqual(item["type"], "integer")
        self.assertEqual(item["default"], 60)
        self.assertFalse(item.get("hidden", False))

    def test_derived_paths_untouched(self):
        """Regression: this change must not affect path derivation."""
        d = derive_paths("D:/X")
        self.assertIn("replay_dir", d)
        self.assertEqual(d["custom_levels_dir"], "D:/X/Beat Saber_Data/CustomLevels")


if __name__ == "__main__":
    unittest.main()
