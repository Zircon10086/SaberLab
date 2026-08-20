"""桌面集成服务（毛玻璃方案 C 的后端，见 others/毛玻璃方案探索.md）。

职责：
1. 获取桌面壁纸文件路径（三级兜底，无需 Pillow/额外依赖）
2. 按窗口句柄取窗口矩形与所在显示器矩形（多显示器感知）
3. 生成前端毛玻璃层所需的几何/壁纸信息

全部使用 ctypes 调 Win32 API，跨版本（Win10/11）可用；浏览器模式下这些端点
无调用方，不产生任何影响。
"""
from __future__ import annotations

import ctypes
import pathlib
import winreg
from ctypes import wintypes
from typing import Optional

# ---------- Win32 常量 ----------
SPI_GETDESKWALLPAPER = 0x0073
SPI_GETDESKBKGND = 0x0074
MAX_PATH = 260

# GetMonitorInfoW 的 RECT（物理像素，含负坐标的多屏布局）
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
    """%APPDATA%\\Microsoft\\Windows\\Themes\\TranscodedWallpaper：
    系统实际显示的壁纸文件（单显示器时最可靠，恒为 JPEG）。"""
    import os
    base = os.environ.get("APPDATA")
    if not base:
        return None
    p = pathlib.Path(base) / "Microsoft" / "Windows" / "Themes" / "TranscodedWallpaper"
    return p if p.exists() else None


def get_wallpaper_path() -> Optional[pathlib.Path]:
    """桌面壁纸文件路径。兜底顺序：
    1. SPI_GETDESKWALLPAPER（当前用户的壁纸文件）
    2. 注册表 HKCU\\Control Panel\\Desktop\\WallPaper
    3. TranscodedWallpaper（系统转码缓存，实际渲染用的那份）
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
    """纯色桌面背景（无壁纸文件时兜底）。返回 "#RRGGBB"。"""
    color = wintypes.UINT()
    if _user32.SystemParametersInfoW(SPI_GETDESKBKGND, 0, ctypes.byref(color), 0):
        rgb = color.value
        r, g, b = rgb & 0xFF, (rgb >> 8) & 0xFF, (rgb >> 16) & 0xFF
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def get_window_rect(hwnd: int) -> Optional[dict]:
    """窗口矩形（物理像素，屏幕坐标）。"""
    rect = RECT()
    if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return {"x": rect.left, "y": rect.top,
            "w": rect.right - rect.left, "h": rect.bottom - rect.top}


def get_monitor_rect(hwnd: int) -> Optional[dict]:
    """窗口所在显示器的矩形（物理像素，屏幕坐标，支持多屏负坐标）。"""
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
    """按窗口标题找句柄（host.py 在 webview 窗口创建后定位它）。

    优先精确匹配；找不到时返回 None（host 侧另有 pywebview 窗口对象可用）。
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

    # EnumWindows 回调原型
    CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows(CALLBACK(enum_cb), 0)
    return found[0] if found else None


def backdrop_payload(hwnd: int, wallpaper_url: str) -> dict:
    """方案 C 的核心数据：窗口/显示器几何 + 壁纸地址。

    前端拿到后：background-image=wallpaper_url，
    background-size = monitor.w × monitor.h（CSS 逻辑像素需除以 devicePixelRatio），
    background-position = -(window.x - monitor.x) / dpr 等 → 恰好显示窗口遮盖区域。
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
        # 窗口是否完全在显示器内（跨屏/半屏时前端自动裁剪）
        "fully_visible": (
            win["x"] >= mon["x"] and win["y"] >= mon["y"] and
            win["x"] + win["w"] <= mon["x"] + mon["w"] and
            win["y"] + win["h"] <= mon["y"] + mon["h"]
        ),
    }
