"""Desktop integration service (backend of acrylic scheme C; see the acrylic-scheme exploration doc at others/毛玻璃方案探索.md).

Responsibilities:
1. Get the desktop wallpaper file path (three-level fallback; no Pillow/extra dependencies needed)
2. Get the window rect and its monitor rect from the window handle (multi-monitor aware)
3. Generate the geometry/wallpaper info the frontend acrylic layer needs

Everything calls Win32 APIs via ctypes and works across versions (Win10/11); in browser mode these endpoints
have no callers and produce no effect.
"""
from __future__ import annotations

import ctypes
import pathlib
import winreg
from ctypes import wintypes
from typing import Optional

# ---------- Win32 constants ----------
SPI_GETDESKWALLPAPER = 0x0073
SPI_GETDESKBKGND = 0x0074
MAX_PATH = 260

# RECT as used by GetMonitorInfoW (physical pixels; supports multi-screen layouts with negative coordinates)
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

MONITOR_DEFAULTTONEAREST = 2


def _transcoded_wallpaper() -> Optional[pathlib.Path]:
    """%APPDATA%\\Microsoft\\Windows\\Themes\\TranscodedWallpaper:
    the wallpaper file actually displayed by the system (most reliable on a single monitor; always JPEG)."""
    import os
    base = os.environ.get("APPDATA")
    if not base:
        return None
    p = pathlib.Path(base) / "Microsoft" / "Windows" / "Themes" / "TranscodedWallpaper"
    return p if p.exists() else None


def get_wallpaper_path() -> Optional[pathlib.Path]:
    """Desktop wallpaper file path. Fallback order:
    1. SPI_GETDESKWALLPAPER (current user's wallpaper file)
    2. Registry HKCU\\Control Panel\\Desktop\\WallPaper
    3. TranscodedWallpaper (system transcoded cache — the one actually rendered)
    """
    buf = ctypes.create_unicode_buffer(MAX_PATH)
    if _user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, MAX_PATH, buf, 0):
        p = pathlib.Path(buf.value)
        if p.exists():
            return p
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Control Panel\Desktop") as key:
            value, _ = winreg.QueryValueEx(key, "WallPaper")
        if value:
            p = pathlib.Path(value)
            if p.exists():
                return p
    except OSError:
        pass
    return _transcoded_wallpaper()


def get_desktop_background_color() -> Optional[str]:
    """Solid-color desktop background (fallback when there is no wallpaper file). Returns "#RRGGBB"."""
    color = wintypes.UINT()
    if _user32.SystemParametersInfoW(SPI_GETDESKBKGND, 0, ctypes.byref(color), 0):
        rgb = color.value
        r, g, b = rgb & 0xFF, (rgb >> 8) & 0xFF, (rgb >> 16) & 0xFF
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def get_window_rect(hwnd: int) -> Optional[dict]:
    """Window rect (physical pixels, screen coordinates)."""
    rect = RECT()
    if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return {"x": rect.left, "y": rect.top,
            "w": rect.right - rect.left, "h": rect.bottom - rect.top}


def get_monitor_rect(hwnd: int) -> Optional[dict]:
    """Rect of the monitor containing the window (physical pixels, screen coordinates; supports negative multi-screen coordinates)."""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    monitor = _user32.MonitorFromWindow(wintypes.HWND(hwnd),
                                        MONITOR_DEFAULTTONEAREST)
    if not monitor or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    m = info.rcMonitor
    return {"x": m.left, "y": m.top,
            "w": m.right - m.left, "h": m.bottom - m.top}


def find_window_by_title(title: str) -> Optional[int]:
    """Find the window handle by title (host.py uses it to locate the webview window after creation).

    Prefers an exact match; returns None if not found (the host side also has the pywebview window object available).
    """
    found: list[int] = []

    def enum_cb(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(MAX_PATH)
        _user32.GetWindowTextW(hwnd, buf, MAX_PATH)
        if buf.value == title:
            found.append(hwnd)
            return False
        return True

    # EnumWindows callback prototype
    CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows(CALLBACK(enum_cb), 0)
    return found[0] if found else None


def backdrop_payload(hwnd: int, wallpaper_url: str) -> dict:
    """Core data for scheme C: window/monitor geometry + wallpaper URL.

    The frontend then applies: background-image=wallpaper_url,
    background-size = monitor.w × monitor.h (CSS logical pixels must be divided by devicePixelRatio),
    background-position = -(window.x - monitor.x) / dpr etc. → exactly displays the window-covered area.
    """
    win = get_window_rect(hwnd)
    mon = get_monitor_rect(hwnd)
    if not win or not mon:
        return {"available": False}
    return {
        "available": True,
        "wallpaper_url": wallpaper_url,
        "window": win,
        "monitor": mon,
        # whether the window is fully inside the monitor (frontend auto-crops when spanning/half off-screen)
        "fully_visible": (
            win["x"] >= mon["x"] and win["y"] >= mon["y"] and
            win["x"] + win["w"] <= mon["x"] + mon["w"] and
            win["y"] + win["h"] <= mon["y"] + mon["h"]
        ),
    }
