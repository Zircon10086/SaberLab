"""Map library regression test (2026-10).

The map-management module lists the local CustomLevels library: one row per map with name,
mapper, BPM, length, difficulties and NPS, paged in SQL because the library is thousands of
rows. This pins the two halves that can break silently:

* the query (`Repository.list_maps_page`): filter matches song name / mapper / author /
  BeatSaver key, the total counts the FILTERED set, paging returns disjoint pages, and the
  sort options order the way the UI labels them;
* the row shaping (`main._map_library_item`): JSON columns are parsed defensively (a broken or
  legacy row must not take the whole page down), per-difficulty NPS is attached, and the star
  flag only claims a rating when there is one.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import backend.main as m  # noqa: E402
from backend.db.repository import Repository  # noqa: E402
from backend.services.enrichment import EnrichmentService  # noqa: E402


def _map(map_hash: str, name: str, mapper: str = "", bpm: float = 120,
         length: float = 100, key: str = "", stars=None) -> dict:
    """A map row fixture.

    `stars` lands on the row's own column (what an older sync path writes); the rating the list
    actually shows comes from the enrichment index, so tests that care about it patch
    `m.enrichment.map_star_index` instead.
    """
    return {
        "map_hash": map_hash, "folder_name": f"{key} ({name})", "path": f"/x/{map_hash}",
        "song_name": name, "song_author": "Artist", "mapper": mapper,
        "bpm": bpm, "song_length": length, "version": "3.2.0",
        "difficulties": json.dumps([
            {"characteristic": "Standard", "difficulty": "Expert", "label": "",
             "filename": "Expert.dat", "njs": 16},
            {"characteristic": "Standard", "difficulty": "ExpertPlus", "label": "",
             "filename": "ExpertPlus.dat", "njs": 18},
        ]),
        "info_json": json.dumps({"song_sub_name": "sub", "environment": "DefaultEnvironment",
                                 "cover_filename": "cover.png", "song_filename": "song.egg",
                                 "note_counts": {"Standard|Expert": 500,
                                                 "Standard|ExpertPlus": 700}}),
        "hash_source": "computed", "last_scanned": "2026-10-01 00:00:00Z",
        "beatmap_key": key, "nps_json": json.dumps({"Standard|Expert": 4.5,
                                                   "Standard|ExpertPlus": 6.25}),
        "ranked_difficulty": "ExpertPlus", "stars": stars,
    }


class MapLibraryQueryTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.repo = Repository(self.tmpdir / "maps.sqlite")
        for row in (
            _map("H1", "Alpha Anthem", mapper="Mapper One", bpm=180, length=120, key="11111"),
            _map("H2", "Beta Ballad", mapper="Second Mapper", bpm=90, length=300, key="22222"),
            _map("H3", "Gamma Groove", mapper="Mapper One", bpm=240, length=60, key="33333"),
            _map("H4", "Camellia Tribute", mapper="Rise & SieR", bpm=200, length=200),
        ):
            self.repo.upsert_map(row)
        # Level ratings per platform (the two integrations rank the same map differently, and
        # each names its modes its own way: ScoreSaber writes "SoloStandard", BeatLeader
        # "Standard").
        for platform, map_hash, stars, mode in (("scoresaber", "H1", 9.0, "SoloStandard"),
                                                ("scoresaber", "H3", 4.0, "SoloStandard"),
                                                ("beatleader", "H2", 11.5, "Standard"),
                                                ("beatleader", "H1", 10.0, "Standard")):
            self.repo.upsert_ss_leaderboard({
                "leaderboard_id": f"lb-{platform}-{map_hash}", "map_hash": map_hash,
                "difficulty_rank": 7, "difficulty_name": "ExpertPlus",
                "game_mode": mode, "difficulty_raw": None,
                "song_name": map_hash, "level_author": "m",
                "stars": stars, "ranked": 1, "qualified": 0, "loved": None,
                "max_pp": None, "plays": None, "last_synced": "now",
            }, platform=platform)

    def test_all_maps_with_total(self):
        rows, total = self.repo.list_maps_page(limit=100)
        self.assertEqual(total, 4)
        self.assertEqual(len(rows), 4)

    def test_search_matches_song_mapper_author_and_key(self):
        for query, expected in (("gamma", ["Gamma Groove"]),
                                ("mapper one", ["Alpha Anthem", "Gamma Groove"]),
                                ("rise", ["Camellia Tribute"]),
                                ("22222", ["Beta Ballad"]),
                                ("Camellia", ["Camellia Tribute"])):
            with self.subTest(query=query):
                rows, total = self.repo.list_maps_page(query=query, limit=100)
                self.assertEqual(sorted(r["song_name"] for r in rows), sorted(expected))
                self.assertEqual(total, len(expected))     # the total follows the filter

    def test_paging_is_disjoint_and_complete(self):
        first, total = self.repo.list_maps_page(limit=2, offset=0)
        second, _ = self.repo.list_maps_page(limit=2, offset=2)
        names = [r["song_name"] for r in first] + [r["song_name"] for r in second]
        self.assertEqual(total, 4)
        self.assertEqual(len(names), 4)
        self.assertEqual(len(set(names)), 4)

    def test_sorting_text_and_number_keys(self):
        """name / author follow the direction; length and plays follow it too."""
        by_name = [r["song_name"] for r in self.repo.list_maps_page(sort="name", limit=10)[0]]
        self.assertEqual(by_name, sorted(by_name, key=str.lower))
        by_name_desc = [r["song_name"]
                        for r in self.repo.list_maps_page(sort="name", direction="desc",
                                                          limit=10)[0]]
        self.assertEqual(by_name_desc, sorted(by_name, key=str.lower, reverse=True))

        by_author = [r["song_author"] for r in self.repo.list_maps_page(sort="author",
                                                                       limit=10)[0]]
        self.assertEqual(by_author, sorted(by_author, key=str.lower))

        by_length = [r["song_length"] for r in self.repo.list_maps_page(sort="length",
                                                                        direction="desc",
                                                                        limit=10)[0]]
        self.assertEqual(by_length, sorted(by_length, reverse=True))
        by_length_asc = [r["song_length"] for r in self.repo.list_maps_page(sort="length",
                                                                            limit=10)[0]]
        self.assertEqual(by_length_asc, sorted(by_length_asc))

        by_plays = [r["plays"] for r in self.repo.list_maps_page(sort="plays", limit=10)[0]]
        self.assertEqual(by_plays, sorted(by_plays))

    def test_star_sort_follows_the_active_platform(self):
        """One star key: it sorts by the rating of whichever data source is active."""
        ss = [(r["song_name"], r["stars_src"])
              for r in self.repo.list_maps_page(sort="stars", direction="asc", limit=10,
                                                platform="scoresaber")[0]]
        self.assertEqual(ss[:2], [("Alpha Anthem", 9.0), ("Gamma Groove", 4.0)])
        bl = [(r["song_name"], r["stars_src"])
              for r in self.repo.list_maps_page(sort="stars", direction="asc", limit=10,
                                                platform="beatleader")[0]]
        self.assertEqual(bl[:2], [("Beta Ballad", 11.5), ("Alpha Anthem", 10.0)])
        # switching the data source must not leave the first platform's numbers behind
        ss_again = [r["song_name"] for r in self.repo.list_maps_page(sort="stars", direction="asc",
                                                                    limit=10,
                                                                    platform="scoresaber")[0]]
        self.assertEqual(ss_again[:2], ["Alpha Anthem", "Gamma Groove"])

    def test_star_sort_direction_flips_the_rating(self):
        """正序 = highest first, 倒序 = lowest first (the user reported a dead toggle).

        Also pins that the name tie-break follows the direction, and that an unrated map is NULL
        rather than 0.0 - coercing it to 0 sorted "no rating" as the lowest rating and put those
        rows FIRST in one direction.
        """
        asc = [(r["song_name"], r["stars_src"])
               for r in self.repo.list_maps_page(sort="stars", direction="asc", limit=10,
                                                 platform="scoresaber")[0]]
        desc = [(r["song_name"], r["stars_src"])
                for r in self.repo.list_maps_page(sort="stars", direction="desc", limit=10,
                                                  platform="scoresaber")[0]]
        self.assertNotEqual(asc, desc)                      # the toggle must do something
        self.assertEqual(asc[:3], [("Alpha Anthem", 9.0), ("Gamma Groove", 4.0),
                                   ("Beta Ballad", None)])
        self.assertEqual(desc[:2], [("Gamma Groove", 4.0), ("Alpha Anthem", 9.0)])
        self.assertIsNone(asc[-1][1])                       # the unrated map stays last
        self.assertIsNone(desc[-1][1])

    def test_every_sort_key_reacts_to_the_direction(self):
        """No key may ignore the direction button.

        The star sort used to sort the rating in one direction only, so the button looked broken
        for that key while working for the others.
        """
        for key in ("name", "author", "length", "stars", "added", "plays"):
            with self.subTest(sort=key):
                asc = [r["map_hash"] for r in self.repo.list_maps_page(
                    sort=key, direction="asc", limit=10, platform="scoresaber")[0]]
                desc = [r["map_hash"] for r in self.repo.list_maps_page(
                    sort=key, direction="desc", limit=10, platform="scoresaber")[0]]
                self.assertNotEqual(asc, desc, f"{key}: the direction changed nothing")

    def test_unrated_maps_go_last_in_both_directions(self):
        """The user's rule: no stars = the end of the list, whichever direction is active.

        Only the rated part flips with the direction; inside the unrated group the name order
        follows the direction as well, so that group stays readable instead of scrambled.
        """
        for direction, expected_rated, expected_unrated in (
                ("asc", ["Alpha Anthem", "Gamma Groove"], ["Beta Ballad", "Camellia Tribute"]),
                ("desc", ["Gamma Groove", "Alpha Anthem"], ["Camellia Tribute", "Beta Ballad"])):
            with self.subTest(direction=direction):
                rows = self.repo.list_maps_page(sort="stars", direction=direction, limit=10,
                                                platform="scoresaber")[0]
                rated = [r["song_name"] for r in rows if r["stars_src"]]
                unrated = [r["song_name"] for r in rows if not r["stars_src"]]
                self.assertEqual(rated, expected_rated)
                self.assertEqual(unrated, expected_unrated)
                self.assertEqual([r["song_name"] for r in rows][-2:], unrated)

    def test_unknown_sort_and_direction_fall_back(self):
        rows, _ = self.repo.list_maps_page(sort="../../etc/passwd", direction="sideways",
                                           limit=10)
        names = [r["song_name"] for r in rows]
        self.assertEqual(names, sorted(names, key=str.lower))


class MapLibraryItemTest(unittest.TestCase):
    def test_item_carries_difficulty_details(self):
        # the rating comes from the enrichment index (the star sort's source), so the test
        # provides it there rather than on the row
        with mock.patch.object(m.enrichment, "map_star_index",
                               return_value={"H1": {"stars": 5.5, "ranked": True}}):
            item = m._map_library_item(_map("H1", "Alpha", mapper="Somebody", key="99999"))
        self.assertEqual(item["song_name"], "Alpha")
        self.assertEqual(item["mapper"], "Somebody")
        self.assertEqual(item["beatmap_key"], "99999")
        self.assertEqual(item["bpm"], 120)
        self.assertEqual(item["song_length"], 100)
        self.assertTrue(item["rated"])
        self.assertEqual(item["stars"], 5.5)
        self.assertEqual(item["ranked_difficulty"], "ExpertPlus")
        self.assertEqual(item["nps_max"], 6.25)
        self.assertEqual(item["nps_min"], 4.5)
        self.assertEqual([d["difficulty"] for d in item["difficulties"]],
                         ["Expert", "ExpertPlus"])
        self.assertEqual(item["difficulties"][1]["nps"], 6.25)
        self.assertEqual(item["difficulties"][1]["njs"], 18)
        # no note totals: nothing in the app records them, and a value derived from NPS would
        # be a different metric wearing that label
        self.assertNotIn("note_count", item)

    def test_unrated_map_is_not_claimed_as_rated(self):
        item = m._map_library_item(_map("H2", "Beta", stars=0))
        self.assertFalse(item["rated"])
        self.assertIsNone(item["stars"])

    def test_star_sort_agrees_with_the_shown_star(self):
        """The value a row is SORTED by must be the value it SHOWS (two user reports).

        Builds a small library through the real endpoint (BeatLeader as the active source: it is
        the integration that also rates unranked levels): one map rated 9★ and ranked, one rated
        11★ but only "unranked", one rated on a non-standard mode only, one unrated. The endpoint
        must return all four (a bare MAX() in the select list without GROUP BY collapses the whole
        result to ONE row in SQLite), in rating order with the unrated map last, and every shown
        star must equal the rating the sort used.
        """
        tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        repo = Repository(tmpdir / "lib.sqlite")
        for map_hash, name, stars in (("R1", "Ranked Nine", 9.0),
                                      ("R2", "Unranked Eleven", 11.0),
                                      ("P1", "Only Lawless", 8.0),
                                      ("U1", "No Rating", None)):
            repo.upsert_map({**_map(map_hash, name), "stars": stars})
        for map_hash, mode, stars, ranked in (("R1", "Standard", 9.0, 1),
                                              ("R2", "Standard", 11.0, 0),
                                              ("P1", "Lawless", 8.0, 0)):
            repo.upsert_ss_leaderboard({
                "leaderboard_id": f"lb-{map_hash}", "map_hash": map_hash,
                "difficulty_rank": 5, "difficulty_name": "ExpertPlus", "game_mode": mode,
                "difficulty_raw": None, "song_name": map_hash, "level_author": "m",
                "stars": stars, "ranked": ranked, "qualified": 0, "loved": None,
                "max_pp": None, "plays": None, "last_synced": "now",
            }, platform="beatleader")
        service = EnrichmentService(repo)
        from fastapi.testclient import TestClient
        client = TestClient(m.app, base_url="http://127.0.0.1:6980")
        with mock.patch.object(m, "repo", repo), \
                mock.patch.object(m, "enrichment", service), \
                mock.patch.object(m, "_active_platform", lambda: "beatleader"):
            body = client.get("/api/maps/library?per_page=50&sort=stars&dir=asc",
                              headers={"origin": "http://127.0.0.1:6980"}).json()
            rows, _ = repo.list_maps_page(sort="stars", direction="asc", limit=50,
                                          platform="beatleader")
        self.assertEqual(len(body["items"]), 4)              # the list must not collapse
        shown = [it["stars"] for it in body["items"]]
        # 倒序在末尾：非标准模式的评级不算数，所以 "Only Lawless" 与未评级一组
        self.assertEqual(shown, [11.0, 9.0, None, None])
        self.assertEqual(shown, [r["stars_src"] for r in rows])   # sort key == shown value
        by_name = {it["song_name"]: it for it in body["items"]}
        self.assertTrue(by_name["Ranked Nine"]["ranked"])
        self.assertFalse(by_name["Unranked Eleven"]["ranked"])    # the yellow-marked row
        self.assertFalse(by_name["Only Lawless"]["rated"])        # a Lawless rating is not shown
        self.assertFalse(by_name["No Rating"]["rated"])

    def test_unranked_rating_is_flagged(self):
        """BeatLeader rates levels that are not formally ranked: that flag must reach the list.

        The frontend paints those with the yellow background the replay list uses, so a map
        whose stars are not ranked must arrive as rated=True + ranked=False (and never as
        ranked=True by accident).
        """
        with mock.patch.object(m.enrichment, "map_star_index",
                               return_value={"H1": {"stars": 7.25, "ranked": False}}):
            item = m._map_library_item(_map("H1", "Alpha"))
        self.assertTrue(item["rated"])
        self.assertEqual(item["stars"], 7.25)
        self.assertFalse(item["ranked"])

        with mock.patch.object(m.enrichment, "map_star_index",
                               return_value={"H1": {"stars": 7.25, "ranked": True}}):
            item = m._map_library_item(_map("H1", "Alpha"))
        self.assertTrue(item["ranked"])

    def test_rating_column_is_not_a_fallback(self):
        """A rating stored on the map row must NOT be shown when the index has none.

        The maps.stars column is written by an older sync path and can hold a rating of a mode the
        index deliberately ignores (measured: Cyaegha had 8.0 stored while the index knows the
        Standard 12.04). Showing it would make the star cell disagree with the star sort.
        """
        with mock.patch.object(m.enrichment, "map_star_index", return_value={}):
            item = m._map_library_item(_map("H1", "Alpha", stars=4.5))
        self.assertFalse(item["rated"])
        self.assertIsNone(item["stars"])


class MapStarIndexTest(unittest.TestCase):
    """The star index keeps the star sort and the rendered star in agreement with the list."""

    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.repo = Repository(self.tmpdir / "stars.sqlite")
        self.svc = EnrichmentService(self.repo)

    def _lb(self, platform, map_hash, difficulty, mode, stars, ranked):
        self.repo.upsert_ss_leaderboard({
            "leaderboard_id": f"{platform}-{map_hash}-{difficulty}-{mode}",
            "map_hash": map_hash, "difficulty_rank": 5, "difficulty_name": difficulty,
            "game_mode": mode, "difficulty_raw": None, "song_name": map_hash,
            "level_author": "m", "stars": stars, "ranked": ranked, "qualified": 0,
            "loved": None, "max_pp": None, "plays": None, "last_synced": "now",
        }, platform=platform)

    def test_scoresaber_star_sort_uses_its_solo_standard_mode(self):
        """ScoreSaber stores "SoloStandard"; a sort filtering on "Standard" finds nothing.

        That mistake made every ScoreSaber map fall back to the (usually empty) stored column,
        so the star sort ordered nothing. The SQL filter and the index rule must agree on how
        each platform names its modes.
        """
        self.repo.upsert_map({**_map("S1", "Standard Rated"), "stars": None})
        self.repo.upsert_map({**_map("S2", "Lawless Only"), "stars": None})
        self._lb("scoresaber", "S1", "ExpertPlus", "SoloStandard", 7.5, 1)
        self._lb("scoresaber", "S2", "ExpertPlus", "SoloLawless", 9.9, 0)
        rows, _ = self.repo.list_maps_page(sort="stars", direction="asc", limit=10,
                                           platform="scoresaber")
        self.assertEqual([r["song_name"] for r in rows], ["Standard Rated", "Lawless Only"])
        self.assertEqual([r["stars_src"] for r in rows], [7.5, None])
        index = self.svc.map_star_index("scoresaber")
        self.assertEqual(index["S1"], {"stars": 7.5, "ranked": True})
        self.assertNotIn("S2", index)      # a non-Standard ScoreSaber rating is not shown

    def test_beatleader_ignores_a_non_standard_only_rating(self):
        """A map rated only on a non-standard mode shows no rating (BeatLeader has those too)."""
        self.repo.upsert_map({**_map("B9", "BeatLeader Lawless only"), "stars": 18.2})
        self._lb("beatleader", "B9", "ExpertPlus", "Lawless", 18.2, 0)
        rows, _ = self.repo.list_maps_page(sort="stars", direction="asc", limit=10,
                                           platform="beatleader")
        self.assertIsNone(rows[0]["stars_src"])
        self.assertNotIn("B9", self.svc.map_star_index("beatleader"))

    def test_rated_map_with_a_ranked_sibling_is_ranked(self):
        """A map is "ranked" as soon as one of its rated leaderboards is formally ranked.

        BeatLeader publishes ratings for unranked levels too; the map's mark must not become
        "unranked" merely because its hardest chart happens to be one of those.
        """
        self._lb("beatleader", "M1", "ExpertPlus", "Standard", 12.0, 0)
        self._lb("beatleader", "M1", "Expert", "Standard", 6.0, 1)
        index = self.svc.map_star_index("beatleader")
        self.assertEqual(index["M1"], {"stars": 12.0, "ranked": True})

    def test_unranked_rating_keeps_the_flag(self):
        self._lb("beatleader", "M2", "ExpertPlus", "Standard", 9.5, 0)
        index = self.svc.map_star_index("beatleader")
        self.assertEqual(index["M2"], {"stars": 9.5, "ranked": False})

    def test_zero_stars_mean_no_rating(self):
        """ScoreSaber writes stars=0 for unranked leaderboards: that is not a rating."""
        self._lb("scoresaber", "M3", "Expert", "Standard", 0, 0)
        index = self.svc.map_star_index("scoresaber")
        self.assertNotIn("M3", index)     # nothing to show, so the row keeps no rating

    def test_beatleader_counts_its_standard_mode_only(self):
        """Both integrations store one rating per mode and the numbers differ.

        Measured on the real library: Cyaegha's ExpertPlus is 12.04 in Standard mode but 12.29 in
        Lawless. Counting every mode made a map's headline star disagree with its own Standard
        difficulty, so only Standard counts (and a map rated ONLY on a non-standard mode shows no
        rating, like ScoreSaber's Lawless rows).
        """
        self.repo.upsert_map({**_map("B1", "BeatLeader Standard"), "stars": None})
        self.repo.upsert_map({**_map("B2", "BeatLeader Lawless only"), "stars": None})
        self._lb("beatleader", "B1", "ExpertPlus", "Standard", 12.0, 0)
        self._lb("beatleader", "B1", "ExpertPlus", "Lawless", 12.3, 0)
        self._lb("beatleader", "B2", "ExpertPlus", "Lawless", 18.2, 0)
        rows, _ = self.repo.list_maps_page(sort="stars", direction="asc", limit=10,
                                           platform="beatleader")
        by_name = {r["song_name"]: r["stars_src"] for r in rows}
        self.assertEqual(by_name["BeatLeader Standard"], 12.0)
        self.assertIsNone(by_name["BeatLeader Lawless only"])
        index = self.svc.map_star_index("beatleader")
        self.assertEqual(index["B1"], {"stars": 12.0, "ranked": False})
        self.assertNotIn("B2", index)

    def test_index_is_per_platform(self):
        self._lb("scoresaber", "M4", "ExpertPlus", "SoloStandard", 7.0, 1)
        self._lb("beatleader", "M4", "ExpertPlus", "Standard", 11.0, 0)
        self.assertEqual(self.svc.map_star_index("scoresaber")["M4"]["stars"], 7.0)
        self.assertEqual(self.svc.map_star_index("beatleader")["M4"]["stars"], 11.0)

    def test_broken_json_columns_do_not_break_the_row(self):
        row = _map("H3", "Gamma")
        row["info_json"] = "{not json"
        row["difficulties"] = "[]"
        row["nps_json"] = ""
        item = m._map_library_item(row)
        self.assertEqual(item["song_name"], "Gamma")
        self.assertEqual(item["difficulties"], [])
        self.assertIsNone(item["nps_max"])


class MapLibraryEndpointTest(unittest.TestCase):
    """The endpoint shape the frontend depends on (repository mocked: read-only contract)."""

    def setUp(self):
        from fastapi.testclient import TestClient
        self.client = TestClient(m.app, base_url="http://127.0.0.1:6980")
        self.headers = {"origin": "http://127.0.0.1:6980"}

    def _get(self, query="", page=1, per_page=50, sort="name", direction="asc"):
        with mock.patch.object(m.repo, "list_maps_page",
                               return_value=([_map("H1", "Alpha")], 1)), \
                mock.patch.object(m.enrichment, "map_star_index",
                                  return_value={"H1": {"stars": 6.0, "ranked": True}}):
            return self.client.get(
                f"/api/maps/library?q={query}&page={page}&per_page={per_page}"
                f"&sort={sort}&dir={direction}",
                headers=self.headers).json()

    def test_response_shape(self):
        body = self._get()
        self.assertEqual((body["total"], body["page"], body["per_page"]), (1, 1, 50))
        self.assertEqual(len(body["items"]), 1)
        self.assertTrue(body["rated_on_page"])
        self.assertEqual(body["sort"], "name")
        self.assertEqual(body["dir"], "asc")
        item = body["items"][0]
        for key in ("map_hash", "song_name", "mapper", "bpm", "song_length", "beatmap_key",
                    "difficulties", "nps_max", "rated", "stars", "version", "folder_name",
                    "added_at", "plays", "platform"):
            self.assertIn(key, item)

    def test_sort_and_direction_are_reported_back(self):
        body = self._get(sort="stars", direction="desc")
        self.assertEqual((body["sort"], body["dir"]), ("stars", "desc"))

    def test_rated_on_page_is_false_without_a_rating(self):
        with mock.patch.object(m.repo, "list_maps_page",
                               return_value=([_map("H2", "Beta")], 1)):
            body = self.client.get("/api/maps/library", headers=self.headers).json()
        self.assertFalse(body["rated_on_page"])

    def test_page_per_page_sort_and_direction_reach_the_query(self):
        seen = {}

        def fake(query="", offset=0, limit=50, sort="name", direction="asc", platform=""):
            seen.update({"query": query, "offset": offset, "limit": limit, "sort": sort,
                         "direction": direction, "platform": platform})
            return [], 0

        with mock.patch.object(m.repo, "list_maps_page", side_effect=fake):
            body = self.client.get(
                "/api/maps/library?q=alpha&page=3&per_page=20&sort=stars&dir=desc",
                headers=self.headers).json()
        self.assertEqual(seen["query"], "alpha")
        self.assertEqual((seen["offset"], seen["limit"]), (40, 20))
        self.assertEqual((seen["sort"], seen["direction"]), ("stars", "desc"))
        self.assertIn(seen["platform"], ("scoresaber", "beatleader"))
        self.assertEqual(body["items"], [])
        self.assertFalse(body["rated_on_page"])


if __name__ == "__main__":
    unittest.main()
