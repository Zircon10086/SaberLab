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
config/        config.yaml
packaging/     PyInstaller spec + 打包文档
_tools/        调试工具（cdp_stack/chro_smoke 等）
_tmp/          测试临时区（探针/截图脚本，可随时清空）
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
  翻译只发生在 `backend/main.py` 的 `TRACK_CACHE_COLUMNS` / `_track_column()` 一处。
  `_palette_public_payload()` 还会把旧缓存里 `method='r80'` 这类短名映射回公开 id，
  否则改名后旧行的"当前生效档"会读不出来。
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

### 6.1 右键菜单框架（context menu，2026-09）
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
- **菜单项**：`{ label, action(ev, el), danger?, disabled? }` 或 `ctxSeparator()`。
  `label` 由注册项自己 `t()` 取（框架只做 `escHtml` 兜底转义）；`danger` 红字（危险操作），
  `disabled` 置灰且不触发。**点击后框架先关菜单再执行 action**，action 抛错会上报 toast
- **放行原生菜单的两种情况**：① 目标在 `input/textarea/[contenteditable]` 内（文本复制
  粘贴是刚需）；② 命中注册项但 `items()` 返回空数组
- **剪贴板**：`copyText(text, okKey)` 统一处理，按"Clipboard API → 聚焦后重试 → 隐藏
  textarea + execCommand"三级回退；**文档未聚焦时 `writeText` 会失败**（窗口在后台点菜单
  就会这样），所以有聚焦重试；空值走 `ctx.copy_empty` 明确提示，不静默
- **已注册：`.replay-item`**（2026-09 用户确定，功能已实装）：
  **打开详情 · 查看同谱面记录 · —— · 打开文件所在位置 · 删除回放文件**
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
    文件本就不在时允许仅清理记录。内存里的 `_energy_curve_cache` 同步失效
  - **`explorer /select` 必须拆成两个 argv 项**：`["explorer.exe", "/select,", path]`。
    写成单参数 `"/select," + path` 会被 explorer 判为无效开关并**回退打开"文档"目录**
    （2026-09 用户报告的 bug）。这一条极易被"整理代码"时改坏，改动前先看这里
  - `POST /api/replays/{id}/reveal` → `explorer /select,<file>`，文件不在时回退打开
    所在文件夹（`reveal` **不碰数据库**；`list` 按 `file_path` 去重，所以"仅移除记录"
    的旧行为会让文件在下次扫描时重新入库——删记录时文件已进回收站，不会复活）
  - 弹窗复用 `openModal({ actions })`（新增按钮区）；**点击动作不自动关弹窗**，
    由调用方在请求成功后再关，失败则保留弹窗并恢复按钮——避免"关了但没删掉"的误导
- 新增场景时给元素加 class（或 `data-*` 携带所需数据），再往 `CTX_MENUS` 加一条即可。
  **注意**：早期版本的 `[data-copy]` 通用兜底与 `copyText()` 已随"复制 Replay ID"
  一起移除（无调用方＝死代码）；将来若某个菜单项需要复制，按当时需求重新引入
- **验收脚本**（DOM 级，不依赖肉眼）：
  `_tmp/verify_ctx_menu.mjs`（坐标定位、菜单项、Escape、不叠加、输入框放行）、
  `_tmp/verify_ctx_features.mjs`（菜单项全貌、同谱记录填歌名并过滤、删除二次确认、
  缺失文件禁用）、`_tmp/verify_delete_flow.mjs`（成功/失败两条 UI 路径，**stub 掉
  fetch 不真删**）、`_tmp/verify_recycle_endpoint.py` +
  `_tmp/verify_recycle_bin_fs.py`（真实删除 → 读回收站 `$I*` 元数据核验原始路径与
  内容字节数）、`_tmp/verify_popover_regression.mjs`
  - **测试环境注意（两个坑，都已踩过）**：① 无头 CDP 里
    `Input.dispatchMouseEvent` 在页面失焦时会**静默丢事件**，右键会"时好时坏"
    ——验收脚本已改为页面内 `MouseEvent` 分发（确定性），真实鼠标路径单独验证过；
    ② 固定 `sleep` 等待应用初始化会在慢启动时假失败，应**轮询等待列表项出现**

### 6.1b 大库规模下的搜索与分页（2026-09 实测 + 待办压力测试）
- **历史页分页**：复用总览同款分页组件（`renderPagination(total, page, pages, unit,
  onPage, el)`，历史页传 `unit="count"`、容器 `#history-pagination`、注入 `onPage`），
  **只按数量分页、一页 300 条**；搜索词/时间范围变化时回到第 1 页
- **分页位置**：与总览**同款三栏对称 grid**（`#tab-history .surface-title` 复制
  `#tab-overview .surface-title` 的规则；左栏=筛选、中栏=分页、右栏空占位）→ 分页
  置顶且严格水平居中（实测偏差 0px）；历史内容包在 `.surface` 里、筛选控件进 `.title-left`
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

### 6.3 AI 报告语言
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
- `_tmp/` 探针（可复用）：`probe_transparent.py`/`probe_dwm.py`（毛玻璃能力）、
  `probe_kpi*.py`（KPI/任务卡片样式）、`probe_layout.py`（详情图表高度）、`probe_height.py`
- `_tmp/shot.ps1` 按窗口标题截图、`_tmp/pngstats.py` numpy 像素统计（无视觉模型时验证 UI 用）
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
