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


# Names load_dotenv injected into os.environ (keys the inherited process environment did
# not already have). Kept for diagnostics: the AI key itself is no longer read from here
# (see Config.ai_api_key), but knowing which names came from .env stays useful.
_DOTENV_INJECTED: set[str] = set()


def dotenv_injected_names() -> frozenset[str]:
    """Names .env loading has set in os.environ during this process's lifetime."""
    return frozenset(_DOTENV_INJECTED)


def read_env_file(path: pathlib.Path, names: set[str] | None = None) -> dict[str, str]:
    """Values defined in a .env file (optionally restricted to `names`).

    Used for secrets: unlike `load_dotenv`, this reports what the FILE says, so the
    file - not the process environment - decides. Empty values are skipped.
    """
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if not k or not v:
            continue
        if names is not None and k not in names:
            continue
        out[k] = v
    return out


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
            _DOTENV_INJECTED.add(k)


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
    # Local secrets file for provider keys (2026-09: the ONLY place the AI key is read
    # from, so an ambient environment variable can never configure the app).
    # It lives next to the executable (PROJECT_ROOT), NOT next to config.yaml, which sits
    # in a config/ subdirectory: <exe dir>/.env + <exe dir>/config/config.yaml.
    dotenv_path: pathlib.Path = field(default_factory=lambda: PROJECT_ROOT / ".env")
    star_palette: str = "community"   # star rating color scheme (schema player.star_palette)
    data_source: str = "scoresaber"   # cloud data source: scoresaber | beatleader (2026-08)
    # [Deprecated] window_seconds / window_step_seconds: fixed time windows retired
    # (2026 decision, see backend/analysis/notes.py); fields kept only for backward
    # compatibility with leftover values in old config.yaml; no longer read by the engine.
    window_seconds: float = 30.0
    window_step_seconds: float = 10.0
    slope_group_notes: int = 50   # note group size for fatigue slope / AI summary (added 2026)
    # Session gap for the overview "by session" paging (2026): two replays further
    # apart than this belong to different play sessions. 60 min is far longer than
    # a song (incl. restarts), so midnight cross-day play and morning/afternoon
    # blocks are grouped correctly.
    session_gap_minutes: int = 60
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
        """The AI key, read from the LOCAL secrets file only (2026-09 user decision).

        Only `<config dir>/.env` counts - the file next to the executable in a release.
        The process environment is deliberately ignored: a machine-wide
        `DEEPSEEK_API_KEY` (e.g. set for another tool) reached double-click launches and
        made an install that had never been configured show a key that was not its own.

        Read on every access so a value saved in the settings page applies immediately
        and a restart can never resurrect a stale value.
        """
        return read_env_file(self.dotenv_path, {self.ai_api_key_env}).get(
            self.ai_api_key_env, "").strip()

    def ai_api_key_source(self) -> str:
        """Origin of the effective API key: 'env_file' (the local file) or '' (absent).

        Kept as a source contract so the settings page can label it; the environment is
        no longer a possible origin, which is what makes the label unambiguous.
        """
        return "env_file" if self.ai_api_key else ""

    def ai_api_key_source_name(self) -> str:
        """Human-identifiable name of the key's origin."""
        return ".env" if self.ai_api_key else ""

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
    ui = raw.get("ui") or {}

    # ---- one-shot migration: session_gap_minutes moved out of analysis.* ----
    # It is a UI grouping threshold that takes part in no analysis, but living in
    # analysis.* made every tweak reset the whole analysis cache (metrics /
    # motion_series wiped, all replays back to pending). Configs written by that
    # short-lived layout still carry the old key, so move it to ui.* and drop the
    # stale entry — the settings page writes back the whole section, so a leftover
    # duplicate key would otherwise linger (and read as "two sources of truth").
    if path.exists() and isinstance(analysis, dict) and "session_gap_minutes" in analysis:
        moved = analysis.pop("session_gap_minutes")
        if not isinstance(ui, dict):
            ui = {}
        if "session_gap_minutes" not in ui:
            ui["session_gap_minutes"] = moved
        raw["ui"] = ui
        try:
            path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                            encoding="utf-8")
            print("[config] migrated analysis.session_gap_minutes -> ui.session_gap_minutes",
                  flush=True)
        except OSError as e:      # read-only config dir: keep the in-memory value
            print(f"[config] session gap migration could not rewrite config.yaml: {e}",
                  flush=True)

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
        # Session gap for the overview "by session" paging: a UI grouping threshold
        # that takes part in NO analysis, so it is kept out of the analysis.*
        # namespace (a change there resets the whole analysis cache). Reads the old
        # analysis.session_gap_minutes as a fallback for configs written by the
        # brief window when it lived there. Clamped so a bad value cannot merge the
        # whole library into one session or split every play.
        session_gap_minutes=max(1, min(1440, int(
            ui.get("session_gap_minutes",
                   analysis.get("session_gap_minutes", 60))))),
        fatigue_edge_seconds=float(analysis.get("fatigue_edge_seconds", 30)),
        host=server.get("host", "127.0.0.1"),
        port=int(server.get("port", 6980)),
        ai_provider=provider,
        ai_base_url=ai.get("base_url") or DEFAULT_AI_BASE_URLS.get(provider, ""),
        ai_model=ai.get("model") or DEFAULT_AI_MODELS.get(provider, ""),
        ai_api_key_env=ai.get("api_key_env") or "DEEPSEEK_API_KEY",
        # the secrets file sits beside the executable, not beside config.yaml
        # (<exe dir>/.env vs <exe dir>/config/config.yaml); an explicit --config path only
        # moves config.yaml, which is why this is derived from the path's parent's parent
        dotenv_path=(path.parent.parent / ".env") if path.parent.name == "config"
        else (path.parent / ".env"),
        ai_temperature=float(ai.get("temperature", 0.3)),
        ai_max_tokens=int(ai.get("max_tokens", 2500)),
        ai_report_enabled=bool(ai.get("ai_report_enabled", True)),
        proxy=network.get("proxy", "") or "",
        timeout_seconds=float(network.get("timeout_seconds", 30)),
    )
    cfg.ensure_dirs()
    return cfg
