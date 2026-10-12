"""Local request guard regression tests (2026-09, security hardening).

Threat model: any web page the user visits can POST to 127.0.0.1:6980 and drive
destructive endpoints (recycling replays, clearing the analysis cache, restarting
the app). DNS rebinding is the other half of the same model — once an attacker's
domain resolves to this machine the browser treats this service as same-origin,
which turns **read** endpoints into an information-disclosure surface too.

These tests pin the guard's three rules and both sides of its behaviour:

* Allow side (the largest regression risk, guaranteed first): same-origin Origin,
  requests carrying no Origin from local tools, ordinary reads, and a different
  port that is still this machine.
* Refuse side: forged Origin, null Origin, read requests with a forged Host,
  cross-site marker, missing Host.

The allow side uniformly asserts `status != 403`: once a request is let through,
the route itself may still return 404/422, which is the endpoint's own business
rather than the guard's verdict.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.main as m

BASE_URL = "http://127.0.0.1:6980"
# one read-only endpoint and one state-changing endpoint: the guard does not
# split by method, so both sides are exercised.
GET_PATH = "/api/status"
POST_PATH = "/api/replays/__no-such-replay__/reveal"
GUARD_DETAIL = "请求已被拒绝，操作没有执行"


class LocalRequestGuardTest(unittest.TestCase):
    """Allow/refuse matrix of the guard middleware."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(m.app, base_url=BASE_URL)

    def _status(self, method: str, path: str, headers: dict | None = None) -> int:
        resp = self.client.request(method, path, headers=headers or {})
        return resp.status_code

    # ---------- allow side: the most common real paths, none of which may be blocked ----------

    def test_same_origin_post_is_allowed(self):
        """A same-origin frontend POST carrying Origin (the most common path) must be allowed."""
        status = self._status("POST", POST_PATH,
                              {"origin": BASE_URL, "sec-fetch-site": "same-origin"})
        self.assertNotEqual(status, 403)

    def test_no_origin_post_from_local_tool_is_allowed(self):
        """curl / launcher identity probe: no Origin, no cross-site marker, connecting straight to this machine."""
        self.assertNotEqual(self._status("POST", POST_PATH), 403)

    def test_get_is_allowed(self):
        self.assertNotEqual(self._status("GET", GET_PATH), 403)

    def test_loopback_origin_on_another_port_is_allowed(self):
        """A different port that is still a local origin does not count as cross-site (the
        configured port and the port actually listened on may differ)."""
        status = self._status("POST", POST_PATH, {"origin": "http://localhost:6981"})
        self.assertNotEqual(status, 403)

    def test_ipv6_loopback_origin_is_allowed(self):
        status = self._status("POST", POST_PATH, {"origin": "http://[::1]:6980"})
        self.assertNotEqual(status, 403)

    def test_same_origin_fetch_metadata_is_allowed(self):
        status = self._status("GET", GET_PATH, {"sec-fetch-site": "same-origin"})
        self.assertNotEqual(status, 403)

    # ---------- refuse side ----------

    def test_cross_site_origin_is_refused(self):
        status = self._status("POST", POST_PATH, {"origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_null_origin_is_refused(self):
        """Sandboxed iframes and file:// pages send a null origin."""
        self.assertEqual(self._status("POST", POST_PATH, {"origin": "null"}), 403)

    def test_non_http_origin_is_refused(self):
        self.assertEqual(
            self._status("POST", POST_PATH, {"origin": "chrome-extension://abcdef"}), 403)

    def test_foreign_host_on_read_is_refused(self):
        """DNS rebinding: a forged Host must be blocked on read endpoints too, otherwise the
        metadata can be read out."""
        status = self._status("GET", GET_PATH, {"host": "evil.example:6980"})
        self.assertEqual(status, 403)

    def test_foreign_host_on_write_is_refused(self):
        status = self._status("POST", POST_PATH, {"host": "evil.example:6980"})
        self.assertEqual(status, 403)

    def test_missing_host_is_refused(self):
        status = self._status("GET", GET_PATH, {"host": ""})
        self.assertEqual(status, 403)

    def test_cross_site_fetch_metadata_is_refused(self):
        """Subresource loads (<img src=...>) carry no Origin, so only the cross-site marker can stop them."""
        status = self._status("GET", "/api/maps/__none__/cover",
                              {"sec-fetch-site": "cross-site"})
        self.assertEqual(status, 403)

    def test_refusal_body_is_translatable_json(self):
        """The refusal must be JSON with a detail field so `api()` can pick it up and route it
        through tErr translation."""
        resp = self.client.post(POST_PATH, headers={"origin": "http://evil.example"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("detail"), GUARD_DETAIL)

    def test_guard_has_no_path_allowlist(self):
        """The guard must not exempt by path: as soon as an exemption table exists, the red
        line's "full coverage" no longer holds."""
        import inspect
        source = inspect.getsource(m._local_request_guard)
        self.assertNotIn("/api/", source)
        self.assertNotIn("url.path", source)


class GuardHelperTest(unittest.TestCase):
    """Two pure parsing helpers: hostname extraction from Host and from Origin."""

    def test_authority_host(self):
        cases = {
            "127.0.0.1:6980": "127.0.0.1",
            "127.0.0.1": "127.0.0.1",
            "localhost:6980": "localhost",
            "LOCALHOST": "localhost",
            " 127.0.0.1:6980 ": "127.0.0.1",
            "[::1]:6980": "::1",
            "[::1]": "::1",
            "[": "",
            "": "",
            "evil.example:6980": "evil.example",
            "127.0.0.1.evil.example": "127.0.0.1.evil.example",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(m._authority_host(raw), expected)

    def test_origin_host(self):
        cases = {
            "http://127.0.0.1:6980": "127.0.0.1",
            "https://localhost": "localhost",
            "http://[::1]:6980": "::1",
            "null": "",
            "NULL": "",
            "": "",
            "file:///C:/x.html": "",
            "chrome-extension://abc": "",
            "http://evil.example": "evil.example",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(m._origin_host(raw), expected)


class ReplayPathConfinementTest(unittest.TestCase):
    """Path confinement for by-path: only the two configured replay directories are allowed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.replay = root / "BeatLeader" / "Replays"
        self.ll_replays = root / "LocalLeaderboard" / "Replays"
        self.outside = root / "Elsewhere"
        # a sibling directory sharing a prefix: a naive startswith would let it through
        self.sibling = root / "BeatLeader" / "Replays2"
        for d in (self.replay, self.ll_replays, self.outside, self.sibling):
            d.mkdir(parents=True)
        self.cfg = SimpleNamespace(replay_dir=str(self.replay),
                                   local_leaderboard_dir=str(self.ll_replays))
        self._patch = mock.patch.object(m, "cfg", self.cfg)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _ok(self, path) -> str:
        return m._require_replay_source_path(str(path))

    def test_file_in_replay_dir_is_allowed(self):
        target = self.replay / "a.bsor"
        self.assertEqual(self._ok(target), str(target.resolve()))

    def test_file_in_local_leaderboard_dir_is_allowed(self):
        """A LocalLeaderboard copy is a legitimate file_path source and must not be blocked by mistake."""
        target = self.ll_replays / "a_1234.bsor"
        self.assertEqual(self._ok(target), str(target.resolve()))

    def test_outside_file_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            self._ok(self.outside / "a.bsor")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_sibling_dir_with_shared_prefix_is_refused(self):
        with self.assertRaises(HTTPException):
            self._ok(self.sibling / "a.bsor")

    def test_traversal_escaping_the_replay_dir_is_refused(self):
        with self.assertRaises(HTTPException):
            self._ok(self.replay / ".." / ".." / "Elsewhere" / "a.bsor")

    def test_the_directory_itself_is_refused(self):
        """The directory itself is not a replay file: only files inside the directory are allowed."""
        with self.assertRaises(HTTPException):
            self._ok(self.replay)

    def test_empty_path_is_refused(self):
        with self.assertRaises(HTTPException):
            self._ok("")

    def test_unconfigured_source_is_skipped(self):
        """An unconfigured replay directory must not become a reason to allow everything."""
        with mock.patch.object(m, "cfg", SimpleNamespace(replay_dir="",
                                                        local_leaderboard_dir="")):
            with self.assertRaises(HTTPException):
                self._ok(self.replay / "a.bsor")


if __name__ == "__main__":
    unittest.main()
