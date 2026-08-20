# -*- mode: python ; coding: utf-8 -*-
"""SaberLab PyInstaller 打包（--onedir，全内置依赖，用户无需任何开发环境）。

构建:  .venv\Scripts\python.exe -m PyInstaller packaging\saberlab.spec --clean --noconfirm
产物:  dist\SaberLab\SaberLab.exe（双击即用；Win10/11 自带 WebView2 运行时）

目录结构（exe 同目录，可写）:
  SaberLab.exe
  frontend\          仪表盘（含 chro\dist 3D 查看器，~71MB 离线包）
  config\config.yaml 配置（设置页可改）
  data\              saberlab.sqlite（运行时生成）
  _internal\         Python 运行时 + 依赖
"""
import pathlib

from PyInstaller.building.datastruct import Tree

ROOT = pathlib.Path(r"C:\Users\ZiRCON\Desktop\SaberLab")

# frontend 打包：只带运行时需要的文件（index.html/app.js/style.css/static/ +
# chro/dist 3D 查看器），排除 node_modules（174MB）与 chro 源码（src/public 等）。
# Tree 是 TOC（三元组），须在 Analysis 之后用 a.datas += 追加
frontend_tree = Tree(str(ROOT / "frontend"), prefix="frontend",
                     excludes=["node_modules", "src", "public"])

a = Analysis(
    [str(ROOT / "backend" / "host.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "config" / "config.yaml"), "config"),
        (str(ROOT / ".env.example"), "."),
    ],
    hiddenimports=[
        "webview",
        "webview.platforms.winforms",
        "clr",
        "clr_loader",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter", "unittest"],
    noarchive=False,
)
a.datas += frontend_tree
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SaberLab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # 保留控制台：启动日志/错误排查；正式发布可改 False
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory=".",  # PyInstaller 6：扁平结构（无 _internal 子目录）
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SaberLab",
    # PyInstaller 6 默认把一切塞进 _internal\；这里要求扁平结构：
    # frontend/ config/ data/ 与 SaberLab.exe 同级（与打包文档一致，
    # 也与 frozen 模式 PROJECT_ROOT=exe 同级的定位一致）
    contents_directory=".",
)
