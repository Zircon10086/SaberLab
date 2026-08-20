# SaberLab 打包（独立运行、无外部依赖）

## 产物形态

```
dist/SaberLab/
├── SaberLab.exe        双击启动（默认自开 WebView2 窗口，不依赖 Chrome）
├── frontend/           仪表盘 + chro 3D 查看器（含 66MB 环境离线包，全部内置）
├── config/config.yaml  配置（设置页可改；损坏时自动备份 .corrupt-*）
├── data/               saberlab.sqlite（首次启动自动建库，无需任何迁移脚本）
├── .env.example        AI Key 模板
└── *.dll / _python 等  Python 运行时 + 依赖（含 numpy/pywebview/pythonnet）
```

- 用户环境要求：**仅 Windows 10/11 自带的 WebView2 Runtime**（Evergreen 已预装；
  缺失时自动回退系统浏览器并提示安装）。
- 无需 Python、Node、pnpm、Chrome、git —— 全部依赖内置。

## 构建命令

```bat
:: 1. 依赖安装（开发机一次性）
py -3 -m pip --python .venv\Scripts\python.exe install fastapi uvicorn numpy pyyaml pywebview pyinstaller

:: 2. 先构建 chro 查看器（frontend/chro 子项目）
cd frontend\chro && pnpm install && pnpm build && cd ..\..

:: 3. 打包
.venv\Scripts\python.exe -m PyInstaller packaging\saberlab.spec --clean --noconfirm
```

## 关键设计（与打包相关的约定）

1. **frozen 定位**：`backend/config/__init__.py` 中 `PROJECT_ROOT` 在 PyInstaller
   环境下 = exe 所在目录（可写、便携），而不是 `sys._MEIPASS` 临时解包目录
   （退出即删，数据库会丢）。因此 `frontend/`、`config/`、`data/` 必须与 exe 同级。
2. **uvicorn 启动**：`backend/host.py` 直接传 `app` 对象（`uvicorn.Config(app=app)`），
   不用 `"backend.main:app"` 导入字符串——后者对 PyInstaller 静态分析隐形，
   frozen 下会 "Could not import module"。
3. **spec 扁平结构**：`contents_directory="."`（PyInstaller 6 默认会塞进 `_internal\`）。
4. **毛玻璃**：见 `others/毛玻璃方案探索.md`（DWM 背景板 → 壁纸推送 运行时降级）。
5. 控制台模式（`console=True`）便于排查；发布版可改 `console=False`（错误会进日志文件）。

## 双模式

| 命令 | 行为 |
|---|---|
| `SaberLab.exe` | WebView2 自有窗口（默认） |
| `SaberLab.exe --browser` | 弹系统浏览器（开发/降级） |
| `SaberLab.exe --acrylic-legacy` | 实验：未文档 API 真 Acrylic |
