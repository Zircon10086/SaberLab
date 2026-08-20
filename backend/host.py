"""SaberLab 独立窗口宿主（阶段 3，架构审查 §6.2）。

职责：
1. 端口探测：默认 8787，被占用则 8787+1..+19 递增（消除端口冲突启动失败）
2. 单实例：检测到已有实例在跑 → 提示并退出（浏览器模式下无窗口控制，简化版）
3. uvicorn 运行于守护线程；窗口关闭 → should_exit → 进程退出
4. 双模式：
   - 默认（webview）：pywebview 5/6 + WebView2 开自有窗口，不弹系统浏览器
   - --browser：与旧版 run.bat 行为一致（弹系统浏览器），开发/降级用
5. 毛玻璃（见 others/毛玻璃方案探索.md）：
   - 生产默认 = 壁纸推送方案 C（后端几何/壁纸 + 前端 CSS 模糊），窗口移动/缩放
     通过 evaluate_js 通知前端刷新 background-position
   - 实测结论（2026-08-21）：pywebview 6.2.1 透明窗口无真正窗体透明
     （客户端区域被窗体 BackColor 灰底覆盖，BackColor=Transparent/TransparencyKey
     补救均无效），DWM 系统背景板（Mica/Acrylic）仅标题栏可见
   - backdrop / acrylic / --acrylic-legacy 保留为实验开关

设计原则：HTTP API 是唯一 IPC（前端不调用 pywebview js_api），保证同一套前端
在窗口模式与浏览器模式下行为一致。
"""
from __future__ import annotations

import argparse
import ctypes
import socket
import sys
import threading
import time
import urllib.request
from ctypes import wintypes

import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import uvicorn

from backend.config import load_config, PROJECT_ROOT
from backend import desktop
# 直接传 app 对象（而不是 "backend.main:app" 导入字符串）：
# PyInstaller 静态分析按 import 收集模块，导入字符串是隐形的，
# frozen 环境下 uvicorn 会报 "Could not import module backend.main"
from backend.main import app

WINDOW_TITLE = "SaberLab — Beat Saber 本地分析实验室"
PORT_RANGE = 20  # 8787..8806


def find_free_port(cfg) -> int:
    """从配置端口开始探测第一个空闲端口。"""
    start = int(getattr(cfg, "port", 8787) or 8787)
    for port in range(start, start + PORT_RANGE):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"端口 {start}..{start + PORT_RANGE - 1} 全部被占用")


def find_existing_instance(cfg, own_port: int) -> str | None:
    """探测是否已有 SaberLab 实例在跑（对候选端口发 /api/status）。"""
    start = int(getattr(cfg, "port", 8787) or 8787)
    for port in range(start, start + PORT_RANGE):
        if port == own_port:
            continue
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/status", timeout=0.5) as r:
                if r.status == 200:
                    return f"http://127.0.0.1:{port}"
        except OSError:
            continue
    return None


# ---------- 毛玻璃：DWM 能力（方案 A/B，Windows 专用） ----------
def try_dwm_backdrop(hwnd: int, value: int) -> bool:
    """尝试官方 DWM 系统背景板（方案 B：Win11 22H2+）。

    DWMWA_SYSTEMBACKDROP_TYPE = 38；value：1=None 2=Mica 3=Acrylic(TransientWindow)。
    Mica 始终渲染（含失焦时）；Acrylic 失焦会被 DWM 自动移除（对常驻工具窗口不友好）。
    返回是否成功；失败（旧系统）由调用方降级到方案 C。
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
    """实验开关 --acrylic-legacy：未文档 API SetWindowCompositionAttribute
    （方案 A，ExplorerBlur 同款）。GradientColor 为 AABBGGRR。"""
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


# 当前活动窗口壳经 backend.dialog 桥接注册（host.py 以 __main__ 运行，
# main.py 若直接 import backend.host 会得到副本模块，全局状态不同步——见 dialog.py）
from backend import dialog


class WebviewShell:
    """pywebview 窗口壳：延迟导入，浏览器模式下完全不碰 pywebview。"""

    def __init__(self, url: str, use_acrylic_legacy: bool,
                 acrylic_mode: str = "auto"):
        import webview
        self._webview = webview
        self._url = url
        self._use_acrylic_legacy = use_acrylic_legacy
        self._acrylic_mode = acrylic_mode  # 请求模式: auto|backdrop|wallpaper|off
        self._window = None
        self._last_root = ""  # 原生文件夹对话框的起始目录（当前游戏根目录）
        self._effective_mode = None  # 探测后的生效模式: backdrop | wallpaper
        self._service_thread = None  # 壁纸服务线程（初始推送 + 1s 轮询）
        self._last_wallpaper_sig = None  # (path, mtime_ns, size)
        self._last_monitor = None       # 显示器几何（跨屏/拔插检测）
        self._diag_done = False
        # —— 移动遮盖（第三轮）：moved/resized → 推"移动中"给前端（模糊拉满）；
        #    恢复双保险（第四轮修复）：
        #    ① 后端 0.5s 轮询 watchdog：距最后移动 ≥1s 推 False（失败自动重试）；
        #    ② 前端兜底：后端 True 信号节流刷新（≤500ms 一次），最后一个
        #       True 后 1.5s 前端无条件自恢复（不依赖 rAF——拖动期间渲染
        #       可能暂停，位置检测不可靠；也不依赖单次 False 推送）——
        self._moving_reported = False
        self._last_move_ts = 0.0
        self._last_push_ts = 0.0
        self._move_watchdog = None

    def _on_window_moving(self):
        """窗口移动/缩放事件（moved/resized）：标记"移动中"并推送 True；
        拖动中每 ≤500ms 节流刷新一次（维持前端兜底计时器），
        恢复由 watchdog + 前端兜底双保险负责。"""
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
        """恢复 watchdog：每 0.5s 检查——若已标记移动中且距最后移动事件
        ≥1s，推送 False（前端恢复模糊）。推送失败静默，下轮自动重试，
        不再依赖单次 Timer。"""
        if self._move_watchdog is not None:
            return

        def watch():
            while self._window:
                time.sleep(0.5)
                if (self._moving_reported and
                        time.time() - self._last_move_ts >= 1.0):
                    self._moving_reported = False
                    print("[host] 移动停止 → 恢复模糊", flush=True)
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
        """shown 事件触发瞬间窗口句柄可能尚未就绪（透明窗口 show/hide hack），
        短轮询等待，避免误判为"DWM 不可用"而降级到方案 C。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            hwnd = self._native_handle()
            if hwnd:
                return hwnd
            time.sleep(0.1)
        return None

    def _on_shown(self):
        # 毛玻璃方案决策（2026-08 实测结论）：
        # - auto 直接走方案 C（壁纸推送）：实测 pywebview 6.2.1 的 transparent 模式
        #   不会产生真正的窗体透明（客户端区域始终被 WinForms 窗体 BackColor 覆盖，
        #   实测为 #f0f0f0 灰），DWM 背景板（Mica/Acrylic）在客户端区域不可见；
        #   BackColor=Transparent / TransparencyKey 两种补救均无效（探针实测）。
        #   方案 C 无需窗口透明，效果已被用户确认。
        # - backdrop/acrylic/--acrylic-legacy 保留为实验开关（标题栏的 DWM 效果
        #   由 pywebview 暗色主题 Mica 提供，客户端区域以 C 为主）。
        if self._acrylic_mode == "off":
            print("[host] acrylic: off（毛玻璃未启用，对照外观）")
            return
        hwnd = self._wait_hwnd()
        if self._acrylic_mode in ("backdrop", "acrylic") and hwnd:
            if self._use_acrylic_legacy and try_legacy_acrylic(hwnd):
                self._effective_mode = "backdrop"
                print("[host] acrylic: backdrop(A) 实验 真Acrylic SetWindowCompositionAttribute")
            elif self._acrylic_mode == "acrylic":
                if try_dwm_backdrop(hwnd, 3):
                    self._effective_mode = "backdrop"
                    print("[host] acrylic: backdrop(B-acrylic) 实验 Acrylic(3) 失焦会消失")
                else:
                    self._effective_mode = "wallpaper"
                    print("[host] acrylic: backdrop(B-acrylic) 失败 → wallpaper(C)")
            elif try_dwm_backdrop(hwnd, 2):
                self._effective_mode = "backdrop"
                print("[host] acrylic: backdrop(B-mica) 实验 Mica(2)（客户端区域仍以 C 为准）")
            else:
                self._effective_mode = "wallpaper"
                print("[host] acrylic: backdrop(B-mica) 失败 → wallpaper(C)")
        else:
            # auto / wallpaper：方案 C（壁纸推送 + 前端模糊），生产默认
            self._effective_mode = "wallpaper"
            if self._acrylic_mode == "auto":
                print("[host] acrylic: wallpaper(C) 壁纸推送（auto 默认，pywebview 无真透明）")
            elif self._acrylic_mode == "wallpaper":
                print("[host] acrylic: wallpaper(C) 壁纸推送（强制）")
            else:
                print("[host] acrylic: wallpaper(C) 拿不到窗口句柄")
        self._start_wallpaper_service()
        self._start_move_watchdog()

    def _wallpaper_url(self) -> str | None:
        """壁纸 URL（带版本号，强制浏览器绕过缓存；壁纸变化时前端据此换图）。"""
        wp = desktop.get_wallpaper_path()
        if not wp:
            return None
        try:
            v = int(wp.stat().st_mtime_ns)
        except OSError:
            v = 0
        return f"/api/desktop/wallpaper?v={v}"

    def _start_wallpaper_service(self):
        """壁纸服务线程（方案 C 第二轮改进，2026-08-21）：

        1. 等前端注册 window.__saberlabBackdrop 就绪（最多 20×0.5s）
        2. 推送初始 payload（monitor 几何 + 壁纸 URL + 模式）
        3. 每 1s 轮询：壁纸文件（路径/mtime/size）或显示器几何变化
           → 推送新 payload（幻灯片壁纸切换、跨屏移动场景）
        4. 窗口位置跟随不再走 moved/resized 推送（evaluate_js 同步阻塞是
           拖动滞后的根源）——前端每帧自读 screenX/screenY 本地裁切。

        backdrop（实验）模式只推一次，不轮询。
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
            if self._effective_mode != "wallpaper":
                return
            while self._window:
                time.sleep(1.0)
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
                    print("[host] 壁纸/显示器变化 → 推送新 backdrop")
                    self._notify_backdrop()

        self._service_thread = threading.Thread(target=service, daemon=True)
        self._service_thread.start()

    def _notify_backdrop(self):
        if not self._window:
            return
        import json as _json
        if self._effective_mode == "backdrop":
            # 方案 A/B：DWM 负责背景，前端只需把背景改为透明
            payload = {"mode": "backdrop"}
        else:
            # 方案 C：壁纸推送（前端做对齐 + 模糊）
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
                    print("[host] backdrop 几何（物理像素，dpr 由前端换算）: "
                          f"window={payload['window']} monitor={payload['monitor']}")
                else:
                    print("[host] backdrop 不可用（拿不到窗口/显示器几何）→ 前端纯色兜底")
        js = ("window.__saberlabBackdrop && "
              f"window.__saberlabBackdrop({_json.dumps(payload)})")
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass

    def start(self):
        webview = self._webview
        # auto/wallpaper/off 用普通不透明窗口（方案 C 页面自绘壁纸层，无需透明）；
        # backdrop/acrylic（实验）才开透明窗口 —— 注意实测 pywebview 透明窗口
        # 客户端区域显示窗体 BackColor 灰底，DWM 背景板仅标题栏可见。
        transparent = self._acrylic_mode in ("backdrop", "acrylic")
        self._window = webview.create_window(
            WINDOW_TITLE, self._url, width=1440, height=900,
            min_size=(1024, 640), confirm_close=True, transparent=transparent)
        # moved/resized 只用于"移动中"状态通知（模糊拉满遮盖），
        # 位置跟随仍由前端每帧自读 screenX/screenY（第三轮分工）。
        self._window.events.shown += self._on_shown
        self._window.events.moved += self._on_window_moving
        self._window.events.resized += self._on_window_moving
        webview.start()


def main():
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

    cfg = load_config()
    port = find_free_port(cfg)
    url = f"http://127.0.0.1:{port}"
    # 前端据此开启毛玻璃层（浏览器模式/off 无 shell 参数，外观不变）
    shell_url = url if args.acrylic_mode == "off" else url + "/?shell=webview"

    if not args.browser:
        existing = find_existing_instance(cfg, port)
        if existing:
            print(f"SaberLab 已在运行（{existing}）。若窗口未显示，请先退出旧实例。")
            print("（可加 --browser 强制新开浏览器页面）")
            return 0

    config = uvicorn.Config(app=app, host="127.0.0.1", port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # 等服务器就绪（最多 15s）
    ready = False
    for _ in range(150):
        try:
            urllib.request.urlopen(url + "/api/status", timeout=0.5).read()
            ready = True
            break
        except OSError:
            time.sleep(0.1)
    if not ready:
        print("服务器启动失败")
        return 1

    print(f"SaberLab: {url}  (replay dir: {cfg.replay_dir})")

    shell = None   # 窗口壳（浏览器模式不创建；_restart_app 闭包引用需先初始化）

    # 应用内重启：设置页「重启 SABER LAB」按钮（/api/restart → dialog 桥）
    def _restart_app():
        if getattr(_restart_app, "_armed", False):
            return   # 防重复触发
        _restart_app._armed = True
        print("[host] 收到重启请求，关闭窗口并拉起新进程…", flush=True)
        # 窗口模式：先销毁窗口——webview.start() 阻塞主线程，
        # 只停 uvicorn 不会让进程退出（旧窗口残留 bug 根因）
        try:
            if not args.browser and shell is not None and shell._window is not None:
                shell._window.destroy()
        except Exception:  # noqa: BLE001
            pass
        # 停 uvicorn → main() 走到 finally（端口已释放）→ 在 finally 里拉起新进程
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
    dialog.register(shell)   # 供 /api/settings/folder-dialog 弹原生文件夹对话框
    try:
        shell.start()
    except Exception as e:  # pywebview 不可用（缺 WebView2 等）→ 浏览器兜底
        print(f"[host] webview 窗口启动失败（{e}），回退为浏览器模式")
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
        # 主动重启：进程即将退出、端口已释放 → 在此拉起新进程（绑回原端口）
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
                print("[host] 新进程已拉起", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[host] 重启拉起失败: {e}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
