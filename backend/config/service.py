"""Config Service（developrules.md §5-§6）。

职责：
- 加载 config.yaml（唯一事实来源）
- 从游戏根目录派生 replay/maps/songcore 路径（用户只需指定 instance_root）
- 校验路径存在性
- 原子写回 config.yaml（tmp → flush → replace）

原则：业务模块一律经 Config 读取路径，禁止硬编码开发机路径。
"""
from __future__ import annotations

import os
import pathlib
import tempfile
from dataclasses import dataclass, field
from datetime import datetime

import yaml

from ..config import load_config, Config, PROJECT_ROOT

# 相对游戏根目录的子路径（Beat Saber 官方安装结构，确定性派生）
DERIVED_PATHS = {
    "custom_levels_dir": "Beat Saber_Data/CustomLevels",
    "replay_dir": "UserData/BeatLeader/Replays",
    "songcore_cache": "UserData/SongCore/SongHashData.dat",
}


@dataclass
class PathStatus:
    """一个派生路径的存在性检查结果。"""
    key: str
    label: str
    path: str
    exists: bool
    ok: bool
    note: str = ""


@dataclass
class SettingsView:
    """给前端看的配置（不含任何 secret）。"""
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
    """统一 Windows 路径分隔符为 /（pathlib 渲染友好）。"""
    if not p:
        return ""
    return str(pathlib.Path(p)).replace("\\", "/")


def derive_paths(instance_root: str) -> dict:
    """从游戏根目录派生三个子路径（不检查存在性）。"""
    root = pathlib.Path(instance_root) if instance_root else None
    out = {}
    for key, rel in DERIVED_PATHS.items():
        out[key] = normalize_path(str(root / rel)) if root else ""
    return out


def check_paths(instance_root: str) -> list[PathStatus]:
    """校验根目录及其派生路径。"""
    results = []
    root = pathlib.Path(instance_root) if instance_root else None

    if not root or not root.exists():
        results.append(PathStatus("instance_root", "游戏根目录",
                                  normalize_path(str(root)) if root else "",
                                  exists=False, ok=False,
                                  note="路径不存在"))
        return results

    results.append(PathStatus("instance_root", "游戏根目录",
                              normalize_path(str(root)),
                              exists=True, ok=True,
                              note="已找到"))
    for key, rel in DERIVED_PATHS.items():
        p = root / rel
        exists = p.exists()
        results.append(PathStatus(key, rel, normalize_path(str(p)),
                                  exists=exists, ok=exists,
                                  note="" if exists else "未找到，请确认根目录正确"))
    return results


class ConfigService:
    """配置的读 / 改 / 存 / 验证。config.yaml 是唯一事实来源。"""

    def __init__(self, config_path: pathlib.Path | None = None):
        self.config_path = config_path or (PROJECT_ROOT / "config" / "config.yaml")

    # ---------- 读 ----------
    def load(self) -> Config:
        return load_config(self.config_path)

    def view(self) -> SettingsView:
        return SettingsView.from_config(self.load())

    # ---------- 改（原子写回） ----------
    def save_instance_root(self, new_root: str) -> dict:
        """写入 instance_root 并原子替换 config.yaml。

        保持文件原有结构，只更新 game.instance_root 与派生路径。
        返回 {saved, restart_required}。
        """
        new_root = (new_root or "").strip().strip('"').strip("'")
        if not new_root:
            return {"saved": False, "error": "游戏根目录不能为空"}

        # 校验：根目录必须存在，且派生路径至少有一个存在
        checks = check_paths(new_root)
        root_ok = checks[0].ok if checks else False
        if not root_ok:
            return {"saved": False, "error": checks[0].note}

        raw = self._read_raw()
        raw.setdefault("game", {})
        raw["game"]["instance_root"] = normalize_path(new_root)
        # 同步写入派生路径（用户可后续微调，缺省自动派生）
        derived = derive_paths(new_root)
        for key, val in derived.items():
            raw["game"][key] = val

        try:
            self._write_atomic(raw)
        except OSError as e:
            return {"saved": False, "error": f"写入配置失败: {e}"}
        return {"saved": True, "restart_required": True,
                "message": "设置已保存，重启 SaberLab 后生效。"}

    # ---------- 内部 ----------
    def _read_raw(self) -> dict:
        if not self.config_path.exists():
            return {}
        text = self.config_path.read_text(encoding="utf-8")
        try:
            return yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            # 损坏的 config.yaml：先把原文件备份（带时间戳），再返回空。
            # 若不备份直接返回 {}，用户下次保存会把“空配置”写回，
            # 所有原有设置被静默清空（架构审查 P0-2.2）。
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = self.config_path.with_name(
                f"{self.config_path.name}.corrupt-{stamp}")
            try:
                backup.write_text(text, encoding="utf-8")
                print(f"[config] config.yaml 解析失败，原内容已备份到 {backup.name}；"
                      f"保存后将写入全新配置。解析错误：{e}")
            except OSError:
                print(f"[config] config.yaml 解析失败且备份失败（{e}）")
            return {}

    def _write_atomic(self, raw: dict) -> None:
        """原子写入：tmp → flush → os.replace。"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.config_path.with_suffix(".yaml.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.config_path)

    # ---------- schema 驱动的读写（§3 / §9） ----------
    def get_all_values(self) -> dict:
        """按 schema 返回所有配置项当前值（secret 脱敏）。"""
        from .schema import get_schema
        cfg = self.load()
        out = {}
        for item in get_schema():
            key = item["key"]
            val = self._get_value(cfg, item)
            out[key] = val
        return out

    def _get_value(self, cfg: Config, item: dict):
        """取单个配置项值；secret 类型返回脱敏对象。"""
        key = item["key"]
        if item.get("type") == "secret":
            raw = cfg.ai_api_key
            if not raw:
                return {"configured": False, "masked": None}
            masked = raw[:5] + "••••••••" + raw[-4:] if len(raw) > 12 else "••••••••"
            return {"configured": True, "masked": masked}
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
            }
            cur = mapping.get(parts[1], "")
        elif parts[0] == "analysis":
            mapping = {
                "window_seconds": cfg.window_seconds,
                "window_step_seconds": cfg.window_step_seconds,
                "fatigue_edge_seconds": cfg.fatigue_edge_seconds,
            }
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
            }
            cur = mapping.get(parts[1], "")
        elif parts[0] == "network":
            mapping = {
                "proxy": cfg.proxy,
                "timeout_seconds": cfg.timeout_seconds,
            }
            cur = mapping.get(parts[1], "")
        return cur

    def save_values(self, updates: dict) -> dict:
        """批量保存配置项（原子写回）。

        updates: {key: value}，只允许 schema 中存在的 key。
        secret 类型写入 .env（不进入 config.yaml）。
        返回 {saved, restart_required, errors: {key: msg}}。
        """
        from .schema import get_schema
        schema_by_key = {it["key"]: it for it in get_schema()}
        unknown = [k for k in updates if k not in schema_by_key]
        if unknown:
            return {"saved": False,
                    "error": f"未知配置项: {', '.join(unknown)}"}

        # 分离 secret 与普通项
        secrets = {}
        normals = {}
        for k, v in updates.items():
            item = schema_by_key[k]
            if item.get("type") == "secret":
                secrets[k] = v
            else:
                normals[k] = item

        # 1) 普通项 → config.yaml
        if normals:
            raw = self._read_raw()
            for k, item in normals.items():
                parts = k.split(".")
                val = self._coerce(item, updates[k])
                raw.setdefault(parts[0], {})[parts[1]] = val
            # 若改了 instance_root 则同步派生路径
            if "game.instance_root" in normals:
                derived = derive_paths(str(updates["game.instance_root"]))
                for dk, dv in derived.items():
                    raw.setdefault("game", {})[dk] = dv
            try:
                self._write_atomic(raw)
            except OSError as e:
                return {"saved": False, "error": f"写入配置失败: {e}"}

        # 2) secret → .env
        if secrets:
            err = self._write_env(secrets)
            if err:
                return {"saved": False, "error": err}

        # restart_required = 任一改动的项需要重启
        restart = any(schema_by_key[k].get("restart_required")
                      for k in updates)
        return {"saved": True, "restart_required": restart,
                "message": "设置已保存" + ("，重启 SaberLab 后生效。" if restart else "。")}

    def _coerce(self, item: dict, val):
        """按 schema 类型做基础类型转换。"""
        t = item.get("type")
        if t in ("integer",):
            return int(val)
        if t in ("float",):
            return float(val)
        if t in ("boolean",):
            return bool(val)
        return str(val)

    def _write_env(self, secrets: dict) -> str | None:
        """把 secret 写入 .env（原子）。key 形如 ai.api_key → 环境变量名。"""
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
        # 原子写 .env
        tmp = env_path.with_suffix(".env.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, env_path)
        return None
