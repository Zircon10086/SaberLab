"""原生窗口对话框桥（__main__ 与 backend.main 之间的共享状态）。

背景：run.bat 以 `python backend\\host.py` 启动时，脚本作为 `__main__`
运行；此时 main.py 路由里 `from backend.host import ...` 会触发 host.py
的**第二次加载**（副本模块），副本的模块级全局状态与 __main__ 不同步
（曾导致 /api/settings/folder-dialog 永远返回 unavailable）。

本模块作为唯一共享状态：__main__（host.py）注册窗口壳，
backend.main（FastAPI 路由）读取并弹原生对话框。
"""
from __future__ import annotations

_shell = None  # WebviewShell 实例（host.py 在窗口模式注册）
_restart_fn = None  # host.py 注册的重启回调（由 /api/restart 调用）


def register(shell) -> None:
    """注册当前活动窗口壳（host.py main() 调用；退出/回退浏览器时传 None）。"""
    global _shell
    _shell = shell


def register_restart(fn) -> None:
    """注册应用重启回调（host.py 启动时调用）。"""
    global _restart_fn
    _restart_fn = fn


def request_restart() -> dict:
    """请求应用重启（设置页「重启 SABER LAB」按钮）。

    返回 {"ok": True}（重启在后台线程安排）或 {"ok": False, "error": ...}。
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
    """弹原生文件夹选择对话框（pywebview FOLDER_DIALOG）。

    返回：{"selected": path} / {"cancelled": True} / {"unavailable": True}。
    """
    shell = _shell
    if shell is None or shell._window is None:
        return {"unavailable": True}
    import webview
    try:
        paths = shell._window.create_file_dialog(
            webview.FOLDER_DIALOG, allow_multiple=False,
            directory=getattr(shell, "_last_root", "") or "")
    except Exception as e:  # 对话框被中断等
        return {"unavailable": True, "error": str(e)}
    if not paths:
        return {"cancelled": True}
    return {"selected": str(paths[0])}
