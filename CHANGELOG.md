# 更新日志

> 版本格式：`vX.Y（日期）` — 变更摘要。README 仅提供本文件链接，详细记录都在这里。

## v1.4（2026-08-21）

### 毛玻璃（三轮迭代 + 可行性研究）
- **方案 C（壁纸推送）定为生产默认**；A（未文档 API 真 Acrylic）/ B（DWM 背景板）实测不可行
  —— pywebview 6.2.1 无真正窗体透明（WebView2 透明了、窗体 BackColor 不透明，客户端区域永远是灰底），
  保留为实验开关（`--acrylic-mode backdrop|acrylic`、`--acrylic-legacy`）
- **前端自裁切**：窗口位置跟随由前端 rAF 每帧读 `screenX/screenY` 本地计算（零 IPC），
  消除拖动滞后；后端只推一次 monitor 几何 + 壁纸 URL
- **壁纸实时更新**：后端 1s 轮询壁纸文件（路径/mtime/size）与显示器几何，变化即推送
  （幻灯片壁纸切换 1s 内生效）；壁纸 URL 带 `?v=<mtime_ns>` 版本号绕过浏览器缓存；
  前端 `Image()` 预加载后换图（无闪变）
- **移动遮盖**：窗口移动/缩放期间背景模糊拉满（blur 120px ≈ 纯色）掩盖滞后不协调感，
  停止 1s 恢复（filter 200ms 过渡）；移动检测在后端（拖动期间渲染/rAF 可能暂停），
  后端 watchdog + 前端 1.5s 兜底双保险（压力测试 10/10 通过，修复"概率性不恢复"）
- **研究结论**：Explorer 级原生毛玻璃（实时模糊被遮挡窗口）**放弃**——最便宜路径仍有未证实的
  GDI 表面 alpha 风险，稳健路径需重写宿主层（3-6 人天）且 Acrylic 失焦即消失、WebView2 透明
  有回归史，20% 效果提升不换 80% 结构。报告归档 `others/原生毛玻璃可行性研究.md`

### 设置页
- "游戏路径"与"路径"卡片合并：只保留游戏根目录（replay/谱面/SongCore 由根目录确定性派生），
  schema 4 项标记 `hidden`
- **原生文件夹选择对话框**：新增 `backend/dialog.py` 桥（修复 `python backend\host.py` 以
  `__main__` 运行导致 `backend.host` 双模块、对话框永远 unavailable 的 bug）；浏览器模式回退手动输入
- 选择后自动验证（根目录 + 谱面目录存在判定），标题旁"✅ 验证成功（已保存，重启生效）"/
  "❌ 验证失败"标红引导重选（不弹窗）；手动输入 400ms 防抖自动验证

### UI 视觉
- Replay 条目：去掉左侧实心状态色条（与背景渐变功能重合），只保留左→右状态色渐变
- 游戏路径验证条框：去色带，补与 replay 条目同款绿/红背景渐变
- KPI 卡片：去左侧色带、去渐变，底色回归半透明 `var(--surface)`（毛玻璃模糊透出）；
  卡片宽度严格 1/4（`min-width:0`），长文本省略号截断
- **任务状态卡片联动**：进度直接以卡片背景呈现——空闲=灰底"空闲/无后台任务"；
  运行中=灰底 + 半透明红蓝渐变按进度从左到右填充，大字=当前处理数据、小字=任务名；
  完成恢复灰底 + toast 提示（"✅ 已完成×××"）
- 详情页时间序列卡片：修复复选框点击导致卡片高度无限增长（图例高度正反馈循环）——
  进入详情时一次性固定图表高度并重画（viewBox 匹配渲染高度，无拉伸），后续高度不接受新变化

### Bug 修复
- `main.py` 装饰器粘连事故：`}@app.get(...)` 拼进 return 语句（`@` 被当矩阵乘法，
  运行时报 `TypeError: unsupported operand type(s) for @`），同时导致
  `/api/scoresaber/validate` 路由静默丢失——已拆分恢复（全文件排查无同类事故）
- host.py 诊断 print 补 `flush=True`（管道/重定向块缓冲）

### 文档
- 新增 `others/毛玻璃ABC实验报告与路线建议.md`、`others/原生毛玻璃可行性研究.md`；
  `毛玻璃方案探索.md` 补三轮实测结论
- 本文件（CHANGELOG.md）与 `DEVELOPMENT.md` 从 README 独立

### 文档与开源
- 文档分离：`CHANGELOG.md`（更新日志）、`DEVELOPMENT.md`（二次开发指南）从 README 独立，
  README 精简为 GitHub 标准简介（others/ 内部文档不进公开仓库）
- **开源协议**：根 `LICENSE` = **GPL-3.0-or-later**（含版权声明与适用范围：主项目本体；
  chro 为独立聚合程序）；`frontend/chro/LICENSE` = GPL-2.0（上游强制）+
  `MODIFICATIONS.md`（修改清单，合规 §2a）；README 协议标注 + 致谢。
  许可兼容性已核实：BS-Open-Replay(MIT，移植来源)、ssapi(纯文档)、
  依赖全宽松、Beon 字体 OFL、environments 资源与上游 chroviewer 公开仓库一致

- **发布工作区**：`GitHub_Build/<version>/` 双版本导出脚本 `_tools/export_github_pkg.ps1`
  —— ①`saberlab-src/`（开发者版：源码+文档，不含依赖，用户自行构建）
  ②`SaberLab-vX.Y.Z-win64.zip`（用户版：PyInstaller 产物全内置依赖，**zip 内含版本文件夹
  SaberLab-vX.Y.Z-win64/ 避免解压平铺**，自动替换 config.yaml 为模板、剔除运行时数据，安全自检）；
  版本号统一 1.4.0
- **打包瘦身**：`saberlab.spec` 改用 `Tree` 打包 frontend（排除 node_modules 174MB 与
  chro 源码 src/public）——dist 381MB → 136MB，用户版 zip 107MB → **44.6MB**

### 用户版测试反馈修复（1.3.0 发行前加固）
- **空白路径拦截**：游戏路径不可用时，前端 5 个任务按钮（扫描入库/批量分析/重扫谱面/星级同步/NPS）全部拦截 + toast 引导到设置页；后端同款校验兜底（400 明确错误）；`/api/status` 新增 `maps_dir.exists`
- **重启按钮 + 即时生效**：设置页新增「重启 SABER LAB」按钮（`/api/restart` → dialog 桥 → host 延迟 2s 拉起新进程并优雅退出，frozen 下重启 exe）；保存设置后运行时配置**热重载**（resolver/pipeline 路径即时生效，无需重启）
- **封面懒修复**：`/api/maps/{hash}/cover` 在 DB 行路径缺失/失效时触发 `ensure_map_path` 针对性扫描（修复打包版首次 ingest 后封面全默认、重启才恢复的不稳定问题；带负缓存 + 30s 防抖，假 hash 不会反复全量扫描）
- **NPS 覆盖 bug 修复**：`upsert_map` 的 `nps_json` 缺省值从 `"{}"` 改为 `None`——`map_scan` 重扫谱面库时不再用空对象覆盖已计算的 NPS（一键刷新并行场景下 NPS 曾被冲空，详情页 NPS 显示 "-"）；实测重算后 914→1023 谱面有值且重扫后保留；详情页 NPS/星级/PP 数据链路验证（7.82/7.28★/ranked ✓）
- **任务依赖竞态修复**：一键刷新并行 5 任务时,`batch`/`ranked_update` 启动即读 replays 表——
  清空数据后 `ingest` 未入库完成,星级同步收集到空 hash 列表(0 条)、批量分析漏项。
  新增 `_wait_ingest_done(kind)`：两类任务先等同组 ingest 完成再取数据
  （实测清空场景全链路:679 leaderboards + 50 pp 缓存 ✓;顺带修复 main.py 缺
  `import time` 导致的 NameError）
- **NPS 空转竞态修复**：全新库/清空后一键刷新时,`nps_update` 在 `map_scan` 建好 maps 行前
  执行会 0/0 空转完成(NPS 全缺失,用户版首发复现)。新增 `_wait_map_scan_done(kind)`：
  nps_update 先等谱面扫描完成（实测全新库 1027/1027、nps_json 1023 条 ✓）
- **0.00 星兜底**：ScoreSaber 对 unranked leaderboard 写入 stars=0/ranked=0——
  enrichment 层统一把 0/None 星转为 unranked（stars=None、ranked=False），
  前端列表/对比页加 `> 0` 双保险；0.00 星不再出现，一律显示 "-"/UNRANKED
  （实测:unranked→None、ranked 谱面 7.28★ 不误伤）
- **任务组清理**：`_start_task` 启动新任务前移除已完成的旧任务——`/api/status` 的
  `tasks` 数组只保留当前活跃组。修复"一键刷新(5 任务)完成后,再点联网更新时
  KPI 误判为多任务组(显示 X/N 而非任务详情)"的问题(实测:旧组清理后 tasks=1 → 详情模式)
- **玩家 ID 自动解析**：重大发现——ScoreSaber ID = Steam ID，BSOR Replay 自带 17 位平台 ID。`_scoresaber_id()`：优先历史配置，否则取最近一次游戏记录的玩家；**过滤 "Noob"（未登录 ScoreSaber 的默认回落用户名，库中占 200 条仍正确解析到真实玩家）**；设置项手动 `scoresaber_id` 隐藏（schema hidden），仅保留兜底玩家名；无 ID 时 pp 填充自动跳过（不再空 ID 404）
- **按钮重构 + 多任务并行**：总览 5 个独立按钮 → 2 个——①**「⚡ 一键刷新」**（`/api/refresh/all`）：并行触发全部 5 个任务（入库/批量分析/谱面扫描/NPS/联网星级），本地只处理新增/变更数据（sha256 去重 + pending 过滤）；②**「联网重新更新数据」**（`/api/refresh/online`）：仅强制刷新云端星级/PP（本地分析数据不动，应对云端数据调整）。后端任务模型单任务槽 → **多任务字典**（kind 键，同 kind 冲突 409、异 kind 并行）；`/api/status` 返回 `tasks` 数组；KPI 任务卡片进度智能区分：**单任务=任务详情模式**（大字=当前处理数据、小字=任务名、背景=任务内 done/total，如联网更新 0/217），**多任务=完成数模式**（背景=完成任务数/总任务数，大字"X/N 任务完成"——判据为任务组大小，
即使只剩 1 个运行也不切详情模式）；完成 toast 用 prevRunning 对比去重（每任务仅弹一次）；
**旧进度条组件已移除**（进度展示完全由 KPI 卡片承担）；任务状态卡片标题旁新增加载动画
（灰色圆环+蓝色弧线旋转，任务运行时显示）

### 用户版测试反馈修复（联网稳定性）
- **未联网拦截**：新增 `/api/network/check`（4s 超时探活 scoresaber.com）——点「联网重新更新
  数据」前预检，离线 toast「当前未联网」并拦截，不再空跑后台任务
- **失败重试轮**：`sync_maps_batch` 单轮 → 多轮队列——每轮结束后 failed 谱面重新入队再同步
  （限速/瞬时错误恢复自动补上，实测 Blackmagik Blazing 同步完成后自动补上星级/PP）；
  单条累计失败 >=3 次放弃并记录谱面名
- **失败名单 toast**：任务完成 toast 展示放弃项（「联网获取数据成功，失败项目：xxx」，最多列 3 个）

---

## v1.2（2026-08）

- **独立窗口化**：`backend/host.py`（端口探测/单实例/uvicorn 线程/pywebview 窗口/优雅退出）+ `run.bat`/`run-browser.bat` 双模式；PyInstaller 全内置打包（`packaging/`，产物 ~345MB 含 66MB 环境离线包）
- **毛玻璃**：三方案探索（`others/毛玻璃方案探索.md`）+ 原型——DWM 背景板/真 Acrylic（运行时降级链）+ 壁纸推送方案 C（`backend/desktop.py` + 前端 acrylic 层，?shell=webview 启用）
- **架构清理**：schema 迁移收敛（新库即建全表，旧库幂等升级，`tests/test_db_schema.py` 回归）；`_enrich_replays` 提取为带缓存的 `services/enrichment.py`；/api/status 改 COUNT(*)；ScoreSaber 网络失败不再投毒缓存；config.yaml 损坏自动备份；任务状态无锁读修复
- **冗余清理**：_tools 121→5；后端死代码；前端死 CSS/死元素 + toast 替换 7 处 alert；chro 移除诊断日志 + 孤儿 worker；未用依赖移除
- 总览页新增「重算谱面 NPS」按钮（联动统一进度条）
- **星级/PP 同步提速**：每线程持久 HTTPS 连接 + 8 线程并发 + 429 退避重试；全量 217 谱面 130 秒（原数十小时）

## v1.1（2026-08）

- 分层分析策略：元数据快照（ingest）→ 详情懒分析 → 后台预计算；`analysis_status` 状态机
- 完成度三态补全：扫描入库即判定（exit/fail/正常通关）
- 统一进度条组件（展开/收起动画、点击关闭、任务期间按钮禁用）
- 详情图表：时间序列独立归一化 + 真实范围图例 + 悬停 crosshair 数值框
- 清缓存即刻生效 + 全局数据刷新
- ChroViewer 移植：核心抽取 → 原装壳层集成 + SaberLab 本地数据源 → 加载卡死 bug 修复（React selectionchange 死循环 + 后端慢速流式，见 `others/bug.md`）

## v1.0 正式版（2026-08）

**核心引擎**：BSOR v1 解析器（黄金夹具全断言通过，重算分与记录分逐分一致）；谱面 Hash 解析（SongCore 算法 + SongHashData.dat 缓存）；确定性指标（Accuracy/Pre-Center-Post/窗口/运动学/疲劳/单手换向）；AI Coach（DeepSeek/Qwen/OpenAI 兼容 + 规则兜底）
**完成度判定**：`-exit-`/NF/时长兜底；以本地谱面为根缓存星级（四色分级）；NPS（v2/v3 兼容）
**UI v2**：NavigationView + Beon 霓虹字标；KPI 行、按天分页、宽屏多列；详情滑块分页、2×3 网格；骨架屏 + 竞态防护；HERO 渐变、封面兜底、悬停缩放
**数据修复**：手部运动预热裁切 + 物理上限过滤；speed_peak 补全；封面三级兜底
**运维**：run.bat CRLF 修复；数据库迁移 v1-v5；后台任务（批量分析/星级同步/NPS）
