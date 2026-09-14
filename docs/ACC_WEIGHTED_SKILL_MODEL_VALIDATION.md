# ScoreSaber 三档实力模型：典型玩家验证与修订

## 测试口径

测试对象均使用完整 ScoreSaber 成绩页；只保留正式 ranked、无 modifier、可还原 ACC 且 ACC 位于 80%–99.5% 的记录，同一 leaderboard 取 ACC 最好的一次。

ACC 仍按提供的 ScoreSaber PP 曲线处理：

```text
utility(acc) = ln(PPCurve(acc))
rawTransfer(target) = (utility(acc) - utility(target)) / λ
λ = 0.09
```

`R96`、`R94`、`R80` 分别代表可在 96%、94%、80% ACC 下稳定处理的等效星级。高星记录最高可获得 2 倍汇总证据权重，但不会改写 ScoreSaber 的星级或 PP 尺度。

## 首轮模型发现的问题

首轮使用“将 ACC 换算量截断后继续计算”的方法。它产生了两个不可接受的问题：

1. 典型 1 的低星高 ACC 被外推为 `R80 = 10.13`，高于典型 2 的高星低 ACC `R80 = 8.34`。
2. 截断会破坏基准间的单调性，可能出现 `R96 > R94`。

根因是：远离目标 ACC 的成绩不能在截断后伪装成该目标的精确观测。低星 98% 能证明精度很强，但不能精确证明 80% 时能打多高星。

## 修订机制：直接估计、展示下界、拒绝伪精度

### 直接证据门槛

仅当该条记录换算到目标 ACC 的难度位移不超过 2.0 个难度坐标单位时，才进入该档评分：

```pseudo
DIRECT_TRANSFER_LIMIT = 2.0

function equivalentStars(record, targetAcc):
    rawTransfer = (utility(record.acc) - utility(targetAcc)) / LAMBDA

    if abs(rawTransfer) > DIRECT_TRANSFER_LIMIT:
        return noDirectEstimate

    return inverseDifficultyAxis(
        difficultyAxis(record.stars) + rawTransfer
    )
```

在当前 `γ = 1.0` 下，这个门槛大致对应：

| 档位 | 直接证据主要 ACC 区间 |
|---|---|
| `R96` | 约 93%–97.6% |
| `R94` | 约 88%–96.3% |
| `R80` | 约 80%–90% |

这使每档只依赖真正与其含义相邻的成绩。

### 远离基准的成绩如何使用

远离目标的成绩不进入数值估计，但保留为已展示能力：

```pseudo
highestObservedAtOrAboveTarget =
    max(stars where record.acc >= targetAcc)
```

若直接证据不足 8 条，显示：

```text
R80：证据不足；已展示 ≥ 12.99★ @ 80%
```

这不是“无法识别实力”，而是拒绝把未测试过的挑战区间伪装成精确能力值。

### 聚合

```pseudo
directRecords = records where equivalentStars != noDirectEstimate

potential = weightedMedian(top 20 directRecords by equivalentStars)
current   = weightedMedian(latest 30 directRecords by playTime)
rating    = 0.70 * potential + 0.30 * current
```

权重：

```pseudo
starWeight    = 1 + min(stars / 14, 1)^2
accWeight     = exp(-0.5 * (abs(rawTransfer) / 2)^2)
recencyWeight = 0.5^(ageDays / 180)
weight = starWeight * accWeight * recencyWeight
```

## 典型玩家结果

| 玩家类型 | 有效记录 | `R96` | `R94` | `R80` | 结论 |
|---|---:|---:|---:|---:|---|
| 典型 1：低难度、高准度 | 47 | 6.64 | 8.06 | 证据不足；已展示 7.93★ | 精度与常规能力明确，但没有足够挑战 ACC 成绩来估 `R80`。 |
| 典型 2：高难度、低准度 | 36 | 证据不足 | 证据不足 | 8.34 | 明确识别为挑战型：能处理约 8.34★@80%，但没有高准度能力证据。 |
| 典型 3：高难度、高准度 | 1,152 | 11.42 | 12.42 | 证据不足；已展示 12.99★ | 顶尖精度与标准能力；因其成绩多在高 ACC，不能凭空估算更高的 80% 挑战上限。 |
| 典型 4：高难度、较高准度 | 555 | 8.44 | 10.60 | 13.79 | 三档均有充分直接证据，形成完整能力曲线。 |

### 数据支持度

| 玩家类型 | `R96` 直接记录 | `R94` 直接记录 | `R80` 直接记录 |
|---|---:|---:|---:|
| 典型 1 | 44 | 33 | 0 |
| 典型 2 | 0 | 1 | 36 |
| 典型 3 | 614 | 429 | 5 |
| 典型 4 | 38 | 310 | 357 |

结果符合预期的风格差异，而且不会再把“低星高准”错误推断成“高星低准挑战能力”。

## 产品规则

1. 默认选图与五色配色只使用 `R94`，且必须有至少 8 条直接证据。
2. `R96` 用于精度训练模式，`R80` 用于挑战训练模式；不能互相替代。
3. 某档证据不足时显示下界文字，不显示伪精确数值，也不生成该档配色。
4. `R94` 的颜色可沿用预测 ACC 分段：灰 ≥98%、绿 95%–98%、黄 91%–95%、红 85%–91%、紫 <85%。
5. ACC 低于 80% 的成绩不应被钳制为 80%；当前版本将其排除，直到建立经验证的 60%–80% 专用曲线。
6. 当三档都有充分直接证据但出现 `R96 > R94` 或 `R94 > R80` 时，标记数据或参数异常，并在展示层做保序回归修正。

## 后续校准

当前 `λ=0.09` 与 `DIRECT_TRANSFER_LIMIT=2.0` 来自本次 ScoreSaber 样本的初始校准。上线前应对更多玩家做按时间切分的验证：用较早成绩预测较晚成绩的 ACC，以 MAE、覆盖率和三档单调性作为目标，联合优化 `λ`、直接证据门槛、时间半衰期和高星证据权重。
