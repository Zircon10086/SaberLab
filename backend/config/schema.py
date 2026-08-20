"""配置项 Schema（developrules.md §3）。

每个配置项元数据包含：key / label / type / description /
restart_required / required / sensitive / group / enum / default。
前端据此动态生成设置 UI（§9），后端据此校验与读写。
"""
from __future__ import annotations

# type 集合：
#   string / integer / float / boolean / enum / directory / file / url / secret

SETTINGS_SCHEMA: list[dict] = [
    # ---------- 路径（hidden：设置页由"游戏路径"卡片接管，仅选择根目录，
    # 后三项由根目录确定性派生，标准 Beat Saber 结构下无需手动指定） ----------
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

    # ---------- 玩家 ----------
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

    # ---------- 分析 ----------
    {
        "key": "analysis.window_seconds",
        "label": "时间窗口（秒）",
        "type": "float",
        "description": "时间序列分析的窗口宽度",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "分析",
        "default": 30.0,
    },
    {
        "key": "analysis.window_step_seconds",
        "label": "采样步长（秒）",
        "type": "float",
        "description": "时间序列采样间隔（1s = 每秒一个点）",
        "restart_required": False,
        "required": False,
        "sensitive": False,
        "group": "分析",
        "default": 1.0,
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

    # ---------- 服务器 ----------
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
        "description": "FastAPI 监听端口（默认 8787）",
        "restart_required": True,
        "required": False,
        "sensitive": False,
        "group": "服务器",
        "default": 8787,
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

    # ---------- 网络 ----------
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


def get_schema() -> list[dict]:
    return SETTINGS_SCHEMA


def get_group_order() -> list[str]:
    """分组展示顺序。"""
    order = []
    for item in SETTINGS_SCHEMA:
        g = item.get("group")
        if g not in order:
            order.append(g)
    return order
