"""Host window fullscreen regression test (2026-10).

The replay card's 全屏 control is one action: make the HOST WINDOW cover its whole display
and fill that window with the replay (docs/DEVELOPMENT.md 5.6c). The window half of it lives
in the dialog bridge (backend/dialog.py), because that is where the window shell is
registered. The platform keeps its own idea of "fullscreen" which goes wrong as soon as
anything else moves or restores the window, so the bridge never trusts a flag: it MEASURES
the window rectangle against its display rectangle, and only asks for a change when the two
disagree. That is the behaviour pinned here:

  * no window (browser mode / --browser) reports unavailable and changes nothing;
  * a window that already covers its display is left alone - an explicit target does not
    toggle it into the opposite state (self-healing, the bug this replaced);
  * a window that does not cover its display is asked to cover it, exactly once;
  * leaving asks once as well, and the answer always describes the measured result;
  * a refusing window (closing, platform refusal) is reported, not raised, and its geometry
    is re-read rather than assumed to have changed;
  * /api/window/fullscreen and /api/status agree, and an explicit target reaches the bridge.
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import backend.dialog as dialog  # noqa: E402
import backend.main as m  # noqa: E402

MONITOR = {"x": 0, "y": 0, "w": 2560, "h": 1440}
SMALL = {"x": 100, "y": 80, "w": 1440, "h": 900}
COVERED = dict(MONITOR)


class _FakeWindow:
    """Stands in for the pywebview window: counts toggles, can refuse to toggle."""

    def __init__(self, fail: bool = False):
        self.calls = 0
        self.fail = fail

    def toggle_fullscreen(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("window is closing")


class _BridgeCase(unittest.TestCase):
    def setUp(self):
        self._old_shell = dialog._shell
        self._old_flag = dialog._fullscreen
        dialog._fullscreen = False
        self._rect = dict(SMALL)
        # the bridge reads the window rectangle through backend.desktop; a shared mutable
        # rect lets a test describe the window before and after a toggle
        patcher_win = mock.patch.object(dialog, "_window_geometry",
                                        side_effect=self._geometry)
        patcher_win.start()
        self.addCleanup(patcher_win.stop)

    def tearDown(self):
        dialog._shell = self._old_shell
        dialog._fullscreen = self._old_flag

    def _geometry(self):
        """Whatever the test currently says the window is (None = not readable)."""
        if self._rect is None:
            return None
        return {"window": dict(self._rect), "monitor": MONITOR}

    def register(self, window, hwnd=1234):
        dialog._shell = SimpleNamespace(_window=window, _hwnd=hwnd)


class HostWindowFullscreenBridgeTest(_BridgeCase):
    def test_browser_mode_reports_unavailable(self):
        """No registered shell (plain browser tab / --browser) -> unavailable."""
        dialog._shell = None
        res = dialog.request_window_fullscreen()
        self.assertTrue(res.get("unavailable"))
        self.assertFalse(dialog.window_fullscreen())

    def test_small_window_is_asked_to_cover_once(self):
        """A normal window gets exactly one toggle and is then reported as covering."""
        window = _FakeWindow()

        def cover():
            window.calls += 1
            self._rect = dict(COVERED)          # as if the platform had moved the window

        self.register(window)
        with mock.patch.object(window, "toggle_fullscreen", side_effect=cover):
            res = dialog.request_window_fullscreen(True)
            self.assertEqual(window.calls, 1)
            self.assertTrue(res.get("available"))
            self.assertTrue(res.get("fullscreen"))
            self.assertTrue(dialog.window_fullscreen())

            # asking again while it is covered is a no-op, not a toggle back to small
            res2 = dialog.request_window_fullscreen(True)
        self.assertEqual(window.calls, 1)
        self.assertTrue(res2.get("fullscreen"))

    def test_already_covered_is_left_alone(self):
        """The self-healing case: a covered window must not be toggled back to small."""
        window = _FakeWindow()
        self.register(window)
        self._rect = dict(COVERED)
        res = dialog.request_window_fullscreen(True)
        self.assertEqual(window.calls, 0)
        self.assertTrue(res.get("fullscreen"))
        self.assertTrue(dialog.window_fullscreen())

    def test_leaving_a_covered_window_asks_once(self):
        """Uncovering is one toggle, and the report follows the measured result."""
        window = _FakeWindow()
        self.register(window)
        self._rect = dict(COVERED)
        dialog._fullscreen = True

        def move_back():
            self._rect = dict(SMALL)

        with mock.patch.object(window, "toggle_fullscreen", side_effect=move_back):
            res = dialog.request_window_fullscreen(False)
        self.assertFalse(res.get("fullscreen"))
        self.assertFalse(dialog.window_fullscreen())

    def test_drifted_state_self_corrects(self):
        """A window that is small while the flag says covered gets uncovered without toggling."""
        window = _FakeWindow()
        self.register(window)
        self._rect = dict(SMALL)
        dialog._fullscreen = True              # stale flag, as after an external resize
        res = dialog.request_window_fullscreen(False)
        self.assertEqual(window.calls, 0)      # already in the wanted state
        self.assertFalse(res.get("fullscreen"))
        self.assertFalse(dialog.window_fullscreen())

    def test_refusing_window_is_reported_not_raised(self):
        """A failing toggle is reported; it must not claim the window moved."""
        window = _FakeWindow(fail=True)
        self.register(window)
        self._rect = dict(SMALL)
        res = dialog.request_window_fullscreen(True)
        self.assertTrue(res.get("unavailable"))
        self.assertIn("error", res)
        self.assertFalse(dialog.window_fullscreen())

    def test_unreadable_geometry_still_asks_for_a_change(self):
        """Without geometry the bridge falls back to asking (and reports what it measured)."""
        window = _FakeWindow()
        self.register(window)
        self._rect = None
        res = dialog.request_window_fullscreen(True)
        self.assertEqual(window.calls, 1)
        self.assertFalse(res.get("fullscreen"))

    def test_cover_cleanup_runs_before_the_toggle(self):
        """The host's cleanup runs before the toggle, so the exit restores the real window."""
        window = _FakeWindow()
        self.register(window)
        self._rect = dict(SMALL)
        order = []

        def cleanup():
            order.append("cleanup")

        def cover():
            order.append("toggle")
            self._rect = dict(COVERED)

        old_cleanup = dialog._cover_cleanup
        dialog._cover_cleanup = cleanup
        try:
            with mock.patch.object(window, "toggle_fullscreen", side_effect=cover):
                dialog.request_window_fullscreen(True)
            self.assertEqual(order, ["cleanup", "toggle"])
            dialog.request_window_fullscreen(True)      # already covered -> no work
            self.assertEqual(order, ["cleanup", "toggle"])
        finally:
            dialog._cover_cleanup = old_cleanup

    def test_escape_signal_is_consumed_once(self):
        """The host's Escape news is handed to the page exactly once."""
        dialog._cover_escape = False
        self.assertFalse(dialog.consume_cover_escape())
        dialog.signal_cover_escape()
        self.assertTrue(dialog.consume_cover_escape())
        self.assertFalse(dialog.consume_cover_escape())

    def test_page_fill_state_reaches_the_host(self):
        """The page tells the host about 页面全屏, so Escape can leave it from anywhere."""
        dialog.set_page_fill(False)
        dialog._fullscreen = False
        self.assertFalse(dialog.fullscreen_on())
        dialog.set_page_fill(True)
        self.assertTrue(dialog.fullscreen_on())          # page enlargement alone counts
        dialog.set_page_fill(False)
        dialog._fullscreen = True                        # covered window counts as well
        self.assertTrue(dialog.fullscreen_on())
        dialog._fullscreen = False
        self.assertFalse(dialog.fullscreen_on())

    def test_covering_display_follows_the_measurement(self):
        """covering_display() answers from the real rectangle, not from a flag."""
        self.register(_FakeWindow())
        self._rect = dict(COVERED)
        self.assertTrue(dialog.covering_display())
        self._rect = dict(SMALL)
        self.assertFalse(dialog.covering_display())

    def test_window_gone_is_unavailable(self):
        """Shell present but its window already detached -> unavailable, no toggle."""
        dialog._shell = SimpleNamespace(_window=None, _hwnd=0)
        res = dialog.request_window_fullscreen(True)
        self.assertTrue(res.get("unavailable"))

    def test_status_follows_the_measurement(self):
        """window_fullscreen() reports the measurement, never a remembered flag."""
        self.register(_FakeWindow())
        self._rect = dict(COVERED)
        dialog._fullscreen = False
        self.assertTrue(dialog.window_fullscreen())
        self._rect = dict(SMALL)
        dialog._fullscreen = True
        self.assertFalse(dialog.window_fullscreen())


class HostWindowFullscreenEndpointTest(_BridgeCase):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        self.client = TestClient(m.app, base_url="http://127.0.0.1:6980")
        self.headers = {"origin": "http://127.0.0.1:6980"}

    def test_endpoint_unavailable_without_a_window(self):
        dialog._shell = None
        with mock.patch.object(m, "_window_geometry", return_value={}):
            body = self.client.post("/api/window/fullscreen", headers=self.headers).json()
        self.assertEqual(body, {"ok": True, "available": False, "fullscreen": False})

    def test_explicit_target_reaches_the_bridge(self):
        """The page may ask for a state, not only for a toggle."""
        seen = {}

        def fake_request(target=None):
            seen["target"] = target
            return {"available": True, "fullscreen": bool(target)}

        with mock.patch("backend.dialog.request_window_fullscreen", side_effect=fake_request):
            body = self.client.post("/api/window/fullscreen", headers=self.headers,
                                    json={"fullscreen": False}).json()
        self.assertEqual(seen["target"], False)
        self.assertFalse(body["fullscreen"])

    def test_status_publishes_the_rectangles(self):
        """The page gets the real rectangles so it never has to guess coverage."""
        with mock.patch.object(m, "_window_geometry",
                               return_value={"window": COVERED, "monitor": MONITOR,
                                             "covers": True}):
            with mock.patch.object(m, "_window_fullscreen", return_value=True):
                status = self.client.get("/api/status", headers=self.headers).json()
        self.assertTrue(status["window"]["fullscreen"])
        self.assertTrue(status["window"]["covers"])
        self.assertEqual(status["window"]["window"], COVERED)
        self.assertEqual(status["window"]["monitor"], MONITOR)


if __name__ == "__main__":
    unittest.main()
