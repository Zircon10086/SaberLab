"""Config Service (developrules.md §5-§6).

Responsibilities:
- Load config.yaml (single source of truth)
- Derive replay/maps/songcore paths from the game root (users only specify instance_root)
- Validate path existence
- Atomically write back config.yaml (tmp -> flush -> replace)

Principle: business modules always read paths through Config; never hardcode dev-machine paths.
"""
from __future__ import annotations

import os
import pathlib
import tempfile
from dataclasses import dataclass, field
from datetime import datetime

import yaml

from ..config import load_config, Config, PROJECT_ROOT

# Sub-paths relative to the game root (official Beat Saber install layout, deterministically derived)
DERIVED_PATHS = {
    "custom_levels_dir": "Beat Saber_Data/CustomLevels",
    "replay_dir": "UserData/BeatLeader/Replays",
    "songcore_cache": "UserData/SongCore/SongHashData.dat",
    # Optional second replay source (LocalLeaderboard mod, 2026-09): derived
    # alongside the required paths, but NEVER required — zero-config feature,
    # enabled automatically when the directory exists.
    "local_leaderboard_dir": "UserData/LocalLeaderboard/Replays",
}


@dataclass
class PathStatus:
    """Existence check result for one derived path."""
    key: str
    label: str
    path: str
    exists: bool
    ok: bool
    note: str = ""
    # Rendering hint for the settings checklist: "ok" (green) | "bad" (red) |
    # "note" (neutral — neither a problem nor a confirmation, e.g. an optional
    # mod that is simply not installed).
    status: str = "ok"


# BeatLeader mod config (relative to the game root): its "keep latest only"
# replay setting deletes older .bsor files of the same map & difficulty.
BEATLEADER_CONFIG_REL = "UserData/BeatLeader.json"
# Config key behind the in-game setting labelled "keep latest only". Missing key
# means the mod's own default, which has always been "old replays are kept"
# (verified on this machine: with the key absent the log shows no deletion
# warnings even on fresh saves) — so absence must NOT be reported as a problem.
BEATLEADER_OVERRIDE_KEY = "OverrideOldReplays"


def beatleader_replay_deletion_check(instance_root: str) -> PathStatus:
    """Report whether the BeatLeader mod is set to delete older local replays.

    Root cause context (HANDOFF §4.25): with `OverrideOldReplays` true the mod
    removes older .bsor files of the same map & difficulty on every save — the
    game log prints "Deleting old replays" / "old replays will be deleted". This
    is a warning row only: it never affects `valid` (the game path itself is
    fine, and the user must change the setting inside the game, not here).
    """
    label = "BeatLeader 回放保留「keep latest only」"
    rel = BEATLEADER_CONFIG_REL
    if not instance_root:
        return PathStatus("replay_retention", label, rel, exists=False, ok=False,
                          note="请先填写游戏根目录", status="note")
    cfg_path = pathlib.Path(instance_root) / BEATLEADER_CONFIG_REL
    # Optional mod: not installed → nothing to warn about (never red here).
    if not cfg_path.exists():
        return PathStatus("replay_retention", label, rel, exists=False, ok=False,
                          note="未找到 BeatLeader 配置，可忽略",
                          status="note")
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return PathStatus("replay_retention", label, rel, exists=True, ok=False,
                          note="配置无法解析（不影响其它检测）", status="note")
    if not isinstance(data, dict) or BEATLEADER_OVERRIDE_KEY not in data:
        return PathStatus("replay_retention", label, rel, exists=True, ok=False,
                          note="配置中无该设置项，旧回放会被保留",
                          status="note")
    value = data.get(BEATLEADER_OVERRIDE_KEY)
    # Only an explicitly enabled value counts as enabled: the mod writes a plain
    # JSON bool, and a bare truthiness test would misread strings like "False"
    # (never treat an unrecognized value as a red warning).
    enabled = value is True or value in (1, "true", "True", "TRUE", "yes")
    if enabled:
        return PathStatus("replay_retention", label, rel, exists=True, ok=False,
                          note="旧回放会被删除，请关闭游戏内 BeatLeader 的「keep latest only」",
                          status="bad")
    return PathStatus("replay_retention", label, rel, exists=True, ok=True,
                      note="已关闭：旧回放会被保留", status="ok")


def check_paths(instance_root: str) -> list[PathStatus]:
    """Validate the root directory and its derived paths."""
    results = []
    root = pathlib.Path(instance_root) if instance_root else None

    if not root or not root.exists():
        results.append(PathStatus("instance_root", "游戏根目录",
                                  normalize_path(str(root)) if root else "",
                                  exists=False, ok=False,
                                  note="路径不存在", status="bad"))
        return results

    results.append(PathStatus("instance_root", "游戏根目录",
                              normalize_path(str(root)),
                              exists=True, ok=True,
                              note="已找到", status="ok"))
    for key, rel in DERIVED_PATHS.items():
        p = root / rel
        exists = p.exists()
        note = ""
        status = "ok" if exists else "bad"
        if not exists:
            note = "未找到，请确认根目录正确"
            if key == "local_leaderboard_dir":
                note = "未找到（可选：LocalLeaderboard 补充扫描源，无该 mod 可忽略）"
                status = "note"
        results.append(PathStatus(key, rel, normalize_path(str(p)),
                                  exists=exists, ok=exists, note=note, status=status))
    # Not a path check, but it belongs to the same "game side is ready" checklist
    # and is the one item the user has to fix inside the game (2026-09, §4.25).
    results.append(beatleader_replay_deletion_check(instance_root))
    return results


@dataclass
class SettingsView:
    """Config exposed to the frontend (no secrets included)."""
    instance_root: str = ""
    replay_dir: str = ""
    custom_levels_dir: str = ""
    songcore_cache: str = ""
    scoresaber_id: str = ""
    ai_provider: str = ""
    ai_model: str = ""
    ai_configured: bool = False

    @classmethod
    def from_config(cls, cfg: Config) -> "SettingsView":
        return cls(
            instance_root=cfg.instance_root,
            replay_dir=cfg.replay_dir,
            custom_levels_dir=cfg.custom_levels_dir,
            songcore_cache=cfg.songcore_cache,
            scoresaber_id=cfg.scoresaber_id,
            ai_provider=cfg.ai_provider,
            ai_model=cfg.ai_model,
            ai_configured=bool(cfg.ai_api_key),
        )

    def to_dict(self) -> dict:
        return {
            "instance_root": self.instance_root,
            "replay_dir": self.replay_dir,
            "custom_levels_dir": self.custom_levels_dir,
            "songcore_cache": self.songcore_cache,
            "scoresaber_id": self.scoresaber_id,
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_configured": self.ai_configured,
        }


def normalize_path(p: str) -> str:
    """Normalize Windows path separators to / (pathlib-rendering friendly)."""
    if not p:
        return ""
    return str(pathlib.Path(p)).replace("\\", "/")


def derive_paths(instance_root: str) -> dict:
    """Derive the three sub-paths from the game root (without checking existence)."""
    root = pathlib.Path(instance_root) if instance_root else None
    out = {}
    for key, rel in DERIVED_PATHS.items():
        out[key] = normalize_path(str(root / rel)) if root else ""
    return out


class ConfigService:
    """Read / modify / persist / validate configuration. config.yaml is the single source of truth."""

    def __init__(self, config_path: pathlib.Path | None = None):
        self.config_path = config_path or (PROJECT_ROOT / "config" / "config.yaml")

    # ---------- read ----------
    def load(self) -> Config:
        return load_config(self.config_path)

    def view(self) -> SettingsView:
        return SettingsView.from_config(self.load())

    # ---------- modify (atomic write-back) ----------
    def save_instance_root(self, new_root: str) -> dict:
        """Write instance_root and atomically replace config.yaml.

        Preserves the file's existing structure, only updating game.instance_root
        and the derived paths. Returns {saved, restart_required}.
        """
        new_root = (new_root or "").strip().strip('"').strip("'")
        if not new_root:
            return {"saved": False, "error": "游戏根目录不能为空"}

        # Validate: the root must exist, and at least one derived path must exist
        checks = check_paths(new_root)
        root_ok = checks[0].ok if checks else False
        if not root_ok:
            return {"saved": False, "error": checks[0].note}

        raw = self._read_raw()
        raw.setdefault("game", {})
        raw["game"]["instance_root"] = normalize_path(new_root)
        # Write derived paths alongside (user can fine-tune later; auto-derived by default)
        derived = derive_paths(new_root)
        for key, val in derived.items():
            raw["game"][key] = val

        try:
            self._write_atomic(raw)
        except OSError as e:
            return {"saved": False, "error": f"写入配置失败: {e}"}
        return {"saved": True, "restart_required": True,
                "message": "设置已保存，重启 SaberLab 后生效。"}

    # ---------- internal ----------
    def _read_raw(self) -> dict:
        if not self.config_path.exists():
            return {}
        text = self.config_path.read_text(encoding="utf-8")
        try:
            return yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            # Corrupt config.yaml: back up the original file first (timestamped),
            # then return empty. Without a backup, returning {} would let the next
            # save write an "empty config" back and silently wipe all existing
            # settings (architecture review P0-2.2).
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = self.config_path.with_name(
                f"{self.config_path.name}.corrupt-{stamp}")
            try:
                backup.write_text(text, encoding="utf-8")
                print(f"[config] Failed to parse config.yaml; original content backed up "
                      f"to {backup.name}; a fresh config will be written on next save. "
                      f"Parse error: {e}")
            except OSError:
                print(f"[config] Failed to parse config.yaml and the backup also failed ({e})")
            return {}

    def _write_atomic(self, raw: dict) -> None:
        """Atomic write: tmp -> flush -> os.replace."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.config_path.with_suffix(".yaml.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.config_path)

    # ---------- schema-driven read/write (§3 / §9) ----------
    def get_all_values(self) -> dict:
        """Return current values of all schema config items (secrets masked)."""
        from .schema import get_schema
        cfg = self.load()
        out = {}
        for item in get_schema():
            key = item["key"]
            val = self._get_value(cfg, item)
            out[key] = val
        return out

    def _get_value(self, cfg: Config, item: dict):
        """Get a single config item's value; secret type returns a masked object."""
        key = item["key"]
        if item.get("type") == "secret":
            raw = cfg.ai_api_key
            if not raw:
                return {"configured": False, "masked": None, "source": "",
                        "source_name": ""}
            masked = raw[:5] + "••••••••" + raw[-4:] if len(raw) > 12 else "••••••••"
            # Where the key came from ('env' | 'env_file'): a machine-wide
            # environment variable is otherwise indistinguishable from a leak.
            return {"configured": True, "masked": masked,
                    "source": cfg.ai_api_key_source(),
                    "source_name": cfg.ai_api_key_source_name()}
        parts = key.split(".")
        cur = None
        if parts[0] == "game":
            mapping = {
                "instance_root": cfg.instance_root,
                "replay_dir": cfg.replay_dir,
                "custom_levels_dir": cfg.custom_levels_dir,
                "songcore_cache": cfg.songcore_cache,
            }
            cur = mapping.get(parts[1], "")
        elif parts[0] == "player":
            mapping = {
                "scoresaber_id": cfg.scoresaber_id,
                "player_name_fallback": cfg.player_name_fallback,
                "star_palette": cfg.star_palette,
                "data_source": cfg.data_source,
            }
            cur = mapping.get(parts[1], "")
        elif parts[0] == "analysis":
            mapping = {
                "window_seconds": cfg.window_seconds,
                "window_step_seconds": cfg.window_step_seconds,
                "slope_group_notes": cfg.slope_group_notes,
                "fatigue_edge_seconds": cfg.fatigue_edge_seconds,
            }
            cur = mapping.get(parts[1], "")
        elif parts[0] == "ui":
            # UI-only preferences: NOT analysis parameters (changing these must not
            # reset the analysis cache — see the schema comment on
            # ui.session_gap_minutes).
            mapping = {"session_gap_minutes": cfg.session_gap_minutes}
            cur = mapping.get(parts[1], "")
        elif parts[0] == "server":
            mapping = {"host": cfg.host, "port": cfg.port}
            cur = mapping.get(parts[1], "")
        elif parts[0] == "ai":
            mapping = {
                "provider": cfg.ai_provider,
                "base_url": cfg.ai_base_url,
                "model": cfg.ai_model,
                "temperature": cfg.ai_temperature,
                "max_tokens": cfg.ai_max_tokens,
                "ai_report_enabled": cfg.ai_report_enabled,
            }
            cur = mapping.get(parts[1], "")
        elif parts[0] == "network":
            mapping = {
                "proxy": cfg.proxy,
                "timeout_seconds": cfg.timeout_seconds,
            }
            cur = mapping.get(parts[1], "")
        return cur

    def _effective_value(self, item: dict, raw):
        """Normalize a stored or submitted value to the schema type for comparison.

        Stored raw yaml values may be None (empty field) or already typed;
        submitted values arrive as strings from the settings form. Both sides
        are coerced so "50" vs 50 compares equal (settings save must only count
        actually-changed keys — the form submits every visible field).
        """
        t = item.get("type")
        if raw is None:
            raw = False if t == "boolean" else 0 if t in ("integer", "float") else ""
        try:
            if t == "integer":
                return int(raw)
            if t == "float":
                return float(raw)
            if t == "boolean":
                return bool(raw)
        except (TypeError, ValueError):
            return raw
        if t == "enum":
            return str(raw)
        return str(raw)

    def changed_keys(self, updates: dict) -> list[str]:
        """Keys in `updates` whose coerced value differs from the stored config.

        The settings form submits every visible field on each save; side effects
        keyed on submission (analysis cache reset, restart-required hint) must
        fire only for values that actually changed, not for unchanged resubmits.
        Secret items are always treated as changed (they are only sent when the
        user typed a new value; the stored one is unreadable by design).
        """
        from .schema import get_schema
        schema_by_key = {it["key"]: it for it in get_schema()}
        raw = self._read_raw()
        changed = []
        for k, v in updates.items():
            item = schema_by_key.get(k)
            if item is None:
                continue
            if item.get("type") == "secret":
                changed.append(k)
                continue
            parts = k.split(".")
            old = (raw.get(parts[0]) or {}).get(parts[1])
            if self._effective_value(item, old) != self._effective_value(item, v):
                changed.append(k)
        return changed

    def save_values(self, updates: dict) -> dict:
        """Batch-save config items (atomic write-back).

        updates: {key: value}; only keys present in the schema are allowed.
        secret-type items are written to .env (not config.yaml).
        Returns {saved, restart_required, changed, errors: {key: msg}}.

        `changed` lists the keys whose value actually differs from the stored
        config; callers must key side effects (analysis cache reset,
        restart-required hint) on it — the form resubmits unchanged fields.
        """
        from .schema import get_schema
        schema_by_key = {it["key"]: it for it in get_schema()}
        unknown = [k for k in updates if k not in schema_by_key]
        if unknown:
            return {"saved": False,
                    "error": f"未知配置项: {', '.join(unknown)}"}

        changed = self.changed_keys(updates)

        # Split secrets from normal items
        secrets = {}
        normals = {}
        for k, v in updates.items():
            item = schema_by_key[k]
            if item.get("type") == "secret":
                secrets[k] = v
            else:
                normals[k] = item

        # 1) normal items -> config.yaml
        if normals:
            raw = self._read_raw()
            for k, item in normals.items():
                parts = k.split(".")
                val = self._coerce(item, updates[k])
                raw.setdefault(parts[0], {})[parts[1]] = val
            # Re-derive sub-paths when instance_root changes
            if "game.instance_root" in normals:
                derived = derive_paths(str(updates["game.instance_root"]))
                for dk, dv in derived.items():
                    raw.setdefault("game", {})[dk] = dv
            try:
                self._write_atomic(raw)
            except OSError as e:
                return {"saved": False, "error": f"写入配置失败: {e}"}

        # 2) secrets -> .env
        if secrets:
            err = self._write_env(secrets)
            if err:
                return {"saved": False, "error": err}

        # restart_required = only among the actually-changed keys
        restart = any(schema_by_key[k].get("restart_required") for k in changed)
        return {"saved": True, "restart_required": restart, "changed": changed,
                "message": "设置已保存" + ("，重启 SaberLab 后生效。" if restart else "。")}

    def _coerce(self, item: dict, val):
        """Basic type coercion according to the schema type."""
        t = item.get("type")
        if t in ("integer",):
            return int(val)
        if t in ("float",):
            return float(val)
        if t in ("boolean",):
            return bool(val)
        return str(val)

    def _write_env(self, secrets: dict) -> str | None:
        """Write secrets to .env (atomic). Keys like ai.api_key -> environment variable name."""
        env_map = {"ai.api_key": ("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY")}
        env_path = PROJECT_ROOT / ".env"
        lines = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()
        changed = False
        for key, val in secrets.items():
            if key not in env_map:
                continue
            env_name, comment = env_map[key]
            val_str = str(val).strip().strip('"').strip("'")
            new_line = f"{env_name}={val_str}"
            found = False
            for i, line in enumerate(lines):
                if line.strip().startswith(f"{env_name}=") or line.strip() == env_name:
                    lines[i] = new_line
                    found = True
                    changed = True
                    break
            if not found:
                lines.append(new_line)
                changed = True
        if not changed:
            return None
        # Atomically write .env
        tmp = env_path.with_suffix(".env.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, env_path)
        return None
