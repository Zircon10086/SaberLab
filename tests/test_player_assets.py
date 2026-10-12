"""Player asset cache (avatar + country flag) and the sidebar player card (2026-09).

Covers the three moving parts of the feature:
1. asset source extraction from both provider snapshots (ScoreSaber keeps the
   raw API payload, BeatLeader normalizes its profile -> avatarUrl; the flag is
   derived from the country code for both)
2. the local cache (download / sniff / read / failure handling) - a failed
   download must never destroy a previously cached image
3. the /api/player/card payload derivation and the two image endpoints
   (no new table, no network access on the read path)
"""
import dataclasses
import pathlib
import sys
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import load_config  # noqa: E402
from backend.services import player_assets as assets  # noqa: E402
import backend.main as m  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"payload" * 4
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 16


def make_cfg(tmpdir: pathlib.Path, proxy: str = "", data_source: str = "scoresaber"):
    """A real Config with a redirected data dir.

    The asset layer itself only needs `data_dir`, but the sync path and the card
    endpoint also read provider/platform fields, so a real Config copy keeps the
    whole flow exercised while nothing is written outside `_tmp`. `data_source`
    is pinned so a developer whose own config selects BeatLeader cannot change
    what these tests assert.
    """
    return dataclasses.replace(load_config(), data_dir=tmpdir, proxy=proxy,
                               data_source=data_source)


class FakeResponse:
    def __init__(self, status=200, body=JPEG):
        self.status = status
        self._body = body

    def read(self, _n=None):
        return self._body


class FakeConn:
    """Stands in for http.client.HTTPSConnection (records the request)."""

    def __init__(self, status=200, body=JPEG, raise_exc=None, log=None):
        self.status, self.body, self.raise_exc = status, body, raise_exc
        self.log = log if log is not None else []
        self.closed = False
        self.tunnel = None

    def request(self, method, target, headers=None):
        self.log.append((method, target))

    def getresponse(self):
        if self.raise_exc:
            raise self.raise_exc
        return FakeResponse(self.status, self.body)

    def set_tunnel(self, host, port):
        self.tunnel = (host, port)

    def close(self):
        self.closed = True


class AssetTestCase(unittest.TestCase):
    PID = "76561190000000000"
    PROFILE = {"name": "𝒁𝒊𝑹𝑪𝑶𝑵", "country": "CN", "rank": 6274, "countryRank": 118}

    def setUp(self):
        self.tmp = pathlib.Path(PROJECT_ROOT / "_tmp" / f"assets_{id(self)}")
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.cfg = make_cfg(self.tmp)

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            try:
                p.unlink() if p.is_file() else p.rmdir()
            except OSError:
                pass
        try:
            self.tmp.rmdir()
        except OSError:
            pass

    def _download(self, conn=None, url="https://cdn.example.com/a.jpg",
                  kind="avatar_scoresaber", key=None, cfg=None):
        # key=None means "use the default"; an explicit "" must stay "" so the
        # empty-key guard can be tested
        with mock.patch.object(assets.http.client, "HTTPSConnection",
                               return_value=conn or FakeConn()):
            return assets.download_asset(cfg or self.cfg, kind,
                                         self.PID if key is None else key, url)

    def _sync_with_assets(self, profile=None):
        """Run the real cloud sync with the provider HTTP layer mocked.

        Exercises the full chain: fetch profile -> persist snapshot -> cache
        avatar + flag, which the card endpoint then reads back."""
        profile = profile or {**self.PROFILE,
                              "profilePicture": "https://cdn.scoresaber.com/avatars/x.jpg"}
        with mock.patch.object(m, "cfg", self.cfg), \
             mock.patch.object(m, "_scoresaber_id", return_value=self.PID), \
             mock.patch.object(m, "_active_platform", return_value="scoresaber"), \
             mock.patch.object(m.scoresaber, "fetch_profile", return_value=profile), \
             mock.patch.object(m.scoresaber, "fetch_scores", return_value=[]), \
             mock.patch.object(m.repo, "save_player_cache"), \
             mock.patch.object(m.skill_tracks, "skill_palette_payload", return_value=None), \
             mock.patch.object(assets.http.client, "HTTPSConnection",
                               return_value=FakeConn()):
            return m._cloud_page_refresh("scoresaber")

    def _card(self, profile=None):
        with mock.patch.object(m, "cfg", self.cfg), \
             mock.patch.object(m, "_scoresaber_id", return_value=self.PID), \
             mock.patch.object(m, "_active_platform", return_value="scoresaber"), \
             mock.patch.object(m.repo, "get_player_cache",
                               return_value={"profile": profile or self.PROFILE}):
            return m.api_player_card()["profile"]

    def _card_image(self, endpoint):
        with mock.patch.object(m, "cfg", self.cfg), \
             mock.patch.object(m, "_scoresaber_id", return_value=self.PID), \
             mock.patch.object(m, "_active_platform", return_value="scoresaber"), \
             mock.patch.object(m.repo, "get_player_cache",
                               return_value={"profile": self.PROFILE}):
            return endpoint()


class TestAssetSources(AssetTestCase):
    def test_beatleader_avatar_key(self):
        self.assertEqual(
            assets.avatar_url("beatleader",
                              {"avatarUrl": "https://a.steamstatic.com/x_full.jpg"}),
            "https://a.steamstatic.com/x_full.jpg")

    def test_scoresaber_avatar_key(self):
        self.assertEqual(
            assets.avatar_url("scoresaber",
                              {"profilePicture": "https://cdn.scoresaber.com/avatars/1.jpg"}),
            "https://cdn.scoresaber.com/avatars/1.jpg")

    def test_missing_or_invalid_profile(self):
        for platform in ("scoresaber", "beatleader"):
            self.assertEqual(assets.avatar_url(platform, {}), "")
            self.assertEqual(assets.avatar_url(platform, None), "")
            self.assertEqual(assets.avatar_url(platform, {"name": "x"}), "")

    def test_flag_url_from_country_code(self):
        self.assertEqual(assets.flag_url("CN"), "https://flagcdn.com/w40/cn.png")
        self.assertEqual(assets.flag_url(" cn "), "https://flagcdn.com/w40/cn.png")
        for bad in ("", "C", "CHN", "C1", None, "  "):
            self.assertEqual(assets.flag_url(bad), "", repr(bad))

    def test_beatleader_fetch_profile_exposes_avatar(self):
        """The provider normalizes its payload: BL `avatar` -> `avatarUrl`
        (regression guard: the key was dropped before 2026-09)."""
        import backend.beatleader as beatleader
        payload = {"name": "TestPlayer", "avatar": "https://a.steamstatic.com/x_full.jpg",
                   "country": "CN", "rank": 1, "countryRank": 2, "pp": 3.0,
                   "scoreStats": {"averageRankedAccuracy": 0.8}}
        with mock.patch.object(beatleader, "_get", return_value=payload):
            p = beatleader.fetch_profile(self.cfg, self.PID)
        self.assertEqual(p["avatarUrl"], "https://a.steamstatic.com/x_full.jpg")

    def test_scoresaber_fetch_profile_keeps_profile_picture(self):
        """ScoreSaber's fetch_profile returns the raw payload, so
        `profilePicture` must survive the round trip."""
        import backend.scoresaber as ss
        payload = {"id": "1", "name": "TestPlayer",
                   "profilePicture": "https://cdn.scoresaber.com/avatars/1.jpg"}
        with mock.patch.object(ss, "_get", return_value=payload):
            p = ss.fetch_profile(self.cfg, "1")
        self.assertEqual(p["profilePicture"], "https://cdn.scoresaber.com/avatars/1.jpg")


class TestMimeSniffing(AssetTestCase):
    def test_recognized_formats(self):
        self.assertEqual(assets.sniff_mime(JPEG), "image/jpeg")
        self.assertEqual(assets.sniff_mime(PNG), "image/png")
        self.assertEqual(assets.sniff_mime(b"GIF89a...."), "image/gif")
        self.assertEqual(assets.sniff_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "image/webp")

    def test_non_image_rejected(self):
        self.assertIsNone(assets.sniff_mime(b"<!DOCTYPE html><html>"))
        self.assertIsNone(assets.sniff_mime(b""))
        self.assertIsNone(assets.sniff_mime(b"<html>"))


class TestAssetDownload(AssetTestCase):
    def test_successful_download_stores_file(self):
        log = []
        path = self._download(FakeConn(log=log))
        self.assertTrue(path)
        stored = pathlib.Path(path)
        self.assertTrue(stored.is_file())
        self.assertEqual(stored.read_bytes(), JPEG)
        self.assertEqual(stored.parent.name, "assets")
        self.assertEqual(stored.name, f"avatar_scoresaber_{self.PID}")
        self.assertEqual(log, [("GET", "/a.jpg")])
        self.assertEqual(list(self.tmp.rglob("*.tmp")), [])   # no leftover temp

    def test_query_string_is_forwarded(self):
        log = []
        self._download(FakeConn(log=log), url="https://cdn.example.com/a.jpg?x=1&y=2")
        self.assertEqual(log[0][1], "/a.jpg?x=1&y=2")

    def test_non_200_keeps_existing_cache(self):
        first = self._download()
        self.assertTrue(first)
        before = pathlib.Path(first).read_bytes()
        self.assertIsNone(self._download(FakeConn(status=404)))
        self.assertEqual(pathlib.Path(first).read_bytes(), before)

    def test_html_error_page_is_not_cached(self):
        self.assertIsNone(self._download(FakeConn(body=b"<!DOCTYPE html>nope")))
        self.assertFalse((self.tmp / "assets").exists())

    def test_oversized_payload_rejected(self):
        self.assertIsNone(
            self._download(FakeConn(body=JPEG + b"x" * (4 * 1024 * 1024))))
        self.assertFalse((self.tmp / "assets").exists())

    def test_flag_size_limit_is_tighter(self):
        """Flags are ~1KB; a 600KB "flag" is not a flag."""
        big = PNG + b"x" * (600 * 1024)
        with mock.patch.object(assets.http.client, "HTTPSConnection",
                               return_value=FakeConn(body=big)):
            self.assertIsNone(assets.download_flag(self.cfg, "CN"))
        self.assertFalse((self.tmp / "assets").exists())

    def test_network_failure_returns_none(self):
        import http.client as http_client
        self.assertIsNone(self._download(FakeConn(raise_exc=OSError("connection reset"))))
        self.assertIsNone(self._download(FakeConn(raise_exc=http_client.HTTPException("boom"))))

    def test_invalid_urls_rejected(self):
        for url in ("", "   ", "http://cdn.example.com/a.jpg", "not-a-url",
                    "/a.jpg", "file:///etc/passwd"):
            self.assertIsNone(self._download(url=url), url)

    def test_missing_key_rejected(self):
        self.assertIsNone(self._download(key=""))
        self.assertIsNone(self._download(key="   "))

    def test_proxy_config_sets_tunnel(self):
        cfg = make_cfg(self.tmp, proxy="127.0.0.1:7890")
        conn = FakeConn()
        self._download(conn, cfg=cfg)
        self.assertEqual(conn.tunnel, ("127.0.0.1", 7890))

    def test_asset_path_is_deterministic_and_sanitized(self):
        self.assertEqual(assets.asset_path(self.cfg, "flag", "CN").name, "flag_CN")
        self.assertEqual(
            assets.asset_path(self.cfg, "avatar_beatleader", self.PID).name,
            f"avatar_beatleader_{self.PID}")
        # path traversal / separators cannot escape the assets directory
        weird = assets.asset_path(self.cfg, "flag", "../../etc/passwd")
        self.assertEqual(weird.parent.name, "assets")
        self.assertNotIn("..", weird.name)
        self.assertNotIn("/", weird.name)

    def test_redownload_overwrites_existing_file(self):
        first = self._download()
        second = self._download(FakeConn(body=PNG))
        self.assertEqual(first, second)
        self.assertEqual(pathlib.Path(first).read_bytes(), PNG)


class TestAssetRead(AssetTestCase):
    def test_read_reports_bytes_mime_and_mtime(self):
        path = self._download()
        got = assets.read_asset(self.cfg, "avatar_scoresaber", self.PID)
        self.assertIsNotNone(got)
        data, mime, mtime = got
        self.assertEqual(data, JPEG)
        self.assertEqual(mime, "image/jpeg")
        self.assertGreater(mtime, 0)
        self.assertTrue(path)

    def test_read_missing_returns_none(self):
        self.assertIsNone(assets.read_avatar(self.cfg, "scoresaber", "nobody"))
        self.assertIsNone(assets.read_avatar(self.cfg, "scoresaber", ""))
        self.assertIsNone(assets.read_flag(self.cfg, ""))
        self.assertIsNone(assets.read_flag(self.cfg, "ZZ"))

    def test_corrupt_cache_is_removed(self):
        path = assets.asset_path(self.cfg, "avatar_scoresaber", self.PID)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"<html>not an image")
        self.assertIsNone(assets.read_avatar(self.cfg, "scoresaber", self.PID))
        self.assertFalse(path.exists())


class TestCardPayload(AssetTestCase):
    def test_fields_and_image_urls(self):
        """End to end through the real path: the cloud sync (provider HTTP
        mocked) caches both images, then the card endpoint serves name/ranks +
        the versioned image URLs."""
        self._sync_with_assets()
        card = self._card()
        self.assertEqual(card["name"], "𝒁𝒊𝑹𝑪𝑶𝑵")
        self.assertEqual(card["country"], "CN")
        self.assertEqual(card["rank"], 6274)
        self.assertEqual(card["countryRank"], 118)
        self.assertEqual(card["platform"], "scoresaber")
        for key, prefix in (("avatar_url", "/api/player/avatar?v="),
                            ("flag_url", "/api/player/flag?v=")):
            self.assertTrue(card[key].startswith(prefix), (key, card[key]))
            self.assertGreater(int(card[key].split("=")[1]), 0)

    def test_both_image_endpoints_serve_cached_bytes(self):
        self._sync_with_assets()
        for endpoint in (m.api_player_avatar, m.api_player_flag):
            resp = self._card_image(endpoint)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.body, JPEG)
            self.assertIn("max-age", resp.headers["cache-control"])
        self.assertEqual(self._card_image(m.api_player_avatar).media_type, "image/jpeg")

    def test_404_when_not_cached(self):
        from fastapi import HTTPException
        for endpoint in (m.api_player_avatar, m.api_player_flag):
            with self.assertRaises(HTTPException) as ctx:
                self._card_image(endpoint)
            self.assertEqual(ctx.exception.status_code, 404)

    def test_no_cache_snapshot_returns_none_profile(self):
        with mock.patch.object(m, "cfg", self.cfg), \
             mock.patch.object(m, "_scoresaber_id", return_value=self.PID), \
             mock.patch.object(m, "_active_platform", return_value="scoresaber"), \
             mock.patch.object(m.repo, "get_player_cache", return_value=None):
            self.assertIsNone(m.api_player_card()["profile"])

    def test_no_player_id_returns_none_profile(self):
        with mock.patch.object(m, "cfg", self.cfg), \
             mock.patch.object(m, "_scoresaber_id", return_value=""), \
             mock.patch.object(m, "_active_platform", return_value="scoresaber"):
            self.assertIsNone(m.api_player_card()["profile"])

    def test_image_urls_empty_when_not_cached(self):
        card = assets.card_payload(self.cfg, "scoresaber", self.PID, self.PROFILE)
        self.assertEqual(card["avatar_url"], "")
        self.assertEqual(card["flag_url"], "")

    def test_avatar_is_platform_scoped(self):
        """The cached avatar belongs to the platform that was synced: switching
        the data source must not show the other platform's avatar. The flag is
        platform-independent (same ISO country code)."""
        with mock.patch.object(assets.http.client, "HTTPSConnection",
                               return_value=FakeConn()):
            assets.download_avatar(self.cfg, "beatleader", self.PID,
                                   "https://cdn.example.com/a.jpg")
            assets.download_flag(self.cfg, "CN")
        self.assertEqual(
            assets.card_payload(self.cfg, "scoresaber", self.PID, self.PROFILE)["avatar_url"], "")
        other = assets.card_payload(self.cfg, "beatleader", self.PID, self.PROFILE)
        self.assertNotEqual(other["avatar_url"], "")
        self.assertNotEqual(other["flag_url"], "")

    def test_rank_passthrough_for_unranked(self):
        for rank, country_rank in ((0, None), (None, 0)):
            card = assets.card_payload(self.cfg, "scoresaber", self.PID,
                                       {"name": "x", "rank": rank,
                                        "countryRank": country_rank})
            self.assertEqual(card["rank"], rank)
            self.assertEqual(card["countryRank"], country_rank)

    def test_empty_profile_returns_none(self):
        for profile in ({}, None, {"id": "1"}):
            self.assertIsNone(
                assets.card_payload(self.cfg, "scoresaber", self.PID, profile), profile)

    def test_country_normalized_to_upper(self):
        card = assets.card_payload(self.cfg, "scoresaber", self.PID,
                                   {"name": "x", "country": " cn "})
        self.assertEqual(card["country"], "CN")


class TestSyncDownloadsAssets(AssetTestCase):
    """The cloud sync is what talks to the network: it must cache both images
    and must not fail when an image cannot be fetched."""

    def _refresh(self, profile, avatar_result="/fake/avatar", flag_result="/fake/flag"):
        with mock.patch.object(m, "cfg", self.cfg), \
             mock.patch.object(m, "_scoresaber_id", return_value=self.PID), \
             mock.patch.object(m.scoresaber, "fetch_profile", return_value=profile), \
             mock.patch.object(m.scoresaber, "fetch_scores", return_value=[]), \
             mock.patch.object(m.repo, "save_player_cache") as save, \
             mock.patch.object(m.skill_tracks, "skill_palette_payload", return_value=None), \
             mock.patch.object(assets, "download_avatar",
                               return_value=avatar_result) as dl_avatar, \
             mock.patch.object(assets, "download_flag",
                               return_value=flag_result) as dl_flag:
            m._cloud_page_refresh("scoresaber")
        return save, dl_avatar, dl_flag

    def test_assets_downloaded_from_profile(self):
        profile = {"name": "TestPlayer", "country": "CN",
                   "profilePicture": "https://cdn.scoresaber.com/a.jpg"}
        save, dl_avatar, dl_flag = self._refresh(profile)
        save.assert_called_once()
        self.assertEqual(dl_avatar.call_args[0][3], "https://cdn.scoresaber.com/a.jpg")
        dl_flag.assert_called_once()
        self.assertEqual(dl_flag.call_args[0][1], "CN")

    def test_failed_download_does_not_break_sync(self):
        profile = {"name": "TestPlayer", "country": "CN",
                   "profilePicture": "https://cdn.scoresaber.com/a.jpg"}
        save, dl_avatar, dl_flag = self._refresh(profile, None, None)
        save.assert_called_once()      # snapshot still persisted
        dl_avatar.assert_called_once()
        dl_flag.assert_called_once()

    def test_missing_sources_skip_downloads(self):
        """No avatar URL / no country code in the snapshot (e.g. the player has
        none): the sync must not even attempt those downloads."""
        save, dl_avatar, dl_flag = self._refresh({"name": "TestPlayer"})
        save.assert_called_once()
        dl_avatar.assert_not_called()
        dl_flag.assert_not_called()

    def test_sync_player_assets_reports_skips(self):
        """`attempted` separates "nothing to fetch" from "fetch failed", which is
        what the sync logs on."""
        with mock.patch.object(assets, "download_avatar", return_value=None) as dl:
            out = assets.sync_player_assets(self.cfg, "scoresaber", self.PID,
                                            {"name": "x"})
        self.assertFalse(out["avatar"].attempted)
        self.assertFalse(out["flag"].attempted)
        dl.assert_not_called()
        with mock.patch.object(assets, "download_avatar", return_value=None):
            out = assets.sync_player_assets(
                self.cfg, "scoresaber", self.PID,
                {"name": "x", "country": "CN",
                 "profilePicture": "https://cdn.scoresaber.com/a.jpg"})
        self.assertTrue(out["avatar"].attempted)
        self.assertIsNone(out["avatar"].stored)
        self.assertTrue(out["flag"].attempted)


if __name__ == "__main__":
    unittest.main()
