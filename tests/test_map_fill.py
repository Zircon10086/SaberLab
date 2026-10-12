"""补全丢失谱面: source resolution, download politeness, archive safety and identity checking.

Everything runs offline: the network is replaced by a stub, and the zips under test are built in
the test itself, so the suite never depends on BeatSaver or BeatLeader being reachable.
"""
from __future__ import annotations

import io
import json
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.db.repository import Repository          # noqa: E402
from backend.maps import fill                          # noqa: E402
from backend.maps.resolver import (MapResolver, compute_level_hash,  # noqa: E402
                                   is_custom_hash, read_level_info)


def make_level(folder: pathlib.Path, song_name: str = "Test Song",
               difficulty: str = "ExpertPlus") -> pathlib.Path:
    """Write a minimal but real level folder: info.dat + one chart file."""
    folder.mkdir(parents=True, exist_ok=True)
    info = {
        "_version": "2.1.0",
        "_songName": song_name,
        "_songAuthorName": "Test Author",
        "_levelAuthorName": "Test Mapper",
        "_beatsPerMinute": 120.0,
        "_songFilename": "song.ogg",
        "_coverImageFilename": "cover.png",
        "_environmentName": "DefaultEnvironment",
        "_difficultyBeatmapSets": [{
            "_beatmapCharacteristicName": "Standard",
            "_difficultyBeatmaps": [{
                "_difficulty": difficulty,
                "_difficultyRank": 5,
                "_beatmapFilename": f"{difficulty}Standard.dat",
                "_noteJumpMovementSpeed": 16.0,
            }],
        }],
    }
    (folder / "Info.dat").write_text(json.dumps(info), encoding="utf-8")
    chart = {"_version": "2.0.0", "_notes": [{"_time": 1.0}, {"_time": 61.0}]}
    (folder / f"{difficulty}Standard.dat").write_text(json.dumps(chart), encoding="utf-8")
    (folder / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n not really a png")
    return folder


def zip_of(folder: pathlib.Path, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.writestr(path.relative_to(folder).as_posix(), path.read_bytes())
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


class IdentityTest(unittest.TestCase):
    def test_custom_hash_vs_official_id(self):
        self.assertTrue(is_custom_hash("86C932D86789FF5DD72C7CC9571399F0F4E0C487"))
        self.assertTrue(is_custom_hash("86c932d86789ff5dd72c7cc9571399f0f4e0c487"))
        for official in ("BETHEREFORYOU", "100BILLS", "CRABRAVE", "DABADABADABADABA"):
            self.assertFalse(is_custom_hash(official), official)

    def test_official_archive_names_cover_the_measured_rule(self):
        names = fill.official_zip_names("BETHEREFORYOU", "Be There For You")
        # the song-name spelling leads: it is the one the CDN actually uses
        self.assertEqual(names[0], "BeThereForYou")
        # digits survive, punctuation does not
        self.assertIn("100Bills", fill.official_zip_names("100BILLS", "$100 Bills"))
        # an all-caps song name stays as it is (the CDN does not re-case it)
        self.assertIn("LUDICROUS", fill.official_zip_names("LUDICROUSPLUS", "LUDICROUS+"))
        # and the level id is always there as a fallback
        self.assertIn("LUDICROUSPLUS", fill.official_zip_names("LUDICROUSPLUS", "LUDICROUS+"))

    def test_names_are_deduplicated_and_never_empty(self):
        names = fill.official_zip_names("CRABRAVE", "Crab Rave")
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(names))
        self.assertEqual(fill.official_zip_names("", ""), [])

    def test_the_custom_level_prefix_is_part_of_no_identity(self):
        """Beat Saber and the replay tools write custom_level_<HASH>; SaberLab's identity (and
        the 3D viewer's request after its official-level patch) is the bare value."""
        from backend.maps.resolver import normalize_level_id
        self.assertEqual(normalize_level_id("custom_level_" + "A" * 40), "A" * 40)
        self.assertEqual(normalize_level_id("CUSTOM_LEVEL_SPINETERNALLY"), "SPINETERNALLY")
        self.assertEqual(normalize_level_id("SPINETERNALLY"), "SPINETERNALLY")
        self.assertTrue(is_custom_hash("custom_level_" + "a" * 40))
        self.assertFalse(is_custom_hash("custom_level_SPINETERNALLY"))

    def test_sanitize_name_rejects_path_characters(self):
        self.assertEqual(fill.sanitize_name("../../etc/passwd"), "etcpasswd")
        self.assertEqual(fill.sanitize_name("C:\\windows"), "Cwindows")
        self.assertEqual(fill.sanitize_name(""), "level")


class SourceResolutionTest(unittest.TestCase):
    """Which URL a level identity turns into, without touching the network."""

    def setUp(self):
        # The repository keeps a connection per thread; leaving one open makes the whole suite
        # print ResourceWarnings at the end (it is a leak, not a test failure).
        self._repos: list = []

    def tearDown(self):
        for repo in self._repos:
            repo.close()

    def test_custom_level_uses_the_url_beatsaver_reports(self):
        body = json.dumps({"id": "1f678", "versions": [
            {"hash": "abc", "downloadURL": "https://r2cdn.beatsaver.com/abc.zip"}]}).encode()
        with mock.patch.object(fill, "http_get", return_value=body) as get:
            url, source = fill.resolve_download_url("86C932D86789FF5DD72C7CC9571399F0F4E0C487")
        self.assertEqual(source, "beatsaver")
        self.assertEqual(url, "https://r2cdn.beatsaver.com/abc.zip")
        self.assertIn("maps/hash/86c932d86789ff5dd72c7cc9571399f0f4e0c487", get.call_args[0][0])

    def test_custom_level_without_a_version_is_not_found(self):
        with mock.patch.object(fill, "http_get", return_value=b'{"versions": []}'):
            with self.assertRaises(fill.DownloadError) as ctx:
                fill.resolve_download_url("86C932D86789FF5DD72C7CC9571399F0F4E0C487")
        self.assertEqual(ctx.exception.reason, "not_found")

    def test_official_level_probes_candidate_names_until_one_answers(self):
        seen: list[str] = []

        def fake_get(url, **kwargs):
            seen.append(url)
            if url.endswith("/BeThereForYou.zip"):
                return b"PK\x03\x04" + b"x" * 32
            raise fill.DownloadError("not_found", "404")

        with mock.patch.object(fill, "http_get", side_effect=fake_get):
            url, source = fill.resolve_download_url("BETHEREFORYOU", "Be There For You")
        self.assertEqual(source, "beatleader")
        self.assertTrue(url.endswith("/BeThereForYou.zip"))
        # the level id alone spells "Bethereforyou"; the song name is what produces the CDN
        # name, so the probe works through the candidates and the hit is the song-name spelling
        self.assertIn("BeThereForYou.zip", [u.split("/")[-1] for u in seen])
        self.assertEqual(seen[-1].split("/")[-1], "BeThereForYou.zip")

    def test_official_level_absent_everywhere_reports_not_found(self):
        with mock.patch.object(fill, "http_get", side_effect=fill.DownloadError("not_found")):
            with self.assertRaises(fill.DownloadError) as ctx:
                fill.resolve_download_url("ALONE", "Alone")
        self.assertEqual(ctx.exception.reason, "not_found")
        self.assertIn("ALONE", ctx.exception.detail)

    def test_blank_identity_is_refused(self):
        with self.assertRaises(fill.DownloadError):
            fill.resolve_download_url("   ")


class PolitenessTest(unittest.TestCase):
    """The mirrors are community-funded: requests are serialised per host and paced."""

    def test_requests_to_one_host_never_overlap_and_stay_paced(self):
        """The real http_get runs here; only the connection is stubbed, so the pacing that is
        measured is the pacing that ships."""
        fill._host_last_request.clear()
        lock = threading.Lock()
        state: dict = {"inflight": 0, "peak": 0, "stamps": []}

        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b"PK\x03\x04data"
        response.getheader.return_value = None

        def fake_open(url, proxy="", timeout=60.0):
            with lock:
                state["inflight"] += 1
                state["peak"] = max(state["peak"], state["inflight"])
                state["stamps"].append(time.time())
            time.sleep(0.05)
            with lock:
                state["inflight"] -= 1
            conn = mock.MagicMock()
            conn.getresponse.return_value = response
            return conn, "/x.zip"

        with mock.patch.object(fill, "_open_https", side_effect=fake_open):
            threads = [threading.Thread(target=lambda: fill.http_get("https://host.test/x.zip"))
                       for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(state["peak"], 1, "two requests to the same host overlapped")
        self.assertEqual(len(state["stamps"]), 3)
        # at least two gaps of _HOST_MIN_GAP_SEC across the three requests
        self.assertGreaterEqual(state["stamps"][-1] - state["stamps"][0],
                                fill._HOST_MIN_GAP_SEC * 2 * 0.8)

    def test_busy_status_retries_and_honours_retry_after(self):
        fill._host_last_request.clear()
        response = mock.MagicMock()
        response.status = 429
        response.getheader.return_value = "1"
        response.read.return_value = b""
        conn = mock.MagicMock()
        conn.getresponse.return_value = response
        ok = mock.MagicMock()
        ok.status = 200
        ok.read.return_value = b"PK\x03\x04data"
        ok.getheader.return_value = None
        conn.getresponse.side_effect = [response, ok]
        sleeps: list[float] = []
        with mock.patch.object(fill, "_open_https", return_value=(conn, "/x")), \
             mock.patch.object(fill.time, "sleep", side_effect=sleeps.append):
            body = fill.http_get("https://host.test/x.zip", timeout=1)
        self.assertEqual(body[:4], b"PK\x03\x04")
        self.assertIn(1.0, sleeps)

    def test_not_found_is_not_retried(self):
        fill._host_last_request.clear()
        response = mock.MagicMock()
        response.status = 404
        response.read.return_value = b""
        response.getheader.return_value = None
        conn = mock.MagicMock()
        conn.getresponse.return_value = response
        with mock.patch.object(fill, "_open_https", return_value=(conn, "/x")):
            with self.assertRaises(fill.DownloadError) as ctx:
                fill.http_get("https://host.test/x.zip")
        self.assertEqual(ctx.exception.reason, "not_found")
        self.assertEqual(conn.getresponse.call_count, 1)

    def test_oversized_body_is_refused(self):
        fill._host_last_request.clear()
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b"x" * 64
        response.getheader.return_value = None
        conn = mock.MagicMock()
        conn.getresponse.return_value = response
        with mock.patch.object(fill, "_open_https", return_value=(conn, "/x")):
            with self.assertRaises(fill.DownloadError) as ctx:
                fill.http_get("https://host.test/x.zip", max_bytes=32)
        self.assertEqual(ctx.exception.reason, "too_large")


class ArchiveSafetyTest(unittest.TestCase):
    def test_round_trip_extracts_a_normal_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = make_level(pathlib.Path(tmp) / "src")
            dest = pathlib.Path(tmp) / "out"
            dest.mkdir()
            written = fill.extract_archive(zip_of(source), dest)
            self.assertGreater(written, 0)
            self.assertTrue((dest / "Info.dat").exists())

    def test_path_traversal_members_are_refused(self):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as zf:
            zf.writestr("Info.dat", "{}")
            zf.writestr("../escape.dat", "x")
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "out"
            dest.mkdir()
            with self.assertRaises(fill.DownloadError) as ctx:
                fill.extract_archive(blob.getvalue(), dest)
            self.assertEqual(ctx.exception.reason, "unsafe_member")
            self.assertFalse((pathlib.Path(tmp) / "escape.dat").exists())

    def test_absolute_and_drive_paths_are_refused(self):
        for member in ("/etc/passwd", "C:/Windows/system32/x.dat", "\\\\server\\share\\x.dat"):
            blob = io.BytesIO()
            with zipfile.ZipFile(blob, "w") as zf:
                zf.writestr(member, "x")
            with tempfile.TemporaryDirectory() as tmp:
                dest = pathlib.Path(tmp) / "out"
                dest.mkdir()
                with self.assertRaises(fill.DownloadError, msg=member) as ctx:
                    fill.extract_archive(blob.getvalue(), dest)
                self.assertEqual(ctx.exception.reason, "unsafe_member")

    def test_backslash_traversal_is_refused(self):
        self.assertFalse(fill._safe_zip_member("..\\evil.dat"))
        self.assertFalse(fill._safe_zip_member("a/../../evil.dat"))
        self.assertTrue(fill._safe_zip_member("sub/dir/Expert.dat"))

    def test_entry_count_and_size_are_capped(self):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as zf:
            for i in range(5):
                zf.writestr(f"f{i}.dat", b"x" * 100)
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "out"
            dest.mkdir()
            with mock.patch.object(fill, "MAX_ARCHIVE_ENTRIES", 2):
                with self.assertRaises(fill.DownloadError) as ctx:
                    fill.extract_archive(blob.getvalue(), dest)
            self.assertEqual(ctx.exception.reason, "too_many_entries")
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "out"
            dest.mkdir()
            with mock.patch.object(fill, "MAX_EXTRACTED_BYTES", 150):
                with self.assertRaises(fill.DownloadError) as ctx:
                    fill.extract_archive(blob.getvalue(), dest)
            self.assertEqual(ctx.exception.reason, "too_large_extracted")

    def test_non_zip_payload_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "out"
            dest.mkdir()
            with self.assertRaises(fill.DownloadError) as ctx:
                fill.extract_archive(b"<html>error</html>", dest)
            self.assertEqual(ctx.exception.reason, "not_zip")

    def test_empty_archive_is_refused(self):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w"):
            pass
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "out"
            dest.mkdir()
            with self.assertRaises(fill.DownloadError) as ctx:
                fill.extract_archive(blob.getvalue(), dest)
            self.assertEqual(ctx.exception.reason, "empty_archive")


class VerifyLevelTest(unittest.TestCase):
    def test_custom_level_hash_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_level(pathlib.Path(tmp) / "lvl")
            real = compute_level_hash(folder)
            result = fill.verify_level(folder, real, "Test Song")
            self.assertEqual(result["verified"], "hash")
            self.assertEqual(result["downloaded_hash"], real)
            # A different map whose name does not match is refused outright.
            with self.assertRaises(fill.DownloadError) as ctx:
                fill.verify_level(folder, "0" * 40, "Some Other Song")
            self.assertEqual(ctx.exception.reason, "hash_mismatch")

    def test_a_newer_version_of_the_same_map_is_accepted_and_flagged(self):
        """BeatSaver serves the CURRENT version: a re-uploaded map hashes differently, and the
        replays that point at the old hash must still be playable (measured 2026-10)."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_level(pathlib.Path(tmp) / "lvl", song_name="3 BIG SHOTS")
            result = fill.verify_level(folder, "0" * 40, "3 BIG SHOTS FROM [KITCHEN GUN]")
            self.assertEqual(result["verified"], "version_changed")
            self.assertEqual(result["downloaded_hash"], compute_level_hash(folder))

    def test_official_level_is_checked_by_song_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_level(pathlib.Path(tmp) / "lvl", song_name="Be There For You")
            ok = fill.verify_level(folder, "BETHEREFORYOU", "Be There For You")
            self.assertEqual(ok["verified"], "name")
            other = fill.verify_level(folder, "BETHEREFORYOU", "Something Else")
            self.assertEqual(other["verified"], "unverified")

    def test_folder_without_a_readable_level_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp) / "lvl"
            folder.mkdir()
            with self.assertRaises(fill.DownloadError) as ctx:
                fill.verify_level(folder, "0" * 40)
            self.assertEqual(ctx.exception.reason, "no_level")

    def test_level_without_charts_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp) / "lvl"
            folder.mkdir()
            (folder / "Info.dat").write_text(json.dumps({
                "_songName": "Empty", "_beatsPerMinute": 120.0,
                "_difficultyBeatmapSets": []}), encoding="utf-8")
            with self.assertRaises(fill.DownloadError) as ctx:
                fill.verify_level(folder, "CRABRAVE")
            self.assertEqual(ctx.exception.reason, "no_difficulties")


class FillOneTest(unittest.TestCase):
    """End-to-end of a single level with the network stubbed: unpack, verify, install, register."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.levels = self.root / "levels"
        self.levels.mkdir()
        self.repo = Repository(self.root / "test.sqlite")
        self.resolver = MapResolver(str(self.root / "empty"), self.repo, "",
                                    str(self.levels))
        self.source = make_level(self.root / "src", song_name="Test Song")
        self.hash = compute_level_hash(self.source)
        self.blob = zip_of(self.source)

    def tearDown(self):
        self.repo.close() if hasattr(self.repo, "close") else None
        self.tmp.cleanup()

    def test_success_registers_the_level_in_the_library(self):
        with mock.patch.object(fill, "resolve_download_url",
                               return_value=("https://r2cdn.beatsaver.com/x.zip", "beatsaver")), \
             mock.patch.object(fill, "http_get", return_value=self.blob):
            res = fill.fill_one(self.hash, "Test Song", repo=self.repo, resolver=self.resolver,
                                levels_dir=self.levels)
        self.assertEqual(res["status"], "ok", res)
        self.assertEqual(res["verified"], "hash")
        installed = self.levels / self.hash
        self.assertTrue((installed / "Info.dat").exists())
        row = self.repo.get_map(self.hash)
        self.assertIsNotNone(row)
        self.assertEqual(row["hash_source"], "downloaded")
        self.assertEqual(pathlib.Path(row["path"]), installed)
        self.assertGreater(float(row["song_length"] or 0), 0)
        info = read_level_info(installed)
        self.assertEqual(info["song_name"], "Test Song")

    def test_a_second_run_reports_it_is_already_local(self):
        (self.levels / self.hash).mkdir()
        make_level(self.levels / self.hash)
        with mock.patch.object(fill, "http_get") as get:
            res = fill.fill_one(self.hash, "Test Song", repo=self.repo, resolver=self.resolver,
                                levels_dir=self.levels)
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["reason"], "already_local")
        get.assert_not_called()

    def test_hash_mismatch_leaves_nothing_behind(self):
        other = make_level(self.root / "other", song_name="Different")
        with mock.patch.object(fill, "resolve_download_url",
                               return_value=("https://r2cdn.beatsaver.com/x.zip", "beatsaver")), \
             mock.patch.object(fill, "http_get", return_value=zip_of(other)):
            res = fill.fill_one(self.hash, "Test Song", repo=self.repo, resolver=self.resolver,
                                levels_dir=self.levels)
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["reason"], "hash_mismatch")
        self.assertFalse((self.levels / self.hash).exists())
        self.assertIsNone(self.repo.get_map(self.hash))
        # the staging folder is gone as well: only the (empty) levels dir remains
        self.assertEqual([p.name for p in self.levels.iterdir()], [])

    def test_network_failure_is_reported_not_raised(self):
        with mock.patch.object(fill, "resolve_download_url",
                               side_effect=fill.DownloadError("not_found", "absent")):
            res = fill.fill_one("ALONE", "Alone", repo=self.repo, resolver=self.resolver,
                                levels_dir=self.levels)
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["reason"], "not_found")

    def test_a_broken_archive_is_reported_and_cleaned_up(self):
        with mock.patch.object(fill, "resolve_download_url",
                               return_value=("https://cdn.songs.beatleader.xyz/x.zip",
                                             "beatleader")), \
             mock.patch.object(fill, "http_get", return_value=b"not a zip at all"):
            res = fill.fill_one("BETHEREFORYOU", "Be There For You", repo=self.repo,
                                resolver=self.resolver, levels_dir=self.levels)
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["reason"], "not_zip")
        self.assertEqual([p.name for p in self.levels.iterdir()], [])

    def test_replacing_an_existing_folder_keeps_a_complete_level(self):
        target = self.levels / self.hash
        make_level(target, song_name="Old")
        with mock.patch.object(fill, "resolve_download_url",
                               return_value=("https://r2cdn.beatsaver.com/x.zip", "beatsaver")), \
             mock.patch.object(fill, "http_get", return_value=self.blob):
            res = fill.fill_one(self.hash, "Test Song", repo=self.repo, resolver=self.resolver,
                                levels_dir=self.levels, replace=True)
        self.assertEqual(res["status"], "ok", res)
        self.assertEqual(read_level_info(target)["song_name"], "Test Song")
        # no staging or stale folder is left next to the level
        self.assertEqual(sorted(p.name for p in self.levels.iterdir()), [self.hash])


def map_row(folder: pathlib.Path, map_hash: str, song_name: str = "Known") -> dict:
    """A complete maps row: upsert_map binds every column, so partial dicts are not accepted."""
    return {
        "map_hash": map_hash, "folder_name": folder.name, "path": str(folder),
        "song_name": song_name, "song_author": "Artist", "mapper": "Mapper",
        "bpm": 120, "song_length": 100, "version": "3.2.0",
        "difficulties": json.dumps([{"characteristic": "Standard", "difficulty": "Expert",
                                     "label": "", "filename": "Expert.dat", "njs": 16}]),
        "info_json": json.dumps({"song_sub_name": "sub"}),
        "hash_source": "computed", "last_scanned": "2026-10-01 00:00:00Z",
        "beatmap_key": "", "nps_json": None, "ranked_difficulty": None, "stars": None,
    }


class MissingLevelsTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(dir=ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repo = Repository(self.root / "test.sqlite")
        self.addCleanup(self.repo.close)

    def _add_replay(self, map_hash, song_name, count=1):
        for i in range(count):
            self.repo.upsert_replay({
                "replay_id": f"{map_hash}-{i}", "file_path": "x.bsor", "file_name": "x.bsor",
                "map_hash": map_hash, "song_name": song_name, "timestamp": "2026-01-01 00:00:00",
            })

    def test_groups_by_kind_and_counts_plays(self):
        self._add_replay("86C932D86789FF5DD72C7CC9571399F0F4E0C487", "Custom", 2)
        self._add_replay("BETHEREFORYOU", "Be There For You", 3)
        folder = self.root / "known"
        folder.mkdir()
        self.repo.upsert_map(map_row(folder, "A" * 40, "Known"))
        self._add_replay("A" * 40, "Known", 1)
        report = fill.missing_levels(self.repo)
        self.assertEqual(report["total"], 2)
        # the play count covers only the levels that are actually missing: the one in the
        # library is not part of this report (its replay is playable right now)
        self.assertEqual(report["plays"], 5)
        self.assertEqual([i["map_hash"] for i in report["official"]], ["BETHEREFORYOU"])
        self.assertEqual(len(report["custom"]), 1)
        # busiest first: the report drives a task the player watches
        self.assertEqual(report["items"][0]["plays"], 3)
        self.assertEqual(report["items"][1]["plays"], 2)

    def test_lower_case_replay_hashes_match_the_library(self):
        folder = self.root / "known"
        folder.mkdir()
        self.repo.upsert_map(map_row(folder, "B" * 40, "Known"))
        self._add_replay("b" * 40, "Known")
        self.assertEqual(fill.missing_levels(self.repo)["total"], 0)


class FillMissingTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(dir=ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.levels = self.root / "levels"
        self.repo = Repository(self.root / "test.sqlite")
        self.addCleanup(self.repo.close)
        self.resolver = MapResolver(str(self.root / "empty"), self.repo, "", str(self.levels))

    def test_reports_what_could_not_be_fetched(self):
        for i in range(3):
            self.repo.upsert_replay({
                "replay_id": f"r{i}", "file_path": "x.bsor", "file_name": "x.bsor",
                "map_hash": "BETHEREFORYOU", "song_name": "Be There For You",
                "timestamp": "2026-01-01 00:00:00"})
        self.repo.upsert_replay({
            "replay_id": "r9", "file_path": "x.bsor", "file_name": "x.bsor",
            "map_hash": "ALONE", "song_name": "Alone", "timestamp": "2026-01-01 00:00:00"})

        def fake_resolve(key, song, **kwargs):
            if key == "BETHEREFORYOU":
                return "https://cdn.songs.beatleader.xyz/BeThereForYou.zip", "beatleader"
            raise fill.DownloadError("not_found", "cannot redistribute DLC audio")

        source = make_level(self.root / "src", song_name="Be There For You")
        with mock.patch.object(fill, "resolve_download_url", side_effect=fake_resolve), \
             mock.patch.object(fill, "http_get", return_value=zip_of(source)):
            report = fill.fill_missing(self.repo, self.resolver, levels_dir=self.levels,
                                       concurrency=3, proxy="", timeout=5)
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["ok"], 1)
        self.assertEqual(report["failed"], 1)
        self.assertEqual([r["map_hash"] for r in report["unavailable"]], ["ALONE"])
        self.assertIn("BETHEREFORYOU", report["results"][0]["map_hash"]
                      if report["results"][0]["status"] == "ok" else report["results"][1]["map_hash"])

    def test_progress_is_reported_once_per_level(self):
        for i, key in enumerate(("ALONE", "NUMB")):
            self.repo.upsert_replay({
                "replay_id": f"r{i}", "file_path": "x.bsor", "file_name": "x.bsor",
                "map_hash": key, "song_name": key, "timestamp": "2026-01-01 00:00:00"})
        seen: list[tuple[int, int]] = []
        with mock.patch.object(fill, "resolve_download_url",
                               side_effect=fill.DownloadError("not_found", "absent")):
            fill.fill_missing(self.repo, self.resolver, levels_dir=self.levels, concurrency=2,
                              progress_cb=lambda i, n, _name: seen.append((i, n)))
        self.assertEqual(sorted(seen), [(1, 2), (2, 2)])

    def test_nothing_missing_is_a_clean_no_op(self):
        with mock.patch.object(fill, "resolve_download_url") as resolve:
            report = fill.fill_missing(self.repo, self.resolver, levels_dir=self.levels)
        self.assertEqual(report["total"], 0)
        resolve.assert_not_called()

    def test_downloads_land_under_the_configured_directory(self):
        self.repo.upsert_replay({
            "replay_id": "r0", "file_path": "x.bsor", "file_name": "x.bsor",
            "map_hash": "CRABRAVE", "song_name": "Crab Rave", "timestamp": "2026-01-01 00:00:00"})
        source = make_level(self.root / "src", song_name="Crab Rave")
        with mock.patch.object(fill, "resolve_download_url",
                               return_value=("https://cdn.songs.beatleader.xyz/CrabRave.zip",
                                             "beatleader")), \
             mock.patch.object(fill, "http_get", return_value=zip_of(source)):
            report = fill.fill_missing(self.repo, self.resolver, levels_dir=self.levels)
        self.assertEqual(report["ok"], 1)
        self.assertTrue((self.levels / "CRABRAVE" / "Info.dat").exists())
        self.assertIsNotNone(self.repo.get_map("CRABRAVE"))


class MultiRootScanTest(unittest.TestCase):
    """The download root is searched and scanned alongside the game's CustomLevels."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(dir=ROOT / "_tmp"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.custom = self.root / "CustomLevels"
        self.levels = self.root / "levels"
        self.custom.mkdir()
        self.levels.mkdir()
        self.repo = Repository(self.root / "test.sqlite")
        self.addCleanup(self.repo.close)
        self.resolver = MapResolver(str(self.custom), self.repo, "", str(self.levels))

    def test_both_roots_end_up_in_the_library(self):
        custom = make_level(self.custom / "custom (song)", song_name="Custom Song")
        downloaded = make_level(self.levels / "CRABRAVE", song_name="Crab Rave")
        make_level(self.levels / ("C" * 40), song_name="Downloaded Custom")
        stats = self.resolver.scan()
        self.assertEqual(stats["scanned"], 3)
        self.assertIsNotNone(self.repo.get_map(compute_level_hash(custom)))
        # the downloaded folder's own name is its identity, no hashing involved
        self.assertIsNotNone(self.repo.get_map("CRABRAVE"))
        self.assertEqual(pathlib.Path(self.repo.get_map("CRABRAVE")["path"]), downloaded)
        self.assertIsNotNone(self.repo.get_map("C" * 40))

    def test_a_missing_download_root_is_not_an_error(self):
        self.levels.rmdir()
        stats = self.resolver.scan()
        self.assertNotIn("error", stats)
        self.assertIn("downloaded_dir_missing", stats)
        self.assertEqual(stats["scanned"], 0)

    def test_a_missing_game_root_is_still_an_error(self):
        import shutil
        shutil.rmtree(self.custom)
        stats = self.resolver.scan()
        self.assertIn("error", stats)
        self.assertIn("CustomLevels", stats["error"])

    def test_resolve_adopts_a_folder_that_is_already_downloaded(self):
        """A level an earlier run (or another instance) downloaded is picked up without a scan.

        `trigger_scan=True` is the interactive path (a cover request, the detail page): the
        folder is adopted directly, which is what keeps this cheap. `trigger_scan=False` is the
        batch contract — it must NOT look anything up, and there is a test for that below.
        """
        make_level(self.levels / "GHOST", song_name="Ghost")
        self.assertIsNone(self.repo.get_map("GHOST"))
        row = self.resolver.resolve("GHOST")
        self.assertIsNotNone(row)
        self.assertEqual(row["map_hash"], "GHOST")
        self.assertEqual(row["hash_source"], "downloaded")
        # and the identity is remembered: a second lookup needs no work at all
        self.assertEqual(self.resolver.resolve("GHOST")["map_hash"], "GHOST")

    def test_batch_lookup_trigger_scan_false_adopts_nothing(self):
        """The batch contract: no scan means no lookup at all, not even the cheap one."""
        make_level(self.levels / "GHOST", song_name="Ghost")
        self.assertIsNone(self.resolver.resolve("GHOST", trigger_scan=False))
        self.assertIsNone(self.repo.get_map("GHOST"))

    def test_adopting_ignores_a_folder_without_a_level(self):
        (self.levels / "GHOST").mkdir()
        (self.levels / "GHOST" / "readme.txt").write_text("no info.dat here")
        self.assertIsNone(self.resolver.resolve("GHOST"))

    def test_download_root_is_not_scanned_twice_when_it_is_the_game_root(self):
        resolver = MapResolver(str(self.custom), self.repo, "", str(self.custom))
        make_level(self.custom / "only (once)")
        self.assertEqual(resolver.scan()["scanned"], 1)


class ConfigTest(unittest.TestCase):
    def test_settings_are_schema_driven_and_clamped(self):
        from backend.config import Config
        from backend.config.schema import SETTINGS_SCHEMA
        keys = {item["key"] for item in SETTINGS_SCHEMA}
        self.assertIn("network.download_concurrency", keys)
        self.assertIn("network.download_timeout_seconds", keys)
        cfg = Config()
        self.assertEqual(cfg.download_concurrency, 3)
        self.assertGreater(cfg.download_timeout_seconds, 0)
        self.assertTrue(str(cfg.downloaded_levels_dir).replace("\\", "/").endswith("data/levels"))


if __name__ == "__main__":
    unittest.main()
