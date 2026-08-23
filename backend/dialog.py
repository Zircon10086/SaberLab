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

_shell = None  # WebviewShell instance (registered by host.py in window mode)
_restart_fn = None  # Restart callback registered by host.py (invoked via /api/restart)

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
