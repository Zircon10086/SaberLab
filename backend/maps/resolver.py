"""Map Resolver: match a replay's map_hash to local CustomLevels.

Hash algorithm (authoritative source: SongCore Utilities/Hashing.cs, matches in-game):
    SHA1( info.dat bytes + each beatmap file's bytes (concatenated in the order
    they appear in _difficultyBeatmapSets in info.dat) ) -> uppercase HEX

Preferentially reads the game's own cache UserData/SongCore/SongHashData.dat
(1000+ maps resolve instantly); when missing/stale, computes hashes with the
algorithm above and writes them into the SQLite cache.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from ..db.repository import Repository


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _find_ci(folder: pathlib.Path, name: str) -> Optional[pathlib.Path]:
    """Case-insensitive file lookup (both info.dat / Info.dat may occur on Windows)."""
    target = name.lower()
    try:
        for p in folder.iterdir():
            if p.is_file() and p.name.lower() == target:
                return p
    except OSError:
        return None
    return None


def read_level_info(folder: pathlib.Path) -> Optional[dict]:
    """Read info.dat, compatible with V2 (underscore-prefixed) and V3 fields."""
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
    
    # Estimate song length from the last note in the beatmap data
    song_length = g("_songLength", "songLength", default=0)
    if not song_length and difficulties:
        bpm = g("_beatsPerMinute", "beatsPerMinute", default=0)
        if bpm > 0:
            # Prefer reading the ExpertPlus or Expert difficulty
            for diff_info in reversed(difficulties):
                fname = diff_info.get("filename")
                if fname:
                    diff_path = _find_ci(folder, fname)
                    if diff_path:
                        try:
                            diff_data = json.loads(diff_path.read_bytes().decode("utf-8-sig"))
                            # v2 field _notes / v3 fields colorNotes (same
                            # extraction as compute_level_nps below — v3-only
                            # maps used to yield song_length=0 here, silently
                            # disabling the <98% completion check in the engine)
                            notes = (diff_data.get("_notes") or diff_data.get("notes")
                                     or diff_data.get("colorNotes") or [])
                            if notes:
                                # Find the beat time of the last note (v2: _time/time; v3: b)
                                last_beat = 0.0
                                for n in notes:
                                    t = n.get("_time", n.get("time", n.get("b", 0)))
                                    if t:
                                        last_beat = max(last_beat, float(t))
                                # Estimate song length (seconds) = (last_beat / bpm) * 60
                                if last_beat > 0:
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
    """Compute NPS per difficulty (notes/sec, note density) for a level.

    Reads the notes list from each difficulty's .dat file:
      duration = last note's beat time converted to seconds (beat * 60 / BPM)
      NPS = total notes / duration
    Returns {"Standard|Expert": 4.2, "Standard|Hard": 3.1, ...}.
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
        # v2 field _notes / v3 fields colorNotes + bombNotes
        notes = (data.get("_notes") or data.get("notes")
                 or data.get("colorNotes") or [])
        if not notes:
            continue
        # Beat time of the last note (v2: _time/time; v3: b)
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
    """Compute the level hash using the SongCore algorithm."""
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
        self._negative_cache: set[str] = set()   # hashes still missing after a scan, to avoid repeated rescans
        self._last_scan = 0.0                    # scan debounce timestamp (batch missing-hash scenario)
        # Full-scan mutex (v1.4.1 fix): concurrent triggers from ensure_map_path/resolve
        # used to run multiple full scans at once (86 orphan cover requests on the history
        # page, 19.5s per pass x concurrency), saturating the FastAPI thread pool plus the
        # SQLite write lock -> all APIs froze for minutes.
        self._scan_lock = threading.Lock()
        self._scanning = False                   # a scan is in progress (concurrent requests fail fast)

    def update_paths(self, custom_levels_dir: str, songcore_cache_path: str = "") -> None:
        """Hot-update paths (call after saving in the settings page, no restart needed)."""
        self.levels_dir = pathlib.Path(custom_levels_dir or "")
        self.songcore_cache_path = (
            pathlib.Path(songcore_cache_path) if songcore_cache_path else None)
        self._negative_cache.clear()   # paths changed, old negative cache is invalid
        self._last_scan = 0.0          # reset debounce timestamp too (new paths should allow an immediate scan)

    # ---------- SongCore cache ----------
    def load_songcore_cache(self) -> dict[str, str]:
        """Return {folder_name: hash}. Cache keys look like '.\\Beat Saber_Data\\CustomLevels\\xxx'."""
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
        """Scan CustomLevels and build the hash -> level cache."""
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
            except Exception:  # noqa: BLE001 - one failing level must not abort the whole pass
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
            # Try to reuse the DB cache: skip recomputation when the folder is unchanged
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
        # Exact lookup by folder path (DB-reuse fallback when the SongCore cache misses).
        # The old implementation loaded the whole table then returned None unconditionally
        # (P1-3.3), making full rescans O(N²).
        return self.repo.get_map_by_path(path)

    # ---------- resolve ----------
    def resolve(self, map_hash: str) -> Optional[dict]:
        """Look up a local level by the replay's map_hash."""
        if not map_hash:
            return None
        key = map_hash.strip().upper()
        row = self.repo.get_map(key)
        if row:
            return row
        if key in self._negative_cache:
            return None
        # DB miss: trigger one targeted scan (for newly downloaded levels).
        # Debounce + mutex (v1.4.1): a scan in progress or one run within the last 30s
        # -> fail fast, never run concurrent/duplicate full scans (86 orphan cover
        # requests on the history page once triggered multiple concurrent full scans,
        # saturating the thread pool and freezing all APIs for minutes).
        # Debounced requests are NOT added to the negative cache: the map was
        # never actually searched, so caching it would poison resolution forever
        # (a level downloaded moments later would never be found until restart).
        with self._scan_lock:
            if self._scanning or time.time() - self._last_scan < 30:
                return None
            self._scanning = True
        try:
            self.scan()
        finally:
            with self._scan_lock:
                self._scanning = False
                self._last_scan = time.time()
        row = self.repo.get_map(key)
        if row is None:
            self._negative_cache.add(key)
        return row

    def ensure_map_path(self, map_hash: str) -> Optional[dict]:
        """Cover lazy-fix: trigger one targeted scan when a DB row's path is missing/stale.

        v1.4.1 fix: when there is no DB row (hash not in the maps table) this NO LONGER
        triggers a full scan - covers are a hot read-only path, and 86 orphan hashes on
        the history page once triggered multiple concurrent full scans (19.5s per pass x
        concurrency), saturating the FastAPI thread pool and freezing all APIs for minutes.
        Row creation is owned by the "rescan map library" (map_scan) task; here we only
        handle the lightweight case of "row exists but path missing/stale", and the mutex
        plus debounce guarantee at most one scan at any time.
        """
        if not map_hash:
            return None
        key = map_hash.strip().upper()
        row = self.repo.get_map(key)
        if row and row.get("path") and pathlib.Path(row["path"]).exists():
            return row
        if not row:
            return None   # no DB row: do not trigger a scan (row creation belongs to map_scan)
        if key in self._negative_cache:
            return None
        # Debounced requests are not negatively cached (see resolve())
        with self._scan_lock:
            if self._scanning or time.time() - self._last_scan < 30:
                return None   # scanning now / scanned within 30s: don't repeat, don't block
            self._scanning = True
        try:
            self.scan()
        finally:
            with self._scan_lock:
                self._scanning = False
                self._last_scan = time.time()
        row = self.repo.get_map(key)
        if row is None:
            self._negative_cache.add(key)
        return row

    def cover_path(self, map_hash: str) -> Optional[pathlib.Path]:
        """Read the cover image from the local level folder.

        Prefers cover_filename in info.json (info.dat's _coverImageFilename);
        when info_json is missing, falls back to re-reading Info.dat or searching
        for common cover filenames directly.
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
            # Fallback 1: re-read _coverImageFilename from Info.dat
            info = read_level_info(folder)
            if info:
                cover = info.get("cover_filename") or ""
        if cover:
            p = _find_ci(folder, cover)
            if p and p.exists():
                return p
        # Fallback 2: common cover filenames
        for name in ("cover.jpg", "cover.png", "cover.jpeg"):
            p = _find_ci(folder, name)
            if p and p.exists():
                return p
        # Fallback 3: any image file (largest first)
        def _size(f: pathlib.Path) -> int:
            # a file may vanish between iterdir() and stat(); an unguarded
            # stat() here once had a path to a 500 on the cover endpoint
            try:
                return f.stat().st_size
            except OSError:
                return 0
        imgs = sorted(
            (f for f in folder.iterdir()
             if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")),
            key=_size, reverse=True)
        return imgs[0] if imgs else None
