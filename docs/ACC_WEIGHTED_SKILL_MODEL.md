# ScoreSaber ACC 感知玩家实力模型

## 结论

玩家实力应表示为“在某个目标 ACC 下可稳定达到的等效星级”，而不是最高单曲 PP、最高星级或单纯的平均星级。模型输出三条互补指标：

| 指标 | 目标 ACC | 含义 |
|---|---:|---|
| `R96` | 96% | 精度竞技实力：高准度发挥时能处理的星级 |
| `R94` | 94% | 标准实力：适合作为默认选图与黄色基准 |
| `R80` | 80% | 挑战实力：能完成高难图、但不以高 ACC 为目标的能力 |

三者使用同一套物理模型和同一条官方 PP 曲线，只改变目标 ACC、可用证据和外推上限；不应把它们做成互相矛盾的三套独立排名。

## 研究发现与边界

### ScoreSaber 的官方 PP 结构

ScoreSaber 说明 PP 取决于 ranked 谱面的难度与该次成绩，并对成绩应用 PP 曲线；总 PP 还会对玩家的各次 PP 成绩做递减加权。[ScoreSaber PP 系统说明](https://wiki.scoresaber.com/ranking-system.html)

使用提供的权威 `pp_curve.json` 和 2,838 条正常 modifier 的 ranked 成绩（30 名玩家、2.5★–14.26★）验证得到：

```text
scorePP ≈ leaderboard.maxPP × C(ACC)
maxPP   ≈ 42.11378 × stars
```

其中 `C` 为 `pp_curve.json` 内的插值曲线。样本中的 PP 曲线复现中位相对误差低于 0.0001%，而 `maxPP—stars` 线性拟合的误差约为 0.003 PP。

这意味着：在 ScoreSaber 的官方 PP 尺度中，5★→6★ 与 10★→11★ 的 `maxPP` 增量都约为 42.11 PP。高星并没有在官方 `maxPP` 中自动使用指数权重。把高星加权是合理的产品选择，但必须明确它是**玩家能力模型的证据权重**，而不是 ScoreSaber 事实。

ScoreSaber 也会对星级做月度重估；12★以上会得到额外重估，14★以上再多一次。因此必须保留每条记录当时或当前使用的星级版本，并允许整体重算。[ScoreSaber reweights](https://wiki.scoresaber.com/ranking/reweights.html)

### 三个 ACC 基准在官方曲线中的位置

| ACC | `C(ACC)` | `ln(C(ACC))` |
|---:|---:|---:|
| 80% | 0.687227 | -0.375091 |
| 94% | 0.941736 | -0.060030 |
| 96% | 1.087188 | 0.083595 |

因此不能把 ACC 当作线性百分比：94%→96% 的竞技价值，远大于 80%→82% 的普通增量。模型必须使用曲线值或其对数，而非直接使用 `ACC - targetACC`。

### 高星凸权重的证据

在当前便利样本上，拟合下述响应模型：

```text
ln(C(ACC)) = playerIntercept - λ × Dγ(stars) + residual
Dγ(stars) = 14 × (stars / 14)^γ
```

样本对 `γ > 1` 没有提供支持：混合样本的最小残差约在 `γ = 0.9`，近期成绩子样本约在 `γ = 0.7`。这些值受玩家选图偏好影响，不能反推真实难度学；但它们足以说明，不能为了符合直觉而把 `γ` 强行设为大于 1。

故本规范采用：

```text
难度坐标 D(stars) 使用 γ = 1.0（与官方星级尺度兼容）
高星的重要性通过单独的 evidence weight 表达
```

未来可通过留出预测验证重新拟合 `γ`；只有当验证集稳定支持 `γ > 1` 时，才启用凸难度坐标。

## 正式模型

### 1. 输入与过滤

仅使用：

```pseudo
isUsableRankedScore =
    leaderboard.ranked == true
    and leaderboard.stars > 0
    and leaderboard.maxPP > 0
    and score.pp > 0
    and score.modifiers == ""
    and leaderboard.maxScore > 0

acc = score.baseScore / leaderboard.maxScore
```

同一玩家同一 leaderboard 只保留 ACC 最好的一次；近期状态另保留最新一次。ScoreSaber 与 BeatLeader 的 stars 不可混合拟合。

### 2. ACC 曲线

```pseudo
function ppMultiplier(acc):
    # 对 pp_curve.json 的分段线性插值
    return interpolate(curve, acc)

function accUtility(acc):
    # 避免 100% 附近曲线陡峭导致单次成绩支配估计
    safeAcc = clamp(acc, 0.80, 0.995)
    return ln(ppMultiplier(safeAcc))
```

### 3. 星级坐标与高星证据权重

```pseudo
STAR_CAP = 14.0
GAMMA = 1.0                 # 初始值；由验证集重新拟合
LAMBDA = 0.09               # 当前样本的初始值；每个数据源独立校准
RHO = 1.0                   # 14★记录相对于低星记录最多 2 倍证据权重

function difficultyAxis(stars):
    return STAR_CAP * (stars / STAR_CAP) ^ GAMMA

function inverseDifficultyAxis(value):
    return STAR_CAP * (value / STAR_CAP) ^ (1 / GAMMA)

function starEvidenceWeight(stars):
    normalized = clamp(stars / STAR_CAP, 0, 1)
    return 1 + RHO * normalized ^ 2
```

`starEvidenceWeight()` 不会宣称 10★→11★在官方 PP 上大于 5★→6★；它只使高星成绩在汇总玩家上限时更有话语权。14★的权重为 2，7★约为 1.25。

### 4. 单条成绩换算为目标 ACC 的等效星级

```pseudo
TRACKS = {
    precision: { targetAcc: 0.96, transferCap: 2.0 },
    standard:  { targetAcc: 0.94, transferCap: 2.5 },
    challenge: { targetAcc: 0.80, transferCap: 3.0 }
}

function equivalentStars(score, track):
    observed = accUtility(score.acc)
    target = accUtility(track.targetAcc)

    # 在难度坐标中的 ACC 换算量，而非线性 ACC 差。
    rawTransfer = (observed - target) / LAMBDA

    # 防止“6★ 98%”被无限外推成极高星 80% 能力，
    # 也防止单次 80% 的高星成绩被过度抹低。
    transfer = clamp(rawTransfer,
                     -track.transferCap,
                     +track.transferCap)

    equivalentAxis = difficultyAxis(score.stars) + transfer
    return inverseDifficultyAxis(max(equivalentAxis, 0.01))
```

`transferCap` 是重要的反偏差机制：PP 曲线在 99% 附近增长极快，但一张低星高准图并不能证明玩家可在远高于该图的星级稳定过关。三个上限应通过后续留出集调参；以上数值是安全的初始值。

### 5. 三档聚合

```pseudo
function evidenceWeight(score, track, now):
    transfer = abs((accUtility(score.acc) - accUtility(track.targetAcc)) / LAMBDA)

    # 接近该档目标 ACC 的成绩最有解释力；远离目标的成绩仍可用，
    # 但会被衰减并受 transferCap 约束。
    accWeight = exp(-0.5 * (transfer / track.transferCap) ^ 2)
    ageDays = daysBetween(score.timeSet, now)
    recencyWeight = 0.5 ^ (ageDays / 180)

    return starEvidenceWeight(score.stars) * accWeight * recencyWeight

function trackRating(scores, track, now):
    records = validRankedStandardScores(scores)
    records = keepBestAccuracyPerLeaderboard(records)

    for record in records:
        record.equivalent = equivalentStars(record, track)
        record.weight = evidenceWeight(record, track, now)

    # 上限使用新评分排序，而不是按 PP 排序。
    potentialSet = top 20 records by equivalent
    potential = weightedMedian(potentialSet.equivalent, potentialSet.weight)

    # 当前状态使用最近记录，不让远古 PB 永久主导。
    currentSet = latest 30 records
    current = weightedMedian(currentSet.equivalent, currentSet.weight)

    if potentialSet.count < 8:
        return insufficientData
    if currentSet.count < 8:
        current = potential

    return 0.70 * potential + 0.30 * current

R96 = trackRating(scores, TRACKS.precision, now)
R94 = trackRating(scores, TRACKS.standard, now)
R80 = trackRating(scores, TRACKS.challenge, now)
```

建议同时输出 `confidence`：由有效成绩数、覆盖星级范围、加权 MAD（离散度）和近期样本数合成。覆盖范围不足 2★或有效成绩少于 8 条时，应显示“低置信度”，不应给出强标签。

## 颜色系统

默认配色使用 `R94`；它对应“匹配实力且相对轻松”的常规选图。先预测玩家在目标图上的 ACC：

```pseudo
function predictedAcc(mapStars, R94):
    utility = accUtility(0.94) + LAMBDA * (
        difficultyAxis(R94) - difficultyAxis(mapStars)
    )
    return inversePpMultiplier(exp(utility))
```

| 颜色 | 预测 ACC | 解释 |
|---|---:|---|
| 灰色 | ≥ 98% | 明显低于实力，热身或放松 |
| 绿色 | 95%–98% | 轻松稳定 |
| 黄色 | 91%–95% | 默认匹配区间 |
| 红色 | 85%–91% | 有挑战，需要专注 |
| 紫色 | ＜85% | 越级、冲刺或挑战图 |

可将 `R96` 用作精度训练模式的黄色中心，将 `R80` 用作挑战训练模式的黄色中心；但三个模式应在界面上明确标注，避免把“能打 80%”误读为“能打 96%”。

## 校准与验证流程

1. 每个数据源、模式、modifier 组合独立拟合；不得混合 ScoreSaber 与 BeatLeader 星级。
2. 对每位玩家按时间切分：早 80% 用于估计 `R96/R94/R80`，后 20% 作为验证。
3. 由 `R` 预测验证成绩的 ACC，优化 MAE、校准误差和分位覆盖率。
4. 在网格中搜索 `GAMMA`、`LAMBDA`、`RHO`、`transferCap` 与时间半衰期；仅保留在多次交叉验证中稳定改善的参数。
5. 首个上线版本固定 `GAMMA=1.0`、`LAMBDA=0.09`，只允许通过验证数据更新。不要预设高星凸权重。
6. ScoreSaber 重估星级或 PP 曲线更新后，重算全部历史成绩与模型参数。

## 需要避免的错误

- 不用最高 PP、最高星级或 PP 排序直接定义能力。
- 不把普通 ACC 百分比差当作能力差；必须走官方 PP 曲线。
- 不让极高 ACC 的低星单次成绩无限外推至挑战能力。
- 不把 `R80` 当作精度实力，或把 `R96` 当作清图能力。
- 不将 ScoreSaber 与 BeatLeader 的 stars、PP 或拟合参数混在同一个模型中。
