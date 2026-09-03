"""Config loading: config/config.yaml + .env (no python-dotenv dependency)."""
from __future__ import annotations

import os
import pathlib
import sys
from dataclasses import dataclass, field

import yaml

# Project root resolution:
# - Running from source: backend/config/__init__.py -> three parent levels = project root
# - PyInstaller bundle: writable data (data/, config/) must live next to the exe,
#   not under the sys._MEIPASS temp extraction dir (removed on exit, DB would be lost)
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    PROJECT_ROOT = pathlib.Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def load_dotenv(path: pathlib.Path | None = None) -> None:
    """Minimal .env loader: KEY=VALUE lines, never overriding existing env vars."""
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


def dotenv_key_names(path: pathlib.Path | None = None) -> set[str]:
    """Names of the variables .env defines (without loading them).

    Used by the in-app restart: the spawned child inherits this process's
    environment, and since load_dotenv never overrides existing vars, a stale
    value (e.g. an old API key loaded before the user saved a new one) would
    win forever. The host strips these names from the child's env so .env is
    re-read fresh on startup.
    """
    path = path or (PROJECT_ROOT / ".env")
    names: set[str] = set()
    if not path.exists():
        return names
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k = line.partition("=")[0].strip()
        if k:
            names.add(k)
    return names


@dataclass
class Config:
    instance_root: str = ""
    replay_dir: str = ""
    custom_levels_dir: str = ""
    songcore_cache: str = ""
    # Optional second replay source (LocalLeaderboard mod, 2026-09):
    # derived from instance_root, auto-enabled when the directory exists;
    # it stores one copy per session (no exit replays) and doubles as a
    # safety copy for missing-file repair (HANDOFF §4.25 待办 2).
    local_leaderboard_dir: str = ""
    scoresaber_id: str = ""
    player_name_fallback: str = ""
    star_palette: str = "community"   # star rating color scheme (schema player.star_palette)
    data_source: str = "scoresaber"   # cloud data source: scoresaber | beatleader (2026-08)
    # [Deprecated] window_seconds / window_step_seconds: fixed time windows retired
    # (2026 decision, see backend/analysis/notes.py); fields kept only for backward
    # compatibility with leftover values in old config.yaml; no longer read by the engine.
    window_seconds: float = 30.0
    window_step_seconds: float = 10.0
    slope_group_notes: int = 50   # note group size for fatigue slope / AI summary (added 2026)
    fatigue_edge_seconds: float = 30.0
    host: str = "127.0.0.1"
    port: int = 6980
    ai_provider: str = "deepseek"
    ai_base_url: str = ""
    ai_model: str = "deepseek-chat"
    ai_api_key_env: str = "DEEPSEEK_API_KEY"
    ai_temperature: float = 0.3
    ai_max_tokens: int = 2500
    ai_report_enabled: bool = True   # checked: call the LLM for reports; unchecked: deterministic rule report (2026-08)
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
    # Optional second replay source path (derived from the game root for
    # old config.yaml without the key; zero-config auto detection).
    ll_dir = game.get("local_leaderboard_dir", "")
    if not ll_dir and game.get("instance_root"):
        ll_dir = str(pathlib.Path(str(game.get("instance_root")))
                     / "UserData" / "LocalLeaderboard" / "Replays").replace("\\", "/")
    cfg = Config(
        instance_root=game.get("instance_root", ""),
        replay_dir=game.get("replay_dir", ""),
        custom_levels_dir=game.get("custom_levels_dir", ""),
        songcore_cache=game.get("songcore_cache", ""),
        local_leaderboard_dir=ll_dir,
        scoresaber_id=str(player.get("scoresaber_id", "")),
        player_name_fallback=player.get("player_name_fallback", ""),
        star_palette=str(player.get("star_palette", "community")),
        data_source=str(player.get("data_source", "scoresaber")).lower(),
        window_seconds=float(analysis.get("window_seconds", 30)),
        window_step_seconds=float(analysis.get("window_step_seconds", 10)),
        slope_group_notes=int(analysis.get("slope_group_notes", 50)),
        fatigue_edge_seconds=float(analysis.get("fatigue_edge_seconds", 30)),
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 6980)),
        ai_provider=provider,
        ai_base_url=ai.get("base_url") or DEFAULT_AI_BASE_URLS.get(provider, ""),
        ai_model=ai.get("model") or DEFAULT_AI_MODELS.get(provider, ""),
        ai_api_key_env=ai.get("api_key_env") or "DEEPSEEK_API_KEY",
        ai_temperature=float(ai.get("temperature", 0.3)),
        ai_max_tokens=int(ai.get("max_tokens", 2500)),
        ai_report_enabled=bool(ai.get("ai_report_enabled", True)),
        proxy=network.get("proxy", "") or "",
        timeout_seconds=float(network.get("timeout_seconds", 30)),
    )
    cfg.ensure_dirs()
    return cfg
