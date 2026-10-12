# 二次开发指南

> SaberLab 的技术架构、开发约定与常见坑。更新日志见 [CHANGELOG.md](CHANGELOG.md)。本文大部分使用 AI 总结。

## 1. 项目概览

| 层 | 技术 | 说明 |
|---|---|---|
| 后端 | Python 3.12+ / FastAPI / uvicorn / numpy / pyyaml / pywebview | 单体 FastAPI,SQLite(WAL) 存储,HTTP API 是唯一 IPC |
| 前端 | 原生 HTML/CSS/JS(零依赖,app.js ~1500 行) | 动态表单由后端 schema 驱动;毛玻璃层仅窗口模式启用 |
| 3D 回放 | **外部组件** Local-ChroViewer（Vite + React + Three.js，ChroViewer 移植，GPL-2.0 独立项目） | 不在本仓库源码内；运行时自动检测构建产物并挂载 `/chro/` |
| 打包 | PyInstaller onedir(`packaging/saberlab.spec`) | 全内置,双击即用 |

设计原则(摘自设计文档):local-first / deterministic-first;原始 Replay 只读;
AI 只解释不产生数据;HTTP API 是唯一 IPC(前端不调用 pywebview js_api)。

## 2. 环境准备

```bat
:: venv 特殊：官方 venv 无 pip，装包必须显式指定解释器
py -3 -m venv --without-pip .venv
py -3 -m pip --python .venv\Scripts\python.exe install fastapi uvicorn numpy pyyaml pywebview

:: （可选）3D 回放组件 Local-ChroViewer（独立 GPL-2.0 项目，不在本仓库源码内）
:: 克隆/构建到 SaberLab 同级目录，后端启动自动检测并挂载 /chro/：
::   git clone <Local-ChroViewer 仓库> ..\Local-ChroViewer
::   cd ..\Local-ChroViewer && pnpm install && pnpm build
```

依赖现状：fastapi/uvicorn/numpy/pyyaml/pywebview 6.2.1/pyinstaller 6.22.2（watchdog、httpx 已移除）。

## 3. 启动

| 命令 | 模式 |
|---|---|
| `run.bat`（= `backend\host.py`） | 独立窗口（WebView2 + 毛玻璃）；**无控制台**（pythonw），日志见 `data/logs/saberlab.log` |
| `run-browser.bat`（= `backend\host.py --browser`） | 系统浏览器（开发模式，无毛玻璃）；同样无控制台 |
| `python backend\host.py` | 直接运行（保留控制台实时日志，排查用） |
| `backend\host.py --acrylic-mode off` | 窗口但禁用毛玻璃（对照外观） |
| `backend\host.py --acrylic-mode backdrop\|acrylic` | 实验：DWM 背景板（已知客户端灰底限制） |

- 端口默认 6980；启动时若 6980..6999 中存在经 `/api/status` 身份 + TCP owner
  PID 双重确认的旧 SaberLab，会直接终止旧进程并优先重新绑定 6980，保证窗口
  唯一且可重新找回。非 SaberLab 程序不会被终止，6980 被其占用时仍顺延到
  6981..6999。并发启动由 Windows 命名 mutex 串行到服务 ready，避免竞态双实例
- 关窗 → 主动退出（uvicorn should_exit，无残留进程）
- **无控制台启动**（2026-08）：`run.bat`/`run-browser.bat` 用 `pythonw.exe`，
  打包版 `console=False`（不弹命令行窗口）；`host._setup_stdio()` 在无
  控制台时把 stdout/stderr 重定向到 `data/logs/saberlab.log`（追加），
  排查日志看该文件；`python backend\host.py` 直接跑保留控制台
- **启动计时日志**：`[host] ready in X.XXs`（服务器就绪）、
  `[host] webview window shown in X.XXs`（窗口显示，events.shown 回调）；
  窗口模式下 pywebview/.NET runtime 在服务器启动期间**并行预热**
  （`webview.platforms.winforms` 预导入），窗口显示几乎与服务器就绪同时

## 4. 目录结构

```
backend/
  bsor/        BSOR v1 解析器（纯函数，零外部耦合）
  maps/        谱面 hash 解析与缓存
  analysis/    确定性指标（scoring/accuracy/notes/motion/fatigue/compare）
  ai/          LLM Provider 抽象 + 提示词 + 规则兜底
  config/      ConfigService（config.yaml 唯一事实来源）+ schema（前端动态表单驱动）
  db/          SQLite schema + repository（迁移全部收敛，新库即建全表）
  services/    enrichment（富化缓存服务）等
  watcher.py   扫描 + 分层分析管线
  scoresaber.py 联网同步（每线程持久连接 + 并发 + 429 退避）
  desktop.py   壁纸/显示器几何（ctypes Win32，毛玻璃方案 C 后端）
  dialog.py    原生对话框桥 + backdrop-ready 标志（__main__ 与 backend.main 的共享状态）
  host.py      独立窗口宿主（端口/单实例/uvicorn 线程/pywebview/毛玻璃）
  main.py      FastAPI 入口（路由组装）
frontend/      原生仪表盘（index.html + app.js + style.css + i18n.js）
frontend/i18n/ 语言对照表（zh-CN/en-US/ja-JP.json，含 lang.name 自述名）
（仓库外）Local-ChroViewer/   3D 回放外部组件（独立 GPL-2.0 项目，Vite 构建；
                        后端按候选路径自动检测其 dist/，见 §5.6）
tests/         单元测试（黄金夹具回归 + schema 自举/升级）
site/          项目网站（GitHub Pages，独立于应用，见 §10）
.github/workflows/pages.yml  网站构建与发布流程
config/        config.yaml
packaging/     PyInstaller spec + 打包文档
_tools/        本地开发工具（不入仓库）：调试（cdp_stack/chro_smoke 等）、图标生成
               （make_icon，见 §11）、可重跑的验收脚本（verify_site / intro_frames /
               verify_guard_*）、注释语言检查（check_comment_lang）
_tmp/          测试临时区（一次性探针/截图，可随时清空——要留的脚本放 _tools/）
```

## 5. 后端要点

### 5.1 配置系统（schema 驱动）
- `config/schema.py` 定义全部配置项（key/label/type/group/hidden/restart_required），
  前端据此动态生成设置 UI，后端据此读写校验
- 设置保存按 **真实变更** 生效（2026-08）：前端只提交脏字段；
  `ConfigService.save_values` 返回 `changed` 键列表，`analysis.*` 缓存重置与
  restart_required 提示均以其为准（表单全量提交曾导致任意保存清空分析缓存）
- 路径派生：`game.instance_root` → replay/custom_levels/songcore（`config/service.py` DERIVED_PATHS，
  标准 Beat Saber 相对路径）+ **可选** `local_leaderboard_dir`
  （`UserData/LocalLeaderboard/Replays`，2026-09 第二扫描源：目录存在即启用——
  零配置自动检测，无设置项；LL 存每场次副本（不存 exit），文件名多 `_<tick>`
  后缀，按 session 键（player+map_hash+10 位 timestamp）判重/修复，详见
  HANDOFF §4.26②。动机：BeatLeader mod「keep latest only」（`OverrideOldReplays`，
  默认开）保存新回放时会删除同谱面难度的旧 .bsor（游戏日志实证；**根因与证据链见
  HANDOFF §4.25，实测累计 217 次删除、其中 78% 是 exit 回放**）——LL 副本即第二
  只读源）；`hidden: True` 的项由"游戏路径"卡片接管（原生文件夹对话框 + 自动验证）
- 原子写回：tmp → flush → os.replace；config.yaml 损坏自动备份 `.corrupt-<ts>`。
  **新增配置项时先问清楚它是不是"分析参数"**：任何 `analysis.*` 键的变更都会触发
  `reset_analysis_cache()`（metrics/motion_series 清空 + 全部 Replay 转 pending）。
  纯 UI 偏好（分组阈值、显示开关…）必须放在其它命名空间（如 `ui.*`）——2026-09
  就因为这个把"总览游玩段阈值"放进 `analysis.*`，调一次就清空了全库分析数据。  遇到损坏警告先核对日志中的**绝对路径**：测试夹具常位于 `_tmp`，不要把测试
  临时配置的预期警告误判成用户 `config/config.yaml` 损坏
- 关键项：`ai.ai_report_enabled`（"使用 AI 生成报告"开关——**只作用于详情页
  手动「生成报告」**：勾选时 `run_ai_report` 调用 LLM，不勾选直接短路到规则
  报告；分析管线从不生成报告，见 §6.2）；`analysis.slope_group_notes`
  （note 分组大小）；`ui.session_gap_minutes`（总览「按游玩段」分页的间隔
  阈值，默认 60 分钟：相邻两次 Replay 间隔超过它即为新的一段；`load_config` 会把
  越界值夹到 [1,1440]，防坏 config.yaml 让全库并成一段。**必须留在 `ui.*` 而不是
  `analysis.*`**——后者一变更就清空分析缓存，而它不参与任何分析计算）；
  `analysis.window_seconds/window_step_seconds`（已弃用，hidden 保留兼容）；
  `player.star_palette`（星级色谱预设：`community` 固定阈值 /
  `personal80` / `personal94` / `personal96`——按玩家自己的成绩估计"在某档准度下
  能稳定处理的星级"，颜色 = 曲目难度相对该基准的位次。模型与参数见
  `docs/ACC_WEIGHTED_SKILL_MODEL.md`，纯函数在 `backend/analysis/skill_model.py`，
  结果缓存于 `player_palette_cache` 表，经 `/api/status` 的 `ui.*` 下发前端；
  数据不足的档位不可选，详见 §5.13）
- 玩家身份：ScoreSaber ID（= Steam ID）**只从 BSOR Replay 自动解析**
  （`repo.latest_player_id()`）；`player.scoresaber_id` 配置项已**弃用**
  （schema 保留 hidden，不再被读取）

### 5.2 dialog.py 桥（重要）
`python backend\host.py` 启动时脚本以 `__main__` 运行；main.py 若 `from backend.host import ...`
会得到 **host.py 的副本模块**（模块级全局状态不同步，曾导致文件夹对话框永远 unavailable）。
共享状态一律经 `backend/dialog.py`：host.py 注册窗口壳，main.py 路由读取。

### 5.3 毛玻璃（方案 C 数据流）
```
backend/desktop.py  壁纸路径三级兜底 + 窗口/显示器几何（ctypes）
host.py 服务线程    初始推送 + 1s 轮询（壁纸 mtime/size、显示器几何变化 → 推送）
frontend app.js     rAF 每帧读 screenX/Y 自裁切（零 IPC）+ 壁纸预加载换图
移动遮盖            moved/resized → __saberlabBackdropMoving(true)；1s 无事件 → false
                    （后端 watchdog + 前端 1.5s 兜底）
reload 重推         前端加载/reload 后 POST /api/desktop/backdrop-ready（dialog.py
                    标志桥）→ 服务线程消费并重新推送 payload（否则语言切换等
                    reload 后毛玻璃永久丢失）
```
前端契约：`window.__saberlabBackdrop(payload)`（mode=wallpaper/backdrop、monitor、wallpaper_url?v=）、
`window.__saberlabBackdropMoving(bool)`。浏览器模式（无 `?shell=webview`）完全不启用。

### 5.4 任务系统
- 5 个长任务（ingest/analyze/map_scan/ranked_update/nps_update）=「查锁 → daemon 线程 →
  前端 pollTask 1.5s 轮询 /api/status」；无队列、无取消、重启即丢
- 任务状态卡片：前端 `updateTaskKpi(t)` 把进度渲染到 KPI 卡片背景（红蓝渐变 +
  文字联动）；运行中 spinner 由 `#kpi-task-card[data-task=running]` 控制。标题
  i18n 只能挂文字 span，不能挂整个 `.kpi-k`（否则 `textContent` 会删除 spinner）

### 5.5 repository
SQLite 每次调用新建连接（WAL，timeout 30s）；所有 SQL 收敛在 `db/repository.py`；
schema 迁移收敛在 `db/models.py`（新库即建全表，`_migrate` 幂等升级旧库）。

### 5.6 插件系统（v2.0.0 起：plugins 目录识别加载）
- 根目录 `plugins/` 按约定识别并加载第一方插件：**不同协议的项目、其它完整
  功能**做成插件放入 `plugins/<插件名>/`，启动时检测目录内容并入主线。
  当前机制：目录含入口文件（`index.html`）即被挂载/启用；**只做第一方插件，
  不开发第三方插件接口/规范**（无专用 API）。
- 当前唯一插件 = 3D 回放（Local-ChroViewer）：源码在**独立项目**
  `Local-ChroViewer/`（GPL-2.0，ChroViewer 移植，不在本仓库）；
  **唯一检测路径** `<仓库>/plugins/chro`（含 `index.html` 即挂载 `/chro/`；
  frozen 下 `PROJECT_ROOT` == `<exe dir>`，同一路径自动覆盖发布布局）——
  **无回退**：移除插件目录立即禁用
- 命中 → `/api/status` 返回 `chro.available=true`；未命中 → 前端详情页
  「查看回放」显示灰字安装提示（三语言，指引放入 `plugins/chro/`）；
  前端依据 `window.chroAvailable`（loadStatus 设置）决定 iframe 还是提示
- 插件目录约定见 `plugins/README.md`

### 5.6b 离开回放页时暂停 chro 播放（2026-09）
- **现象**：详情页三个 pane 是**恒定 DOM + track 位移**切换（`.panes-track` translateX），
  iframe 始终留在文档里，所以切「数据一览」或离开详情页后，插件在后台继续播放
  （音频与场景都不停），只有打开另一条 Replay 才会因 iframe 重建而重置。
- **需求语义（2026-09-14 定案）**：离开当前画面就暂停、**位置保留**；
  **回到页面不自动继续**，由玩家手动点播放。暂停与"打开新回放时重置"是两件事，
  后者仍由 iframe 重建自然完成。
- **落点**：暂停必须发生在插件自己的 transport 上（`useSongTransport.pause()`），
  父页拿不到它。两条路径都在 `syncChroPlayback()`（`frontend/app.js` 的 CHRO 段，
  由 `switchTab` 与 `switchDetailPane` 末尾调用，**只在离开时动作**）：
  1. 插件带 `data-saberlab-host` 标记 → 发一条契约消息，插件自己暂停（首选）；
  2. 老插件无标记 → 回退：先判定它是否真在播放，再用插件自身的播放/暂停快捷键
     （`chroPressPlayPause()` 向 iframe 文档派发一次 Space keydown）。
- **可见性判据**：三个 `.dpane` **永远不带 `active`**（只有 `.dt-tab` 按钮带），
  故用 `.dt-tab.active` 的 `data-pane === "replay"` + `#tab-detail.active` 两个条件。
- **回退路径的三条实测结论**（`_tmp/probe_chro_pause_paths.mjs` 可复跑）：
  1. 插件时间轴读数（自己渲染的 `MM:SS`）**约 0.9s 才跳一次**，不是 100ms——
     判定"是否在播放"的取样窗口必须 ≥1.5s（`CHRO_PROBE_MS`），用几百毫秒必然误判。
  2. **rAF 计数不能用来判播放状态**：渲染循环恒定约 320 帧/2s，暂停后照跑。
  3. 父页 → iframe 的 postMessage **必须发到 `frame.contentWindow`**；
     `window.postMessage(...)` 只投给本窗口自身，不会下发给子帧（曾据此误判"通道不可用"）。
- **为什么现在是单向**：自动继续曾经需要"宿主记住自己暂停过、回来再判定读数是否冻住"
  这套状态机，而插件自身在页面回到前台时也会切换播放状态，两者会打架（实测反复出现
  状态对不上）。改成只暂停后，宿主无需记任何状态，**回到页面时它什么都不做**，
  玩家的手动操作永远说了算。
- 打开另一条 Replay：`openDetail` 开头清掉还在飞的判定定时器（iframe 即将被重建）。
- 插件产物缺失/跨源/派发失败时全部静默降级（不报错，行为退回改动前）。

**⚠️ 为什么会有两层实现（2026-09-14 定案，务必先读）**
`plugins/chro/` 那份分发物**没有自己的构建流程**——它原本是 SaberLab 仓库里的
`frontend/chro/`，因为开源协议冲突被整块移到 `plugins/chro/`（只搬路径、没做封装），
随后直接压缩上传到 Local-ChroViewer 仓库。因此它**无法从现源码重建**（现树是
TanStack Start 服务端构建，产物形态与它完全不同）。用户决定：
**先上最小补丁让功能可用；独立构建 chro 的能力列入待办、日后再做。**
所以现状是：

1. **生效路径 = 桥接补丁（契约）**：`_tools/inject_chro_bridge.py` 把一段引导脚本注入
   `plugins/chro/index.html` 的 `<head>`，实现下述契约并在文档根上打
   `data-saberlab-host="1"`。宿主探测到标记即走契约、不再用 DOM 回退。
   该脚本**幂等**（重复执行字节不变，已实测），文件末尾留了一行提醒注释；
   **将来替换 `plugins/chro/` 后必须重新注入一次**。插件目录里还带了一份由
   `_tools/sync_chro_bridge_tool.py` 生成的同款脚本（`inject_saberlab_bridge.py`），
   便于只拿到插件目录的人自行重放——**两份由生成器保持一致，不要手工改插件目录那份**。
2. **DOM 回退（保留）**：给不带标记的插件（旧包/别处来的包）兜底，依赖插件内部细节
   （约 0.9s 的读数节奏 + 空格快捷键）。**一旦插件带上标记就不会走这里**，将来
   独立构建落地后可以直接删掉这一段。
3. **viewer 源码里的正式接口（已写好，等独立构建时随包发布）**：
   `Local-ChroViewer/web/src/modules/viewer/use-host-playback-commands.ts`（新文件）
   + `viewer-shell.tsx` 一行挂载（live / watch-party 模式下禁用）。

**契约：单向暂停，不自动继续**（2026-09-14 用户定案）
- 宿主只在"回放页离开当前画面"时发一条
  `{type:'saberlab:playback', action:'pause'}`（发到 iframe 的 `contentWindow`）；
  **回到回放页什么都不发**——播放保持暂停，由玩家自己手动继续。
- 插件侧校验 `event.source === window.parent` 且 `event.origin === location.origin`；
  只在**确实正在播放**时才暂停，因此这条指令永远不会把停着的回放启动起来。
- 暂停保留播放位置（不清零）；打开另一条 Replay 时 iframe 重建，插件自身重置。
- 桥接版与 viewer 源码版都做了幂等处理（点击后确认状态、必要时重试），
  重复注入的监听器也伤不到它。
- 支持契约的文档会标记 `<html data-saberlab-host="1">`；宿主据此决定走契约还是 DOM 回退。

**验证**：`_tmp/verify_chro_pause_only.mjs`（契约路径：标记存在 / 播放在读数推进 /
离开即停且位置保留 / **回来仍停住** / 手动点播放从原位继续 / 重复暂停不翻回，全 PASS）、
`_tmp/verify_chro_pause2.mjs`（DOM 回退四路径，全 PASS）、
`_tmp/probe_chro_readout_cadence.mjs`（读数节奏，判定窗口参数的依据）。

### 5.6c 3D 回放的两个全屏（2026-10）

- **位置**：回放卡片自身标题行的右侧，紧跟「查看回放」标签之后；两个按钮是
  `页面全屏` / `全屏`。**不在 chro 插件内部**——插件产物保持原样（`plugins/chro/`
  与 chro 仓库两份字节一致），宿主不改插件、不注入、不依赖插件内部 DOM。
- **分层用法**：全屏时仍要能点回普通视图，按钮在卡片上随卡片的显隐走，不依赖
  插件怎么渲染自己的工具栏。
- **页面全屏**（`页面全屏`）：给 `<html>` 打 `saberlab-page-fullscreen`，由
  `frontend/style.css` 收起头部与导航，并把回放卡片变成铺满窗口的一层。
  - 卡片用 `position: fixed` + `width:100vw/height:100vh`：三个 pane 是
    **300% 宽 track + translateX 位移**的滑块（不是显示切换），且祖先上有 transform
    （`.panes-track`/`#tab-detail`），`inset:0` 会按该祖先解析、宽度算错，所以铺满
    写视口单位；卡片盖住滑块即可，不动三 pane 结构。
  - **必须同时把滑块的 transform 关掉**（`html.saberlab-page-fullscreen #panes-track
    { transform: none !important; }`，2026-10 修）：卡片是**被 transform 的祖先里的
    fixed 元素**，所以它的包含块是 `.panes-track` 而不是视口，滑块的
    `translateX(-66.67%)` 是**按 track 宽算的百分比**——track 宽随 pane 内容变化，
    这个位移就把整个 pane 推到负 x，卡片被搬到屏幕左侧之外。用户看到的现象正是
    「点页面全屏后界面全没了、只剩左上角一个返回」（2026-10 用户报告；当时的
    41 项验收里有一项 `elementFromPoint` 命中检查本来会抓到，但它的前提是手点过
    三 pane 标签把位移写成了页面内联的 `-66.6667%`，脚本自己点按钮时读到的是历史
    遗留的矩阵值，于是**偶发通过**）。页面全屏下三 pane 本就不需要滑动（详情页签已隐藏）。
  - **必须 `!important` 的还有返回条**：`#detail-topbar` 的显示/隐藏是 `app.js`
    写的**内联 `display`**（`openDetail` 设为 flex、`goBack` 设为 none），内联声明
    压过样式表规则。少了 `!important`，「收起页面家具」这条规则静默失效，返回条会
    压在铺满的卡片上面——点它就离开详情页而不是退出页面全屏。
  - `#tab-detail` 在全屏时给 `height:100% + overflow:hidden`，其下百分比才有解析基准；
    卡片保留标题行（`页面全屏` 的出口就在那），只隐藏标签文字，按钮收成图标。
  - 状态属于页面：离开详情页（`switchTab`）与打开另一条回放（`openDetail`）都会
    `clearChroFullscreen()`，不让放大布局跟着走进下一条回放。
- **两个控件的分工（2026-10 用户定案，务必不要合并）**：
  - `页面全屏` = 「让我现在这个窗口被回放填满」——只动页面、不动窗口。**已完善，逻辑不要
    再改**（用户明确要求）。
  - `全屏` = **一次点击的完整动作**：先把窗口铺满所在显示器，**再自动进入页面全屏**，于是
    整屏只剩回放；退出也是一次动作，**先撤页面全屏、再把窗口缩回原大小**（顺序不可颠倒，
    否则会有一瞬间「小窗口上盖着一层铺满窗口的卡片」）。用户给的口径：「点击全屏后画面自动
    覆盖显示器，按 Esc 或退出按钮后先退出页面全屏，再回到小窗口」。
  - 因此「全屏」是**建立在另一个控件之上的复合动作**，不是和它并列的独立状态。
    （历史教训：中间试过两种错法——① 让「全屏」只做窗口、界面原样；② 对 iframe 调
    `requestFullscreen()` 只铺满当前窗口。两者都不满足「一键把回放铺满显示器」。）
- **全屏**（`全屏`）：**宿主窗口铺满所在显示器**，走 pywebview：
  `POST /api/window/fullscreen` → `backend/dialog.py request_window_fullscreen()` →
  `window.toggle_fullscreen()`。pywebview 的 WinForms 实现会去掉边框、把窗口边界设为
  所在显示器的完整边界、最大化，并关掉窗口圆角与边框（DWM）；退出时按记录恢复原状态、
  原位置与原尺寸（原显示器不在了就居中回主屏）。实测窗口矩形与显示器矩形**逐像素相等**
  （2560×1440，验收脚本第 7 段用 `backend.desktop` 在宿主侧量的——页面看不到自己的
  系统窗口矩形，所以这件事**不能在页面里断言**）。
  - **状态以测量为准，不靠标记（2026-10 改）**：平台的"是否全屏"是 pywebview 自己记的，
    **窗口一旦被外部改动它就永久错位**（实测：状态说"已覆盖"而窗口停在 1899×1189，按钮与
    Esc 一起失灵）。现在 `backend/dialog.py` 用 `backend.desktop` 量**真实窗口矩形与所在
    显示器矩形**：`covers_display()` 相等即视为已覆盖；带 `target` 的请求**先量、需要才
    `toggle_fullscreen()`、动作后再量一次**作为返回值。重复请求、外部改动、脏状态都自愈。
    前端只读 `/api/status` 的 `window.covers` / `window.fullscreen`，`window.window`
    与 `window.monitor` 也一并下发（页面按矩形对比，不按视口猜）。浏览器模式没有窗口，
    接口返回 `available: false`，前端退回页面全屏。
  - **`_force_cover` 必须在 toggle 之前**：宿主在"要覆盖"时做一次 DWM 属性清理（关圆角、
    关边框）。pywebview 按**进入时的窗口状态**记录恢复点，事后改窗口样式会让退出"恢复"成
    被改过的状态（实测：退出后窗口仍停在显示器尺寸）。该函数现在只碰 DWM 两个属性，
    不动 style 与位置。
  - **前端接线**：`loadStatus` 记 `window.windowFullscreen`；点击时打接口，按返回值
    **进则自动 `applyChroPageFullscreen(true)`**、退则走 `leaveChroFilledLayout()`
    （先撤页面全屏 → 下一帧再发接口缩窗口，见 `chroCoverExitPending` 标记）；按钮文案
    （`window.fullscreen` / `window.exit_fullscreen`）随之同步。
    `startChroWindowWatch()` 每秒半问一次状态，外部改动也能同步回界面。
- **Esc 退出**：一次 Esc = 一次完整退出（窗口 + 页面全屏），顺序同上。
  - **焦点落进 chro 帧后，页面完全收不到 Escape**（2026-10 实测：父页面与 iframe 的
    `keydown` 都不触发，同一时刻按字母键却正常到达 iframe），所以这一半只能由宿主兜底：
    `host.py` 的 `_escape_watch` 线程用 `GetAsyncKeyState` 每 50ms 看一次键态，
    **只在这两种全屏之一开着时才动作**——窗口覆盖时先还原窗口，然后一律
    `dialog.signal_cover_escape()` 置标记；页面每秒轮询 `/api/status` 读到
    `window.cover_escape` 后走与按钮相同的退出流程（宿主也会立刻
    `evaluate_js(window.__saberlabCoverEscape())` 叫一次，轮询是兜底）。
    先试过 `WH_KEYBOARD_LL` 钩子：**在本进程里从不触发**（低层钩子要求安装线程跑消息泵，
    而主线程在 `webview.start()` 之前没有消息泵）。
    - **页面全屏也要让宿主知道**（2026-10 补）：`applyChroPageFullscreen()` 每次变化时发
      `POST /api/window/page-fill {on}`，`dialog.set_page_fill()` 记着，
      `dialog.fullscreen_on()` = 页面全屏 ∨ 窗口覆盖。这样"焦点在回放里"时**页面全屏
      同样能一个 Esc 退出**——否则宿主只认窗口覆盖，页面全屏就没有捕获机制。
  - 焦点在应用自己这边时，前端 `document` 的 keydown **capture** 阶段处理并
    `stopPropagation`：**同一次 Esc 不会再触发详情页的「Esc = 返回」**（那个返回处理器在
    正文里，我们比它先跑）。
  - 历史教训：曾写成「一次只撤一层、由外向内」，用户按一次只回到「小窗口 + 页面全屏」，
    与他要的「按一次就回到进来之前」不符——现在的口径是**进来是一步、出去也是一步**。
- **进入时的提示（toast）**：`页面全屏` 与 `全屏` 成功后各弹一条 info 提示说明"已铺满、
  按 Esc 退出"（`replay.page_fullscreen_on` / `replay.fullscreen_on`，三语齐备）。
  从「页面全屏」进「全屏」时只弹后者（前者传 `quiet` 抑制），避免两条同时出现；
  退出时不弹（用户要的是进入提示，退出本身看得见）。
  `applyChroPageFullscreen(on, {quiet})` 是唯一入口，`quiet` 只影响提示。
- **页面全屏下标题行改为浮层**：回放必须真正贴满四边，所以 `.surface-title` 在页面全屏时
  改成 `position:absolute`（右上角浮在画面上，见 style.css），退出用的按钮自带半透明底以
  保证在画面上可读；`app.js` 的 `resizeFrame()` 在页面全屏下也不再留 4px 余量
  （实测修好后 frame 与视口**逐像素相等**：2560×1440 vs 2560×1440）。
- **文案**：`replay.page_fullscreen` / `replay.exit_page_fullscreen` 与
  `window.fullscreen` / `window.exit_fullscreen` 中英日齐备；按钮的 `aria-label` 与
  `title` 随状态同步，不写“为什么”。
- **验收**：`_tools/verify_replay_fullscreen.py`（**61 项**，`run.bat` 窗口 + CDP 9222）。
  两个按钮都用**真实鼠标事件**按、Esc 用**真实按键**发送并且**每次都只发一次**（第二个
  Esc 就是详情页的返回键）。脚本头部记着环境事实：该窗口 `document.visibilityState`
  实测为 `visible`；窗口覆盖后视口变成整个显示器（本机 `screen=2560x1440`、`dpr=1.5`），
  脚本判定「一键进入后回放贴满整屏」与「一键退出后回到进入前的框」。
  退出顺序有一条专门检查：用 MutationObserver + fetch 包装记录「撤页面全屏」与
  「请求缩窗口」谁先发生，断言**先撤页面全屏**。
  宿主侧几何检查（第 7 段）自己 EnumWindows 找到窗口句柄再量矩形，跑完把窗口还原。

**待办：chro 独立构建**（用户 2026-09-14 决定延期）。调查结论（`_tools` 外的实验已清理）：
`scripts/build.py` 只产桌面版，没有产插件包的那一步；要产可静态部署的 `/chro/` 包需要
① 新增静态入口（现源码没有 index.html，由框架生成）② 构建配置分模式（`base:'/chro/'`、
去掉 nitro/Start 服务端插件）③ 把服务端专属路由与函数从客户端路由树隔离出去并重新生成
路由树（生成的路由树会把 `routes/api/preview.*` 一起拉进来，那条链依赖
`@resvg/resvg-js` 原生二进制与 `satori`）④ 在真实窗口里验证地图包加载与 3D 渲染。
服务端面很小（3 个元数据函数 + 4 个 API 路由，只服务分享预览），播放引擎本身零服务端依赖；
难点不是"问题多"而是"根本没有静态产物这条路"。估计 1–2 天。

- **提交归属**：chro 的源码改动提交到它自己的仓库（同款 GPL-2.0）；
  SaberLab 提交**不得包含 `plugins/` 下任何文件**（`.gitignore` 已排除 `plugins/chro/`）。
  桥接补丁属于**产物文件**，随 `plugins/chro/` 一起走（发布打包会带上整个目录）。



### 5.7 双平台云端数据（scoresaber | beatleader，2026-08）
- **数据源切换**：设置 → 玩家 →「云端数据源」卡片（segmented control，
  `player.data_source` 配置项，schema 驱动）；点击即保存并刷新页面
- **ID 通用**：两平台都用 BSOR 自动解析的 ScoreSaber ID（= Steam ID，
  17 位），无需任何手动输入
- **数据隔离**：`scoresaber_cache`（玩家档案+成绩）、`scoresaber_leaderboards`
  （谱面星级）、`map_ranked_cache`（stars/pp 索引）、`player_palette_cache`
  （个人色谱）四张缓存表均带 `platform` 列（PK 含 platform）；切换平台时
  另一平台数据**完全不动**，可来回切换；旧库迁移时老数据标 `scoresaber`
  （`repository._migrate` 幂等重建）
- **enrichment** 按当前平台读 leaderboards/ranked_cache（快照按平台缓存）；
  列表/详情/历史/色谱全部自动跟随
- **ranked_update / 一键刷新** 按平台路由：`scoresaber.sync_maps_batch` 或
  `beatleader.sync_maps_batch`（BeatLeader 用 `/leaderboards/hash/{hash}`
  一次拿全难度；ranked = difficulty.status==3；官方 OST（status 5/7）
  **显示星级但不产生 PP**——用户决策）
- **一键刷新完整云端语义**：仍是 5 个任务组；`ranked_update` 在榜单/玩家 PP
  索引完成后串行调用 `_cloud_page_refresh(active_platform)`，刷新玩家档案、近期
  成绩与动态水平。手动云端页 POST 刷新成功后前端显示三语言 success toast
- **云端数据页**：导航「云端数据」（原 ScoreSaber 入口改造），按当前平台
  调 `/api/scoresaber|/api/beatleader`（GET 缓存 / POST 拉取并计算动态水平）；
  交叉验证按钮仅 ScoreSaber 平台显示（BeatLeader 无对应功能）
- **个人基准按平台独立**：各平台各自跑能力模型（星级不可混用），缓存分平台
  存储，切换数据源后色谱跟着换
- BeatLeader API 客户端：`backend/beatleader.py`（字段与 scoresaber 对齐，含能力模型
  需要的 `accuracy` / `base_score`，详见模块注释）

### 5.8 PP 预测（准确率预览，v2.1.0）
- 公式：`pp = maxPP × curve(acc)`（ScoreSaber 官方 pp-curve 黑盒复刻）。curve
  内嵌于 `backend/analysis/pp_predict.py`（37 节点分段线性，acc=0.95 → 倍率
  1.0，来源与抓取日期见模块注释），**离线确定性，不运行时拉取**
- maxPP 来源：`scoresaber_leaderboards.max_pp`（榜单 info 同步落库），语义 =
  95% acc 时的 PP（H1「100% acc 的 PP」被本地数据否定，差 ~5.4×；H2 干净样本
  ±0.1%，实证脚本 `_tmp/verify_pp_curve.py` + `_tmp/pp_curve.json`）
- 端点 `GET /api/replays/{id}/pp-preview`：仅 scoresaber 平台（BL 公式不同 →
  400）；leaderboard 选取复用 `enrichment.pick_leaderboard`（与快照折叠同一
  `_lb_better` 平局裁决）；预览默认值始终是 Replay 当前显示的 acc（NF/exit
  不折半、不跳 60%；低于 60% 时动态扩展 lo）；unranked / max_pp 缺失 → 404
- 前端：replay 列表 ranked 条目 PP 值加 `.pp-click`（ranked + SS 平台才可点）；
  `openPpPreview` = **通用锚定弹出组件 `openPopover`**（app.js，`closePopover`
  幂等）：锚定触发元素下方弹出（下方放不下翻转到上方，`--pop-drop` 反转入场
  方向；左右夹边），不加遮罩不阻断交互，点击外部 / Escape / 滚动 / 缩放关闭，
  同格点击切换；出入场动画 `popIn/popOut` + `prefers-reduced-motion` 降级。
  全屏 `openModal` 为**独立全局强提醒接口**（顶层浮层；切割细节已转详情页内部
  卡片，二者无依附，弹窗版 `openSliceModal` 已删除）。曲线节点由后端下发，
  前端仅做节点间线性插值（公式本体在后端）；i18n `replay.pp_*` + `err.pp_*`
- **数据语义**：`map_ranked_cache.pp` 是玩家该难度的**云端最好成绩**（top-100
  同步），绝不能直接贴给同谱每条本地 Replay；ScoreSaber enrichment 用
  leaderboard `max_pp × curve(local acc)` 逐场计算列表 PP（NF 的已获得 PP 再按
  `score_effective/score` 折算，exit 置空）。PP preview 同样只用 maxPP；
  `replay_pp` 字段仅供云端最佳参考。BeatLeader 保持现有缓存展示，不实现预测
- 坑：滑条值是百分数（60–100），显示用原值、查曲线除以 100（曾双重除法导致
  acc 显示 0.78%）；popover 外部点击监听须 `setTimeout(0)` 延后绑定（否则打开
  弹窗的那次点击会立刻触发关闭）
- **popover 材质（2026-08 修复）**：`rgba(20,22,30,0.66)`（= body.acrylic 的
  --surface 值，两模式统一——不要用 `var(--surface)`，浏览器模式下会变不透明
  灰块）+ `backdrop-filter: blur(14px) saturate(150%)`（header 同款）；
  z-index 200（高于 header/sidebar，低于 toast 1000）

### 5.9 本地回放留存（回放为什么会消失）
- **删文件的是 mod，不是 SaberLab**：BeatLeader mod 的回放设置「keep latest only」
  （`UserData/BeatLeader.json` 的 `OverrideOldReplays`，**默认开启**）会在保存新
  回放时**删除同谱面难度的旧 .bsor**。证据：游戏日志
  `[WARNING] OverrideOldReplays is enabled, old replays will be deleted` +
  `[INFO] Deleting old replay: <文件>`。实测（2026-09-11，全部
  `Logs/*.log(.gz)`）：**217 次删除 / 213 个去重文件 / 跨度 2025-12-13~2026-09-07，
  其中 78%（170/217）是 `-exit-` 回放**。与 BeatLeader 服务端策略一致（网站也只
  保留每个榜单的最新回放）——"网站上也没有"由此解释。
  完整证据链见 HANDOFF §4.25；可重跑统计：`_tmp/_verify_bl_count.py`
  （**必须解压 .gz、正则不得跨行**，坑见该脚本 docstring）
- 影响与当前处置：
  - **LocalLeaderboard 作为第二只读源**（`game.local_leaderboard_dir`，派生
    `UserData/LocalLeaderboard/Replays`，目录存在即启用）：每场次独立存档，
    一键刷新可把文件缺失行的 `file_path` 修复指向幸存副本；同场次去重键 =
    玩家 + 谱面 hash + 10 位时间戳；LL 独有场次正常入库
  - **exit 回放没有 LL 副本** → 曾不可恢复；文件缺失行现在显示明确的"文件缺失"
    降级提示（详情横幅 + 徽章，见 HANDOFF §4.26①），而不是笼统"无数据"
  - **该设置现已在游戏内关闭**（2026-09-11 起），exit 回放终于能被长期保留。
    排查"文件缺失"时**先看游戏日志，不要假设是 SaberLab 弄丢了文件**
- **设置页「游戏路径」卡片会就地检测该设置**（2026-09）：
  `service.check_paths()` 在目录检测之后追加一行 `replay_retention`
  （`beatleader_replay_deletion_check()` 读 `<root>/UserData/BeatLeader.json` 的
  `OverrideOldReplays`）。要点：
  - **`status` 三态**：`ok`（绿勾，已关闭）/ `bad`（红叉，已开启）/ `note`
    （中性——mod 未安装、配置无该键、配置无法解析、根目录未填）；
    前端 `renderRootChecks` 按 `status` 渲染，缺失时回退 `ok`
  - **只有明确写入 true 才算开启**（`True` / `1` / `"true"` / `"yes"`）；
    字符串 `"False"`、其它未知值一律不算——**宁可不报也不误报**
  - **该行不参与 `valid`**：`valid` 仍只看根目录 + `CustomLevels`，因为路径本身
    有效、修改只能在游戏内完成（红叉只是提示，不会把徽章变成"验证失败"）
  - 文案：label 走 `settings.game.retention_label`；note 的中文原文在
    `zh-CN.json` 的 `settings.game.retention_*`（前端按 key 映射），
    en/ja 同时在 `err` 段保留中文原文 → 译文映射（与既有后端消息同款机制）

### 5.10 能量 / 失败时间（官方算法移植，2026-09）
- **为什么自己算**：`.bsor` 的 `failTime` 字段从未被 BeatLeader mod 写入
  （本机 498/498 恒为 0），所以失败时刻只能在本地重算
- **算法来源：反编译**（不是猜）。游戏逻辑在 `Beat Saber_Data\Managed\Main.dll`
  （Mono/.NET 程序集，非 IL2CPP，**可完整反编译**）。工具已全局安装：
  `dotnet`（SDK 8.0.425）+ `ilspycmd`（`%USERPROFILE%\.dotnet\tools`）。
  命令：`ilspycmd -l c <dll>` 列类型、`-t <Type>` 反编译单类（输出为 UTF-16，
  需转 UTF-8）。**三版本 `GameEnergyCounter` 常数与逻辑完全一致**
  （1.39.0 / 1.40.8 / 1.44.1；1.44 仅把 `Time.deltaTime` 换成
  `TimeHelper.DeltaTime` 并新增障碍物分析埋点）
- **官方常数**（`backend/analysis/energy.py` 的模块头逐条记录）：
  good +0.01、bad −0.10、miss −0.15、切炸弹 −0.15、burst 元素 +0.002/−0.025/−0.03、
  障碍物 −1.3/秒；Bar 起始 0.5、Battery/instaFail 起始 1.0（4 格电）、上限 1.0；
  能量 ≤1e-5 → 置 0 并触发失败；失败后锁存。**NF 特殊**：游戏在 NF 生效后停止扣血，
  但 NF 是"失败瞬间被游戏自动打开"的，因此模拟**不因 NF 抑制扣血**
  （`EnergyConfig.no_fail_declared` 仅作记录），否则会抹掉导致 NF 的那次失败
- **接入点**：`analysis/engine.py` 调 `simulate_energy()`，结果进 `summary.energy`
  并落 `metrics` 表的 `energy` 段（`overall/energy` scope，无需新表）。
  **`completion_status` 判定未改**——仍走原规则（文件名 exit / NF / 录制 fail_time /
  时长兜底）；改用计算值是单独的产品决策
- **NF 修正的实测效果**：修正前后本机"判负回放"数 **1 → 174**（416 条有源文件者）
- **UI 呈现**（2026-09）：详情页时间轴卡片下方新增 `#d-energy` 小结（最低能量、
  失败时间或"未耗尽"、扣血来源分解、撞墙次数），时间轴启用**失败时间红轴**标记
  （不再依赖恒为 0 的 `.bsor failTime`）。**fail 时间与 `completion_status` 刻意分离**：
  fail 后坚持打完 / fail 前主动退出 / fail 后退出都能如实呈现，前端不做意图猜测
- **批量重算**：`POST /api/analyze/all?force=true` 无视 `analysis_status` 重算全部
  回放（新增引擎指标后回填历史数据用，避免为几个数字清空整个分析缓存）
- **待办（用户 2026-09 指定）：`completion_status` 改为按回放数据精确计算**。
  现状依赖**文件名** `-exit-` 标记 + NF 修饰词 + 录制 fail_time + 时长兜底，
  **文件名不可靠**（第三方命名、重命名、其它 mod 写入都会失真）。改造方向：
  用回放内可复现的事实（结尾时间 vs 谱面时长、能量是否归零、最后事件时间、
  暂停/重开）替代文件名判断；继续保持"完成度分类"与"事实性 fail 时间"分离；
  改造须同步 `tests/` 与文档，并对比新旧分类差异
- **衍生指标一并产出**（用户要求：同一份数据顺手算出的东西都留接口）：
  能量事件流（可按时间重放整条曲线）、`drain_by_reason`、`charge_by_hand`、
  `drain_by_hand`、`obstacle_hits`（撞墙检查点）、`min/max/end_energy`、
  `time_in_danger`、`final_charge/total_drain`
- **校验证据**：45 个"撞墙瞬间剩余能量"检查点全部可用官方公式复现，反推出的
  障碍物驻留时长 0.0105–0.311s（中位 0.067s，无负值）；另有独立判据——
  exit/NF 回放中模型判负时刻早于回放结束时长的比例 bar 183/281、battery 254/281
- **已知边界**：障碍物按"进入时刻 + 1 帧驻留"计入（回放只记录进入时刻、不含离开；
  精确积分需要谱面几何 + HMD 轨迹，列为后续项）。**实测误差（2026-09，全库 498 文件）**：
  仅 22 条（4.4%）带障碍物数据、共 45 次入墙；每次漏扣中位 0.072 / 均值 0.108 /
  最大 **0.555**（≈ 整个 Bar 的 0.5）；补上真实扣血后 **1 条由"未失败"翻转为"失败"**、
  3 条 fail_time 前移 >1s（2 条 >5s，最大 **17s**）。即对 ~98% 的回放影响可忽略，
  但对"真的在障碍物里待过"的回放影响严重——修复需谱面障碍物几何 + HMD 轨迹做
  head-vs-obstacle 扫掠（帧数据已解析且带 pose，属于中等规模增量）
- burst 元素经 `NoteParams.scoring_type` 正确区分（本地回放中确实存在，前 40 份
  就有 1279 个）

### 5.11 分析性能：落库路径与批量并行（2026-09，实测数据）
- **实测剖面（全库 416 条）**：解析 4.5ms + 分析 16ms + **落库 59ms**（占 74%）
  ——**瓶颈是数据库往返，不是计算**。优化后：解析 4.5 + 分析 16 + 落库 9.5 ≈ 30ms/条
- **三条规则，改动它们前先看这里**：
  1. `Repository` **按线程复用连接**（`_cached_conn()`）。新建连接在本机
     100MB 库上约 **18.7ms**，复用约 **2.2ms**（差 8.7 倍）。连接不随每次调用关闭，
     所以**临时库/测试要显式 `repo.close()`**（否则 Windows 下删不掉文件——
     `tests/test_platform_sync.py` 与 `test_db_schema.py` 的 `_close_repos()` 就是为此）
  2. 一次分析的全部写操作走 `Repository.session()` → **单连接单事务**；
     绝不能在里面再建新连接（`_ConnCtx` 在会话内是 no-op）。好处不只是快：
     **一条回放的数据是原子的**，失败整体回滚
  3. **不要在批量路径里触发地图库全量重扫**。`MapResolver.resolve()` 默认会在 DB
     未命中时 `scan()`，而一次 scan 在本机（1032 个谱面目录）要 **13.8 秒**。
     批量走 `ReplayPipeline(map_scan=False)` 并**前置扫描一次**；
     交互式单文件分析保持默认（新下载谱面才能被识别）
- **批量并行**：只用**进程**（线程实测 1.04–1.24x，热点是 GIL 绑定的 Python 循环；
  进程 4 worker 实测 **1.95x**）。`BATCH_WORKERS=4`（设计目标是 6 核机器留余量）、
  `BATCH_PARALLEL_MIN=24`（低于此值进程池启动成本不划算）。Windows 下 spawn 需要
  **`__main__` 保护**，worker 内避免闭包/局部函数
- **实测结果（全库 416 条强制重算）**：**246s → 20.4s（约 12x）**，
  其中前置地图扫描 ~14s 是固定成本，分析本身 13.4s → 6.9s
- **第 3 步（计算记忆化/单遍化，2026-09）**：串行分析 13.4s → **9.8s**
  （单条 33 → 24.2ms，**+26.6%**），端到端全量 20.4s → **18.7s**。三项改动：
  1. `NoteParams.decode` **记忆化**（`_PARAMS_CACHE`，缓存对象是 frozen dataclass）
     ＋**解析时一次性预解码**进 `NoteEvent._params`：一次完整分析原本要解码 40531 次
  2. `cut_scores` **记忆化**（缓存在 `NoteEvent._cut_scores`）：scoring 与 note 分组
     都要用它，原本每条 good/bad note 算两遍
  3. `_group_metrics` 的 NoteEvent 路径**单遍计数**，替代每 note 4 次 `_attr()`
     （一次分析约 4 万次函数调用）——**指标表达式与命名逐字不变**
- **红线**：任何"提速"改动都不得改变指标数值。每次改动后跑
  `_tmp/verify_numeric_regression.py`（重算后与库中 metrics/notes 逐行比对，要求零差异），
  再跑全量测试。第 3 步实测：12 条回放**零差异**
- **尚未做的热点（实测数据，供后续决策）**：`scoring.cut_scores` 16ms、
  `accuracy.analyze_accuracy` 14ms（per-note 循环）、`_path_economy` 14ms 的 per-pair
  循环、`round()` 69245 次调用 9ms、**notes/walls 逐条 Python 解码 16–22ms**
  （后者需把 notes 换成 numpy 结构化数组：收益最大、风险也最高，属独立重构）
- **可选的持久性取舍（未实施）**：WAL 下 `synchronous=FULL` 单次提交 7.66ms，
  改 `NORMAL`（SQLite 官方对 WAL 的推荐）为 2.99ms。库是全派生数据、可从 .bsor 重建，
  但仍属持久性取舍，需单独决策

### 5.12 侧栏玩家卡片与玩家资产缓存（v2.2.0）
- **数据来源**：卡片读的是 `scoresaber_cache.profile_json`（玩家档案快照，
  与云端数据页同一份），字段 name / country / rank / countryRank；写入方是
  `_cloud_page_refresh`（云端数据页「拉取数据并计算动态水平」与一键刷新的
  `ranked_update` 段落）。**不新建表**：快照里已有这三个字段，
  `GET /api/player/card` 直接把它们整理成卡片字段
- **头像与国旗**：`backend/services/player_assets.py`——同步时下载一次 → 落
  `data/assets/`（`avatar_<platform>_<player_id>` / `flag_<CC>`，无扩展名、按
  魔数嗅探 MIME）→ 之后由 `GET /api/player/avatar|flag` 只读本地文件服务
  （URL 带 `?v=<文件 mtime>`，可长期缓存且换图自动失效）。
  设计约束：**读路径永不联网**（离线可用、WebView 不直接访问外部域名）、
  **下载失败绝不影响同步**（保留旧图，`sync_player_assets` 返回
  `attempted/stored` 供日志区分"无来源"与"下载失败"）、可走 `network.proxy`
- **头像 URL 来源**：ScoreSaber `profilePicture`（cdn.scoresaber.com）；
  BeatLeader `player.avatar` → 由 `beatleader.fetch_profile` 归一为 `avatarUrl`
  （Steam CDN）。**国旗用图片而非 emoji**：WebView2（Chromium/Windows）不渲染
  区域指示符字形，旗帜 emoji 会退化成「CN」两个字母（2026-09-10 实测），
  故按国家代码从 flagcdn.com 取 40px PNG 并同样本地缓存；均缺失时前端退回
  首字母 / 国家代码文本，绝不显示破图
- 缓存文件路径**是派生的**，不写进 profile 快照——本地缓存路径属于基础设施
  状态而非 API 载荷，派生可保证快照随时可重新拉取
- 端点：`GET /api/player/card`（无快照 → `profile: null`，前端隐藏整块，属正常态）、
  `GET /api/player/avatar`、`GET /api/player/flag`（404 → 前端走兜底）
- 前端：`frontend/app.js` 的 `loadPlayerCard/renderPlayerCard`（启动时与最近
  replay 并行加载；云端同步完成后立即刷新）；DOM 在 `index.html` 的
  `.sidebar-bottom`（玩家信息栏 + 「服务器运行中」整块由 `margin-top: auto` 压底——
  两者若各自 auto 会平分留白、玩家栏飘到中间）；样式 `.player-card/.pc-*`
- **两栏与分割线（勿改回卡片）**：左下角是**两栏**（玩家信息 / 服务器状态），
  各由 `border-top: 1px solid var(--border)` 分隔——与 `.sidebar-footer` 原有
  分隔线同款，左右内边距也用 `--space-3` 对齐，故两栏文字左缘一致。
  玩家栏**无背景、无圆角**（曾用 `--surface-2` 圆角卡片，2026-09-10 改为分割线，
  与页脚设计语言统一）；`.player-card.hidden` 已删（全局 `.hidden` 已
  `display:none !important`，同一元素上两条规则特异性相同会互相干扰）
- **排名行自适应（勿改回固定单列）**：`.pc-ranks` 用 `flex-wrap: wrap`，
  两段排名并排、总宽超出文字列时在**条目边界**换行（`.pc-rank` 各自
  `white-space: nowrap`，不会从数值中间断开）。实测基准：文字列可用 117px，
  本地数值需 109.1px（55.3 + 45.8 + 8 间距）→ 单行；拟造长数值则自动两行且
  左右都不溢出。曾误判为「必须两行」而写成 `flex-direction: column`，
  属过度修正（`_tmp/verify_player_ranks.mjs` / `_wrap.mjs` 可复测这两条判据）

### 5.13 ACC 感知的玩家能力模型（2026-09）
- **规范在文档里，实现要对齐它们**：`docs/ACC_WEIGHTED_SKILL_MODEL.md`（模型与参数）、
  `docs/ACC_WEIGHTED_SKILL_MODEL_VALIDATION.md`（直接证据门、下界、"数据不足"口径）。
  纯函数在 `backend/analysis/skill_model.py`（确定性、无网络、无 LLM、无 UI）。
  **改参数前先读那两篇**——它们是产品规范，不是参考意见。
- **三条基准**：`R96`（96% 准度下的实力）、`R94`（常规）、`R80`（挑战）。同一套物理模型、
  同一条官方 PP 曲线，只有目标 ACC、可用证据与位移上限不同。
- **ACC 从哪来**（这是一个容易踩的坑）：模型要的是 ScoreSaber 计 PP 用的那个 ACC
  （`baseScore / leaderboard.maxScore`），**不是** `modifiedScore`。
  `scoresaber.fetch_scores` 因此保留 `base_score` / `max_score` 两个原始字段；
  BeatLeader 直接给 `accuracy`（百分数，除以 100）。**本地 replay 的 `accuracy`
  与它同量**（用 pp 曲线反推 62 条样本比对：中位偏差 4e-6、最大 1e-3），所以离线也能算。
- **±2.0 位移门是这套模型的核心机制**：一条成绩只有在
  `|(u(acc) − u(目标ACC)) / λ| ≤ 2.0` 时才进入该档估值，否则只作为"已展示下界"。
  在 λ=0.09 下这等价于每档只接受目标 ACC 附近很窄的一段：R96≈93.1–97.2%、
  R94≈87.8–96.3%、R80≈80.0–89.8%（`tests/test_skill_model.py` 固定了这三个区间）。
  **别再推导它**：我曾自己算错边界并误判"文档矛盾"，实际实现与文档一致。
- **"数据不足"是合法结果**：某档有效证据少于 8 条就没有数值，前端显示「数据不足」，
  设置项里该项置灰不可选（`option_meta` 由 `/api/status` 与 `/api/settings/schema` 下发）。
  不要为了填满三档而回退到别的算法。
- **数据流**：云端页「拉取数据并计算动态水平」→ 拉成绩（SS 合并 recent+top，
  上限 `CLOUD_SCORE_LIMIT=300`；BL 按日期翻页）→ `skill_model.rate_player()` →
  `player_palette_cache` → `/api/status` 注入可选色板 → 前端 `starColor` 分档。
  颜色锚点 = 当前选中的那一档，`build_tiers()` 仍是唯一的分档实现。
- **id 命名（2026-09-14 用户定调）**：对外一律 `personal80` / `personal94` / `personal96`
  （配置枚举、`/api` 载荷、i18n、`option_meta`），为将来引入别的目标 ACC 留出空间；
  **缓存列名保持短名 `r80`/`r94`/`r96`**（内部存储细节，且在线库已是这套列名）——
  翻译只发生在 `backend/services/skill_tracks.py` 的 `TRACK_CACHE_COLUMNS` /
  `track_column()` 一处。`palette_public_payload()` 还会把旧缓存里 `method='r80'`
  这类短名映射回公开 id，否则改名后旧行的"当前生效档"会读不出来。
- **接线位置（2026-09 瘦身）**：这些函数原本长在 `backend/main.py`，现已下沉到
  `backend/services/skill_tracks.py`（本文件 §5.13 描述的全部缓存/转译/可用性逻辑）
  与 `backend/services/energy_curve.py`（时间轴能量曲线的取样与有界缓存）。
  两个模块都把 `repo` 作为**显式参数**接收，不再读路由层的模块级全局；
  路由层只保留调用。改动这两块逻辑时改服务层，不要在 `main.py` 里重建副本。
- **旧 `classify_player` 已停用保留**（模块头有 DEPRECATED 说明）：不再被运行时调用，
  测试仍能跑；新功能稳定一个周期后删除（用户决定）。
- **参数校准**：`_tools/calibrate_skill_model.py`。默认读**云端缓存**（与线上模型同源，
  `--source replays` 可换成本地回放），按时间 80/20 切分，报每档覆盖率 / MAE / P90 误差 /
  偏置 / 曲线钳制次数 / 三档单调性，并与"永远预测平均 ACC"的基线对比；`--fit` 会扫
  λ（0.03–0.20）与证据门的组合。**本机实测结论：样本太少（校验段仅 8 条），
  λ 拟合结果不可用**——要让参数从"文档模板值"变成"数据验证值"，需要更多玩家的成绩历史
  （`--json` 接受外部记录文件）。**扫出来的最优值不要直接改**：按规范需在多个玩家/切分上
  稳定获胜才替换。
- **验证脚本**：`_tmp/verify_skill_palette_ui.py`（断言渲染出来的文案而不只是有无值——
  它抓到过一次 key 改名漏改的 bug）、`_tmp/inspect_skill_ratings.py`（直接打印三档输出）、
  `_tools/cdp.py`（标准库实现的 CDP 客户端，供 Python 侧 UI 验证使用）。
- **待办**：按预测 ACC 的五色分档（`skill_model.predicted_acc()` 已实现未接线）。

### 5.14 谱面管理（2026-10，基础界面；管理页下一步）
导航「对比」下方的**谱面管理**：把本地 CustomLevels 谱面库列出来，点一条进入
**谱面详情**（§5.15），查看各难度的数据、密度曲线与本地游玩记录。

- **数据源 = `maps` 表**（不是实时遍历目录）：`/api/maps/library` 从
  `Repository.list_maps_page()` 分页取；这张表由谱面扫描（`MapResolver.scan()`）从
  CustomLevels 建立，与封面接口、回放富化同源。实测本机 1037 个目录 → **1032 行**
  （差集 = 1 个没有 info.dat 的残留目录 + 4 个此前无法解析的 v4 谱面，见下）。
  行内容全部来自数据库，**不触发任何文件访问**（封面走既有的 `/api/maps/{hash}/cover`）。
- **info v4 支持（2026-10 补）**：`read_level_info()` 原先只认 v2/v3（`_songName` /
  `_difficultyBeatmapSets`），而 4.0.0+ 的结构是 `song{title,author}` / `audio{bpm}` /
  `difficultyBeatmaps[]{characteristic,difficulty,beatmapDataFilename,beatmapAuthors}`。
  旧读取器对 v4 返回**空谱面**，于是这 4 张在库里是空白行（无歌名、无难度、无 NPS）。
  修法：新增 `_read_level_info_v4()` 分支（返回 None 表示"不是 v4"，v2/v3 路径不变），
  时长仍由最难的谱面文件末音符推出（v4 的 `song.duration` 是拍数，不是秒）。
  实测这 4 张现在都能读出歌名/作者/谱师/BPM/时长/难度/NPS。
- **顺带的缓存自愈**：`_process_folder()` 原先只要"目录未变 + 有行"就跳过重算，因此
  **解析器修好后这些空白行也不会被刷新**（目录本身没变）。现在**歌名为空的行永不复用**，
  下次扫描会重读它们。修完跑一次扫描（1037 目录 / 15.8s）→ 空白行 0。
- **行内容与列**：封面缩略图、歌名（无歌名时退回目录名）、`作者 · 谱师 · BPM · 版本`、
  难度徽标（沿用 `.pill.diff-*` 配色）、难度数 / NPS（取该谱面各难度的最大值）/
  时长 / BPM；**星级列只在当前页确实有已评级谱面时才出现**（本机 1032 张里只有 158 张有
  星级，常驻一列"–"会被读成坏字段）。
- **排序（2026-10 第三版：六个键 + 正序/倒序按钮）**：`/api/maps/library?sort=&dir=`，
  `sort ∈ {name, author, length, stars, added, plays}`（未知值回落 `name`），
  `dir ∈ {asc, desc}`。SQL 侧实现见 `Repository._map_order_table()` / `_map_library_sql()`：
  - `name` 歌名、`author` 曲师（同名再按歌名）、`length` 时长、`added` 下载时间、
    `plays` 游玩次数（`replays` 按 map_hash 计数的子查询 JOIN）；
  - **只有一个星级键**（原来的 SC / BL 两项已合并，用户 2026-10 决定）：本地能拿到的星级
    永远属于**当前数据源**（设置 → 云端数据源），所以分平台两项只会在只同步了一边时排出
    完全相同的顺序——两项并列只会误导。现在这一项天然跟随当前数据源。
  - `stars` 难度：按**谱面本身**的星级排（不是玩家成绩）。方向**会**翻转数值列：
    **正序 = 星级最高在前**（与其它键一致——时长 / 下载时间 / 游玩次数的正序都是"最大在前"，
    对难度列这是最有用的读法），倒序 = 最低在前；同组的歌名次序跟随方向。
    **没有星级的谱面一律排到末尾**（用户明确要求；"没有星级"不等于"0 星"）。
  - ⚠️ **两个与方向相关的坑（用户报过"按星级排序的正序/倒序切换不反应"）**：
    1. 星级的 ORDER BY 一度写死 `stars_src DESC`（只有歌名跟随方向），于是这条排序在两个
       方向下顺序完全相同，看起来像按钮失灵。数值列必须由方向驱动：`_map_library_sql()`
       对 `stars_src` 单独处理——基句写 DESC，`desc` 时翻成 ASC（其余键仍是 `{dir}` 直接替换）。
    2. **不能给星级加 `COALESCE(..., 0)`**：那会把"无评级"变成 0 星（一个真实星级，且是最低），
       于是"未评级排末尾"会**依赖方向**——某一边它们会窜到最前面。取数保持 `NULL`，
       由 ORDER BY 里的 `stars_src IS NULL` 明确放到末尾。
    3. 回归：`test_every_sort_key_reacts_to_the_direction` 逐个键断言"两个方向必须给出不同
       顺序"（这条正是用户报的 bug 的通用化），`test_star_sort_direction_flips_the_rating`
       断言正序=最高在前、倒序=最低在前、未评级在两个方向都在末尾。
  - 时长 / 下载时间为空的行同样排末尾（未知不等于"最短/最早"）。
  - ⚠️ **排序用的星级必须与显示用的星级同源同算法**（踩过两次）：
    1. 显示值来自 `enrichment.map_star_index()`；SQL 侧是 `rated` 子查询（按平台 GROUP BY
       取 MAX）。**两者取数规则必须一致**，否则会出现"按 18.25★ 排、格子显示 13.44★"。
    2. 两个平台对 mode 的命名不同：**ScoreSaber 写 `SoloStandard`**，BeatLeader 写
       `Standard`。ScoreSaber 的每个 characteristic 各自有评级，展示与排序都只认它的
       standard 图（`SoloStandard`）；BeatLeader 一张谱只有一个评级（其各 mode 一致），
       全部计入。写成 `game_mode = 'Standard'` 会让 ScoreSaber 一行都匹配不到，整列退化成
       库里的旧星级列（基本为空）。
    3. **绝不能在 SELECT 列表里裸写 `MAX(...)`**：没有 GROUP BY 的聚合会把整个结果压成
       **一行**（SQLite 会为该行挑任意一条记录的其它列），列表会静默只剩一张谱面。
       星级必须来自带 GROUP BY 的子查询 JOIN；ORDER BY 在子查询**外层**执行，这样排序键
       可以直接引用 JOIN 出来的列，不必重复聚合表达式（重复会让 SQLite 报列名歧义，
       也会打乱占位符绑定顺序——`?` 的个数要与参数列表逐个对齐）。
- **"下载时间"是什么**：`maps.added_at`（新列，2026-10 迁移）。**不能用 `last_scanned`**——
  `upsert_map` 每次扫描都重写它，实测 1032 行只有 12 个不同值、全部落在 11 秒内，排序会
  退化成随机。做法：
  - 列由 `_migrate()` 加到旧库；新插入时取 `COALESCE(:added_at, :last_scanned)`，
    老行由**谱面扫描开头的 `_backfill_added_at()`** 一次性回填，值为**谱面文件夹自身的时间戳**
    （玩家视角的"装进游戏的时间"），文件夹不可读则用当时时间；
  - `ON CONFLICT ... added_at=COALESCE(maps.added_at, excluded.added_at)`：**已写入的值永不
    被覆盖**，重扫不会把历史时间刷成今天（实测回填后跨度 2021-04 → 2026-08）；
  - 回填只对 `added_at IS NULL` 的行生效，第二次扫描是空操作。
- **"未认证"星级（黄色底）**：BeatLeader 会给**尚未正式认证**的谱面也打星级。回放列表对这种
  星级用黄底 + hover"该歌曲尚未认证，无 pp 得分"（`.star-unranked`）；谱面管理沿用同一标记
  （实测当前 BL 数据里 rated 的谱面大多数是这一类：前 3 页 150 张有星级，其中 108 张未认证）。
  后端把 `ranked` 随星级一起下发：**只要该谱面有一条带星级的榜单是正式认证的，就算已认证**
  （不能在都是未认证时随便挑一行）。
  - ⚠️ **黄底不等于黄字**：`.star-unranked` 只提供背景/边框/圆角，**不含 `color`**——文字颜色
    必须由星级分档类给出（`star-gray/green/yellow/red/purple`，档位来自 `player.star_palette`，
    由 `/api/status` 的 `ui.star_palettes` 下发）。只挂 `star-unranked`（谱面管理最初就是这样）
    会让文字**继承卡片颜色（白）**，白字压在黄底上。回放列表的写法是
    `class="${starColor(stars)} star-unranked"`（实测：6.89★ 未认证 = 黄底 + `rgb(245,197,66)`
    文字）；谱面管理与它共用 `starChipHtml(stars, ranked)`，并把 `star-gray` 作为**最后兜底**，
    以防分档函数返回空类名时又退回继承色。
- **不加音符总数**：`info.json` 里没有音符数，库里也没存（`note_counts` 这个名字只出现在
  谱面内部字段，从未落库），所以不做"NPS × 时长"这类冒充同名指标的估算，列表用 NPS 表示。
- **布局**：完全照「历史」一栏——`.surface` + 三栏对称标题行（左筛选 / 中分页 / 右空位）+
  `.replay-list`，行也用 `.replay-item` 网格，因此与历史页对齐一致（实测行宽 960、网格列
  相同）。分页在 SQL 侧（`per_page` 默认 50、上限 500），因为库是上千行。
  - ⚠️ **宽屏两列断点极易踩的坑（用户报过两次）**：`.replay-list` 在 `min-width` 断点后
    切到 `repeat(auto-fill, minmax(960px, 1fr))`。**断点若比"内容区真的放得下两列"的最小
    视口宽度还小**，媒体查询已经认为该排两列、但 `auto-fill` 只放得下一列，于是那一列被
    `1fr` 拉满整行——表现为"卡片超长，没有像 replay 那样变成一行两条"。
    - 实测（CDP + 视口仿真，2026-10）：**内容区 = 视口 − 307**（208 侧栏 + 99 页面内边距），
      两列需要 `2×960 + 20 = 1940`，所以断点必须 **≥ 2247**；原值 2240 让 **2240–2259px**
      整段变成死区（实测 2240 视口：列表 1933px 一行；2260：两列 966px）。
      现断点为 **2248**，`.day-header` 的断点必须与它一致（两处注释互相引用）。
    - 回归：`tests/test_wide_list_layout.py`（5 项）用 CSS 算术守住"断点 − chrome ≥ 两列"、
      两个断点一致、单列有 `max-width` 上限、多列仍用同一 `minmax(960px, 1fr)`，
      并**守住 chrome 的测量值**（侧栏 token 变化即失败，提醒重新测量）。
    - 无论哪种宽度，`.replay-list` 单列都必须有 `max-width: 960px` + `margin: 0 auto`：
      `1fr` 在只剩一列时会把列拉满，只有 max-width 能兜住。
    - 验证脚本：`_tmp/probe_map_width.py`（扫宽度量几何，含死区定位）、
      `_tmp/probe_map_overflow.py`（查横向溢出与控件是否被压坏）。
- **搜索**：歌名 / 谱师 / 曲师 / BeatSaver key 的 LIKE 匹配（`q` 参数），前端 250ms 去抖后
  回到第 1 页。
- **排序控件**：下拉用 §6.3 的毛玻璃列表下拉（值放隐藏 input `#maps-sort`），旁边是
  **正序/倒序按钮** `#maps-dir`——一个按钮两个状态，`data-dir` 为唯一事实来源，
  按钮文案写"按下去会切到哪种"（升序时显示"倒序"）、`aria-pressed` 表示当前状态、箭头随之翻转；
  点击即 `loadMaps(1)` 刷新。两处状态都在 `loadMaps()` 里同步到界面。
- **重新扫描**按钮复用既有任务通道：`POST /api/maps/rescan` → `map_scan` 后台任务，
  前端 `pollTask()` 显示进度（与总览的进度卡片同一套）。
- **验收**：`tests/test_map_library.py`(23) 覆盖查询（过滤/总数/分页不相交/文本与数值键的
  双向排序/星级排序的各平台取数规则/未评级永远在末尾/未知排序与方向回落）与行整形
  （难度明细、未评级不谎报、未认证标记、坏 JSON 不炸整页、接口形状与 sort/dir 回显、
  参数透传），以及**端到端一致性**：受控小库上经真实接口取星级排序，返回行数、顺序、
  "排序键 == 显示值"三者同时断言（这是两个用户可见 bug 的回归测试）；
  UI 侧用 `_tmp/probe_maps_sort_all.py` 在真实窗口逐键点过、`_tmp/probe_star_sort_bl.py`
  核对星级顺序 + 黄底标记 + tooltip、`_tmp/probe_all_sorts.py` 逐键核对两个平台的次序。

### 5.15 谱面详情（2026-10，一难度一页）

导航「谱面详情」页 + 从谱面列表点进来的详情页。**结构照 replay 详情**（顶栏返回 + 头部大卡 +
页签），差别在"一页是什么"：**replay 是一次游玩**，页签是这一场的不同视角；**谱面是一首歌带多个
难度**，所以**一个难度一页**（用户 2026-10 决定），每页只讲那一张谱面的数据与本难度的游玩记录。

- **三个接口**（都只读、都本地）：
  - `GET /api/maps/{hash}/detail`：头部大卡 + 每个难度的清单（`difficulties[]`，按 info.dat 的
    顺序 Easy→ExpertPlus）+ 谱面级游玩汇总（次数 / 通关数 / 最佳分数 / 最佳准确率 / 首末时间）。
  - `GET /api/maps/{hash}/chart/{characteristic|difficulty}`：**打开时现场分析**这张谱面文件，
    返回音符数 / 音符跨度 / 文件大小 + **密度时间序列** + 本难度的游玩记录（`history[]`，
    已富化 + 带 file_available）。与 replay 详情同款「分层加载」：详情先渲染，这一页的结果随后补上。
  - ~~`/api/maps/{hash}/files`~~ **已删除**（用户 2026-10：不需要文件卡片）。
- **密度时间序列**（`maps/resolver.read_chart_timeseries`，用户 2026-10 要求）：一条谱面的
  "整体 / 左手 / 右手 / 炸弹"密度，**按 1 秒一桶**统计，不需要回放：
  - 只数玩家切得到的东西（color note + arc + chain）；手别取自对象的**头列**（`x < 2` 左、
    `>= 2` 右；arc/chain 归到起始那只手）；**炸弹单独一条曲线**（炸弹不切，混进音符密度会虚高
    ——回放详情的时间轴同样把炸弹排除在 density 之外）。
  - y 是**每秒密度**（notes/s），四条例线可直接比较；桶从第一个对象到最后一个对象，
    **空桶是真实的 0**（谱面里的空档），不是缺数据。再对 ±2 桶平滑：单个 1 秒桶可能装着一串
    和弦，尖峰会盖住整段（回放详情的 per-note 密度也做了同性质的平滑，见
    `timeline.DENSITY_SMOOTH`）。
  - **原始逐桶计数**也一并返回（`bucket_counts`）：平滑序列是"视图"，**计量是计数**，
    界面与测试都以计数为准。
  - **拍与秒必须同向换算**：`t = beat × 60 / BPM`，桶位置为
    `floor((beat − first_beat) × 60 / BPM / bucket_seconds)`。2026-10 修正前曾误用
    `BPM / 60`，把大量音符夹到尾桶；旧记录中「Cyaegha 尾端数百音符是谱面设计」的判断
    已被三音符夹具与真实谱面复核推翻。修正后 Cyaegha Expert 的平滑峰值从 **278 → 10.6
    个/秒**，计数总量不变；只影响谱面详情的密度序列，不改变 Replay 的分析结果或数据库。
    回归以多个 BPM、非零首拍、半秒桶与空档逐桶核对，不能只检查曲线长度或总数。
  - ⚠️ **v4 侧文件**：音符数据若存在 `<name>Data` 里（数组元素是索引），这一难度返回
    `available=false`，界面写"无法绘制"，而不是画一条 0 线。
  - ⚠️ **0 拍是真实位置**：早期写法用 `n.get("b") or 0` 判断，会把**第 0 拍上的音符整批丢掉**
    （v3 谱面第一小节很常见）；现在按"键存在"判断（`key in n and n[key] is not None`）。
- **音符数怎么算**（`maps/resolver.read_chart_stats`）：只数玩家切得到的东西——color notes
  加上该谱面有的 bomb / arc / chain（与 BeatSaver 的口径一致）。**v4 的谱面数据可能在独立文件里**
  （数组元素是索引），这种难度返回 `available=false`，界面写"谱面数据存在独立文件里，暂不统计
  音符数"，**绝不把 0 当成"这个难度没有音符"**。实测：v4 5 个难度 1–6ms，v2 大谱面 46ms。
  结果按「文件夹 + 最新文件 mtime」缓存（上限 200 条），改过的谱面会重读。
- **每页三张卡**（用户 2026-10 定稿）：**难度数据 / 时间序列 / 我的成绩**（文件卡片已按用户要求
  移除）。时间序列卡用 `lineChart` + `.chart-controls`，与回放详情的时间轴同一套组件与观感，
  但取数是**谱面本身**：四个勾选项"整体密度 / 左手密度 / 右手密度 / 炸弹密度"，
  默认勾选前三个（`drawMapTimeline()` 独立维护自己的曲线注册表与开关接线）。
  - **共用布局与渐入**：沿用 `.detail-grid` 的响应式三栏，时间序列使用普通 `.chart`
    而不是运动小图的 `.chart.small`；`freezeDetailCharts(root)` 只测量并固定本页图表一次，
    再无动画重绘以对齐 SVG viewBox。勾选曲线不改变高度，窗口缩放只重绘内容，不重复增高。
  - **共用入场流程**：`renderDetailTransition()` 处理两页的快速加载渐入与慢速骨架交接，
    容器从透明态进入 **420ms** 过渡；卡片同用 `cardIn 300ms` 与错峰延迟。
    取消旧渐入定时器后再渲染，防止快速连切被上一轮清理提前结束；骨架仍延迟 1.5 秒，
    难度骨架对应三张卡，不借用 Replay 的六卡高度。
  - **难度状态隔离**：每次切难度有自己的 `AbortController` 与请求序号；成功、错误、
    骨架定时器和延迟交接都核对当前谱面对象、难度、请求序号与页面节点。缓存只存响应，
    不写当前页面；缓存命中和新响应都在这条守卫后的渲染里提交 `chartSeries`。
    旧成功响应、旧错误和离开页面后的响应不得改图。重新打开同一谱面仍恢复该难度的数据。
  - **页签边界**：Replay 的绑定、active 切换与回放可见性查询只用
    `#detail-tabs .dt-tab`，不能全局查 `.dt-tab`——谱面难度同样使用这套视觉类名。
- **骨架屏延迟（用户 2026-10 反馈）**：难度页**必须**与 replay 详情一样等
  `MAP_DETAIL_SKELETON_DELAY`（1.5s）才出骨架。原先一点页签就出骨架，而解析只要几毫秒，
  骨架刚出现就被替换，观感是闪烁而不是加载。实测切页签时骨架出现次数 = 0。
- **每难度星级**：`_difficulty_star_index()` 按 `(map_hash, difficulty)` 取**标准玩法**的星级
  （BeatLeader `Standard` / ScoreSaber `SoloStandard`）。**不能混用其它玩法**：实测 Cyaegha 的
  ExpertPlus 标准是 12.04★、Lawless 是 12.29★，混用会让 Standard 那一行顶着 Lawless 的数值。
  同一规则也用在 `enrichment.map_star_index()`：**谱面头部的星级 = 它自己标准难度的最高星**，
  这样头部大卡与难度页永远同源（此前是"所有玩法取最大"，头部会显示 Lawless 的数）。
- **默认打开哪一页**：先看玩家自己的记录（打过的那一页，次数最多者优先），没有记录再退到
  最难的 Standard；都没有则取最后一个难度。理由：打开一张谱面通常是看"我练的那张"。
- **`maps.stars` 列不再是兜底**：`_map_library_item()` 只用 enrichment 索引。该列由旧的同步路径
  写入，可能带着被索引刻意忽略的玩法数值——曾经因此出现"排序用一个数、格子显示另一个数"。
- **返回 / Esc**：`.detail-topbar` 同款结构；Esc 由全局 keydown 处理，先判谱面详情
  （`mapDetailIsOpen()`）再判 replay 详情，两条路径都走各自的返回按钮。**返回按钮在 `init()` 里
  绑定一次**——早前绑在 replay 详情的渲染函数里，导致谱面详情页的返回与 Esc 全都无效（用户可见
  的 bug）。
- **导航项**：`nav.mapdetail`。直接点它而没有打开过谱面时显示提示语；打开过则恢复那张谱面
  （`switchTab` 里恢复页面/顶栏，离开时隐藏顶栏）。两条通路：谱面列表点行、导航项。
- **"正在打开"也算有内容**（2026-10 用户报告的提示与谱面同屏）：`openMapDetail()` 会先
  清空容器并显示它，请求还在飞的时候 `currentMapDetail` 仍是 `null`；此时切到别的页面再
  切回谱面详情，旧版 `switchTab` 的 `if (currentMapDetail)` 判定为假，于是把
  「点击一张谱面查看详情」的提示显示出来——而容器里已经是正在渲染的谱面，提示就留在了上方
  （只有 `openMapDetail` 会再把它藏起来，所以它一直挂到下一次打开谱面）。
  现在判定是 **`currentMapDetail || 请求在飞`**（`mapDetailReqSeq > 0 && 容器未隐藏`），
  提示只在真正什么都没有时才出现
- **谱面预览卡（2026-10，用户需求：难度页签右边一个按钮，点开就是内嵌 3D 看谱）**：
  - **设计必须是 Replay 详情那一套，不要另做一套**（用户 2026-10 明确要求，第一版做成了
    "自定义切换 UI + 隐藏页签"，被要求重做）：**页签条留在屏幕上**（它自己带选中态），
    下面是 `.panes-track` 同款滑块 `#md-track`（200% 宽、`.md-card` 各 50%、
    `translateX(0 / -50%)` + `--t-slow`）；`mapDetailTab(name, instant)` 负责切换与复位。
    **不要**改成"隐藏一张显示另一张"
  - **页签条长什么样**：难度页签 + 末尾的「谱面预览」都住在 `#md-diff-tabs` 里，
    而 `#md-diff-tabs` 放在 `.surface-title.md-strip-row` 的**左栏**（中/右栏是空占位）——
    与总览/历史的列表同一个三栏骨架。这一行是"紧凑"的来源：
    - 只有一个难度时：页签 91px + 「谱面预览」105px 紧挨着（实测 `strip 213px`），
      不再被拉伸到整行（用户报的正是这个）
    - 难度多时：`#md-diff-tabs .md-scroll` 是唯一的横向滚动容器，**难度页签与「谱面预览」
      都在它里面、顺序也是页签在前**，所以按钮永远跟在最后一个难度后面；11 个难度的实测
      是"整条一起滚动"，而不是按钮压住最后一个页签（那正是分成两个滚动盒子的后果）
    - **别再往 `.title-left` 里塞东西**：它是 flex 容器，塞进去的盒子 `max-width: 100%`
      不生效，会让内部滚动盒子失去收缩能力
  - **切难度也要滑**（用户 2026-10 要求，与 Replay 详情切卡片同一套动画）：
    `slideDifficultyPage(render)` 把"当前页"复制成一份冻结克隆放到新页旁边，轨道滑一张卡宽，
    结束后再拆克隆复位；第一次渲染（页里还是空的）不滑，骨架定时器仍走原来的渐入
    （它是占位符不是难度）
  - **页面全屏 / 全屏照搬**：预览卡标题行有与 Replay 详情同款的两个按钮
    （`data-chro-fullscreen` 分组 + `data-fullscreen-mode="page|native"`）。全屏逻辑已泛化：
    `chroFullscreenToggler()` 取所有分组、`bindChroFullscreenControls(root)` 按卡片接线、
    `chroViewerFrame()` 按当前卡片返回 iframe、`chroReplayPaneSelected()` 把"谱面预览卡打开"
    也算作 3D 卡在屏幕上（否则离开页面时会漏掉暂停）。**页签/顶栏/hero 在页面全屏时隐藏
    靠 `#tab-mapdetail.active` 那一组 CSS**，与 `#tab-detail.active` 平行
  - **iframe 只在第一次进预览卡时创建**（`loadMapPreview()`），回到难度卡不销毁，
    所以来回切换不会重载 3D 场景；`resetMapPreview()` 在 `openMapDetail`/`closeMapDetail`
    里清掉它（换谱面 / 离开页面必须让引擎停下）
  - **交给 3D 组件的是整张谱面**：宿主把 `/api/maps/{hash}/package` 的压缩包（info.dat +
    全部难度 .dat + 封面 + 音频，**一次全部**）交给 chro。**绝不能只给 info.dat**——
    实测（与本机 LocalChroViewer 独立版同款手法）组件不会自己去找其余文件，少一个就播不了
  - **通道是 postMessage + 注入脚本**（同一版块 §5.6b 的宿主桥）：`plugins/chro/index.html`
    里的桥脚本（生成器 `_tools/inject_chro_bridge.py`，产物随附 `inject_saberlab_bridge.py`）
    收到 `{type:'saberlab:loadmap', hash, difficultyLabel}` 后下载压缩包、合成一个 File、
    **用一次合成 drop 事件**交给组件自己的文件通道（`event.dataTransfer.files`；
    目标是 `#root` 的第一个子元素——实测派发到 `<body>` 会被忽略），等难度下拉可用后按
    **标签**选中对应难度，并通过 `{type:'saberlab:mapload', event:'load'|'ready'|'error'}`
    回报宿主
  - **为什么不能用 `?map=` 打开**（2026-10 实测，别再试）：SaberLab 版 chro 关掉了远端源
    （`VITE_ENABLED_SOURCES` 为空），而它自己的 `?map=` 走 BeatSaver 源，一进门就报
    "This source is not enabled on this ChroViewer deployment"；本地的地图解包函数只服务于
    "回放 → 它的谱面"那条链。所以地图必须由宿主喂给它的文件通道
  - **难度按标签匹配**：chro 的难度名 = 该谱面自己的 `_difficultyLabel`，没写标签时
    ExpertPlus 显示 `Expert+`（`parse-map-package.ts` 的 `difficultyLabel()`）；SaberLab 在
    扫描时记录的 `label` 就是这个值，所以直接拿它匹配（11 个难度实测全部命中）
  - **换难度 = 重开一次组件**（`location.replace('/chro/')` + 把请求存在 sessionStorage 里
    由新文档续做）：组件每次换难度都会重新解析谱面，而在解析过程中点击难度下拉会读到**旧的**
    选项列表——实测会陷入"永远选到同一个难度"的循环；重开可预测，压缩包走组件自己的缓存，
    不会二次下载
  - **文件在打开的一瞬间就交出去**（用户 2026-10 反馈"要等零点几秒才传入"）：请求写在
    **iframe 的 URL** 里（`/chro/?saberlabMap=<hash>&saberlabLabel=<label>`），桥在文档启动时
    读它并立刻开始下载——与 Replay 详情用 `?replayUrl=` 是同一个形状。早期版本是父页面在
    iframe 出现之后再 `postMessage`，那要等父页面醒来、再等一帧，实测多出零点几秒；
    而且这条路上有一个坑：桥脚本在 `<head>` 里，此时 `document.body` 还不存在，
    `loadMap()` 往 body 上挂"加载中"遮罩会抛 TypeError、整个请求被静默丢掉——**启动路径必须
    等 `DOMContentLoaded`**（实测踩到过：打开预览后查看器一直停在初始页，且宿主收不到任何回报）
  - **离开 3D 卡会暂停**（用户 2026-10 反馈）：`mapDetailTab()` 末尾调 `syncChroPlayback()`，
    与离开 Replay 详情的回放页一致；`resetMapPreview()`（换谱面 / 关页面）在拆掉 iframe 之前
    也先暂停一次。**注意**：点"当前已选中的那个难度页签"同样是"从 3D 卡回来"的常见动作，
    所以页签点击处理里 `mapDetailTab("page")` 必须**无条件**执行，只有页面重渲染是有条件的
    ——早版把两者一起 `return` 掉，暂停就漏了。`chroSendPause()` 与 `chroFrameDoc()` 现在
    都走 `chroViewerFrame()`，也就是"当前在屏幕上的那张 3D 卡"
  - **全屏接线的两个坑（2026-10 回归，用户报告"两个按钮都没反应"）**：
    ① 把接线从 `#chro-fullscreen-controls` 泛化成 `[data-chro-fullscreen]` 时，回放卡片那一组
    忘了加这个属性 → 它的按钮彻底失去 onclick；② `toggleChroNativeFullscreen()` 仍把
    `chroFullscreenToggler()`（现在是**数组**）当成单个元素调 `querySelector`，抛
    `TypeError: group.querySelector is not a function`，把整个「全屏」动作打断。
    **改这类共享选择器时，两张卡要一起改**，并跑 `verify_map_preview.py` 的第 8 步
    （它按两张卡 × 两个按钮逐个点，并读回宿主窗口状态）
  - **`?shell=webview` 不能丢**：宿主窗口用它开启外壳能力（含"让窗口铺满显示器"）。
    验收脚本若直接 `navigate('http://127.0.0.1:6980/')`，页面就落进浏览器模式，
    「全屏」只会退化成页面放大——这是脚本问题，不是功能问题。脚本改用
    `http://127.0.0.1:6980/?shell=webview`，非外壳模式下明确 SKIP 那一条
  - **`.replay-item` 被谱面卡片复用的连带 bug**（2026-10 回归）：`bindReplayItems()` 原来给
    所有 `.replay-item` 绑点击，而谱面卡片也带这个类（借用卡片布局），于是**点一张谱面会打开
    某个回放的详情页**。现在绑定 `.replay-item:not(.map-item)`，由
    `tests/test_map_detail_frontend.py` 钉住
  - **查看器会偶发卡住，桥有一次重试**（2026-10 实测）：同一个谱面在这个浏览器会话里反复预览时，
    嵌入式组件偶尔收下压缩包后一直停在自己的 "Loading map…"，既不报错也不给难度列表
    （实测：重开一次或换个会话就正常，说明是组件状态问题而不是压缩包坏了）。桥在
    `pickDifficulty()` 超时后**重启一次查看器并重放同一次请求**
    （`sessionStorage['saberlab:maploadRetried']` 标记，只试一次，避免死循环）。
    这是既有行为的延续：换难度时桥本来就会重启查看器
  - **画面尺寸跟卡片走**（2026-10 用户反馈"高度是固定的"）：`sizeMapPreviewFrame()` 与
    Replay 详情同一条规则——**非全屏时 16:9**（`wrap.clientWidth * 9 / 16`），
    **页面全屏时取卡片自身高度**（`max(240, wrap.clientHeight)`，铺满整窗不留边）；
    并且用一个 **ResizeObserver**（`watchMapPreviewSize()`，模块级单例，换谱面时
    `disconnect + observe` 复用）盯住卡片。早版只在创建 iframe 那一刻量了一次，
    所以窗口缩放、切卡、退出全屏都不会重算——"高度固定"就是这么来的
  - **验收**：`_tools/verify_map_preview.py`（**49 项**，可跑真实窗口 9222 或无头 Edge 9333；
    页签顺序与紧凑度、滑块位移恰为一张卡宽且带过渡、切难度时轨道确实在动、组件只被要求加载
    一次、查看器显示该谱面与播放控件、**打开预览后 15 秒内就绪且只请求一次**、
    **画面在 1200/1600/1000 三种卡宽下都保持 16:9**、同难度返回不重载、
    **离开 3D 卡后播放器变暂停且时钟停走**、换难度后标题显示的就是那个难度的标签、
    **两张卡的两个全屏按钮都接线且真的执行**、**单难度谱面**的页签不拉伸且按钮紧邻）
- **验收**：`tests/test_chart_timeseries.py` 验证时间桶位置与计数；
  `tests/test_map_detail_frontend.py` 用 Node 执行源码中的函数，固定缓存、旧响应隔离、
  页签作用域、图表一次性高度与渐入清理；真实窗口用 `_tools/verify_map_detail_timeline.py`
  （**45 项通过**），覆盖首次打开/切难度渐入、缓存切回、重复打开、勾选恢复、Replay 页签、
  乱序成功/失败、延迟骨架、错误/无图/无游玩状态、离开取消与窗口缩放。
  探针只在页面内注入受控响应，不修改真实谱面、回放、配置或数据库。
- **上面那条提示同屏的回归**：`_tools/verify_maps_layout.py`（无头 Edge，14 项）把谱面详情的
  请求人为放慢到 900ms，再在飞行中切页回来，断言提示没有出现、容器仍然可见；
  同一脚本还量了谱面管理的分页居中与控件尺寸

### 5.16b 官方曲目的回放为什么看不了，以及怎么修（2026-10，用户报告）

嵌入的 3D 组件用 `/([0-9a-f]{40})$/i.exec(replay.metadata.levelId)` 判断"这张回放属于哪张本地
谱面"——只认**自制谱面**。官方曲目的 `levelId` 是 `custom_level_SPINETERNALLY`（前缀由回放
工具加），正则匹配不到，组件于是直接拒收，报
`ScoreSaber replay is missing a custom map hash`；把这一处放宽之后，后面两处"必须是 40 位哈希"
的守卫又把它变成 `invalid Beat Saber map hash`（其中一处在取 SaberLab 的
`/api/maps/{id}/package` 之前）。

- **修法**：`_tools/patch_chro_official_replays.py` 把"身份"改成 **有 40 位哈希就用哈希，
  否则用去掉 `custom_level_` 前缀的关卡 ID**，并让读取本地谱面包的守卫接受这种 ID。
  关卡 ID 正好就是 SaberLab 自己的键：§5.16 把官方曲目存在 `data/levels/<关卡ID>`，
  `package` 端点按同一个键取包，所以两边天然对上
- **这是对构建产物的补丁**（插件是外部 GPL-2.0 项目 Local-ChroViewer 的 Vite 产物）：脚本
  幂等、可 `--revert`，首次修改前留 `.orig` 备份。**插件一旦重新构建就要重跑**；更好的做法是
  把同样的改动落到插件源码里（见 HANDOFF 待办）
- **SaberLab 侧同时放宽了身份识别**：`normalize_level_id()` 去掉 `custom_level_` 前缀，用于
  `/api/maps/{hash}/detail`、`/api/maps/{hash}`、`/api/replays/by-map/{hash}`、`ensure-preview`
  与下载器入口——带前缀的请求不会再变成 404，也不会把 `custom_level_<hash>` 形式的历史数据
  当成另一张谱面
- **验收**：`_tools/verify_official_replay.py`（**6 项**，真实窗口 9222 或无头 Edge 9333）
  打开一条官方曲目回放，断言它真的在播且两种报错都不出现，再验一条自制谱面回放没有被这个
  补丁带坏

### 5.16 补全丢失谱面（2026-10，用户需求）

回放只带一个关卡标识：`replays.map_hash`（BSOR 的 `mapHash`）。它的取值把两种世界分开，
各自有各自的来源，这也是整个功能的骨架：

| 标识形态 | 是什么 | 下载来源 |
|---|---|---|
| 40 位十六进制 | 自制谱面（SongCore 哈希） | BeatSaver：`GET api.beatsaver.com/maps/hash/<小写hash>`，用返回的 `versions[].downloadURL` |
| 其它大写 ID（`100BILLS` / `CRABRAVE`） | 游戏自带（OST/DLC）曲目 | `cdn.songs.beatleader.xyz/<歌名去掉非字母数字>.zip` |

- **官方曲目为什么本地永远找不到**（实测，别再试别的路子）：它们不是关卡文件夹，而是打包
  资源（`Beat Saber_Data\StreamingAssets\BeatmapLevelsData\<id小写>\<id>`、`DLC\Levels\<名>\<名>`），
  没有 info.dat、没有难度 .dat，`compute_level_hash()` 第一步就是找 info.dat，找不到即跳过
  ——本机 29 个官方 ID、65 条回放因此全部断链
- **官方压缩包的命名规则**：CDN 上是**歌名**去掉非字母数字（`Be There For You` →
  `BeThereForYou`、`$100 Bills` → `100Bills`），**不是**关卡 ID 的驼峰化（`BETHEREFORYOU`
  驼峰化得到 `Bethereforyou`，CDN 上没有这个名字）。所以 `official_zip_names()` 把**歌名拼法
  放在第一位**，关卡 ID 只作兜底（单词 ID 两种拼法相同，如 `CRABRAVE` → `CrabRave`）。
  实测本机 29 个官方 ID 中 **18 个能下到**；其余 11 个（Alone / Icarus / Numb / Starboy /
  Stronger / Get Lucky / Light It Up / Ludicrous+ / Mercenary / I Feel It Coming /
  DABADABADABADABA）是**付费 DLC**，所有命名变体都不存在——这类只能如实报告「取不到」
- **落盘位置**：`data/levels/<标识>/`（SaberLab 自己的目录，**不动用户的游戏目录**，用户决定）。
  配置键 `game.downloaded_levels_dir` 可覆盖（默认 `PROJECT_ROOT/data/levels`），设置页不暴露
- **查找顺序**：CustomLevels → 下载目录 → 下载流程。`MapResolver` 现在有两个根
  （`levels_dir` + `extra_levels_dir`），`scan()` 遍历两者；下载根里的**文件夹名就是身份**
  （自定义谱面是哈希、官方曲目是关卡 ID），所以扫描不重算哈希。`resolve()` 命中不到时会先
  `adopt_downloaded_folder()` 直接认领（比全量扫描便宜得多），再退到扫描。**注意
  `trigger_scan=False` 的批处理契约**：那条路径不查找、不认领，有测试钉住
- **两个入口**：总览的「补全丢失谱面」（批量任务 `map_fill`，`POST /api/maps/fill`，进度写
  `_tasks`，前端 1.5s 轮询，与其它任务同一条链路；`GET /api/maps/missing` 给出待补清单与分类
  计数）；谱面详情的 3D 预览入口（`POST /api/maps/{hash}/ensure-preview`，单张，
  `loadMapPreview()` 在 `in_library` 为假时先补一次，成功则重开详情页，失败则 toast：
  `not_found` → 「无法下载 DLC 谱面」，其它 → 「谱面文件获取失败」）
- **礼貌与安全（都是硬要求）**：
  - **每个来源同时只有 1 个请求**（`_host_lock`）+ 请求间最小间隔 0.4s；总并发由
    `network.download_concurrency`（默认 3，钳制 1..8）决定。这两个 CDN 是社区公益镜像
  - 429/5xx 读 `Retry-After` 退避重试（最多 3 次），404 **不重试**
  - 只允许 https；响应体上限 64MB；**魔数校验**（必须 `PK`）不信 Content-Type
  - **解包是本仓库第一段 zip 解包代码**，所以防护写全：拒绝绝对路径 / 盘符 / `..` / 反斜杠
    穿越，条目数 ≤4000，解压后总量 ≤256MB（zip 炸弹），空包即失败
  - **一致性校验**：自定义谱面复算 SongCore 哈希，等于请求的标识即为 `hash`；
    **哈希不同不等于错**（实测 `052AAA3A…` 取回的是 `82D8B81F…`）——BeatSaver 只提供该谱面的
    **当前版本**，谱面上传者重传过就会换哈希，而玩家的回放指向旧版本。所以哈希不符时再比歌名
    （去符号、忽略大小写，允许一方包含另一方），相符即接受并记为 `version_changed`，并且
    照旧按**请求的标识**落盘（这样指向旧版的回放都能命中）；只有歌名也对不上才报
    `hash_mismatch` 并丢弃整个临时目录。官方包没有可复算的标识，做结构校验（可解析、
    有难度）加歌名比对，不符只记 `unverified` 而不静默接受
  - **谱面本身不在库里时，谱面详情页也要能打开**（否则旧回放点「查看谱面」只能看到报错）：
    `/api/maps/{hash}/detail` 返回 404 时，前端用 `replayFallbackRow()` 走
    `GET /api/replays/by-map/{hash}` 取回「歌名、谱师、玩过几次、上次玩的难度」，用这些元数据
    把页面搭起来并把 `in_library` 置 false，3D 卡据此先补谱面。**注意路径里那个 `by-map`
    字面段**：`/api/replays/{replay_id}` 是它的同级路由，实测会把
    `/api/replays/lookup?map_hash=…` 这种请求吃掉并答「replay 不存在」
  - **详情新增两个字段**：`in_library`（行在但文件夹没了时为 false）与 `played_difficulty`
    （回放记录的难度：谱面文件缺失时这是唯一的难度信息，3D 组件按它选中难度）
  - 解到临时目录 → 校验 → **原子搬入** `data/levels/<标识>/`（已存在时先改名旧目录再替换，
    读者永远看不到半个谱面）
- **副作用**：下载成功后立刻 `upsert_map`（`hash_source='downloaded'`）并清掉该标识的负缓存，
  所以封面、`/api/maps/{hash}/package`（3D 看谱）、详情、NPS 立即可用，不需要重启。
  官方曲目入库后**连带修好**一个既有问题：`analysis/engine.py` 的"时长不足 98% 即未完赛"判定
  以前对官方回放恒不触发（`maps.song_length` 取不到），现在这些回放有了真实时长
- **验收**：
  - `tests/test_map_fill.py`（**45 项**，纯离线，网络被替身替换）：标识判别、官方命名候选与
    顺序、BeatSaver 取 URL、404 不重试、429 退避、超大响应拒绝、**同源请求不重叠且按间隔**
    （跑的是真实 `http_get`，只替身连接）、解包穿越/盘符/反斜杠/条目数/总量/空包、哈希不符拒绝、
    版本变化接受并标记、官方结构校验、落盘原子性与无残留、`missing_levels` 分组与计数
    （大小写不敏感）、双根扫描与直接认领、`trigger_scan=False` 不查找、配置项钳制
  - `_tools/verify_map_fill.py`（**25 项**，真实窗口 9222 或无头 Edge 9333）：按钮存在且位于两个
    刷新按钮之间、进了 `ACTION_BUTTONS`（运行期禁用）、`/api/maps/missing` 的分类与计数、
    **点真实按钮**起任务 → KPI 显示 → 完成后按钮恢复 → 完成文案不是裸 key、三条预览文案已翻译、
    `ensure-preview` 对 DLC 返回 `not_found`、**从回放走进缺谱面的详情页再打开 3D 卡**，
    取不到时页面显示「无法下载 DLC 谱面」
  - `_tools/verify_official_replay.py`（**6 项**）：官方曲目与自制谱面的回放都会在 3D 区域里
    真的播起来（依赖 §5.16b 的产物补丁；插件产物更换后这项会先失败）
  - `_tools/live_map_fill.py`（**15 项，需要联网**）：对真实 BeatSaver / BeatLeader 下载
    「一张自制谱面 + 两个官方曲目 + 一个 DLC」，校验自制谱面哈希复算相等、官方包可解析且有时长、
    DLC 报 `not_found`、下载根里没有残留临时目录
### 5.17 chro 插件：两个项目各自独立演进（2026-10，用户决定）

**决定**：`Local-ChroViewer`（桌面版）与 `saberlab/chro`（SaberLab 插件分发）按**两个独立项目**
维护，不再追求互相同步。SaberLab 继续带着插件一起开发自己的功能；Local-ChroViewer 专注自己的
桌面体验。

这不是偷懒，而是被实测逼出来的：两个产品的**渲染模式不同**。SaberLab 的插件是挂在 `/chro/`
下的纯客户端应用（`createRoot(#root)`），而 Local-ChroViewer 的主体走整文档 hydrate
（`hydrateRoot` + prerender）。把当前源码的构建产物按 `/chro/` 挂好实测会 `Invariant failed`
并渲染空白，而且 `saberlab/chro` 里那份 `createRoot` 形态的产物在本仓库历史里找不到对应源码
（v1.0.0 的 `start.ts` 与当前一致）。所以"一条命令重建插件产物"这件事本身不成立，除非先给
分发加一个 SPA 模式——那是分发自己的架构工作，不是同步问题。

**因此当前的产物维护方式**（这是正式方式，不是临时手段）：

| 位置 | 状态 |
|---|---|
| `plugins/chro/`（SaberLab 仓库，gitignored） | 在用的插件产物，随 SaberLab 一起就地演进 |
| `Local-ChroViewer/saberlab/chro/` | 同一份产物的独立副本，两份**字节一致**（`684952b2…`） |
| `_tools/patch_chro_official_replays.py` | 幂等地把官方曲目身份修复打到**两份**产物上（`--check` 只报告，`--revert` 还原） |

源码层面的修复依然落在插件源码里，这才是长期位置（见下表）；产物补丁只是让在用的产物与
源码结论一致，插件将来重做产物时可以整个删掉。

**源码现状**（已改，`pnpm exec vp check` 全绿，311 文件）：

- `web/src/core/replay/types.ts` — `replayMapHash()`：有 40 位哈希用哈希，否则用去掉
  `custom_level_` 前缀的关卡 ID（官方曲目回放的身份，缺失即回放被拒）
- `web/src/sources/local/provider.ts` — `fetchLocalMap()` 接受关卡 ID、不再小写化；并且
  **移植了 SaberLab 的「缺谱面自动补全」**：首次取包失败时调
  `POST /api/maps/{id}/ensure-preview`，再轮询 `GET /api/maps/{id}/package` 直到拿到包。
  后端没有这个端点时静默跳过，行为退回原文案（不认识的契约不该影响它）
- 顺手修掉**既有**类型错误：模块级 `resolveLocalFirst` 调用 hook 闭包里的 `downloadOptions`
  （TS2304 + 缺 `BeatSaverMapSource` 导入），此前让 `pnpm verify` 一直是红的

**重建工具链**（备好并逐段验证，等分发将来做 SPA 模式时可直接用）：

| 脚本 | 作用 |
|---|---|
| `web: pnpm exec vp build --base=/chro/`（`VITE_ENABLED_SOURCES=""`） | 产出挂在 `/chro/` 下的本地源版本 |
| `_tools/package_chro_saberlab.py --stage <dir>` | 抽取 assets/environments/fonts/hitsounds/models/textures/twemoji（丢掉 `.br`/`.gz` 孪生文件） |
| `_tools/build_chro_shell.py --from <public> --out <index.html>` | 生成入口文档并把根相对路径重写到 `/chro/` |
| `_tools/inject_chro_bridge.py --target <index.html>` | 注入宿主桥（幂等） |
| `_tools/verify_chro_dist.py --dist <dir>` | 临时静态服务 + 无头 Edge 验证产物，**再**动 SaberLab |

**桥与源码的分工**：源码自带 `use-host-playback-commands.ts`（暂停契约），而注入桥的 DOM
点击式暂停不依赖 React 挂载，弱机上更可靠；两者收到同一条 pause 消息、都幂等（只在播放时
暂停、只暂停不重启），保留双保险是有意的。


## 6. 前端要点

- 零依赖单文件：`index.html`（静态骨架 + 设置/详情模板）、`app.js`（渲染 + 交互）、`style.css`
- 动态设置表单：`/api/settings/schema` 驱动（`renderSettingsForm`，`hidden` 项跳过）
- **枚举项（`type: "enum"`）不用原生 `<select>`**（2026-09）：原生下拉的展开列表由
  操作系统绘制，页面无法改外观，故改为「毛玻璃触发按钮 `.enum-trigger` +
  `openEnumPopover()` 复用通用 `openPopover` 列表」——材质/出入场动画/锚定翻转/
  关闭方式与 PP 预测窗口同源。**值存在隐藏 `<input data-key>` 上**，所以
  `collectSettings()` 的通用读取与脏值检测、后端保存链路都不需要改动；
  `settingsEnums` 存每次渲染的 {value,label}，`openPopover({anchor,body,onClose})`
  新增列表变体样式 `.popover-list/.popover-opt`（`.popover:has(.popover-list)`
  只覆盖 padding/圆角，PP 预览等其它 popover 不受影响）。
  **开关语义**：`enumOpenId` 记录当前展开的下拉，同一按钮再点即 `closePopover()`
  收起（而不是重播入场动画）；`onClose` 里复位，因此点击外部/Escape/滚动关闭后
  仍可再次打开。改这块务必保留这个判重，否则会出现"点两次都只弹不收"的观感 bug
- 图表：`lineChart`（SVG + crosshair 悬停）；详情页等高逻辑 —— `fixDetailLayout()` 进入时
  一次性固定图表高度（不要改回 `height: auto` 实时计算，会触发高度正反馈循环）
- 毛玻璃层：`#acrylic-backdrop`（fixed inset 0 + blur），移动中 `.moving` class 拉满模糊

### 6.1 右键菜单框架（context menu，2026-09）- **下拉/浮层别再造第二套**：菜单与下拉都建立在通用 `openPopover` 之上；如果要做的是
  "从固定选项里选一个"，直接用 **§6.3 的毛玻璃列表下拉**（`openListPopover`），不要用
  原生 `<select>`，也不要另写浮层。
- **目标**：新增右键功能 = 往注册表加一条，**不改框架代码**。文件都在 `frontend/app.js`：
  - `CTX_MENUS`：注册表，每项 `{ match: 选择器, items(el) -> 菜单项[] }`
  - `bindContextMenus()`：**文档级委托，只绑一次**（在 `init()` 里 `I18N.init` 之后调用，
    因为菜单文案要 `t()`）。用 `closest` **自内向外**匹配，第一个命中的注册项胜出
  - `openContextMenu(items, x, y)`：渲染 + 定位 + 点击分发
  - `buildContextMenuHtml()` / `ctxMenuItem()` / `ctxSeparator()`：菜单项构造
- **复用而非重造浮层**：`openPopover` 新增 `at: {x, y}`（视口坐标，优先于 `anchor`）与
  `className`、`blockContextMenu`。因此菜单的毛玻璃材质、出入场动画、Escape 关闭、
  外部点击关闭、滚动/缩放关闭**全部继承**现有组件；`blockContextMenu` 让"在别处再点
  右键"先关旧菜单（捕获阶段监听 `contextmenu`），避免菜单叠加
- **确认弹窗卡片与菜单同一材质**（2026-10 用户要求）：`.modal` 的卡片背景从
  `var(--surface)`（不透明灰、**无模糊**）改成与 `.popover` **完全相同的**
  `rgba(20,22,30,0.66)` + `blur(14px) saturate(150%)`。菜单开着时点出确认弹窗，
  两张卡必须是同一片玻璃；半透明遮罩（`.modal-overlay` 的 0.42）负责文字对比度，
  卡片本身不再靠不透明背景压住内容。改任一处材质数值时另一处要同步（回放删除与
  谱面删除用的是同一个 `openModal`）
- **菜单项**：`{ label, action(ev, el), danger?, disabled? }` 或 `ctxSeparator()`。  `label` 由注册项自己 `t()` 取（框架只做 `escHtml` 兜底转义）；`danger` 红字（危险操作），
  `disabled` 置灰且不触发。**点击后框架先关菜单再执行 action**，action 抛错会上报 toast
- **放行原生菜单的两种情况**：① 目标在 `input/textarea/[contenteditable]` 内（文本复制
  粘贴是刚需）；② 命中注册项但 `items()` 返回空数组
- **剪贴板**：`copyText(text, okKey)` 统一处理，按"Clipboard API → 聚焦后重试 → 隐藏
  textarea + execCommand"三级回退；**文档未聚焦时 `writeText` 会失败**（窗口在后台点菜单
  就会这样），所以有聚焦重试；空值走 `ctx.copy_empty` 明确提示，不静默。
  该函数随"复制 Replay ID"在 2026-09 删除（不留死代码），2026-10 因两个复制项按同一套
  回退重新引入；`okKey` 让两项各自给 toast 文案（`ctx.copied` / `ctx.copied_link`）
- **卡片要带齐菜单需要的数据**：注册项只从元素上读（`ctxData(el, KEYS)` 把 `data-*` 收成
  对象，**缺失保持 `null` 而不是空串**——"没有 key"要和"空 key"区分开，前者置灰）。
  卡片渲染函数负责把数据写进 `data-*`（如 `replayItem()` 的 `data-song-author` /
  `data-key` / `data-in-library`）
- **已注册：`.map-item`（谱面管理卡片）+ `.replay-item`（回放卡片）**（2026-10 用户确定，
  两类卡片同一套顺序）：
  **查看详情 · 查看游玩记录 · [查看谱面] · —— · 复制歌曲名称 · 复制下载链接 · ——
  · 打开谱面所在位置 · 删除谱面文件**（回放卡片的后两项是「打开文件所在位置 · 删除回放文件」）
  - **`.map-item` 必须注册在 `.replay-item` 之前**：谱面卡片的 class 是
    `replay-item map-item`（复用卡片布局），而 `closest` 自内向外取**第一个命中的注册项**，
    顺序反了谱面卡片就会落进回放菜单（`tests/test_ctx_menu.py` 钉住这个顺序）
  - 谱面卡片**没有"查看谱面"**（它本身就是谱面）
  - 谱面卡片的两项复制数据不在 `/api/maps/library` 的投影里（该接口只给列表要用的列），
    由 `fillMapMenuData()` 在渲染前按 map_hash 逐条补齐（每张一次、会话内缓存，失败就保持
    "未知"让对应项置灰）；**菜单本身绝不发请求等数据**
  - 后两项依据 `data-folder-available` 置灰（前端乐观标记；真正判定在
    `_map_folder()`：行不存在 → 404，文件夹不在 → 410）
- **谱面的两个 OS 动作**（2026-10）：
  - `POST /api/maps/{hash}/reveal` → `explorer.exe <谱面文件夹>`。**打开的是这张谱面自己的
    文件夹**，不是 `custom_levels_dir` 根目录（用户明确要求；谱面文件夹就是这张谱面）
  - `POST /api/maps/{hash}/recycle` → 复用回放删除的两步安全顺序：① `_send_to_recycle_bin()`
    把**整个谱面文件夹**移入系统回收站（`FileSystem::DeleteDirectory(..., SendToRecycleBin)`，
    与文件的 `DeleteFile` 同一个 helper，按 `target.is_dir()` 选方法）；② 成功后才
    `Repository.delete_map()` 在**单个事务**里删 `maps` + `map_ranked_cache` +
    `scoresaber_leaderboards` 三处，并 `enrichment.invalidate()`（快照按 maps 表构建）。
    **①失败则 ②不执行**，绝不出现"记录没了但文件夹还在"
  - **回放行故意不删**：`.bsor` 是用户自己的数据，用户的指令是删谱面；那些回放继续可用，
    只是列表不再有封面、右键不再有「查看谱面」（与"谱面文件夹缺失"的回放同一表现）。
    要删回放是另一个动作（回放卡片的「删除回放文件」）
  - `_send_to_recycle_bin()` 失败会**重试一次**（间隔 0.4s）：2026-10 实测本机有两次回收站
    操作首次返回非 0（防病毒/索引/云同步短暂占用），重试即成功；真失败（只读卷等）两次都失败
    仍按 500 报出
  - **查看同谱面记录**填的是**歌曲名**（`data-song`）。历史搜索 `histScore` 只匹配
    歌曲名与 5 位 beatmap_key，**填 map_hash 永远搜不到**——早期版本就是这么错的，
    别改回去
  - 两个文件相关项依据 `data-file-available`（由后端 `file_available` 决定）禁用
  - **删除回放**：`POST /api/replays/{id}/recycle` → 两步，顺序不可颠倒：
    ① 文件走 `Microsoft.VisualBasic.FileIO.FileSystem.DeleteFile(..., SendToRecycleBin)`
    （**移到回收站，不是永久删除**；路径经 base64 传参，避免引号/中文被命令行破坏）；
    ② `Repository.delete_replay()` 在**单个事务**里移除 `replays` 行与全部派生数据
    （notes/metrics/windows/motion_series/accuracy_curve/ai_reports），
    `experiments` 的 baseline/candidate 引用置空（实验记录本身保留）。
    **先成功进回收站才删记录**——中途失败不会留下"记录没了但文件还在"；
    文件本就不在时允许仅清理记录。内存里的能量曲线缓存（`services/energy_curve.py`
    的模块级 `_CACHE`）经 `energy_curve.clear(replay_id)` 同步失效
  - **`explorer /select` 必须拆成两个 argv 项**：`["explorer.exe", "/select,", path]`。
    写成单参数 `"/select," + path` 会被 explorer 判为无效开关并**回退打开"文档"目录**
    （2026-09 用户报告的 bug）。这一条极易被"整理代码"时改坏，改动前先看这里
  - `POST /api/replays/{id}/reveal` → `explorer /select,<file>`，文件不在时回退打开
    所在文件夹（`reveal` **不碰数据库**；`list` 按 `file_path` 去重，所以"仅移除记录"
    的旧行为会让文件在下次扫描时重新入库——删记录时文件已进回收站，不会复活）
  - 弹窗复用 `openModal({ actions })`（新增按钮区）；**点击动作不自动关弹窗**，
    由调用方在请求成功后再关，失败则保留弹窗并恢复按钮——避免"关了但没删掉"的误导
  - **查看谱面**只在**谱面的谱面文件夹在本库里**时出现（`data-in-library`，由后端
    enrichment 依据 maps 表给出），否则收起该项——谱面详情页没有可显示的内容
  - **复制歌曲名称**写入 `歌曲名 - 曲师`（曲师来自 maps 表，经 enrichment 附加在回放行上；
    本库里没有这张谱面时只有歌名，不会留下空的 ` - ` 尾巴）；**复制下载链接**写入
    `https://beatsaver.com/maps/{5 位 key}`，没有 key 的谱面（本地 WIP 谱面）该 **项置灰**
- 新增场景时给元素加 class（或 `data-*` 携带所需数据），再往 `CTX_MENUS` 加一条即可。
  **注意**：早期的 `[data-copy]` 通用兜底已随"复制 Replay ID"一起移除（无调用方＝死代码），
  现在是两个明确命名的复制项各自调用 `copyText()`；不要再加通用兜底注册项
- **验收脚本**（DOM 级，不依赖肉眼）：
  - 入仓版：`_tools/verify_ctx_menu.py`（**42 项**，`run.bat` 窗口 + CDP 9222；两类卡片的
    菜单项与顺序、`查看谱面` 的出现/收起、两个复制项的**剪贴板往返**（用 `Get-Clipboard`
    读回真值）与 toast 文案、无 key 项置灰并实测计算样式、查看谱面跳转与返回、
    谱面卡片未落进回放菜单、谱面两项动作的**路由与确认流程**（确认前不发请求、取消不发请求、
    确认后 POST 到该谱面、toast 与列表刷新；只读/破坏性动作在页面内被替换成记录调用，
    绝不真删），见 `tests/test_map_context_actions.py` 的单元覆盖
  - `_tools/probe_recycle_folder.py`：**真实回收站探针**（无 mock）——造一个带 info.dat /
    ExpertPlus.dat / song.egg 的谱面文件夹，调 `_send_to_recycle_bin()`，然后读回收站
    Shell 命名空间核对条目名与原始父目录
  - 未入仓的 `_tmp/verify_ctx_menu.mjs` / `_tmp/verify_ctx_features.mjs` /
    `_tmp/verify_delete_flow.mjs` / `_tmp/verify_recycle_endpoint.py` 等是 2026-09 那轮的
    一次性脚本（**`_tmp/` 会被清空**，需要时按上面的入仓脚本重写，不要去找它们）
  - **测试环境注意（两个坑，都已踩过）**：① 无头 CDP 里
    `Input.dispatchMouseEvent` 在页面失焦时会**静默丢事件**，右键会"时好时坏"
    ——验收脚本已改为页面内 `MouseEvent` 分发（确定性），真实鼠标路径单独验证过；
    ② 固定 `sleep` 等待应用初始化会在慢启动时假失败，应**轮询等待列表项出现**

### 6.1b 大库规模下的搜索与分页（2026-09 实测 + 待办压力测试）- **历史页分页**：复用总览同款分页组件（`renderPagination(total, page, pages, unit,
  onPage, el)`，历史页传 `unit="count"`、容器 `#history-pagination`、注入 `onPage`），
  **只按数量分页、一页 300 条**；搜索词/时间范围变化时回到第 1 页
- **分页位置**：与总览**同款三栏对称 grid**（`#tab-history .surface-title` 复制
  `#tab-overview .surface-title` 的规则；左栏=筛选、中栏=分页、右栏空占位）→ 分页
  置顶且严格水平居中（实测偏差 0px）；历史内容包在 `.surface` 里、筛选控件进 `.title-left`
- **谱面管理页（2026-10 第四版）同样如此**：`#tab-maps .surface-title` 用同一套 grid，
  右栏补一个空的 `.title-right` 占位（`index.html`），分页实测居中偏差 0px；三页同一读法
- **筛选行的尺寸规则（2026-10，用户报"正序/倒序与重新扫描比别的控件小"）**：
  这两个按钮去掉了 `.mini` 后回到常规尺寸，之前能塞进一行只是因为 `.mini` 更窄。
  实测根因：grid 两侧 `1fr` 栏在 1440 窗口下只有 **336px**，而该行内容自然宽度 ≈ **480px**
  （输入 200 + 排序 93 + 方向 78 + 重新扫描 90 + 三个 gap），`nowrap` 的 flex 行于是把最后
  一个按钮压到 **45px 宽**、文字折成三行、控件高 **98px**（其余控件也被压到 58px）。
  修法：`#tab-maps .filters` 允许 `flex-wrap`，行内控件 `flex: 0 0 auto` + 按钮
  `white-space: nowrap`（宽度 ≥ 约 1800px 仍是一行；更窄时折成两行，控件尺寸不变），
  排序控件在窄栏里不再强制 132px；分页在中间栏，居中不受折行影响。
  **别用"改小按钮"来修这类问题**：那是把用户抱怨的现象当成解法
- **搜索取数策略**：**默认与搜索都用 `HIST_SEARCH_LIMIT = 10000`**（前端常量）后
  本地过滤 + 分页——默认视图也必须能翻页（只显示 300 条却不能翻页是不对的）。
  `/api/history` 的 `limit` 上限已放宽到 **50000**（原 2000）
- **为什么是 10000（实测数据）**：
  | 项目 | 实测 |
  |---|---|
  | 服务器取数 | 约 **1083 字节/行**（424 行 = 448 KB / **23 ms**） |
  | 客户端过滤+排序 6000 行 | **2 ms** |
  | 渲染一页 300 行 | **13 ms**（9230 DOM 节点） |
  | 2000 条上限在 6000 条库上的命中损失 | **漏 67%**（4527 命中只搜到 1512） |
  推算：6000 行 ≈ 6.3 MB、10800 行 ≈ 11.4 MB。**瓶颈只在取数上限，不在过滤或渲染**
- **⚠️ 待办（用户指定，尚未执行）：极端规模压力测试**——把本地库放大到
  **10800 条回放**（约 25 倍于当前 424 条），逐项验证 SaberLab 的各项功能是否仍能
  正常工作：入库/扫描、批量分析（4 进程落库）、总览按天/游玩段分页、历史页搜索与
  分页、详情页、时间轴、对比、AI 报告、云端同步、以及**数据库体积与查询耗时**
  （当前 424 条时 `data/saberlab.sqlite` 约 100MB；线性放大后需重新评估索引与
  分页查询）。**做法建议**：复制真实库到临时路径后按行克隆放大（`replays` + 派生表
  同步放大，文件路径需指向真实文件或标记为缺失），在**单独进程/端口**上测，避免
  污染用户库；测完删除临时库
- 搜索只匹配**歌曲名 + 5 位 beatmap_key**，**不匹配玩家名**：一台机器通常只有一个
  玩家，未登录默认名（如 `Noob`）与登录后是同一人（实测谱面交集 56 张），
  按玩家名过滤只会让同一人的记录互相隐身

### 6.2 多语言（i18n）
- **两条映射约定（改动文案时必须成对改，否则静默失效）**
  1. **`err`/`msg`/`task.current` 段的键是「后端消息原文」**（zh 表没有这三段），
     由 `tErr()/tMsg()` 查表翻译。**改后端文案就必须同步改 en/ja 的键**，
     否则 `t()` 走原文兜底——不报错，只是译文悄悄失效
  2. **前端点号键**（`t("err.offline")`、`data-i18n`）三语表必须齐备，
     缺失时界面会**直接显示键名本身**
  - 陷阱：后端长消息常用**相邻字符串隐式拼接**（跨两行），
    按整串搜索/替换会**找不到**（历史上就因此在"改后端文案"时漏改了一条）
- **回归测试 `tests/test_i18n_mapping.py`**（5 项）覆盖上述约定：
  用 AST 取后端中文字面量（正确处理隐式拼接与 f-string 占位）做模板匹配，
  并校验引用键齐备、en/ja 覆盖 zh 基准键集、三段语言间条数一致

- 机制：`frontend/i18n.js`（`I18N.init/t/renderLangSwitch`）+ `frontend/i18n/{lang}.json`
  对照表（zh-CN 为基准表，缺失 key 回退中文）；语言偏好存 localStorage（`saberlab.lang`）
- **语言自动发现**：`GET /api/i18n/langs` 扫描 `frontend/i18n/*.json`（文件名正则
  `[a-z]{2}(-[A-Z]{2})?`），读各文件 `lang.name` 作按钮名——新增语言只需放一个
  json 文件，设置页语言卡片自动出现按钮
- 文本接入：静态文本 `data-i18n` / `data-i18n-placeholder` / `data-i18n-title`
  （含子元素的标题文本须包 `<span data-i18n>`）；动态文本用 `t(key, params)`；
  后端消息用查表翻译（en/ja 表以中文原文为 key，`{param}` 模板正则匹配，
  zh 恒原文）：`tErr(msg)` 错误消息（`err` 段）、`tMsg(msg)` 确认消息
  （`msg` 段：设置保存/清缓存）、`tTaskCurrent(msg)` 任务进度
  （`task.current` 段）
- 设置项文案：schema 的 label/description 是中文，前端按配置项 key 查
  `set.{key}.label/.desc` + `set.group.{group}`（缺失回退中文）
- 图表标签（TL_LABELS/TL_VALUE_FMT）依赖 dict，须在 `I18N.init()` 后经
  `buildTimelineI18n()` 构建（模块顶层调用 t() 时 dict 尚未加载）
- Squircle 圆角：`style.css` 末尾 `@supports (corner-shape: squircle) { .surface, .kpi { border-radius: 40px; corner-shape: squircle; } }`
  （Chrome 139+ 生效，旧浏览器回退 12px；**须放样式表末尾**——放前面会被
  `.kpi` 自身的 border-radius 覆盖，见 §4.18 踩坑）

### 6.3 可复用组件：毛玻璃列表下拉（**下拉选择一律用它**，2026-10 用户指定）

**任何"从几个固定选项里选一个"的控件都用这个组件，不要用原生 `<select>`**——原生下拉的
展开列表由操作系统绘制，页面完全无法改外观，在毛玻璃界面里是一块突兀的系统控件。用户已把
这套外观定为标准，并要求标记为可复用（2026-10）。

- **调用**：`openListPopover({ id, options, value, trigger, ariaLabel, onPick })`
  （`frontend/app.js`，紧邻设置页的 enum 控件）：
  - `id` 稳定字符串，同时作为"同一个触发按钮再点一次 = 收起（不是重播入场动画）"的判重键；
  - `options` = `[{ value, label, disabled?, reason? }]`，`disabled` 项置灰不可点、右侧显示原因；
  - `value` 当前值（打勾项）；`trigger` 触发按钮元素；`onPick(value, label)` 选中回调；
  - **状态归调用方**：本函数只渲染、标记当前值与回调，不保存任何业务状态。
- **外观与交互全部继承通用 `openPopover`**：毛玻璃材质、`popIn/popOut` 出入场、锚定在触发
  按钮下方且空间不足自动翻转、点击外部 / Escape / 滚动 / 缩放关闭。**不要另写一套浮层。**
- **样式类**（`style.css`，与设置页同一套，改一处两处同时变）：
  `.enum-select`（含 `min-width`）→ `.enum-trigger`（+ `.open` / `.enum-caret` /
  `.enum-value`）；列表内 `.popover-list` / `.popover-opt`（+ `.active` / `.disabled` /
  `.popover-opt-label` / `.popover-opt-check` / `.popover-opt-reason`）。
- **标记结构**（值放隐藏 input，按钮只显示文案）：
  `<div class="enum-select"><input type="hidden" id="X" value="...">` +
  `<button class="enum-trigger" id="X-trigger" aria-haspopup="listbox" aria-expanded="false">` +
  `<span class="enum-value">当前项文案</span><svg class="enum-caret">…</svg></button></div>`；
  选中后由 `onPick` 写回隐藏 input 并更新 `.enum-value`。
- **已采用**：
  1. 设置页 enum 控件（`settingsControl()` 的 `type: "enum"` 分支 → `openEnumPopover(cid)`；
     **首个使用者是设置 → 星级色谱**，外观标准即出自这里）。它多一层 schema 语义，值存在
     `input[data-key][data-enum]` 上，`collectSettings()` 的读取与脏值检测链路不变。
  2. 谱面管理的排序（`openMapsSortPopover()`，值在隐藏 `#maps-sort` 上，选中即 `loadMaps(1)`）。
- **i18n**：`data-i18n*` 只覆盖 `i18n` / `i18n-placeholder` / `i18n-title` 三种属性；
  需要 aria-label 时**在 JS 里用 `t()` 设置**（`data-i18n-aria-label` 不被识别，静默无效）。

### 6.4 AI 报告语言
- `ai/prompts.py`：`build_system_prompt(lang)` = 英文基础规则 + 强制输出语言指令
  （MUST/务必/必ず）+ 语言化小节名（## 结论 / ## Conclusion / ## 結論）——提示词
  主体必须保持英文，否则 LLM 偏向跟随主体语言（§4.16 实测教训）
- 入口透传：`/api/ai/analyze/{id}?lang=`（前端 `I18N.lang`）；
  规则报告（`ai/fallback.py` `_TEXT` 三语言模板）同样跟随
- 是否调用 LLM：由 `ai.ai_report_enabled` 配置决定（`run_ai_report` 单点短路）
- **报告唯一入口 = 详情页手动生成**（v2.1.0 决策，2026-09-01）：分析管线
  （watcher `process_file`/`analyze_all_new` 等）从不生成报告——批量曾对每条
  replay 调 LLM（~20s/条），清缓存全量批量 ≈ 2 小时「卡死」；前端按钮随报告
  状态显示「生成报告」/「重新生成」。回归守卫 `tests/test_watcher_ai_report.py`

## 7. 测试与调试

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
- **UI/E2E 最终验收必须使用独立窗口**：通过 `run.bat` 或不带 `--browser` 的
  `backend/host.py` 启动 WebView2；浏览器模式只允许做局部诊断，不能算最终 UI/
  E2E 验证。独立窗口截图/交互需按窗口标题 `SaberLab — Beat Saber 本地分析实验室`
  定位
- Golden Fixture #001：SECRET BOSS Expert（`tests/test_bsor_parser.py`，2069 notes 全断言）
- **可重跑的验收脚本都在 `_tools/`**（`_tmp/` 会被清空，2026-09-29 已因此丢过一批
  `_tmp/probe_*.py` 毛玻璃/KPI/布局探针，文中其他章节提到的 `_tmp/verify_*` 多数也已不在）：
  - 真实窗口：`run.bat` 前设 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222`，
    再用 `_tools/cdp.py`（标准库 CDP 客户端）驱动；`_tools/verify_guard_ui_e2e.py`（33 项
    UI 流程）、`_tools/verify_guard_live.py`（来源闸门 35 个原始 socket 探针）、
    `_tools/verify_replay_fullscreen.py`（3D 回放两个全屏 40 项，§5.6c）、
    `_tools/verify_ctx_menu.py`（右键菜单 42 项，含剪贴板往返与谱面确认流程，§6.1）、
    `_tools/verify_maps_layout.py`（谱面管理排布 + 谱面详情提示状态机 14 项，§5.15）、
    `_tools/verify_map_preview.py`（谱面预览卡、滑动动画与两张卡的全屏接线 48 项，§5.15）、
    `_tools/verify_modal_material.py`（弹窗与菜单同材质 7 项，§6.1）
  - 无需桌面窗口时可用**无头 Edge**（CDP 9333）跑同样的检查：`_tools/verify_maps_layout.py`
    与 `_tools/verify_modal_material.py` 正是从这个端口量的——只读 HTTP 服务，不碰窗口
  - 前端纯函数/注册项的回归测试用 Node 跑（`tests/test_ctx_menu.py`、`test_map_detail_frontend.py`）：
    从 `app.js` **原样抽取函数体**再配桩执行，不复制实现、不另写一份即将过期的替身
  - 网站：`_tools/verify_site.py`、`_tools/intro_frames.py`（§10）
  - 图标：`_tools/icon_preview.py`、`_tools/make_icon.py --check`（§11）
  - 注释语言：`_tools/check_comment_lang.py`（AGENTS §16.1，列出仍含中文的注释行）
  - `_tools/shot.ps1` 按窗口标题截图、`_tools/pngstats.py` numpy 像素统计（无视觉模型时
    验证 UI 用）
- 调试注意：窗口模式日志在 run.bat 控制台；`print` 到管道/重定向需 `flush=True`

## 8. 构建与发布约定（2026-08 用户决策，强制）

> 目标：开发环境与用户版本行为一致，提前暴露用户版才会出现的问题。

1. **构建后清理**：每次 PyInstaller 构建导出成功（`GitHub_Build\<版本>\` 归档生成）后
   **必须删除临时构建内容**——`build/`（PyInstaller 中间产物）与 `dist/`（构建输出）。
   `_tools/export_github_pkg.ps1` 末尾已自动清理；手动构建按此约定清理。
   版本归档是构建的唯一留存；参考旧版代码一律从 `GitHub_Build\<版本>\saberlab-src\` 获取，
   **不得依赖本地 dist/build**（它们随时会被删除）。
2. **禁止回退引用构建**：代码/检测逻辑不得存在"回退到本地构建产物"的路径。
   反例（已修复）：chro 曾回退 `../Local-ChroViewer/dist`，开发环境能加载而用户版缺失——
   现检测唯一路径为第一方插件目录 `plugins/chro/`。
3. **开发环境 = 用户版行为**：开发环境的检测路径/依赖解析/权限必须与发布版一致。
   任何"开发便利"路径若与用户版不同，必须评估行为分叉；宁可先复制/放置产物到用户版
   同款位置，也不加开发专属回退。
4. **每个版本必须写 RELEASE_NOTES.md**：每次打包导出新版本（
   `_tools/export_github_pkg.ps1` 产出 `GitHub_Build\<版本>\` 后），必须同步编写
   `GitHub_Build\<版本>\RELEASE_NOTES.md`——面向用户的**简洁双语**更新日志（标题 +
   🔍更新内容 + 🚀快速开始 + 🔧完整变更记录链接，格式参照已发布版本的 release
   notes），随 GitHub Release 一并发布；不得跳过或事后补写。
   **注意导出脚本会先清空 `GitHub_Build\<版本>\`**，所以这个文件必须在导出**之后**写。
5. **发布包不携带任何密钥（2026-09 用户决策 + 事故复盘）**：
   - `packaging\saberlab.spec` **不嵌入** `config/config.yaml` 与 `.env`。曾经嵌入
     作者配置时，冻结版会从 PyInstaller 的 `_MEIPASS` 读到它，于是本地构建保留作者的
     端口/路径，配置里的任何秘密也可能进入发行包。缺文件是安全的：`load_config()`
     从默认值起步。当前用户版 zip 实际只含空的 `.env.example`（2026-09-14 复核）。
   - ⚠️ **以下三条是决策记录，尚未实现**（2026-09-14 复核：zip 无 `.env`；代码无
     `PLACEHOLDER_API_KEY` / `is_placeholder_secret()`；`_tools/check_release_secrets.py`
     不存在）：
     - 用户版 zip 放**真实 `.env`（占位符值）**，覆盖陈旧目录残留的旧 `.env`
       （只放 `.env.example` 覆盖不掉它，结果就是"用户版带着旧密钥"）；
     - 占位符 = `sk-` + 35 个重复字符（`backend.config.PLACEHOLDER_API_KEY`），
       `is_placeholder_secret()` 把重复字符 body 判定为**不是**密钥
       （`ai.configured` False、界面「未配置」、不会拿去请求服务端）；
     - 导出硬闸门对源包与 zip 扫描真实值 / key 形状 token（`sk-` + 20 位以上、
       前后不能是标识符字符），命中即中止导出。
   - **密钥只从本地配置文件读取（2026-09-14 用户决定，取代早先的"来源可见化"）**：
     `Config.ai_api_key` 每次访问都重新读 `<config 同级>/.env`，
     **完全不读进程环境变量**。原因：机器级用户环境变量（`HKCU\Environment` 里的
     `DEEPSEEK_API_KEY`）会随双击启动进入应用，曾让"从未配置过的用户版"显示真实密钥
     掩码（HANDOFF §4.37）。改为只读文件后，环境变量无论怎么设都不生效。
     - 实现：`read_env_file(path, names)` 报告**文件里写了什么**；`Config.dotenv_path`
       在 `load_config()` 里指向 config.yaml 所在目录，所以 `--config` 显式路径也能
       让两者同处一地。空值/注释行不算配置。
     - 顺带消掉的旧问题：以前 `load_dotenv()` 注入环境变量且**从不覆盖已有变量**，
       于是"保存新密钥后重启仍用旧值"——host 的重启 spawn 靠 `dotenv_key_names()`
       剥离子进程环境变量来兜底；现在密钥不走环境变量，那条兜底只作为纵深防御保留。
     - 设置页与 `/api/status` 仍带 `ai.api_key_source`（`env_file` / 空），文案为
       「来源：本地配置文件」。回归测试 `tests/test_api_key_source.py`(10)，其中一条
       专门断言**环境变量必须被忽略**。
     - `ai.api_key_env` 配置项现在只用于选择**在 `.env 文件里找哪个键名**
       （默认 `DEEPSEEK_API_KEY`），不再表示"读环境变量"。
   - **测试夹具不得使用"像 key 的假 key"字面量**（例如 `sk-bdb…`）——任何密钥扫描
     （包括将来的导出自检）都无法区分它与真 Key。需要 key 形状时在运行时拼装
     （`"sk-" + "a1b2c3…"`），源码里不要出现字面量（现有测试已按此写）。

6. **发布后先更新 HANDOFF §1，再继续开发**（2026-09 加固，同类事故已两次）：
   每完成一次发布，第一件事是把 `docs/HANDOFF.md` §1 的**当前快照**改成与实际一致
   （HEAD 提交、工作树状态、测试基线计数），然后才动下一轮代码。HANDOFF §1 声称的
   状态与 `git log -1` 不符时，以仓库为准——旧快照要求过"不要清理工作树"这类
   保护性动作，会让下一位开发者拒绝正常操作。**不要**只改 §1 以外的章节。
7. **发布前跑"忽略规则是否藏了该进仓库的文件"检查**（2026-09 加固；2026-10 修正做法）：
   历史：`.gitignore` 里有一条**整目录** `tests/`，而 29 个测试文件是在这条规则之前提交的，
   于是**此后新增的测试全部被静默忽略**——v2.3.0 发布前实测发现 20 个测试文件（含
   `test_map_fill.py`、`test_i18n_mapping.py`）根本不在仓库里，公开仓库跑的测试集与本地
   不一致）。只改 `tests/` 一条不够：任何"忽略整目录、但目录里已有被跟踪文件"的规则都是
   同一类错误，所以检查要覆盖**全部**目录规则。
   ⚠️ 不要用 `git ls-files --others --exclude-standard tests/` 做这个检查——被忽略的目录在
   该命令下**恒为空**，漏掉的文件根本不会出现。
   正确做法是逐条目录规则比较"跟踪/未跟踪"：

   ```bat
   .venv\Scripts\python.exe _tmp\audit_ignored_trees.py
   ```

   判定：某条目录规则下**同时**存在被跟踪与未被跟踪的文件 → 该规则值得复核（可能正在静默
   吞掉新文件）。输出为 0 个问题时才算通过。
   当前 `tests/` 的写法（2026-10 修正）是**目录仍忽略、但文件放行**，这样新测试文件在
   `git status` 里可见、必须显式 `git add`，而 `__pycache__` 之类仍被挡住：

   ```gitignore
   tests/
   !tests/
   !tests/*.py
   !tests/fixtures/*.bsor
   ```
8. **文档语言与代码注释语言**（2026-09-16 用户决策，规则原文见 `AGENTS.md` §16.1）：
   - **文档只有中文**：`AGENTS.md` 与 `docs/` 下的文件（两个 README 除外）只保留中文版。
     **不要新增译文副本，也不要保留已有的**——副本会静默过期，需要别的语言时用工具翻
     一份即可。删除译文前必须先确认它是中文版的子集（本章程曾因此藏过一条只存在于英文
     版的记录）。
   - **README 是唯一例外**：`README.md`（英文）与 `README.zh.md`（中文）都维护。
   - **代码注释与文档字符串一律英文**：`#`、`//`、`/* */`、`<!-- -->` 与 Python
     docstring。读代码不需要切换语言。
   - **用户可见文案仍是中文**（§9.1）：界面文案是产品面，不是文档。特别注意 `err` /
     `msg` / `task.current` 三段的**中文原文就是查表键**，改一个字译文就静默失效
     （由 `tests/test_i18n_mapping.py` 兜住）。

## 9. 常见坑速查

1. **venv 无 pip**：装包一律 `py -3 -m pip --python .venv\Scripts\python.exe install ...`
2. **双模块**：main.py 不要直接 import backend.host 的模块级状态（见 §5.2，走 dialog.py）
3. **WebView2 透明限制**：pywebview transparent 模式无窗体级透明（客户端区域=窗体底色），
   毛玻璃 A/B 方案因此不可行；DWM 背景板仅标题栏可见
4. **chro 独立构建**：3D 回放是外部项目 Local-ChroViewer（仓库同级）；改其
   源码后必须 `pnpm build`，并把构建产物放入第一方插件目录 `plugins/chro/`
   ——**唯一检测路径，无回退**：产物不落该目录则 `/chro/` 不挂载、
   详情页显示安装提示
5. **打包**：`uvicorn.Config(app=app)` 传对象而非导入字符串（frozen 下不可解析）；
   `PROJECT_ROOT` 在 frozen 下 = exe 同目录
6. **控制台编码**：中文输出在 GBK 控制台正常；管道重定向时确认编码/加 flush
7. **`server.host` 只能是回环名**（2026-09）：来源闸门拒绝一切非回环 Host，所以
   `0.0.0.0` 之类的监听地址会让界面完全连不上、且没有任何提示。设置项已收紧为本机
   枚举，加载时越界值回退到默认并打日志；回环集合由 `backend/config/__init__.py` 的
   `LOOPBACK_HOSTS` 单点定义，闸门与设置 schema 都读它（改集合只改这一处）。
   启动器 `host.py` 固定绑回环，不受该设置影响——**不要把闸门放开去支持局域网访问**，
   本程序是本地应用。

## 10. 项目网站（GitHub Pages，2026-09）

项目宣传网站放在 `site/`，与应用本体完全独立：构建脚本（`build.py` / `site_charts.py`）
不 import `backend/`、不读用户数据，也不参与应用打包。唯一的例外是本地开发工具
`site/tools/export_run.py`（见下文"真实一局"），它不在发布流程里运行。
发布地址 `https://zircon10086.github.io/SaberLab/`（仓库子路径）。

页面顺序：开场切割动画 → 首屏读数面板（一刀的 Pre / Center / Post）→ 真实一局
（逐刀图表 + 4×3 网格）→ 工作方式 → 应用截图 → 功能 → 下载。视觉语言是"测量仪器"：
细线、角标、等宽读数；红/蓝只表示左/右手，琥珀色只表示丢分。

```bat
.venv\Scripts\python.exe site\build.py           :: 构建到 site\dist（已被 .gitignore 的 dist/ 覆盖）
.venv\Scripts\python.exe site\build.py --serve   :: 构建并在 http://127.0.0.1:8000/SaberLab/ 预览
```

- **纯静态 + 零依赖**：`site/build.py` 只用标准库，Pages 流程只需 runner 自带的
  `python3`。模板在 `site/templates/`，文案在 `site/i18n/<语言>.json`（扁平键），
  语言与路径在 `site/site.json`（默认语言在根目录，其余语言各占一个子目录，如 `zh/`；
  `detect` 决定哪些浏览器语言从首页进入该语言，见下文"到站语言"）。
- **构建即闸门**：某语言缺键、有键没被模板用到、未知占位符、坏的相对链接/图片
  （含 `srcset`）、坏的页内锚点——任何一项都让构建失败，于是发布流程不会上线半翻译
  或断链的页面。`tests/test_site_build.py` 证明每道闸门都会真的触发。
- **转义规则**：文案值默认 HTML 转义；只有以 `_html` 结尾的键可以含标签，且只能嵌套
  上下文占位符（`{{repo_url}}` 等），不能嵌套别的文案键。
- **链接用相对路径**：普通页面的 `{{root}}` 是 `./` / `../`，所以从磁盘直接打开
  `dist/` 也能看；`404.html` 会被任意 URL 返回，因此用绝对子路径 `/SaberLab/`。
  以后换自定义域名，只改 `site.json` 的 `base_url`。
- **404 只有一张**：GitHub Pages 只认根目录的 `404.html`，它包含每种语言各一段
  （用 `templates/_not_found_block.html` 按语言渲染），标题保持语言中立。
- **截图是派生物**：`site/assets/img/*.webp` 由 `docs/screenshots/*.png` 生成
  （原图 3840px、共 12MB → 两档宽度共约 660KB）。换截图后重新生成：

  ```bash
  for n in overview replay chro; do for w in 1920 960; do
    ffmpeg -y -i docs/screenshots/$n.png -vf "scale=$w:-2:flags=lanczos" \
      -c:v libwebp -quality 82 -compression_level 6 site/assets/img/$n-$w.webp; done; done
  ffmpeg -y -i docs/screenshots/saberlab-logo-transparent.png -vf "scale=972:-2:flags=lanczos" \
    -c:v libwebp -quality 90 site/assets/img/logo.webp
  ffmpeg -y -i docs/screenshots/overview.png -vf "scale=1200:-2:flags=lanczos,crop=1200:630:0:0" \
    -q:v 3 site/assets/img/og.jpg
  ```
- **发布流程** `.github/workflows/pages.yml`：只在 `main` 上 `site/**` 或流程文件本身
  变化时运行（也可手动触发）；构建 → 上传产物 → 部署。第三方 action 全部**固定到
  提交 SHA**（注释里写版本号），升级时同时改 SHA 与注释。仓库需一次性设置
  **Settings → Pages → Source：GitHub Actions**，默认 token 无法替你打开 Pages。
- **网站文案**：读者是潜在用户，规则沿用 AGENTS §9.1 的精神——只写已经实现的功能，
  不写计划；数字与说法以 README 与本文档为准。当前没有任何统计/追踪脚本、也不加载
  外部字体或 CDN；要接入属于产品决策（本地优先是卖点，先问用户）。
- **开场动画** `site/assets/intro.js`（WebGL2 手写，无第三方库）：蓝色光剑慢动作切开
  方块，HUD 按游戏计分口径依次测出切前挥刀、离中心距离、切后跟随，读数面板逐行点亮、
  总分计数，然后页面在面板周围淡入。方块正面的箭头是游戏里的实心楔形，轮廓
  `ARROW_OUTLINE` 与应用切片详情的箭头相同（见 §11）。
  - **几何取自读数面板**的 `data-pre-deg` / `data-center-cm` / `data-post-deg`；面板上
    印的分数、进度条与总分由 `tests/test_site_build.py` 用
    `backend.analysis.scoring.cut_scores` 按官方规则核对。换示例刀时改这三个属性和
    面板数字，二者不一致测试会失败——动画、HUD 与印刷数字不可能各说各的。
  - **何时不播**：每个标签页会话只播一次（sessionStorage `saberlab.intro`）；带 `#`
    的深链、`prefers-reduced-motion`、没有 WebGL2 或浮点渲染目标、着色器/上下文任何
    错误，都直接显示页面。`<head>` 内联脚本另设 2.5 s 兜底：intro.js 没接管就撤掉遮罩。
    无 JS 时页面原样可读。播放中按键（Tab 除外）、点击、滚轮、触摸、切走标签页、
    明显改变窗口尺寸都会跳过；面板上的「重播」可再看。
  - **可复现**：画面是时间轴位置的纯函数，`?intro-t=2.4` 冻结在 2.4 s，
    `window.__saberlabIntro.seek(t)` 移动冻结帧（仅冻结模式暴露）。
    `_tools/intro_frames.py [desktop|phone] [t…]` 批量截帧到 `_tmp/intro_*.png`。
  - **构图**：相机用离轴镜头把方块、手与挥刀弧线框进读数面板让出的区域（桌面 >980px
    面板在右；更窄时面板缩小贴底）；HUD 标签被限制在该区域内，不会钻到面板下面。
  - **滚动条**：播放期间锁滚动，落地时滚动条回来造成的布局位移由读数面板的 FLIP
    过渡吸收。不要改用 `scrollbar-gutter: stable`：保留的滚动条槽画在固定定位的舞台
    之上，会在右侧留下一条暗边（已踩过）。
- **真实一局**：`site/data/run.json` 是开发者本人一局回放的逐刀数据，由
  `.venv\Scripts\python.exe site\tools\export_run.py <回放 ID 前缀>` 经 Repository
  从本地库导出（只读；不含玩家名/ID、文件路径与回放 ID；去掉炸弹与连击段元素）。
  `site/site_charts.py` 在构建时把它排成内联 SVG 图表、网格与各项数字——纯排版，唯一
  的派生量是"最弱的 10 秒"（按起点滑动的 10 s 均分，Miss/Bad 计 0，取第一个最小值）。
  数据字段缺失或与文案键重名都会让构建失败。换一局：重新导出再构建。
- **字体**：字标用 Beon（Bastien Sozeau，SIL OFL 1.1），自托管在
  `site/assets/fonts/`，许可证 `OFL.txt` 同目录随站点发布，页脚注明出处；正文用系统
  字体栈。
- 渲染验收（非应用 UI，可用 headless Edge）：`_tools/verify_site.py`（32 项）在桌面
  1440px 与手机 375px 下检查样式生效、单个 h1、图片全部解码、滚动后无未揭示元素、无横向
  溢出、无控制台错误、404 与语言切换；再验到站语言（见下条，12 项）、开场动画的完整播放
  （桌面/手机，终态逐项核对）、Escape 跳过、「重播」按钮尺寸与换行、重播 + 跳过按钮、
  二次访问、深链、减少动态效果、无 WebGL2、无 JS，以及图表悬停读数。需要带 WebGL2 的
  headless Edge：本机 `_tools/start_headless_edge.ps1` 启动的实例即为硬件 WebGL2；先起
  预览服务器 `site\build.py --serve`。
  - ⚠️ **本机 Edge 报告的语言是 `zh-CN`**，网站首页会把它送去 `/zh/`。所以两个脚本都用
    `Emulation.setUserAgentOverride` 的 `acceptLanguage` 把语言钉成 `en-US`，并清掉
    `saberlab.lang`；新写的网站脚本也要这样做，否则访问首页的检查会落到中文页。
- **到站语言**（2026-09-30，用户需求）：只有默认语言的首页（英文根目录）会选语言，按
  顺序：① 从本站其他页面来的（`document.referrer` 以站点根开头）→ 不动；② 访客在语言
  切换处选过 → 按选择（`localStorage` `saberlab.lang`，由 `site.js` 在点击 / 中键点击
  切换链接时写入）；③ 浏览器首选语言（`navigator.languages[0]`）匹配某语言的 `detect`
  前缀 → 去该语言（`zh` 覆盖 zh-CN / zh-TW / zh-HK，都进简体中文）。跳转用
  `location.replace`，保留查询串与锚点，跳转前隐藏页面避免闪一下英文。
  - **`/zh/` 本身永远不跳转**：分享出去的中文链接对谁都得是中文，搜索引擎也要能收录它。
    所以"选过英文的人打开 `/zh/` 链接"看到的仍是中文——这是有意的。
  - 配置：`site/site.json` 里非默认语言的 `"detect": ["zh"]`；构建检查前缀是小写语言
    标签、不被两种语言同时认领、默认语言不能有。构建把 `{"home", "urls", "detect"}`
    写进首页 `<head>` 内联脚本（其余页面为 `null`），脚本在开场动画判定**之前**运行，
    跳转时不会先启动动画。
  - 测试：`tests/test_site_build.py` 的 `LanguagePickTest` 用 Node 在假浏览器里跑构建
    出来的真实内联脚本，逐条核对上面的判定表（含繁体、前缀误配、站内来源、失效的记忆值、
    锚点、`/zh/` 不跳转）；真实浏览器里的同一组行为由 `_tools/verify_site.py` 复核。

## 11. 应用图标（2026-09）

图形：一个方块从箭头尖端被竖直切开，左半红、右半蓝（与游戏里的左右手、应用标题栏
字标 SABER 红 / LAB 蓝一致），两半沿切口错开，光剑在切缝与方块上下露出。

**方块箭头是实心楔形，不是 ">" 形折线**（2026-09-30 用户更正）：平顶、两侧短边、下方
尖端。标准轮廓是切片详情里的 `frontend/app.js` `SLICE_NOTE_ARROW`（500 视框，方块
450 宽），它与用户按游戏实物描绘的参考图一致。图标与网站开场动画都按方块尺寸等比使用
这一轮廓，不单独放大；`tests/test_app_icon.py` 的 `NoteArrowShapeTest` 核对三处一致。
以后任何地方要画方块，都从这一轮廓取形状。

- **唯一手工编辑的文件**是母版 `docs/screenshots/saberlab-icon.svg`，其余全部生成：

  | 文件 | 用途 |
  |---|---|
  | `frontend/saberlab.ico` | 窗口标题栏 / 任务栏 / exe 图标；浏览器模式的标签页图标 |
  | `site/assets/favicon.svg` | 网站图标与页头、下载区的标志（与母版逐字节相同；Pages 只发布 `site/`） |
  | `site/assets/favicon-32.png` | 不支持 SVG 图标的浏览器 |
  | `site/assets/apple-touch-icon.png` | 180 px，底色为网站背景（iOS 会把透明区域填黑） |

  README 直接引用母版。
- **重新生成**：改完母版后

  ```bat
  powershell -File _tools\start_headless_edge.ps1
  .venv\Scripts\python.exe _tools\make_icon.py          :: 写出上表四个文件
  .venv\Scripts\python.exe _tools\make_icon.py --check  :: 只比对，不一致时退出码 1
  ```

  栅格化交给浏览器：headless Edge 在每个目标尺寸上直接把 SVG 画进同尺寸画布（不是
  大图缩小），像素经 CDP 取回；同一台机器上结果逐字节稳定，所以 `--check` 可用。
  `.ico` 含 16/20/24/32/40/48/64/96/256 九档：96 及以下为 32 位 DIB，256 为 PNG 压缩
  （Windows 对这一档的要求）。写入器只用标准库。Win32 `LoadImage` 与 .NET
  `System.Drawing.Icon` 均已实测能读出全部九档。
- **窗口图标为什么要显式传**：pywebview 默认从 `sys.executable` 取图标，源码运行时那是
  `python.exe`（任务栏显示 Python 图标）。所以 `backend/host.py` 的
  `webview.start(icon=APP_ICON)` 显式传 `frontend/saberlab.ico`；打包版由
  `packaging/saberlab.spec` 的 `EXE(icon=...)` 把同一个文件嵌进 exe。文件缺失不致命，
  pywebview 会回到可执行文件自带的图标。已在 `run.bat` 窗口实测：标题栏小图标与任务栏
  大图标均为 SaberLab 图标。
- **小尺寸是设计约束**：16 px 下要能认出红蓝两半，32 px 起能认出箭头。所以不用细线和
  细小高光——切缝约 18 单位（256 画布）、方块占 184 / 256；箭头按游戏比例，不为小尺寸
  放大。改图形时先用 `_tools/icon_preview.py <svg>` 在 16–256 px、深浅背景上看实际像素，
  再生成。
- **回归测试** `tests/test_app_icon.py`：箭头形状与标准轮廓一致（图标、开场动画）、
  网站副本与母版一致、`.ico` 九档结构正确、
  像素抽查（左红右蓝、角落透明，能抓出 BGRA 通道颠倒与行序上下颠倒）、SVG 自包含
  （无脚本、无外部引用、`url(#…)` 都有定义）、窗口与前端确实引用了图标文件。
