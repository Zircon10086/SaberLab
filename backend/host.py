"""SaberLab standalone window host (Phase 3, architecture review §6.2).

Responsibilities:
1. Port probing: default 6980; if occupied, increment 6980+1..+19 (eliminates startup failure from port conflicts)
2. Single instance: if an instance is already running, notify and exit (browser mode has no window control; simplified)
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

from backend.config import load_config, PROJECT_ROOT
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


def find_existing_instance(cfg, own_port: int) -> str | None:
    """Check whether a SaberLab instance is already running (sends /api/status to candidate ports).

    Parallel probe (2026-08 startup optimization): the serial loop waited up to
    0.5s per occupied-but-not-SaberLab port (worst case ~9.5s); now all
    candidates are probed concurrently with a shorter timeout, so startup is
    fast even when the fallback range is partially occupied.
    """
    start = int(getattr(cfg, "port", 6980) or 6980)
    candidates = [p for p in range(start, start + PORT_RANGE) if p != own_port]

    def probe(port: int) -> str | None:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/status", timeout=0.4) as r:
                if r.status == 200:
                    return f"http://127.0.0.1:{port}"
        except OSError:
            pass
        return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        for found in pool.map(probe, candidates):
            if found:
                return found
    return None


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

    cfg = load_config()
    port = find_free_port(cfg)
    url = f"http://127.0.0.1:{port}"
    # the frontend uses this to enable the acrylic layer (browser mode/off have no shell param; appearance unchanged)
    shell_url = url if args.acrylic_mode == "off" else url + "/?shell=webview"

    if not args.browser:
        existing = find_existing_instance(cfg, port)
        if existing:
            print(f"SaberLab is already running ({existing}). If the window is not visible, quit the old instance first.")
            print("(Add --browser to force opening a new browser page)")
            return 0

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

    # wait for the server to be ready (up to 15s)
    ready = False
    for _ in range(150):
        try:
            urllib.request.urlopen(url + "/api/status", timeout=0.5).read()
            ready = True
            break
        except OSError:
            time.sleep(0.1)
    if not ready:
        print("Server failed to start")
        return 1

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
        # active restart: the process is about to exit and the port is released → spawn the new process here (rebinds the original port)
        if getattr(_restart_app, "_armed", False):
            try:
                if getattr(sys, "frozen", False):
                    cmd = [sys.executable, *sys.argv[1:]]
                else:
                    cmd = [sys.executable, str(pathlib.Path(__file__).resolve()),
                           *sys.argv[1:]]
                import subprocess
                subprocess.Popen(cmd, cwd=str(PROJECT_ROOT),
                                 creationflags=getattr(subprocess,
                                                       "CREATE_NEW_CONSOLE", 0))
                print("[host] new process spawned", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[host] restart spawn failed: {e}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
