# 更新日志

> 版本格式：`vX.Y（日期）` — 变更摘要。本文部分内容使用 AI 补全描述。

## v2.1.0（2026-09-02，待发布）

### 新增
- **PP 预测（准确率预览）**：点击 ranked Replay 条目的 PP 值，在其**下方锚定弹出
  预测小窗**（空间不足自动翻转到上方，出入场动画；点击外部 / Escape / 滚动关闭，
  再次点击同一 PP 值切换关闭），拖动滑条读取不同准确率下的预估 PP（布局对齐
  ScoreSaber 官方 Accuracy preview 卡片；重置按钮回到本次游玩的准确率）。新增
  通用锚定弹出组件 `openPopover`（不加遮罩、不阻断页面交互）；全屏弹窗
  `openModal` 为**独立全局强提醒接口**（与 SliceDetails 无依附——切割细节已转
  详情页内部卡片展示）
- **ScoreSaber PP 公式复刻**：`pp = maxPP × curve(acc)`——曲线内嵌自 SC 官方
  pp-curve 接口（37 节点分段线性，acc=0.95 归一化），maxPP 取榜单缓存；经本地
  实测数据回归验证（干净样本误差 ≤±0.1%）。预测只依赖榜单 maxPP，对任何 ranked
  谱面可用（不要求玩家成绩在 top-100 缓存内）；NF 已获得 PP 按 SS 见到的
  有效分折算，预测滑条仍锚定 Replay 当前显示的 acc；
  计算收敛在 `backend/analysis/pp_predict.py`（确定性纯函数，离线可用）
- 新增 `GET /api/replays/{id}/pp-preview`：仅 ScoreSaber 数据源（BeatLeader 公式
  不同，暂未支持）；unranked / 星级未同步返回结构化 404；i18n 三语言
- **弹窗外观打磨**：PP 预览卡片改为项目 **acrylic 玻璃预设**——底色采用
  `body.acrylic` 的 `--surface` 半透明值 `rgba(20, 22, 30, 0.66)`（浏览器/
  毛玻璃 webview 模式统一，不再吃 `var(--surface)` 的不透明灰底），模糊采用
  header 同款 `backdrop-filter: blur(14px) saturate(150%)`；`z-index` 提至
  200（高于 header/sidebar，低于 toast）——底层列表/壁纸经模糊透出，
  不再是盖在内容上的"不透明灰块"（浏览器模式的实心 `#191e29` 底色与
  毛玻璃模式有透明度却无模糊的噪点感一并消除）
- **消失动画修复**：`popOut` 原只有 `to` 关键帧，动画起点取元素基础态
  （`opacity: 0`）→ 实际 0→0，关闭时卡片瞬间消失无淡出。补 `from`
  显式为入场完成态；出场位移取 `--pop-drop` 反向（正常弹出向下滑出、
  翻转弹出向上滑出，与入场方向对称）
- **滑块手动重写（原生 range 弃用外观）**：原生 `input[type=range]` 的
  WebKit thumb 与渐变 track 几何各自计算，thumb 中心在 `[半宽, 宽-半宽]`
  线性而渐变是 `[0, 宽]` 线性 → 两端错位、圆钮与进度条不重合。改为
  **自绘 track/fill/thumb**（三元素共用同一坐标：fill 右缘 = thumb 圆心，
  坐标与原生 value→位置映射逐像素一致，拖动手感保持浏览器原生行为），
  原生 input 仅作无痕交互层（拖拽/点击/键盘全部走原生）
- **toast 改版**：位置从右下角改为**顶部居中向下弹出**（入场从上方 14px 落下、
  淡出向上收起，与入场对称）；**修复入场动画失效**——旧版插入即命中显示态，
  浏览器首帧就是目标样式、过渡无从发生，改为插入后双 rAF 再加 `.in`；
  左侧纵向色带退役，改用与列表状态卡片同款的**语义色渐变背景**（16% 透明度
  左侧渐变、65% 处淡出）；卡片顶部新增**全宽进度条**（从右向左缩短到最左端、
  左端固定），进度条归零自动淡出移除（reduced-motion 下走 setTimeout 兜底）；
  多条 toast 顶部纵列堆叠
- **toast 尺寸与状态**：卡片放大（padding 14/22、max-width 440、文字 15px，
  背景面积增幅大于字号）；显示时间 4s → 3s；新增 **success 状态**（绿色
  渐变背景 + 绿色进度条，任务完成提示改用之；kind 体系 = success 绿 /
  error 红[失败/警告] / info 蓝）
- **PP 卡片精确 ACC 输入**：滑条右侧的准确率显示改为**可输入框**——手动
  输入精确 ACC 百分比（60–100），回车即刻生效并联动滑条/PP 值，越界自动
  收拢到边界，非法输入回滚；拖动滑条时输入框同步回显
- **原始回放文件缺失降级标注**（2026-09）：ingest 只增不删，DB 行可能比
  原始 .bsor 长寿——缺失时详情页 hero 下方显示红色警告横幅（"原始回放
  文件缺失"+ 说明），切割细节/3D 回放/手部运动给出"原始文件缺失，暂不可用"
  的明确原因（不再笼统"无数据"），时间序列等已持久化数据照常渲染；总览/
  历史/同谱历史条目加「文件缺失」红色徽章（三语言）
- **LocalLeaderboard 第二扫描源**（2026-09，零配置自动检测）：
  `UserData/LocalLeaderboard/Replays` 目录存在即启用——与 BeatLeader 同场次
  的副本自动跳过（不重复入库）；**原始文件被外部删除时，一键刷新自动把该
  场次的 file_path 修复指向 LocalLeaderboard 幸存副本**（分析数据不动）；
  LocalLeaderboard 独有场次正常入库（文件缺失事故的第二只读源）；完成 toast
  提示找回/新增数量

### 变更
- **启动时替换旧 SaberLab 实例**：若默认端口 6980（以及回退范围）已由 SaberLab
  占用，启动器通过状态身份与 TCP owner PID 双重确认后终止旧实例并重新绑定
  6980，不再因后台实例窗口丢失而直接退出。普通程序占用端口时绝不误杀，仍按
  6981..6999 顺延；命名启动锁防止同时双击产生竞态双实例
- **本地 Replay 的 ScoreSaber PP 改为逐场计算**：`map_ranked_cache.pp` 是该难度
  的云端最佳成绩，不能代表每一次本地游玩。总览/历史/详情现用已同步的
  leaderboard `maxPP × curve(本场 acc)` 为每条完整 Replay 独立估算；Cyaegha
  Expert 五次本地记录由错误的同一 `300.9pp` 修正为约 `300.9 / 291.4 /
  280.6 / 269.0 / 260.5pp`。NF 按 ScoreSaber 有效分口径估算，exit 不伪造
  已获得 PP。**仅 ScoreSaber 使用该公式，BeatLeader PP 预测仍未实现**
- **一键刷新补齐玩家云端数据**：原 5 个任务组不变；`ranked_update` 完成谱面
  leaderboard/玩家 PP 索引后，继续刷新当前平台的玩家档案、近期成绩并重算
  动态水平（同任务串行，避免重复 API 流量）。云端数据页手动拉取成功后新增
  绿色 toast（三语言）
- **批量分析不再生成任何报告**（用户决策，2026-09-01）：v2.0.1 修复「批量
  报告静默失败」后，该行为首次真正执行即暴露设计冲突——清缓存后的全量批量
  会对**每条 replay 调用一次 LLM**（~20s/条，345 条 ≈ 2 小时），一键刷新
  表现为「卡在批量分析迟迟不完成」（实测 7 分钟仅产出 23 条报告）。现分析
  管线（watcher）完全不产出报告，`run_ai/ai_client/build_context/lang` 参数
  链移除；**报告唯一入口 = 详情页「生成报告」按钮**（`/api/ai/analyze/{id}`），
  按「使用 AI 生成报告」开关决定 LLM 或规则报告；无报告时按钮显示「生成
  报告」，已有报告显示「重新生成」。实测 321 条批量分析约 110 秒完成、
  ai_reports 零新增
- **弹窗独立通用化**：`openModal`/`closeModal` 定位为**独立全局强提醒接口**
  （顶层浮层组件，样式类 `.modal-*`，onClose 经 `modalclose` 事件），与
  SliceDetails 无依附；切割细节已转详情页**内部卡片**展示（点击方块就地展开），
  弹窗版 `openSliceModal` 已删除
- **未调用端点标记作废**：前端不再使用、被一键刷新取代的任务/分析端点
  保留但标注 DEPRECATED（不删除，避免破坏 API 兼容性）

### 修复
- **NF/exit PP 预测不再跳到 60%**：预览默认值直接使用 Replay 当前显示的 acc；
  若真实 acc 低于常规 60% 下限，滑条下限自动扩展到该值，而非强制夹到 60%
- **任务状态转圈动画恢复**：`data-i18n` 原挂在整个标题容器上，语言初始化会
  用纯文本替换容器并删除 spinner 节点；现只翻译文字 span，spinner 由卡片
  `data-task=running` 状态显隐
- **窗口滚动条视觉统一**：WebView2 原生黑条白底改为深色半透明轨道 + 低饱和
  红蓝渐变 thumb，根页面与内部滚动区域共用；BeatLeader 未认证星级黄框同步
  降低背景/边框透明度，减少对星级数字的干扰
- **保存设置不再清空分析数据**：设置表单每次保存都会提交全部字段，后端按
  「提交了 analysis.* 键」判定参数变更，导致任意保存都清空分析缓存
  （metrics/windows/motion_series 删除 + 全部 replay 重置 pending）。现改为
  按「值真实变更」判定（后端 `save_values` 返回 changed 键列表，前端只提交
  脏字段）；「重启后生效」提示同样只在真实变更重启级设置时出现
- **批量分析 AI 报告全部静默失败**：分析管线把 `build_context` 函数误传为
  `run_ai_report` 的 context 参数，`json.dumps` 抛 TypeError 被吞——一键刷新/
  批量分析/分析最新的报告（含规则报告兜底）从未落库；详情页「重新生成」
  传参正确所以未暴露
- **保存 AI 设置部分不生效**：设置保存后的热重载未刷新 LLM 客户端快照——
  temperature/max_tokens（无需重启项）保存后静默沿用旧值，首次配置 API Key
  后 AI 状态也不更新
- **保存 API Key 后应用内重启仍用旧 Key**：重启子进程继承旧环境变量而
  `.env` 加载不覆盖已有变量——重启时剥离 `.env` 提供的键，子进程重新读取
- **BeatLeader 星级可能挂错难度**：enrichment 平局裁决依赖的 `game_mode`
  列未随快照查询，且 `"SoloStandard"` 与 BeatLeader 的 `"Standard"` 不匹配
  ——多 characteristic 同名难度时任意行胜出（Lightshow 星级可能挂到
  Standard）
- **v3 谱面完成度判定失效**：歌曲时长估算兜底不认 v3 的 `colorNotes`/`b`
  字段 → song_length=0 → <98% 完成度检查失效（无 `-exit-` 文件名的中途退出
  被标为已完成）
- **谱面负缓存投毒**：扫描防抖期到来的 hash 被直接记入负缓存（实际并未
  搜索），期间新下载的谱面此后永远解析不到，直到重启或手动重扫
- **FC 判定纳入炸弹**：切到炸弹与 bad/miss 一样断 combo（与官方计分移植
  一致），此前 full_combo 只检查 bad/miss（`counts.bomb` 只统计实际切到的
  炸弹；未切到的炸弹在 BSOR 事件流中无事件，不影响判定）

### 多语言
- 补齐三语言共同缺失键：对比表列头（`compare.value_a/b`）、离线拦截
  （`err.offline`）、空库提示（`err.db_empty`）——此前在 en/ja 下显示原始
  键名字符串
- 「加载中…」占位接入 i18n（总览/历史列表初始态）；任务失败歌名分隔符
  按语言渲染
- 任务进度文案（"准备中…"/"谱面同步:xx" 等）与设置保存/清缓存确认消息经
  新增 `msg` / `task.current` 查表段翻译（en/ja；zh 显示原文）
- 规则报告语言透传补全：`/api/analyze/{id}` 与 `/api/analyze/by-path`
  支持 `?lang=`
- err 段补 14 条后端消息映射；枚举选项补 off/personal/custom 翻译

### 内部
- enrichment 快照改单 tuple 赋值（消除平台切换的撕裂读窗口）
- 封面兜底排序加 stat 竞态守卫（文件消失不再引发封面接口 500）
- 全量 212 项单元测试通过

## v2.0.0（2026-08-23）

> 版本说明：本版本原计划为 v1.6.0，因引入**插件系统**这一架构级改动（影响
> 目录结构、检测机制、打包与许可证边界），决定升版为 v2.0.0。

### 架构：插件系统（plugins 目录识别加载）
- **新增第一方插件系统**：根目录 `plugins/` 按约定识别并加载插件——不同协议
  的项目、其它完整功能可做成插件放入 `plugins/<插件名>/`，应用启动时检测
  目录内容并入系统主线（当前机制：目录含入口文件即挂载/启用）；本期只做
  第一方插件，**不开发第三方插件接口/规范**
- **首个插件 = 3D 回放（Local-ChroViewer）**：已转入**独立项目**开发
  （`Local-ChroViewer/`，GPL-2.0-only，ChroViewer 移植），SaberLab 仓库不再
  包含其源码；构建产物作为插件放入 `plugins/chro/` 即被加载（唯一检测路径，
  无回退——移除即禁用）；release 集成时按独立作品聚合（mere aggregation）
  并在包内声明外部 GPL-2.0 组件
- **插件缺失提示**：详情页「查看回放」在插件缺失时显示灰字安装提示（中英日
  三语言，指引放入 `plugins/chro/`），其余功能不受影响

### 双平台云端数据（ScoreSaber | BeatLeader）
- **数据源切换**：设置 → 玩家 →「云端数据源」卡片（segmented control，
  点击即保存并刷新页面）；`player.data_source` 配置项（schema 驱动）
- **ID 通用**：两平台均使用 BSOR 自动解析的 ScoreSaber ID（= Steam ID），
  无需任何手动输入
- **数据隔离**：玩家档案/谱面星级/stars-pp 索引/个人色谱四张缓存表全部
  平台分列（`platform` 列 + 复合主键）；切换平台时另一平台数据**完全不动**，
  可来回切换对比两平台评价；旧库自动迁移（老数据标 scoresaber，不丢失）
- **BeatLeader 客户端**（`backend/beatleader.py`）：玩家档案/成绩/按谱面
  hash 批量拉全难度星级（一次请求）；ranked = difficulty.status==3；
  官方 OST 谱面（status 5/7）**显示星级但不产生 PP**
- **云端数据页**：导航改为「云端数据」（原 ScoreSaber 入口），按当前平台
  拉取与展示（玩家档案 + 最近成绩 + 动态水平色带）；交叉验证仅 ScoreSaber
  平台可用；「拉取数据并计算动态水平」按钮两平台共用
- **一键刷新/联网更新按平台路由**；个人动态色谱按平台独立缓存（切换平台
  后黄色基准与列表颜色自动跟随）

### 切割细节（SliceDetails 移植）
- 「判定统计」卡片 → **「切割细节」**：4×3 note 网格（12 方块）平均分 +
  点击方块就地展开左右手**双 9 宫格**（网格缩小到顶部、选中方块高亮）
- **Python 纯函数重写**（`backend/analysis/slicedetails.py`）：方向映射、环形
  角度平均、slider/burst 特殊 note 有效分母、越界/非标准事件排除；不落库，
  实时解析原始 `.bsor`（实测 ~19ms）
- **带符号切偏**：note 中心重建（x/y 网格公式 + z = 切点 z；实证与 BSOR
  cutDistanceToCenter 自洽 ~6mm，并与 SimSaber 逆向运动模型交叉验证
  <1mm / 数 mm）
- **切割轨迹可视化**：实线 = 实际切割路径、虚线 = 中心满分参考线，两线间距
  = 带符号切偏；note 造型采用手绘 slicenote.svg / slicenote-any.svg（染色
  区分左右手、斜向 note 主体随方向旋转、Any 为点版）
- 九宫格格子下方数值行（平均分 / note 数）；hover 显示 pre/post/acc/切偏详情

### 详情页与总览 UI
- 顶栏 KPI 扩展：GOOD / MISS-BAD（照搬列表显示逻辑）/ NOTE 迁入，NOTE 与 NPS
  间竖向分割线；BOMB 前端隐藏
- 时间序列图表隐藏 y 轴固定 0-100% 刻度（网格线、图例真实范围、hover 保留）
- 总览分页移到标题行（与按天/按数量/搜索同高、行内居中）；分页刷新条目
  逐条淡入动画
- 网格/九宫格动效体系：网格缩放居中动画、九宫格容器 `0fr→1fr` 高度过渡
  （展开全程卡片高度单调、无空隙）、纯 opacity 淡入延迟对齐网格动画
  （原地浮现，不影响等高布局）

### 设置与视觉
- **星级色谱可配置**：设置 → 玩家 新增「星级色谱」下拉框（schema enum 驱动，
  选项名三语言）；STARS 配色档位定义由后端 `/api/status` 统一下发
  （`ui.star_palette` + `ui.star_palettes`），前端纯分档渲染（后端未下发时
  回退旧 4 档）；默认「社区惯例」5 档：<3 灰 / 3–5 绿 / 5–7 黄 / 7–9 红 /
  9★+ 紫；为不同能力玩家定制色谱预留扩展位（新增预设只需扩展后端
  `STAR_PALETTES`）
- **个人动态色谱**（`player.star_palette = personal`）：按玩家自己的
  ScoreSaber 成绩计算**黄色基准**（有效记录按 PP 降序取前 20 条的
  Q25/Q50 均值，round 到 0.25；忽略时间排除短期状态波动；排除 NF）；
  颜色 = 曲目难度相对玩家水平的位次（灰/绿/黄/红/紫，±0.5 / ±1.5 边界）；
  ScoreSaber 页「拉取数据并计算动态水平」按钮拉取成绩并顺带计算，
  结果缓存本地（离线可用，无缓存自动回退社区惯例）；玩家信息卡片
  黄字显示当前平均水平 + 五色色带（每段标注判定范围）；算法文档见
  `docs/STAR_PALETTE_ALGORITHM.md`；玩家 ID 只从 BSOR 自动解析
  （`player.scoresaber_id` 配置项正式弃用，不再读取）

### 数据研究
- **SimSaber 交叉验证**（MIT）：note 位置三重验证（x 完全一致 / y 毫米级 /
  cutDistance 0.1mm 自洽）+ 计分对账（官方移植与 replay 记录逐分一致）
- 验证工具沉淀：`_tools/start_headless_edge.ps1`（CDP 调试实例）、
  `_tmp/verify_v160.mjs`（37+ 断言 UI 回归）

### 测试
- 新增 `tests/test_slice_details.py`（23 项：tile/方向映射、环形平均、特殊
  note、带符号切偏、排除规则）；全量单元测试 128 项通过

## v1.5.0（2026-08-23）

### 分析引擎：固定时间窗口退役
- 所有基于时间的分析改为 **note 事件锚定**（时间轴 = 首末 note）：刀速/密度
  改为 per-note 曲线（±7 局部均值 / ±5 邻域 + 圆润）、疲劳斜率与 AI 时间摘要
  改为 note 分组（每 N 个 note 一组，设置可调）；中段密度低谷忠实呈现谱面结构；
  固定时间窗口配置项标记弃用（hidden，字段保留兼容）
- **acc 曲线改为官方口径**（score/maxScore，含惩罚与倍率）——曲线终点与
  replay 记录、3D 回放逐分一致

### 多语言（简体中文 / English / 日本語）
- 界面语言切换（JSON 对照表；设置 → 语言卡片**自动发现语言文件**，新增语言
  只需放一个 json）
- AI 报告与规则报告输出语言跟随界面语言；后端注释与日志统一英文

### 设置与视觉
- 新增「使用 AI 生成报告」开关（设置 → AI）：不勾选时生成确定性规则报告，
  不调用 LLM（节省额度）
- Squircle（G2 连续曲率）圆角卡片（Chrome 139+ 原生支持，旧浏览器自动回退）

### 修复
- 切换语言后毛玻璃背景丢失（reload 后经 backdrop-ready 通知重新推送壁纸）
- 设置页 boolean 配置项渲染失败（局部变量遮蔽全局 i18n 函数）
- NF 失败时间红轴标记：实现完成但**暂停**——实测 BeatLeader 0.9.33 的
  .bsor failTime 字段恒为 0

## v1.4（2026-08-21）

### 毛玻璃背景
- **壁纸推送方案定为生产默认**；A（未文档 API 真 Acrylic）/ B（DWM 背景板）实测不可行
  —— pywebview 6.2.1 无真正窗体透明（WebView2 透明了、窗体 BackColor 不透明，客户端区域永远是灰底），
  保留为实验开关（`--acrylic-mode backdrop|acrylic`、`--acrylic-legacy`）

### 设置页
- "游戏路径"与"路径"卡片合并：只保留游戏根目录（replay/谱面/SongCore 由根目录确定性派生），
  schema 4 项标记 `hidden`
- **原生文件夹选择对话框**：新增 `backend/dialog.py` 桥（修复 `python backend\host.py` 以
  `__main__` 运行导致 `backend.host` 双模块、对话框永远 unavailable 的 bug）；浏览器模式回退手动输入
- 选择后自动验证可达性以及提示。

### UI 视觉
- Replay 条目：去掉左侧实心状态色条（与背景渐变功能重合），只保留左→右状态色渐变
- 游戏路径验证条框：去色带，补与 replay 条目同款绿/红背景渐变
- KPI 卡片视觉效果优化：去左侧色带、去渐变，底色回归半透明。
- **任务状态卡片联动**：进度直接以卡片背景呈现——空闲=灰底"空闲/无后台任务"；
  运行中=灰底 + 半透明红蓝渐变按进度从左到右填充，大字=当前处理数据、小字=任务名；
  完成恢复灰底 + toast 提示（"✅ 已完成×××"）
- 详情页时间序列卡片：修复复选框点击导致卡片高度无限增长（图例高度正反馈循环）——
  进入详情时一次性固定图表高度并重画（viewBox 匹配渲染高度，无拉伸），后续高度不接受新变化

### Bug 修复
- `main.py` 装饰器粘连 bug 和路由静默丢失 bug ，已修复

### 其它
- **打包瘦身**：`saberlab.spec` 改用 `Tree` 打包 frontend（排除 node_modules 174MB 与
  chro 源码 src/public）——dist 381MB → 136MB，用户版 zip 107MB → **44.6MB**

## v1.3（2026-08-21）

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
- **0.00 星强制覆盖**：ScoreSaber 对 unranked leaderboard 写入 stars=0/ranked=0——
  enrichment 层统一把 0/None 星转为 unranked（stars=None、ranked=False），
  前端列表/对比页加 `> 0` 双保险；0.00 星不再出现，一律显示 "-"/UNRANKED
- **任务组清理**：`_start_task` 启动新任务前移除已完成的旧任务——`/api/status` 的
  `tasks` 数组只保留当前活跃组。
- **玩家 ID 自动解析**：自动从 BSOR Replay 提取玩家的 17 位平台 ID；**过滤 "Noob"（疑似未登录 ScoreSaber 的默认回落用户名）**；无 ID 时 pp 填充自动跳过（不会空 ID 导致 404）
- **按钮重构 + 多任务并行**：总览 5 个独立按钮 → 减少至 2 个—— ①**「⚡ 一键刷新」**（`/api/refresh/all`）：并行触发全部 5 个任务（入库/批量分析/谱面扫描/NPS/联网星级），本地只处理新增/变更数据（sha256 去重 + pending 过滤）；②**「联网重新更新数据」**（`/api/refresh/online`）：仅强制刷新云端星级/PP（本地分析数据不动，应对云端数据调整）。后端任务模型单任务槽 → **多任务字典**（kind 键，同 kind 冲突 409、异 kind 并行）；`/api/status` 返回 `tasks` 数组；KPI 任务卡片进度智能区分：**单任务=任务详情模式**（大字=当前处理数据、小字=任务名、背景=任务内 done/total，如联网更新 0/200）。
**旧进度条组件已移除**（进度展示完全由 KPI 卡片承担）；任务状态卡片标题旁新增加载动画。

### 联网稳定性
- **未联网拦截**：新增 `/api/network/check`（4s 超时探活 scoresaber.com）——点「联网重新更新
  数据」前预检，离线 toast「当前未联网」并拦截，不再空跑后台任务
- **失败重试轮**：`sync_maps_batch` 单轮 → 多轮队列——每轮结束后 failed 谱面重新入队再同步；
  单条累计失败 >=3 次放弃并记录谱面名
- **失败名单 toast**：任务完成 toast 展示放弃项（「联网获取数据成功，失败项目：xxx」，最多列 3 个）

## v1.2（2026-08-20）

- **独立窗口化**：`backend/host.py`（端口探测/单实例/uvicorn 线程/pywebview 窗口/退出）+ `run.bat`/`run-browser.bat` 双模式；PyInstaller 全内置打包（`packaging/`，产物 ~345MB 含 66MB 环境离线包）
- **架构清理**：schema 迁移收敛（新库即建全表，旧库幂等升级，`tests/test_db_schema.py` 回归）；`_enrich_replays` 提取为带缓存的 `services/enrichment.py`；/api/status 改 COUNT(*)；ScoreSaber 网络失败不再投毒缓存；config.yaml 损坏自动备份；任务状态无锁读修复
- **冗余清理**：后端死代码；前端死 CSS/死元素 + toast 替换 7 处 alert；chro 移除诊断日志 + 孤儿 worker；未用依赖移除
- **新增按钮**：总览页新增「重算谱面 NPS」按钮（联动统一进度条）
- **星级/PP 同步提速**：每线程持久 HTTPS 连接 + 8 线程并发 + 429 退避重试；全量 217 谱面 130 秒（原数十小时）

## v1.1（2026-08-19）

- **分层分析策略**：元数据快照（ingest）→ 详情懒分析 → 后台预计算；`analysis_status` 状态机
- **完成度三态补全**：扫描入库即判定（exit/fail/正常通关）
- **详情图表**：时间序列独立归一化 + 真实范围图例 + 悬停 crosshair 数值框
- **优化清除缓存逻辑**：清缓存即刻生效 + 全局数据刷新
- **ChroViewer 移植**：核心抽取 → 原装壳层集成 + SaberLab 本地数据源 → 修复卡死 bug

## v1.0 （2026-08-18）

- **核心引擎**：BSOR v1 解析器；谱面 Hash 解析（SongCore 算法 + SongHashData.dat 缓存）；确定性指标（Accuracy/Pre-Center-Post/窗口/运动学/疲劳/单手换向）；AI Coach（DeepSeek/Qwen/OpenAI 兼容 + 规则兜底）
- **完成度判定**：`-exit-`/NF/时长兜底；以本地谱面为根缓存星级（四色分级）；NPS（v2/v3 兼容）
- **UI v2**：NavigationView + Beon 霓虹字标；KPI 行、按天分页、宽屏多列；详情滑块分页、2×3 网格；骨架屏 + 竞态防护；HERO 渐变、封面兜底、悬停缩放
- **数据修复**：手部运动预热裁切 + 物理上限过滤；speed_peak 补全；封面三级兜底
- **运维**：run.bat CRLF 修复；数据库迁移 v1-v5；后台任务（批量分析/星级同步/NPS）
