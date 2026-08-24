# ScoreSaber 玩家实力与曲目颜色分级算法

版本：1.1（2026-08 修订）

> v1.1 修订：① 玩家 ID 来源明确为 **BSOR Replay 自动解析**（config 中的
> `scoresaber_id` 已弃用，不再需要玩家手动输入）；② 个人样本选择从
> 「按时间排序取最近 20 条」改为 **「忽略时间、按 PP 降序取前 20 条」**
> —— 排除玩家短期状态不稳定带来的干扰，PP 反映跨难度的持续实力。

用途：根据某个玩家的 ScoreSaber 游玩记录，为玩家标注实力阶段，并根据曲目
stars 相对于玩家能力的差异分配颜色。

## 1. 核心思想

颜色表示的不是曲目绝对难度，而是曲目难度相对于当前玩家实力的位置：

- 灰色：明显低于玩家当前水平
- 绿色：略低于玩家当前水平
- 黄色：与玩家实力匹配，玩起来相对舒适
- 红色：明显高于玩家当前水平
- 紫色：极限挑战或远超当前水平

黄色标准星数必须按玩家个人数据计算，不能对所有玩家使用同一个固定 stars 阈值。

## 2. 玩家身份（v1.1）

玩家 ID 从本地 BSOR Replay 自动解析（ScoreSaber ID = Steam ID，17 位数字，
BSOR 解析后存于 replays.player_id）。取**最近一次游玩**的玩家作为「当前
玩家」（多玩家库时以最近游玩者为准，与交叉验证的口径一致）。

`config.yaml` 中的 `player.scoresaber_id` 已**弃用**（不再被读取）；无需玩家
手动输入任何 ID。本地库无 Replay 时无法解析身份，动态水平不可用。

## 3. 输入数据

玩家的 ScoreSaber 游玩记录（每次「拉取数据并计算动态水平」拉取最近
成绩并**缓存到本地**，离线时用缓存计算/展示）。每条记录至少包含：

```text
stars       曲目星数
pp          该次成绩对应的单曲原始 PP
timeSet     成绩完成时间（仅展示，不参与样本选择）
modifiers   使用的 Modifiers
ranked      是否为 ranked 曲目
```

## 4. 有效记录筛选

默认只使用以下记录：

```text
ranked == true
stars > 0
pp > 0
stars 和 pp 都是有效数值
```

默认排除带有 `NF`（No Fail）的成绩，因为这类成绩不适合用来估计玩家正常
情况下的舒适难度。其他 Modifiers 是否排除由项目决定，但全项目保持一致
（当前：仅排除 NF）。

## 5. 个人黄色标准星数

### 5.1 取样本（v1.1：按 PP，忽略时间）

将有效记录按 `pp` **从高到低**排序（时间因素刻意忽略——短期状态不稳定，
PP 反映跨难度的持续实力），取前 20 条作为玩家当前能力样本。

设这些记录的 stars 数值为：

```text
S = [s1, s2, ..., sn]   (n <= 20)
```

计算：

```text
Q25 = S 的第 25 百分位
Q50 = S 的中位数
```

### 5.2 计算黄色标准

黄色标准星数定义为：

```text
yellow_stars = round_to_quarter(0.5 × Q25 + 0.5 × Q50)
```

其中：

```text
round_to_quarter(x) = round(x × 4) / 4
```

使用 Q25 和 Q50 的平均值，是为了让黄色区域略偏向「舒适难度」，同时避免
被少数特别简单或特别困难的成绩影响。

## 6. 曲目颜色分配

对任意一首曲目，计算：

```text
delta = map_stars - yellow_stars
```

然后按照以下规则分配颜色（实现为绝对阈值 tiers，前端按
`stars < tier.max` 取第一档；`delta = ±0.5 / ±1.5` 边界按闭区间语义处理）：

| 颜色 | 条件 | 含义 |
|---|---:|---|
| 灰色 | `delta < -1.5` | 明显偏简单 |
| 绿色 | `-1.5 ≤ delta < -0.5` | 略低于匹配水平 |
| 黄色 | `-0.5 ≤ delta ≤ +0.5` | 匹配玩家实力、相对舒适 |
| 红色 | `+0.5 < delta ≤ +1.5` | 明显挑战 |
| 紫色 | `delta > +1.5` | 极限或远超当前水平 |

### 示例

如果某玩家的黄色标准星数为 `7.5★`：

| 曲目 stars | 颜色 |
|---:|---|
| `< 6.0★` | 灰色 |
| `6.0–6.99★` | 绿色 |
| `7.0–8.0★` | 黄色 |
| `8.01–9.0★` | 红色 |
| `> 9.0★` | 紫色 |

## 7. 伪代码（v1.1：PP 排序）

```text
function classify_player(records):
    valid = filter(records,
        ranked == true,
        stars > 0,
        pp > 0,
        modifiers does not contain NF
    )

    if valid is empty:
        return status = "unknown"

    max_single_pp = max(record.pp for record in valid)

    if max_single_pp < 200:
        stage = "初级/休闲"
        fallback_stars = 5.75
    else if max_single_pp < 350:
        stage = "进阶/高阶"
        fallback_stars = 7.00
    else:
        stage = "竞技向"
        fallback_stars = 8.75

    top = sort_by_pp_desc(valid).take(20)      # v1.1: pp order, time ignored

    if count(top) >= 20:
        q25 = percentile(top.stars, 0.25)
        q50 = median(top.stars)
        yellow_stars = round_to_quarter((q25 + q50) / 2)
    else if count(top) >= 8:
        personal = round_to_quarter(
            (percentile(top.stars, 0.25) + median(top.stars)) / 2
        )
        yellow_stars = round_to_quarter((personal + fallback_stars) / 2)
    else:
        yellow_stars = fallback_stars

    return stage, max_single_pp, yellow_stars


function color_for_map(map_stars, yellow_stars):
    delta = map_stars - yellow_stars

    if delta < -1.5:
        return "gray"
    if delta < -0.5:
        return "green"
    if delta <= 0.5:
        return "yellow"
    if delta <= 1.5:
        return "red"
    return "purple"
```

## 8. 数据流与离线行为

```text
云端 API（拉取数据并计算动态水平；scoresaber | beatleader 按 player.data_source）
      ↓ 拉取 profile + 最近成绩
本地缓存 scoresaber_cache（平台分列，离线可用；切换平台不互相影响）
      ↓ classify_player()（确定性纯计算）
player_palette_cache（按 平台+玩家 缓存 yellow_stars / stage / 样本数 / 时间）
      ↓ build_tiers() → 绝对阈值
/api/status → ui.star_palettes 注入 personal 预设（当前平台的缓存）
      ↓
前端 starColor 按 tiers 分档渲染（与固定色板同一机制）
```

- **平台独立**：ScoreSaber 与 BeatLeader 各自拉取、各自缓存、各自计算
  yellow 基准（两平台星级数值不同，能力分级自然不同）；切换数据源后
  列表颜色与云端页立即跟随，另一平台数据保留可随时切回。
- 网络失败不破坏本地：缓存存在时个人色板继续可用；无缓存则 `personal`
  自动回退 `community` 固定色板。
- 缓存随「拉取数据并计算动态水平」刷新（云端数据页唯一入口）。
- `player.star_palette = personal` 时前端列表/详情 STARS 按个人色板着色；
  切换回 `community` 立即恢复固定阈值。

## 9. 设计边界

这套算法衡量的是「玩家适合游玩的曲目难度」，不是严格意义上的比赛实力。

其中 `yellow_stars` 应作为曲目颜色分级的主要依据。

## 10. 实现位置

- 算法纯函数：`backend/analysis/player_palette.py`（classify_player /
  build_tiers / round_to_quarter / percentile；确定性、无 LLM、无网络）
- 缓存：`player_palette_cache` 表（db/models.py SCHEMA + repository）
- 拉取与注入：`backend/main.py`（/api/scoresaber/refresh 顺带计算、
  /api/status 注入 personal 预设）
- 展示：ScoreSaber 页玩家卡片（黄字黄色基准 + 五色色带）、列表/详情
  STARS 颜色（复用 tiers 机制）
