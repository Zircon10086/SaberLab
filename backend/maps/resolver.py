"""Map Resolver：把 Replay 的 map_hash 匹配到本地 CustomLevels。

hash 算法（权威来源 SongCore Utilities/Hashing.cs，与游戏内一致）：
    SHA1( info.dat 字节 + 各 beatmap 文件字节（按 info.dat 中
    _difficultyBeatmapSets 出现顺序拼接） ) -> 大写 HEX

优先读取游戏自己的缓存 UserData/SongCore/SongHashData.dat（1000+ 谱面
瞬间完成），缺失/失效时按上述算法自行计算并写入 SQLite 缓存。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from ..db.repository import Repository


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _find_ci(folder: pathlib.Path, name: str) -> Optional[pathlib.Path]:
    """大小写不敏感地查找文件（Windows 下 info.dat / Info.dat 均可能出现）。"""
    target = name.lower()
    try:
        for p in folder.iterdir():
            if p.is_file() and p.name.lower() == target:
                return p
    except OSError:
        return None
    return None


def read_level_info(folder: pathlib.Path) -> Optional[dict]:
    """读取 info.dat，兼容 V2（下划线前缀）与 V3 字段。"""
    info_path = _find_ci(folder, "info.dat")
    if info_path is None:
        return None
    try:
        raw = json.loads(info_path.read_bytes().decode("utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None

    def g(*keys, default=None):
        for k in keys:
            if k in raw:
                return raw[k]
        return default

    difficulties = []
    sets = g("_difficultyBeatmapSets", "difficultyBeatmapSets", default=[]) or []
    for s in sets:
        characteristic = s.get("_beatmapCharacteristicName") or s.get("characteristicName") or ""
        for d in s.get("_difficultyBeatmaps") or s.get("difficultyBeatmaps") or []:
            diff = d.get("_difficulty") or d.get("difficulty") or ""
            label = ((d.get("_customData") or {}).get("_difficultyLabel")
                     or (d.get("customData") or {}).get("difficultyLabel") or "")
            fname = d.get("_beatmapFilename") or d.get("beatmapFilename") or ""
            njs = d.get("_noteJumpMovementSpeed") or d.get("noteJumpMovementSpeed")
            difficulties.append({
                "characteristic": characteristic,
                "difficulty": diff,
                "label": label,
                "filename": fname,
                "njs": njs,
            })
    
    # 估算歌曲长度：从谱面数据的最后一个 note 计算
    song_length = g("_songLength", "songLength", default=0)
    if not song_length and difficulties:
        bpm = g("_beatsPerMinute", "beatsPerMinute", default=0)
        if bpm > 0:
            # 优先读取 ExpertPlus 或 Expert 难度
            for diff_info in reversed(difficulties):
                fname = diff_info.get("filename")
                if fname:
                    diff_path = _find_ci(folder, fname)
                    if diff_path:
                        try:
                            diff_data = json.loads(diff_path.read_bytes().decode("utf-8-sig"))
                            notes = diff_data.get("_notes") or diff_data.get("notes") or []
                            if notes:
                                # 找到最后一个 note 的 beat 时间
                                last_beat = max(n.get("_time", 0) or n.get("time", 0) for n in notes)
                                # 估算歌曲长度（秒）= (last_beat / bpm) * 60
                                song_length = (last_beat / bpm) * 60
                                break
                        except (json.JSONDecodeError, OSError):
                            continue
    
    return {
        "raw": raw,
        "version": g("_version", "version", default=""),
        "song_name": g("_songName", "songName", default=""),
        "song_sub_name": g("_songSubName", "songSubName", default=""),
        "song_author": g("_songAuthorName", "songAuthorName", default=""),
        "mapper": g("_levelAuthorName", "levelAuthorName", default=""),
        "bpm": g("_beatsPerMinute", "beatsPerMinute", default=0),
        "song_length": song_length,
        "song_filename": g("_songFilename", "songFilename", default=""),
        "cover_filename": g("_coverImageFilename", "coverImageFilename", default=""),
        "environment": g("_environmentName", "environmentName", default=""),
        "difficulties": difficulties,
    }


def compute_level_nps(folder: pathlib.Path, info: Optional[dict] = None) -> dict:
    """计算谱面每个难度的 NPS（notes/秒，方块密度）。

    从各难度 .dat 文件读取 notes 列表：
      时长 = 最后一个 note 的 beat 时间换算为秒（beat * 60 / BPM）
      NPS = notes 总数 / 时长
    返回 {"Standard|Expert": 4.2, "Standard|Hard": 3.1, ...}。
    """
    if info is None:
        info = read_level_info(folder)
        if info is None:
            return {}
    bpm = float(info.get("bpm") or 0)
    if bpm <= 0:
        return {}
    out: dict[str, float] = {}
    for d in info.get("difficulties") or []:
        fname = d.get("filename") or ""
        characteristic = d.get("characteristic") or ""
        difficulty = d.get("difficulty") or ""
        if not fname or not difficulty:
            continue
        bp = _find_ci(folder, fname)
        if bp is None:
            continue
        try:
            data = json.loads(bp.read_bytes().decode("utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        # v2 字段 _notes / v3 字段 colorNotes + bombNotes
        notes = (data.get("_notes") or data.get("notes")
                 or data.get("colorNotes") or [])
        if not notes:
            continue
        # 最后一个 note 的 beat 时间（v2: _time/time；v3: b）
        last_beat = 0.0
        for n in notes:
            t = n.get("_time", n.get("time", n.get("b", 0)))
            if t:
                last_beat = max(last_beat, float(t))
        if last_beat <= 0:
            continue
        duration_sec = last_beat * 60.0 / bpm
        if duration_sec <= 0:
            continue
        out[f"{characteristic}|{difficulty}"] = round(len(notes) / duration_sec, 2)
    return out


def compute_level_hash(folder: pathlib.Path, info: Optional[dict] = None) -> Optional[str]:
    """按 SongCore 算法计算谱面 hash。"""
    info_path = _find_ci(folder, "info.dat")
    if info_path is None:
        return None
    h = hashlib.sha1()
    try:
        h.update(info_path.read_bytes())
    except OSError:
        return None
    if info is None:
        info = read_level_info(folder)
        if info is None:
            return None
    for d in info["difficulties"]:
        fname = d.get("filename") or ""
        if not fname:
            continue
        bp = _find_ci(folder, fname)
        if bp is not None:
            try:
                h.update(bp.read_bytes())
            except OSError:
                continue
    return h.hexdigest().upper()


class MapResolver:
    def __init__(self, custom_levels_dir: str, repo: Repository,
                 songcore_cache_path: str = ""):
        self.levels_dir = pathlib.Path(custom_levels_dir)
        self.repo = repo
        self.songcore_cache_path = pathlib.Path(songcore_cache_path) if songcore_cache_path else None
        self._negative_cache: set[str] = set()   # 扫描后仍找不到的 hash，避免反复 rescan
        self._last_scan = 0.0                    # scan 防抖时间戳（批量缺失 hash 场景）

    def update_paths(self, custom_levels_dir: str, songcore_cache_path: str = "") -> None:
        """路径热更新（设置页保存后调用，无需重启）。"""
        self.levels_dir = pathlib.Path(custom_levels_dir or "")
        self.songcore_cache_path = (
            pathlib.Path(songcore_cache_path) if songcore_cache_path else None)
        self._negative_cache.clear()   # 路径变了，旧否定缓存作废

    # ---------- SongCore cache ----------
    def load_songcore_cache(self) -> dict[str, str]:
        """返回 {文件夹名: hash}。缓存 key 形如 '.\\Beat Saber_Data\\CustomLevels\\xxx'。"""
        out: dict[str, str] = {}
        if not self.songcore_cache_path or not self.songcore_cache_path.exists():
            return out
        try:
            data = json.loads(self.songcore_cache_path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return out
        for path_key, v in data.items():
            if not isinstance(v, dict):
                continue
            song_hash = v.get("songHash") or v.get("SongHash") or ""
            if not song_hash:
                continue
            folder_name = pathlib.PureWindowsPath(path_key).name
            if folder_name:
                out[folder_name] = song_hash.upper()
        return out

    # ---------- scan ----------
    def scan(self, progress_cb: Optional[Callable[[int, int, str], None]] = None,
             force_recompute: bool = False) -> dict:
        """扫描 CustomLevels，建立 hash -> 谱面 缓存。"""
        stats = {"scanned": 0, "from_songcore_cache": 0, "computed": 0,
                 "reused_db": 0, "errors": 0, "duration_sec": 0.0}
        t0 = time.time()
        if not self.levels_dir.exists():
            stats["error"] = f"CustomLevels 目录不存在: {self.levels_dir}"
            return stats

        folders = [p for p in self.levels_dir.iterdir() if p.is_dir()]
        sc_cache = self.load_songcore_cache()
        total = len(folders)

        for i, folder in enumerate(folders):
            if progress_cb and (i % 50 == 0 or i == total - 1):
                progress_cb(i + 1, total, folder.name)
            stats["scanned"] += 1
            try:
                self._process_folder(folder, sc_cache, stats, force_recompute)
            except Exception:  # noqa: BLE001 - 单个谱面失败不影响整体
                stats["errors"] += 1
        stats["duration_sec"] = round(time.time() - t0, 2)
        return stats

    def _process_folder(self, folder: pathlib.Path, sc_cache: dict[str, str],
                        stats: dict, force_recompute: bool) -> None:
        info = read_level_info(folder)
        if info is None:
            stats["errors"] += 1
            return

        song_hash = sc_cache.get(folder.name)
        if song_hash:
            stats["from_songcore_cache"] += 1
            source = "songcore_cache"
        else:
            # 尝试复用 DB 缓存：文件夹没有变化就不重算
            existing = self._db_map_by_path(str(folder))
            folder_mtime = self._folder_mtime(folder)
            if (not force_recompute and existing and existing.get("last_scanned")
                    and folder_mtime <= self._parse_ts(existing["last_scanned"])):
                stats["reused_db"] += 1
                return
            song_hash = compute_level_hash(folder, info)
            if not song_hash:
                stats["errors"] += 1
                return
            stats["computed"] += 1
            source = "computed"

        self.repo.upsert_map({
            "map_hash": song_hash,
            "folder_name": folder.name,
            "path": str(folder),
            "song_name": info["song_name"],
            "song_author": info["song_author"],
            "mapper": info["mapper"],
            "bpm": info["bpm"] or 0,
            "song_length": info["song_length"] or 0,
            "version": info["version"],
            "difficulties": json.dumps(info["difficulties"], ensure_ascii=False),
            "info_json": json.dumps({
                "environment": info["environment"],
                "song_filename": info["song_filename"],
                "cover_filename": info["cover_filename"],
                "song_sub_name": info["song_sub_name"],
            }, ensure_ascii=False),
            "hash_source": source,
        })

    @staticmethod
    def _folder_mtime(folder: pathlib.Path) -> float:
        latest = 0.0
        try:
            for p in folder.iterdir():
                if p.is_file():
                    latest = max(latest, p.stat().st_mtime)
        except OSError:
            pass
        return latest

    @staticmethod
    def _parse_ts(s: str) -> float:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%SZ").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            return 0.0

    def _db_map_by_path(self, path: str) -> Optional[dict]:
        # 按文件夹路径精确查询（SongCore 缓存未命中时的 DB 复用回退）。
        # 旧实现加载全表后无条件 return None（P1-3.3），全量重扫时 O(N²)。
        return self.repo.get_map_by_path(path)

    # ---------- resolve ----------
    def resolve(self, map_hash: str) -> Optional[dict]:
        """按 replay 的 map_hash 查本地谱面。"""
        if not map_hash:
            return None
        key = map_hash.strip().upper()
        row = self.repo.get_map(key)
        if row:
            return row
        if key in self._negative_cache:
            return None
        # DB 未命中：触发一次针对性扫描（新下载的谱面）。
        # 防抖：批量分析大量缺失 hash（含歌名假 hash 如 "CYCLEHIT"）时，
        # 只对第一个缺失 hash 全量扫描一次，其余直接判负——避免 40 次全量 scan。
        if time.time() - self._last_scan < 30:
            self._negative_cache.add(key)
            return None
        self.scan()
        self._last_scan = time.time()
        row = self.repo.get_map(key)
        if row is None:
            self._negative_cache.add(key)
        return row

    def ensure_map_path(self, map_hash: str) -> Optional[dict]:
        """封面懒修复：DB 行路径缺失/失效时触发一次针对性扫描。

        修复打包版首次 ingest 后封面全默认、重启才恢复的问题——
        ingest 建行时可能未含有效 path，封面请求时补齐。
        带负缓存与 30s 防抖：假 hash（如歌名 "GHOST"）不会反复全量扫描。
        """
        if not map_hash:
            return None
        key = map_hash.strip().upper()
        row = self.repo.get_map(key)
        if row and row.get("path") and pathlib.Path(row["path"]).exists():
            return row
        if key in self._negative_cache:
            return None
        if time.time() - self._last_scan < 30:
            return None   # 防抖：30s 内已全量扫描过，不重复
        self.scan()
        self._last_scan = time.time()
        row = self.repo.get_map(key)
        if row is None:
            self._negative_cache.add(key)
        return row

    def cover_path(self, map_hash: str) -> Optional[pathlib.Path]:
        """从本地谱面文件夹读取封面照片。

        优先 info.json 里的 cover_filename（info.dat 的 _coverImageFilename）；
        info_json 缺失时兜底：重新读 Info.dat 或直接找常见封面文件名。
        """
        row = self.repo.get_map(map_hash.strip().upper())
        if not row:
            return None
        folder = pathlib.Path(row["path"]) if row.get("path") else None
        if not folder or not folder.exists():
            return None

        cover = ""
        try:
            extra = json.loads(row.get("info_json") or "{}")
            cover = extra.get("cover_filename") or ""
        except json.JSONDecodeError:
            pass
        if not cover:
            # 兜底 1：重新读 Info.dat 的 _coverImageFilename
            info = read_level_info(folder)
            if info:
                cover = info.get("cover_filename") or ""
        if cover:
            p = _find_ci(folder, cover)
            if p and p.exists():
                return p
        # 兜底 2：常见封面文件名
        for name in ("cover.jpg", "cover.png", "cover.jpeg"):
            p = _find_ci(folder, name)
            if p and p.exists():
                return p
        # 兜底 3：任意图片文件（优先体积大的）
        imgs = sorted(
            (f for f in folder.iterdir()
             if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")),
            key=lambda f: f.stat().st_size, reverse=True)
        return imgs[0] if imgs else None
