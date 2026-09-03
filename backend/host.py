"""SaberLab standalone window host (Phase 3, architecture review §6.2).

Responsibilities:
1. Single instance: replace a verified SaberLab listener left on 6980..6999,
   then prefer the configured port (6980 by default); unrelated occupants are
   never killed and still cause safe fallback to the next port
3. uvicorn runs in a daemon thread; window close → should_exit → process exits
4. Dual modes:
   - Default (webview): pywebview 5/6 + WebView2 opens its own window without launching the system browser
   - --browser: matches legacy run.bat behavior (launches the system browser), for development/fallback
5. Acrylic glass (see the acrylic-scheme exploration doc at others/毛玻璃方案探索.md):
   - Production default = wallpaper push scheme C (backend geometry/wallpaper + frontend CSS blur); window move/resize
     notifies the frontend via evaluate_js to refresh background-position
   - Measured conclusion (2026-08-21): pywebview 6.2.1 transparent windows have no true window transparency
     (the client area is covered by the form's BackColor gray; BackColor=Transparent/TransparencyKey
     remedies are both ineffective), DWM system backdrop (Mica/Acrylic) is only visible on the title bar
   - backdrop / acrylic / --acrylic-legacy are kept as experimental switches

Design principle: the HTTP API is the only IPC (the frontend does not call pywebview js_api), ensuring the same
frontend behaves identically in window mode and browser mode.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import socket
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes

import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import uvicorn

import os

from backend import APP_INSTANCE_ID
from backend.config import load_config, dotenv_key_names, PROJECT_ROOT
from backend import desktop


def _setup_stdio() -> None:
    """Console-less startup support (pythonw.exe / frozen --windowed):
    sys.stdout/stderr are None there, so any print() would crash; redirect
    them to data/logs/saberlab.log (append) to keep the logs inspectable.
    Console mode (plain `python backend\\host.py`) is untouched.
    """
    if sys.stdout is not None:
        return
    log_dir = PROJECT_ROOT / "data" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "saberlab.log"
        f = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
        print(f"--- SaberLab start {time.strftime('%Y-%m-%d %H:%M:%S')} "
              f"(console hidden, log: {log_path}) ---", flush=True)
    except OSError:
        # No console and no writable log dir: silence prints instead of crashing
        class _Null:
            def write(self, *a, **k):  # noqa: D102
                pass
            def flush(self, *a, **k):  # noqa: D102
                pass
        sys.stdout = sys.stderr = _Null()


# Pass the app object directly (instead of the "backend.main:app" import string):
# PyInstaller static analysis collects modules via imports; import strings are invisible,
# and in a frozen environment uvicorn would report "Could not import module backend.main"
from backend.main import app

WINDOW_TITLE = "SaberLab — Beat Saber 本地分析实验室"
PORT_RANGE = 20  # 6980..6999
_TCP_TABLE_OWNER_PID_LISTENER = 3
_MIB_TCP_STATE_LISTEN = 2
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x00000080
_STARTUP_MUTEX_NAME = r"Local\SaberLab.Startup.ReplaceInstance"


def find_free_port(cfg) -> int:
    """Probe for the first free port starting from the configured port."""
    start = int(getattr(cfg, "port", 6980) or 6980)
    for port in range(start, start + PORT_RANGE):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"端口 {start}..{start + PORT_RANGE - 1} 全部被占用")


class _MibTcpRowOwnerPid(ctypes.Structure):
    _fields_ = [("state", wintypes.DWORD),
                ("local_addr", wintypes.DWORD),
                ("local_port", wintypes.DWORD),
                ("remote_addr", wintypes.DWORD),
                ("remote_port", wintypes.DWORD),
                ("pid", wintypes.DWORD)]


def _listener_pid(port: int) -> int | None:
    """Return the Windows PID listening on 127.0.0.1:port (stdlib only)."""
    if sys.platform != "win32":
        return None
    size = wintypes.DWORD(0)
    get_table = ctypes.windll.iphlpapi.GetExtendedTcpTable
    get_table.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                          wintypes.BOOL, wintypes.ULONG, wintypes.ULONG,
                          wintypes.ULONG]
    get_table.restype = wintypes.DWORD
    get_table(None, ctypes.byref(size), False, socket.AF_INET,
              _TCP_TABLE_OWNER_PID_LISTENER, 0)
    if not size.value:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if get_table(buf, ctypes.byref(size), False, socket.AF_INET,
                 _TCP_TABLE_OWNER_PID_LISTENER, 0) != 0:
        return None
    count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
    base = ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD)
    row_size = ctypes.sizeof(_MibTcpRowOwnerPid)
    for i in range(count):
        row = _MibTcpRowOwnerPid.from_address(base + i * row_size)
        local_port = socket.ntohs(row.local_port & 0xFFFF)
        if row.state == _MIB_TCP_STATE_LISTEN and local_port == port:
            return int(row.pid)
    return None


def _looks_like_saberlab_status(data) -> bool:
    """Recognize current instances and pre-marker SaberLab releases."""
    if not isinstance(data, dict) or data.get("ok") is not True:
        return False
    if data.get("app_instance") == APP_INSTANCE_ID:
        return True
    # Upgrade compatibility: older releases lack app_instance/pid. Require the
    # distinctive status shape before treating the listener as SaberLab.
    db = data.get("db")
    config = data.get("config")
    ai = data.get("ai")
    chro = data.get("chro")
    return (isinstance(db, dict)
            and "replays" in db and "maps" in db
            and isinstance(data.get("replay_dir"), dict)
            and isinstance(data.get("maps_dir"), dict)
            and isinstance(ai, dict) and "provider" in ai
            and isinstance(chro, dict) and "available" in chro
            and isinstance(config, dict)
            and "replay_dir" in config and "custom_levels_dir" in config)


def _probe_saberlab(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/status", timeout=0.6) as r:
            if r.status != 200:
                return None
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return data if _looks_like_saberlab_status(data) else None


def _terminate_process(pid: int, timeout_ms: int = 5000) -> bool:
    """Terminate a previously verified SaberLab process and wait for exit."""
    if sys.platform != "win32" or pid <= 0 or pid == os.getpid():
        return False
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE | _SYNCHRONIZE,
                                  False, wintypes.DWORD(pid))
    if not handle:
        return False
    try:
        if not kernel32.TerminateProcess(handle, 0):
            return False
        return kernel32.WaitForSingleObject(handle, timeout_ms) == _WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def replace_existing_instances(cfg) -> list[tuple[int, int]]:
    """Replace verified SaberLab listeners in the configured port range.

    Identity and ownership are checked independently: /api/status must identify
    SaberLab, and its PID (when present) must equal the Windows TCP owner PID.
    An unrelated service on 6980 is never terminated.
    """
    ports = _candidate_ports(cfg)
    owners_before = {port: _listener_pid(port) for port in ports}
    occupied = [port for port in ports if owners_before[port]]
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = dict(zip(occupied, pool.map(_probe_saberlab, occupied)))
    replaced: list[tuple[int, int]] = []
    for port in occupied:
        status = statuses[port]
        if status is None:
            continue
        pid = owners_before[port]
        # Close the time-of-check/time-of-use gap: the listener that answered
        # the SaberLab status probe must still own the port before termination.
        if _listener_pid(port) != pid:
            print(f"[host] listener changed during probe on {port}; ignored",
                  flush=True)
            continue
        reported_pid = status.get("pid")
        try:
            reported_pid = (int(reported_pid)
                            if reported_pid is not None else None)
        except (TypeError, ValueError):
            reported_pid = -1
        if not pid or (reported_pid is not None and reported_pid != pid):
            print(f"[host] ignored unverifiable SaberLab response on {port}",
                  flush=True)
            continue
        if _terminate_process(pid):
            replaced.append((port, pid))
            print(f"[host] replaced old SaberLab instance pid={pid} port={port}",
                  flush=True)
        elif _listener_pid(port) == pid:
            # Starting on a fallback port here would violate the single-window
            # guarantee and recreate the invisible-background-instance problem.
            raise RuntimeError(
                f"无法终止旧 SaberLab 实例 pid={pid}（端口 {port}）")
    return replaced


def _candidate_ports(cfg) -> list[int]:
    """Standard and custom port ranges that may contain an old instance."""
    configured = int(getattr(cfg, "port", 6980) or 6980)
    return sorted(set(range(6980, 6980 + PORT_RANGE))
                  | set(range(configured, configured + PORT_RANGE)))


def _acquire_startup_mutex(timeout_ms: int = 15000):
    """Serialize concurrent launchers through instance replacement + bind."""
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                      wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, _STARTUP_MUTEX_NAME)
    if not handle:
        raise RuntimeError("无法创建 SaberLab 启动锁")
    wait = kernel32.WaitForSingleObject(handle, timeout_ms)
    if wait not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        kernel32.CloseHandle(handle)
        raise RuntimeError("等待另一个 SaberLab 启动操作超时")
    return handle


def _release_startup_mutex(handle) -> None:
    if handle and sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


# ---------- Acrylic glass: DWM capability (scheme A/B, Windows only) ----------
def try_dwm_backdrop(hwnd: int, value: int) -> bool:
    """Try the official DWM system backdrop (scheme B: Win11 22H2+).

    DWMWA_SYSTEMBACKDROP_TYPE = 38; value: 1=None 2=Mica 3=Acrylic(TransientWindow).
    Mica always renders (including when unfocused); Acrylic is auto-removed by DWM when unfocused
    (not friendly to persistent tool windows).
    Returns success; on failure (older systems) the caller falls back to scheme C.
    """
    try:
        attr = ctypes.c_int(value)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), 38, ctypes.byref(attr),
            ctypes.sizeof(attr))
        return result == 0
    except (AttributeError, OSError):
        return False


def try_legacy_acrylic(hwnd: int, tint: int = 0x99_30_30_30) -> bool:
    """Experimental switch --acrylic-legacy: undocumented API SetWindowCompositionAttribute
    (scheme A, the same one ExplorerBlur uses). GradientColor is AABBGGRR."""
    class AccentPolicy(ctypes.Structure):
        _fields_ = [("AccentState", ctypes.c_int),
                    ("AccentFlags", ctypes.c_int),
                    ("GradientColor", ctypes.c_uint),
                    ("AnimationId", ctypes.c_int)]

    class WindowCompositionAttributeData(ctypes.Structure):
        _fields_ = [("Attribute", ctypes.c_int),
                    ("Data", ctypes.c_void_p),
                    ("SizeOfData", ctypes.c_size_t)]

    accent = AccentPolicy(4, 2, tint, 0)  # 4=ACCENT_ENABLE_ACRYLICBLURBEHIND
    data = WindowCompositionAttributeData(19, ctypes.cast(
        ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))
    try:
        result = ctypes.windll.user32.SetWindowCompositionAttribute(
            wintypes.HWND(hwnd), ctypes.byref(data))
        return bool(result)
    except (AttributeError, OSError):
        return False


# The currently active window shell is registered through the backend.dialog bridge
# (host.py runs as __main__; if main.py imports backend.host directly it gets a duplicate module
# whose global state is not synchronized — see dialog.py)
from backend import dialog


class WebviewShell:
    """pywebview window shell: lazy import; browser mode never touches pywebview."""

    def __init__(self, url: str, use_acrylic_legacy: bool,
                 acrylic_mode: str = "auto"):
        import webview
        self._webview = webview
        self._url = url
        self._use_acrylic_legacy = use_acrylic_legacy
        self._acrylic_mode = acrylic_mode  # requested mode: auto|backdrop|wallpaper|off
        self._window = None
        self._last_root = ""  # starting directory of the native folder dialog (current game root)
        self._effective_mode = None  # effective mode after probing: backdrop | wallpaper
        self._service_thread = None  # wallpaper service thread (initial push + 1s polling)
        self._last_wallpaper_sig = None  # (path, mtime_ns, size)
        self._last_monitor = None       # monitor geometry (cross-screen / plug-unplug detection)
        self._diag_done = False
        # — Move masking (round 3): moved/resized → push "moving" to the frontend (max blur);
        #   recovery with two safeguards (round 4 fix):
        #   ① Backend 0.5s polling watchdog: push False once ≥1s since the last move (auto-retries on failure);
        #   ② Frontend fallback: throttled refresh on backend True signals (≤500ms apart); 1.5s after the last
        #      True, the frontend unconditionally self-recovers (does not rely on rAF — rendering may pause
        #      during a drag, making position detection unreliable; also does not rely on a single False push) —
        self._moving_reported = False
        self._last_move_ts = 0.0
        self._last_push_ts = 0.0
        self._move_watchdog = None

    def _on_window_moving(self):
        """Window move/resize event (moved/resized): mark as "moving" and push True;
        throttle-refresh at most once per 500ms during a drag (keeps the frontend fallback timer alive);
        recovery is handled by the watchdog + frontend fallback double safeguard."""
        now = time.time()
        self._last_move_ts = now
        if not self._moving_reported:
            self._moving_reported = True
            self._last_push_ts = now
            self._push_moving(True)
        elif now - self._last_push_ts >= 0.5:
            self._last_push_ts = now
            self._push_moving(True)

    def _start_move_watchdog(self):
        """Recovery watchdog: check every 0.5s — if marked as moving and ≥1s has passed since the
        last move event, push False (frontend restores blur). Push failures are silent and retried on
        the next round; no longer depends on a single Timer."""
        if self._move_watchdog is not None:
            return

        def watch():
            while self._window:
                time.sleep(0.5)
                if (self._moving_reported and
                        time.time() - self._last_move_ts >= 1.0):
                    self._moving_reported = False
                    print("[host] movement stopped → restoring blur", flush=True)
                    self._push_moving(False)

        self._move_watchdog = threading.Thread(target=watch, daemon=True)
        self._move_watchdog.start()

    def _push_moving(self, moving: bool):
        if not self._window:
            return
        try:
            self._window.evaluate_js(
                "window.__saberlabBackdropMoving && "
                f"window.__saberlabBackdropMoving({str(moving).lower()})")
        except Exception:
            pass

    def _native_handle(self) -> int | None:
        try:
            hwnd = int(self._window.native.Handle)
            if hwnd:
                return hwnd
        except (AttributeError, TypeError):
            pass
        return desktop.find_window_by_title(WINDOW_TITLE)

    def _wait_hwnd(self, timeout: float = 2.0) -> int | None:
        """The window handle may not be ready the instant the shown event fires (transparent-window
        show/hide hack); poll briefly to avoid misjudging it as "DWM unavailable" and falling back
        to scheme C."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            hwnd = self._native_handle()
            if hwnd:
                return hwnd
            time.sleep(0.1)
        return None

    def _on_shown(self):
        # Startup timing (2026-08 optimization tracking): window visible ≈
        # webview.start() returned control to the GUI loop.
        if getattr(self, "_t_start", None) is not None:
            print(f"[host] webview window shown in "
                  f"{time.perf_counter() - self._t_start:.2f}s", flush=True)
        # Acrylic scheme decision (2026-08 measured conclusions):
        # - auto goes straight to scheme C (wallpaper push): measured pywebview 6.2.1 transparent mode
        #   does not produce real window transparency (the client area is always covered by the WinForms
        #   form BackColor, measured as #f0f0f0 gray); DWM backdrop (Mica/Acrylic) is not visible in the
        #   client area; both BackColor=Transparent / TransparencyKey remedies are ineffective (probe-verified).
        #   Scheme C needs no window transparency, and the effect has been confirmed by users.
        # - backdrop/acrylic/--acrylic-legacy remain experimental switches (the title bar's DWM effect
        #   comes from the pywebview dark theme Mica; the client area is primarily scheme C).
        if self._acrylic_mode == "off":
            print("[host] acrylic: off (acrylic disabled, baseline appearance)")
            return
        hwnd = self._wait_hwnd()
        if self._acrylic_mode in ("backdrop", "acrylic") and hwnd:
            if self._use_acrylic_legacy and try_legacy_acrylic(hwnd):
                self._effective_mode = "backdrop"
                print("[host] acrylic: backdrop(A) experimental real Acrylic SetWindowCompositionAttribute")
            elif self._acrylic_mode == "acrylic":
                if try_dwm_backdrop(hwnd, 3):
                    self._effective_mode = "backdrop"
                    print("[host] acrylic: backdrop(B-acrylic) experimental Acrylic(3) disappears when unfocused")
                else:
                    self._effective_mode = "wallpaper"
                    print("[host] acrylic: backdrop(B-acrylic) failed → wallpaper(C)")
            elif try_dwm_backdrop(hwnd, 2):
                self._effective_mode = "backdrop"
                print("[host] acrylic: backdrop(B-mica) experimental Mica(2) (client area still uses C)")
            else:
                self._effective_mode = "wallpaper"
                print("[host] acrylic: backdrop(B-mica) failed → wallpaper(C)")
        else:
            # auto / wallpaper: scheme C (wallpaper push + frontend blur), production default
            self._effective_mode = "wallpaper"
            if self._acrylic_mode == "auto":
                print("[host] acrylic: wallpaper(C) wallpaper push (auto default, pywebview has no real transparency)")
            elif self._acrylic_mode == "wallpaper":
                print("[host] acrylic: wallpaper(C) wallpaper push (forced)")
            else:
                print("[host] acrylic: wallpaper(C) could not get window handle")
        self._start_wallpaper_service()
        self._start_move_watchdog()

    def _wallpaper_url(self) -> str | None:
        """Wallpaper URL (with a version number to force the browser to bypass cache; the frontend
        swaps images based on this when the wallpaper changes)."""
        wp = desktop.get_wallpaper_path()
        if not wp:
            return None
        try:
            v = int(wp.stat().st_mtime_ns)
        except OSError:
            v = 0
        return f"/api/desktop/wallpaper?v={v}"

    def _start_wallpaper_service(self):
        """Wallpaper service thread (scheme C round 2 improvement, 2026-08-21):

        1. Wait for the frontend to register window.__saberlabBackdrop (up to 20×0.5s)
        2. Push the initial payload (monitor geometry + wallpaper URL + mode)
        3. Poll every 1s: wallpaper file (path/mtime/size) or monitor geometry changes
           → push a new payload (slideshow wallpaper switching, cross-screen movement scenarios)
        4. Window position tracking no longer uses moved/resized pushes (synchronous evaluate_js
           blocking is the root cause of drag lag) — the frontend reads screenX/screenY locally
           every frame for cropping.

        5. Frontend reload (page load / language switch) re-registers
           __saberlabBackdrop — the /api/desktop/backdrop-ready flag is consumed
           every second and triggers a re-push of the initial payload
           (2026-08 fix: switching language broke the glass). backdrop
           (experimental) mode also responds to reload re-pushes, but skips
           wallpaper polling.
        """
        if self._service_thread is not None:
            return

        def service():
            ready = False
            for _ in range(20):
                if not self._window:
                    return
                try:
                    ready = self._window.evaluate_js(
                        'typeof window.__saberlabBackdrop === "function"')
                except Exception:
                    ready = False
                if ready:
                    break
                time.sleep(0.5)
            if not ready:
                return
            self._notify_backdrop()
            while self._window:
                time.sleep(1.0)
                # Frontend reload (e.g. language switch) re-registers
                # __saberlabBackdrop, but wallpaper/monitor did not change so
                # the poll below would never re-push — consume the ready flag
                # set by /api/desktop/backdrop-ready and re-push the initial
                # payload (2026-08 fix: switching language broke the glass).
                if dialog.consume_backdrop_ready():
                    print("[host] frontend ready (reload) → re-pushing backdrop",
                          flush=True)
                    self._notify_backdrop()
                if self._effective_mode != "wallpaper":
                    # backdrop (experimental) mode: no wallpaper polling, but
                    # still respond to reload re-push requests above.
                    continue
                changed = False
                wp = desktop.get_wallpaper_path()
                sig = None
                if wp:
                    try:
                        st = wp.stat()
                        sig = (str(wp), st.st_mtime_ns, st.st_size)
                    except OSError:
                        sig = None
                if sig != self._last_wallpaper_sig:
                    self._last_wallpaper_sig = sig
                    changed = True
                hwnd = self._native_handle()
                mon = desktop.get_monitor_rect(hwnd) if hwnd else None
                if mon != self._last_monitor:
                    self._last_monitor = mon
                    changed = True
                if changed:
                    print("[host] wallpaper/monitor changed → pushing new backdrop")
                    self._notify_backdrop()

        self._service_thread = threading.Thread(target=service, daemon=True)
        self._service_thread.start()

    def _notify_backdrop(self):
        if not self._window:
            return
        import json as _json
        if self._effective_mode == "backdrop":
            # scheme A/B: DWM handles the background; the frontend only needs to make its background transparent
            payload = {"mode": "backdrop"}
        else:
            # scheme C: wallpaper push (frontend does alignment + blur)
            hwnd = self._native_handle()
            payload = desktop.backdrop_payload(
                hwnd, self._wallpaper_url()) if hwnd else {"available": False}
            payload["mode"] = "wallpaper"
            if hwnd:
                wallpaper = desktop.get_wallpaper_path()
                if wallpaper is None:
                    payload["background_color"] = \
                        desktop.get_desktop_background_color()
            if not self._diag_done:
                self._diag_done = True
                if payload.get("available"):
                    print("[host] backdrop geometry (physical pixels, dpr converted by frontend): "
                          f"window={payload['window']} monitor={payload['monitor']}")
                else:
                    print("[host] backdrop unavailable (could not get window/monitor geometry) → frontend solid-color fallback")
        js = ("window.__saberlabBackdrop && "
              f"window.__saberlabBackdrop({_json.dumps(payload)})")
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass

    def start(self):
        webview = self._webview
        # auto/wallpaper/off use a normal opaque window (scheme C draws the wallpaper layer in-page; no transparency needed);
        # backdrop/acrylic (experimental) enable the transparent window — note that measured pywebview transparent windows
        # show the form BackColor gray in the client area, and the DWM backdrop is only visible on the title bar.
        transparent = self._acrylic_mode in ("backdrop", "acrylic")
        self._t_start = time.perf_counter()
        self._window = webview.create_window(
            WINDOW_TITLE, self._url, width=1440, height=900,
            min_size=(1024, 640), confirm_close=True, transparent=transparent)
        # moved/resized are only used to notify the "moving" state (max-blur masking);
        # position tracking still comes from the frontend reading screenX/screenY every frame (round 3 division of labor).
        self._window.events.shown += self._on_shown
        self._window.events.moved += self._on_window_moving
        self._window.events.resized += self._on_window_moving
        webview.start()


def main():
    _setup_stdio()   # console-less startup (pythonw / frozen windowed): redirect prints to data/logs/saberlab.log
    parser = argparse.ArgumentParser(description="SaberLab host")
    parser.add_argument("--browser", action="store_true",
                        help="弹系统浏览器（不启动 webview 窗口）")
    parser.add_argument("--acrylic-legacy", action="store_true",
                        help="实验：使用未文档 API 的真 Acrylic（方案 A）")
    parser.add_argument("--acrylic-mode", choices=["auto", "backdrop",
                                                   "acrylic", "wallpaper",
                                                   "off"],
                        default="auto",
                        help="毛玻璃方案：auto=壁纸推送方案 C（生产默认，无需透明窗口；"
                             "实测 pywebview 透明模式无真透明，DWM 背景板仅标题栏可见）；"
                             "backdrop=实验：强制 DWM Mica(2)；acrylic=实验：强制 DWM "
                             "Acrylic(3)（失焦消失）；wallpaper=强制壁纸推送方案 C；"
                             "off=不启用毛玻璃（对照外观）")
    args = parser.parse_args()
    t0 = time.perf_counter()

    startup_mutex = _acquire_startup_mutex()
    try:
        cfg = load_config()
        replace_existing_instances(cfg)
        port = find_free_port(cfg)
        url = f"http://127.0.0.1:{port}"
        # the frontend uses this to enable the acrylic layer (browser mode/off have no shell param; appearance unchanged)
        shell_url = url if args.acrylic_mode == "off" else url + "/?shell=webview"

        config = uvicorn.Config(app=app, host="127.0.0.1", port=port,
                                log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        # Startup optimization (2026-08): pywebview loads the .NET runtime + WebView2
        # only inside start(); preload them in parallel while the server warms up,
        # so the window appears ~immediately after readiness (window mode only).
        webview_preload = threading.Event()

        def _preload_webview():
            try:
                from webview.platforms import winforms  # noqa: F401  # loads clr/.NET runtime
            except Exception:  # noqa: BLE001
                pass
            finally:
                webview_preload.set()

        if not args.browser:
            threading.Thread(target=_preload_webview, daemon=True).start()

        # wait for the server to be ready (up to 15s). The startup mutex stays
        # held through readiness, so a concurrent launcher cannot race the bind.
        ready = False
        for _ in range(150):
            try:
                urllib.request.urlopen(url + "/api/status", timeout=0.5).read()
                ready = True
                break
            except OSError:
                time.sleep(0.1)
        if not ready:
            server.should_exit = True
            print("Server failed to start")
            return 1
    finally:
        _release_startup_mutex(startup_mutex)

    print(f"SaberLab: {url}  (replay dir: {cfg.replay_dir})")
    print(f"[host] ready in {time.perf_counter() - t0:.2f}s (server up)", flush=True)

    shell = None   # window shell (not created in browser mode; the _restart_app closure reference must be initialized first)

    # in-app restart: settings page "RESTART SABER LAB" button (/api/restart → dialog bridge)
    def _restart_app():
        if getattr(_restart_app, "_armed", False):
            return   # prevent duplicate triggers
        _restart_app._armed = True
        print("[host] restart requested, closing window and spawning a new process...", flush=True)
        # Window mode: destroy the window first — webview.start() blocks the main thread,
        # so stopping only uvicorn would not let the process exit (root cause of the leftover-window bug)
        try:
            if not args.browser and shell is not None and shell._window is not None:
                shell._window.destroy()
        except Exception:  # noqa: BLE001
            pass
        # stop uvicorn → main() reaches finally (port released) → spawn the new process in finally
        try:
            server.should_exit = True
        except Exception:  # noqa: BLE001
            pass

    dialog.register_restart(_restart_app)

    if args.browser:
        import webbrowser
        webbrowser.open(url)
        try:
            thread.join()
        except KeyboardInterrupt:
            pass
        return 0

    shell = WebviewShell(shell_url, args.acrylic_legacy, args.acrylic_mode)
    shell._last_root = cfg.instance_root or ""
    dialog.register(shell)   # so /api/settings/folder-dialog can open the native folder dialog
    try:
        # Wait for the parallel webview preload (bounded; it runs while the
        # server warms up, so this is normally already done).
        webview_preload.wait(timeout=5)
        t_win = time.perf_counter()
        shell.start()
    except Exception as e:  # pywebview unavailable (e.g. missing WebView2) → browser fallback
        print(f"[host] webview window failed to start ({e}), falling back to browser mode")
        dialog.register(None)
        import webbrowser
        webbrowser.open(url)
        try:
            thread.join()
        except KeyboardInterrupt:
            pass
    finally:
        dialog.register(None)
        server.should_exit = True
        # Release the listening socket before an in-app restart spawns its
        # successor; otherwise the child can briefly see an unresponsive 6980
        # and relocate even though this instance is already shutting down.
        if thread.is_alive():
            thread.join(timeout=5)
        # active restart: the process is about to exit and the port is released → spawn the new process here (rebinds the original port)
        if getattr(_restart_app, "_armed", False):
            try:
                if getattr(sys, "frozen", False):
                    cmd = [sys.executable, *sys.argv[1:]]
                else:
                    cmd = [sys.executable, str(pathlib.Path(__file__).resolve()),
                           *sys.argv[1:]]
                import subprocess
                # Strip .env-provided vars from the inherited environment: the
                # child's load_dotenv never overrides existing vars, so a stale
                # value (e.g. an API key loaded before the user saved a new one)
                # would otherwise win forever across restarts (2026-08 fix).
                env = {k: v for k, v in os.environ.items()
                       if k not in dotenv_key_names()}
                subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=env,
                                 creationflags=getattr(subprocess,
                                                       "CREATE_NEW_CONSOLE", 0))
                print("[host] new process spawned", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[host] restart spawn failed: {e}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
