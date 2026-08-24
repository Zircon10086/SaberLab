"""Config item Schema (developrules.md §3).

Each config item's metadata contains: key / label / type / description /
restart_required / required / sensitive / group / enum / default.
The frontend dynamically generates the settings UI from this (§9); the backend
validates and reads/writes against it.
"""
from __future__ import annotations

# type set:
#   string / integer / float / boolean / enum / directory / file / url / secret

SETTINGS_SCHEMA: list[dict] = [
    # ---------- paths (hidden: the settings page uses the "game path" card instead;
    # only the root directory is chosen, the other three are deterministically derived
    # from it and need no manual entry under a standard Beat Saber layout) ----------
    {
        "key": "game.instance_root",
        "label": "游戏根目录",
        "type": "directory",
        "description": "Beat Saber 安装根目录（自动派生谱面/Replay/SongCore 相对路径）",
        "restart_required": True,
        "required": True,
        "sensitive": False,
        "group": "路径",
        "hidden": True,
    },
    {
        "key": "game.replay_dir",
        "label": "Replay 目录",
        "type": "directory",
        "description": "BeatLeader 本地 Replay (.bsor) 文件夹（留空则从根目录派生）",
        "restart_required": True,
        "required": False,
        "sensitive": False,
        "group": "路径",
        "hidden": True,
    },
    {
        "key": "game.custom_levels_dir",
        "label": "谱面目录",
        "type": "directory",
        "description": "CustomLevels 自定义谱面文件夹（留空则从根目录派生）",
        "restart_required": True,
        "required": False,
        "sensitive": False,
        "group": "路径",
        "hidden": True,
    },
    {
        "key": "game.songcore_cache",
        "label": "SongCore 缓存文件",
        "type": "file",
        "description": "游戏 SongCore 的 SongHashData.dat（留空则从根目录派生）",
        "restart_required": True,
        "required": False,
        "sensitive": False,
        "group": "路径",
        "hidden": True,
    },

    # ---------- player ----------
    {
        "key": "player.scoresaber_id",
        "label": "ScoreSaber ID",
        "type": "string",
        "description": "已废弃：玩家 ID（= Steam ID）从 BSOR Replay 自动解析，无需手动填写",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "玩家",
        "hidden": True,
    },
    {
        "key": "player.player_name_fallback",
        "label": "玩家名（兜底）",
        "type": "string",
        "description": "Replay 无玩家名时的回退显示名",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "玩家",
    },
    {
        "key": "player.star_palette",
        "label": "星级色谱",
        "type": "enum",
        "enum": ["community", "personal"],
        "description": "按星级为 Replay 条目与详情页 STARS 数字配色的方案：社区惯例（固定阈值）或个人动态（按玩家自己的 ScoreSaber 成绩计算黄色基准，颜色 = 曲目难度相对玩家当前水平的位次）。个人动态在 ScoreSaber 页点击「拉取数据并计算动态水平」后生效，离线时沿用上次结果；从未拉取过（无缓存）时自动回退社区惯例",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "玩家",
        "default": "community",
    },
    {
        "key": "player.data_source",
        "label": "云端数据源",
        "type": "enum",
        "enum": ["scoresaber", "beatleader"],
        "description": "云端数据来源：ScoreSaber 或 BeatLeader（玩家 ID 两平台通用，从 BSOR 自动解析；切换后另一平台已缓存的数据保留不动，可随时切回）。设置页「玩家」栏的切换按钮即此配置",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "玩家",
        "default": "scoresaber",
        "hidden": True,
    },

    # ---------- analysis ----------
    {
        "key": "analysis.window_seconds",
        "label": "时间窗口（秒）",
        "type": "float",
        "description": "（已弃用）固定时间窗口宽度。分析已改为按 note 事件锚定（per-note 曲线 + note 分组斜率），此项不再参与任何计算；保留仅为兼容旧 config.yaml",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "分析",
        "default": 30.0,
        "hidden": True,
    },
    {
        "key": "analysis.window_step_seconds",
        "label": "采样步长（秒）",
        "type": "float",
        "description": "（已弃用）固定时间窗口采样步长。分析已改为按 note 事件锚定，此项不再参与任何计算；保留仅为兼容旧 config.yaml",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "分析",
        "default": 1.0,
        "hidden": True,
    },
    {
        "key": "analysis.slope_group_notes",
        "label": "斜率分组大小（note）",
        "type": "integer",
        "description": "疲劳斜率与 AI 时间摘要的分组粒度：每 N 个 note 聚合为一组（组内中位时间为横轴锚点），再对全曲各组线性拟合得到每分钟变化斜率。组越小对局部波动越敏感但噪声越大，组越大趋势越平滑——两者结论可能不同，请按谱面密度选择：高速谱可调小（如 30），稀疏谱可调大（如 100）。默认 50（约 10-15 秒的方块量）",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "分析",
        "default": 50,
    },
    {
        "key": "analysis.fatigue_edge_seconds",
        "label": "疲劳对比边缘（秒）",
        "type": "float",
        "description": "疲劳分析取最前/最后 N 秒对比",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "分析",
        "default": 30.0,
    },

    # ---------- server ----------
    {
        "key": "server.host",
        "label": "监听地址",
        "type": "string",
        "description": "FastAPI 监听地址（默认 127.0.0.1）",
        "restart_required": True,
        "required": False,
        "sensitive": False,
        "group": "服务器",
        "default": "127.0.0.1",
    },
    {
        "key": "server.port",
        "label": "端口",
        "type": "integer",
        "description": "FastAPI 监听端口（默认 6980）",
        "restart_required": True,
        "required": False,
        "sensitive": False,
        "group": "服务器",
        "default": 6980,
    },

    # ---------- AI ----------
    {
        "key": "ai.provider",
        "label": "AI Provider",
        "type": "enum",
        "enum": ["off", "deepseek", "qwen", "openai", "custom"],
        "description": "AI 引擎提供方（off = 关闭，使用规则报告兜底）",
        "restart_required": True,
        "required": False,
        "sensitive": False,
        "group": "AI",
        "default": "deepseek",
    },
    {
        "key": "ai.base_url",
        "label": "API Base URL",
        "type": "url",
        "description": "OpenAI 兼容 API 地址（provider=custom 时必填）",
        "restart_required": True,
        "required": False,
        "sensitive": False,
        "group": "AI",
    },
    {
        "key": "ai.model",
        "label": "模型",
        "type": "string",
        "description": "模型名称（如 deepseek-chat / gpt-4o-mini）",
        "restart_required": True,
        "required": False,
        "sensitive": False,
        "group": "AI",
        "default": "deepseek-chat",
    },
    {
        "key": "ai.api_key",
        "label": "API Key",
        "type": "secret",
        "description": "AI API Key（存储于 .env，前端仅显示脱敏状态）",
        "restart_required": True,
        "required": False,
        "sensitive": True,
        "group": "AI",
    },
    {
        "key": "ai.temperature",
        "label": "温度",
        "type": "float",
        "description": "LLM 采样温度（越低越确定性）",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "AI",
        "default": 0.3,
    },
    {
        "key": "ai.max_tokens",
        "label": "最大 Token",
        "type": "integer",
        "description": "LLM 单次回复最大 token 数",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "AI",
        "default": 2500,
    },
    {
        "key": "ai.ai_report_enabled",
        "label": "使用 AI 生成报告",
        "type": "boolean",
        "description": "勾选后生成报告时调用 LLM（需配置 API key）；不勾选则使用确定性规则报告（不调用 AI，节省额度）",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "AI",
        "default": True,
    },

    # ---------- network ----------
    {
        "key": "network.proxy",
        "label": "代理",
        "type": "url",
        "description": "HTTP 代理（留空则用系统代理）",
        "restart_required": True,
        "required": False,
        "sensitive": False,
        "group": "网络",
    },
    {
        "key": "network.timeout_seconds",
        "label": "超时（秒）",
        "type": "float",
        "description": "网络请求超时时间",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "网络",
        "default": 30.0,
    },
]


# Star rating color palettes (player.star_palette). Each palette is a list of
# tiers in ascending `max` order; a replay's stars pick the first tier where
# stars < max. `max: None` = unbounded (top tier). `cls` is a CSS class name
# applied to the STARS number (defined in frontend/style.css). The palette is
# shipped to the frontend via /api/status so display logic stays single-source.
STAR_PALETTES: list[dict] = [
    {
        "id": "community",
        "tiers": [
            {"max": 3.0, "cls": "star-gray"},    # beginner / low difficulty
            {"max": 5.0, "cls": "star-green"},   # medium
            {"max": 7.0, "cls": "star-yellow"},  # hard
            {"max": 9.0, "cls": "star-red"},     # expert
            {"max": None, "cls": "star-purple"}, # top tier (9★+)
        ],
    },
]


def get_star_palette(palette_id: str) -> dict | None:
    """Return a palette definition by id (None when unknown)."""
    for p in STAR_PALETTES:
        if p["id"] == palette_id:
            return p
    return None


def get_schema() -> list[dict]:
    return SETTINGS_SCHEMA


def get_group_order() -> list[str]:
    """Group display order."""
    order = []
    for item in SETTINGS_SCHEMA:
        g = item.get("group")
        if g not in order:
            order.append(g)
    return order
