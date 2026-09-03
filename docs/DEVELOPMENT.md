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
others/        设计文档（见 §8 索引）
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
  HANDOFF §4.26②）；`hidden: True` 的项由"游戏路径"卡片接管（原生文件夹对话框 + 自动验证）
- 原子写回：tmp → flush → os.replace；config.yaml 损坏自动备份 `.corrupt-<ts>`。
  遇到损坏警告先核对日志中的**绝对路径**：测试夹具常位于 `_tmp`，不要把测试
  临时配置的预期警告误判成用户 `config/config.yaml` 损坏
- 关键项：`ai.ai_report_enabled`（"使用 AI 生成报告"开关——**只作用于详情页
  手动「生成报告」**：勾选时 `run_ai_report` 调用 LLM，不勾选直接短路到规则
  报告；分析管线从不生成报告，见 §6.2）；`analysis.slope_group_notes`
  （note 分组大小）；`analysis.window_seconds/window_step_seconds`（已弃用，hidden 保留兼容）；
  `player.star_palette`（星级色谱预设：`community` 固定阈值 /
  `personal` 个人动态——按玩家自己的 ScoreSaber 成绩算黄色基准，
  颜色 = 曲目相对玩家水平的位次；算法见 `docs/STAR_PALETTE_ALGORITHM.md`，
  纯函数在 `backend/analysis/player_palette.py`，结果缓存于
  `player_palette_cache` 表，经 `/api/status` 的 `ui.*` 下发前端）
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
- **个人色谱按平台独立**：各平台各自 top20-pp 计算 yellow 基准，缓存分平台
  存储，切换数据源后色谱跟着换
- BeatLeader API 客户端：`backend/beatleader.py`（字段与 scoresaber 对齐，
  详见模块注释与 `docs/STAR_PALETTE_ALGORITHM.md`）

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

## 6. 前端要点

- 零依赖单文件：`index.html`（静态骨架 + 设置/详情模板）、`app.js`（渲染 + 交互）、`style.css`
- 动态设置表单：`/api/settings/schema` 驱动（`renderSettingsForm`，`hidden` 项跳过）
- 图表：`lineChart`（SVG + crosshair 悬停）；详情页等高逻辑 —— `fixDetailLayout()` 进入时
  一次性固定图表高度（不要改回 `height: auto` 实时计算，会触发高度正反馈循环）
- 毛玻璃层：`#acrylic-backdrop`（fixed inset 0 + blur），移动中 `.moving` class 拉满模糊

### 6.1 多语言（i18n）
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

### 6.2 AI 报告语言
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

## 9. 构建与发布约定（2026-08 用户决策，强制）

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

## 10. 常见坑速查

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
