"""配置加载：config/config.yaml + .env（不依赖 python-dotenv）。"""
from __future__ import annotations

import os
import pathlib
import sys
from dataclasses import dataclass, field

import yaml

# 项目根定位：
# - 源码运行：backend/config/__init__.py → 三级父目录 = 项目根
# - PyInstaller 打包：可写数据（data/、config/）必须放在 exe 旁边，
#   而不是 sys._MEIPASS 临时解包目录（退出即删，数据库会丢）
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    PROJECT_ROOT = pathlib.Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def load_dotenv(path: pathlib.Path | None = None) -> None:
    """极简 .env 加载：KEY=VALUE 行，不覆盖已存在的环境变量。"""
    path = path or (PROJECT_ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


@dataclass
class Config:
    instance_root: str = ""
    replay_dir: str = ""
    custom_levels_dir: str = ""
    songcore_cache: str = ""
    scoresaber_id: str = ""
    player_name_fallback: str = ""
    window_seconds: float = 30.0
    window_step_seconds: float = 10.0
    fatigue_edge_seconds: float = 30.0
    host: str = "127.0.0.1"
    port: int = 8787
    ai_provider: str = "deepseek"
    ai_base_url: str = ""
    ai_model: str = "deepseek-chat"
    ai_api_key_env: str = "DEEPSEEK_API_KEY"
    ai_temperature: float = 0.3
    ai_max_tokens: int = 2500
    proxy: str = ""
    timeout_seconds: float = 30.0

    data_dir: pathlib.Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    reports_dir: pathlib.Path = field(default_factory=lambda: PROJECT_ROOT / "reports")
    db_path: pathlib.Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "saberlab.sqlite")
    parsed_dir: pathlib.Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "parsed")

    @property
    def ai_api_key(self) -> str:
        return os.environ.get(self.ai_api_key_env, "").strip()

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.reports_dir, self.parsed_dir,
                  self.data_dir / "raw_replays"):
            pathlib.Path(d).mkdir(parents=True, exist_ok=True)


DEFAULT_AI_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
}
DEFAULT_AI_MODELS = {
    "deepseek": "deepseek-chat",
    "qwen": "qwen-plus",
    "openai": "gpt-4o-mini",
}


def load_config(path: pathlib.Path | None = None) -> Config:
    load_dotenv()
    path = path or (PROJECT_ROOT / "config" / "config.yaml")
    raw: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    game = raw.get("game") or {}
    player = raw.get("player") or {}
    analysis = raw.get("analysis") or {}
    server = raw.get("server") or {}
    ai = raw.get("ai") or {}
    network = raw.get("network") or {}

    provider = str(ai.get("provider") or "deepseek").lower()
    cfg = Config(
        instance_root=game.get("instance_root", ""),
        replay_dir=game.get("replay_dir", ""),
        custom_levels_dir=game.get("custom_levels_dir", ""),
        songcore_cache=game.get("songcore_cache", ""),
        scoresaber_id=str(player.get("scoresaber_id", "")),
        player_name_fallback=player.get("player_name_fallback", ""),
        window_seconds=float(analysis.get("window_seconds", 30)),
        window_step_seconds=float(analysis.get("window_step_seconds", 10)),
        fatigue_edge_seconds=float(analysis.get("fatigue_edge_seconds", 30)),
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 8787)),
        ai_provider=provider,
        ai_base_url=ai.get("base_url") or DEFAULT_AI_BASE_URLS.get(provider, ""),
        ai_model=ai.get("model") or DEFAULT_AI_MODELS.get(provider, ""),
        ai_api_key_env=ai.get("api_key_env") or "DEEPSEEK_API_KEY",
        ai_temperature=float(ai.get("temperature", 0.3)),
        ai_max_tokens=int(ai.get("max_tokens", 2500)),
        proxy=network.get("proxy", "") or "",
        timeout_seconds=float(network.get("timeout_seconds", 30)),
    )
    cfg.ensure_dirs()
    return cfg
