"""Native window dialog bridge (shared state between __main__ and backend.main).

Background: when run.bat starts with `python backend\\host.py`, the script
runs as `__main__`; at that point `from backend.host import ...` in main.py
routes triggers a **second load** of host.py (a duplicate module), whose
module-level global state is out of sync with __main__ (this once caused
/api/settings/folder-dialog to always return unavailable).

This module serves as the single shared state: __main__ (host.py) registers
the window shell, and backend.main (FastAPI routes) reads it to pop native
dialogs.
"""
from __future__ import annotations

import threading
from typing import Optional

_shell = None  # WebviewShell instance (registered by host.py in window mode)
_restart_fn = None  # Restart callback registered by host.py (invoked via /api/restart)

# Window fullscreen state, owned by this bridge. The platform window is asked to cover its
# whole display (pywebview toggle_fullscreen) but does not report the result, so the bridge
# is the single owner: it remembers what it asked for, and /api/status publishes it for the
# frontend. Window mode only - browser mode has no window to cover.
_fullscreen = False
_fullscreen_lock = threading.Lock()
# Optional Win32 cleanup supplied by the host: called right after the window is covered, to
# make the borderless/exact-bounds state explicit (a leftover frame shows as a thin line
# along the screen edges - 2026-10 user report).
_cover_cleanup = None

# Set by the host's Escape watch when Escape is pressed while a fullscreen is on. Escape never
# reaches the page in that situation (measured 2026-10: with the replay focused, the key is
# consumed before any JavaScript in either document sees it), so the host has to notice it and
# hand the news over - that keeps one Escape working as "leave fullscreen" no matter where the
# focus is.
_cover_escape = False
_page_fill = False
_cover_escape_lock = threading.Lock()

# Frontend backdrop-ready flag: set by main.py when the frontend reports its
# acrylic layer is ready (page load / language-switch reload), consumed by the
# host wallpaper service thread to re-push the backdrop payload (2026-08 fix:
# a reload used to permanently lose the wallpaper background because the
# service thread only pushes on wallpaper/monitor changes).
_backdrop_ready = False
_backdrop_lock = threading.Lock()


def set_backdrop_ready() -> None:
    """Mark the frontend acrylic layer as ready (page load / reload).

    Called from the /api/desktop/backdrop-ready route (main.py); the host
    wallpaper service thread consumes it and re-pushes the payload.
    """
    global _backdrop_ready
    with _backdrop_lock:
        _backdrop_ready = True


def consume_backdrop_ready() -> bool:
    """Read and clear the ready flag (called by the host service thread)."""
    global _backdrop_ready
    with _backdrop_lock:
        v = _backdrop_ready
        _backdrop_ready = False
        return v


def register(shell) -> None:
    """Register the current active window shell (called by host.py main();
    pass None when exiting or falling back to the browser)."""
    global _shell
    _shell = shell


def register_restart(fn) -> None:
    """Register the app restart callback (called by host.py at startup)."""
    global _restart_fn
    _restart_fn = fn


def request_restart() -> dict:
    """Request an app restart (the "Restart SABER LAB" button on the settings page).

    Returns {"ok": True} (restart scheduled on a background thread) or
    {"ok": False, "error": ...}.
    """
    fn = _restart_fn
    if fn is None:
        return {"ok": False, "error": "当前运行模式不支持应用内重启"}
    try:
        fn()
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def request_folder_dialog() -> dict:
    """Pop a native folder picker dialog (pywebview FOLDER_DIALOG).

    Returns: {"selected": path} / {"cancelled": True} / {"unavailable": True}.
    """
    shell = _shell
    if shell is None or shell._window is None:
        return {"unavailable": True}
    import webview
    try:
        paths = shell._window.create_file_dialog(
            webview.FOLDER_DIALOG, allow_multiple=False,
            directory=getattr(shell, "_last_root", "") or "")
    except Exception as e:  # dialog interrupted, etc.
        return {"unavailable": True, "error": str(e)}
    if not paths:
        return {"cancelled": True}
    return {"selected": str(paths[0])}


def _window_geometry() -> Optional[dict]:
    """The window's own rectangle plus the rectangle of the display it sits on.

    Read from Win32 (backend.desktop) rather than asked of pywebview: this is the ground
    truth about whether the window currently covers its display, and it stays true when
    something outside the app moves or resizes the window.
    """
    shell = _shell
    if shell is None or shell._window is None:
        return None
    hwnd = getattr(shell, "_hwnd", None)
    if not hwnd:
        return None
    from backend import desktop
    win = desktop.get_window_rect(hwnd)
    mon = desktop.get_monitor_rect(hwnd)
    if not win or not mon:
        return None
    return {"window": win, "monitor": mon}


def covers_display(geom: Optional[dict] = None) -> bool:
    """True when the window rectangle equals the rectangle of its display."""
    geom = geom or _window_geometry()
    if not geom:
        return False
    win, mon = geom["window"], geom["monitor"]
    return (abs(win["x"] - mon["x"]) <= 1 and abs(win["y"] - mon["y"]) <= 1
            and abs(win["w"] - mon["w"]) <= 1 and abs(win["h"] - mon["h"]) <= 1)


def request_window_fullscreen(target: Optional[bool] = None) -> dict:
    """Make the window cover its display (target True), stop covering it (False), or just
    report/toggle when target is None.

    Whether the window is asked to change is decided by MEASURING it, not by a flag: the
    platform keeps its own idea of fullscreen, and a window that was moved or restored by
    anything else makes that idea wrong (2026-10: the app could believe it was covered while
    the window sat at its old size, so the button and Escape did nothing). Returns
    {"available": True, "fullscreen": bool, "window": rect, "monitor": rect} on window
    mode, {"unavailable": True} in browser mode, or {"error": ...} when the window refuses.
    """
    global _fullscreen
    shell = _shell
    if shell is None or shell._window is None:
        return {"unavailable": True}
    with _fullscreen_lock:
        geom = _window_geometry()
        covered = covers_display(geom)
        if target is None:
            wanted = not covered
        else:
            wanted = bool(target)
        if wanted != covered:
            # The Win32 cleanup runs BEFORE the toggle: the platform snapshots the window as
            # it is at that moment, so cleaning up afterwards would make the exit "restore"
            # the cleaned-up (borderless, display-sized) state instead of the real one.
            if wanted:
                cleanup = _cover_cleanup
                if cleanup is not None:
                    try:
                        cleanup()
                    except Exception:  # noqa: BLE001 - cosmetic pass, never fatal
                        pass
            try:
                shell._window.toggle_fullscreen()
            except Exception as e:  # noqa: BLE001 - window closing, platform refusal
                return {"unavailable": True, "error": str(e)}
            # re-measure: the window may not have moved (platform refusal, closing)
            geom = _window_geometry()
            covered = covers_display(geom)
        _fullscreen = covered
        answer = {"available": True, "fullscreen": covered}
        if geom:
            answer["window"] = geom["window"]
            answer["monitor"] = geom["monitor"]
        return answer


def window_fullscreen() -> bool:
    """Whether the window currently covers its display (False in browser mode).

    Measured, so it cannot drift away from the real window.
    """
    global _fullscreen
    if _shell is None or getattr(_shell, "_window", None) is None:
        return False
    _fullscreen = covers_display()
    return _fullscreen


def window_geometry() -> dict:
    """Window + display rectangles for the page, or {} without a window."""
    geom = _window_geometry()
    if not geom:
        return {}
    return {"window": geom["window"], "monitor": geom["monitor"],
            "covers": covers_display(geom)}


def set_cover_cleanup(fn) -> None:
    """Register the host's Win32 cleanup for the covered state (called by host.py)."""
    global _cover_cleanup
    _cover_cleanup = fn


def set_page_fill(on: bool) -> None:
    """Remember whether the page-level enlargement (页面全屏) is on.

    It is a page state, but leaving it must work exactly like leaving the covered window - one
    Escape - so the host has to know about it: with the replay focused the page never sees the
    key (see signal_cover_escape).
    """
    global _page_fill
    with _cover_escape_lock:
        _page_fill = bool(on)


def fullscreen_on() -> bool:
    """Whether any fullscreen is on: the window covering the display, or the page enlargement."""
    with _cover_escape_lock:
        return _page_fill or _fullscreen


def covering_display() -> bool:
    """Whether the window currently covers its display (measured, not remembered)."""
    return covers_display()


def signal_cover_escape() -> None:
    """The host noticed an Escape press while a fullscreen was on (its key watcher)."""
    global _cover_escape
    with _cover_escape_lock:
        _cover_escape = True


def consume_cover_escape() -> bool:
    """Read and clear that signal (called by the status route the page polls)."""
    global _cover_escape
    with _cover_escape_lock:
        if not _cover_escape:
            return False
        _cover_escape = False
        return True
