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


def _read_level_info_v4(folder: pathlib.Path, raw: dict) -> Optional[dict]:
    """Read an info.dat using the v4 schema (nested song/audio objects).

    v4.0.0+ restructured the whole file: metadata moved into "song" / "audio", the difficulty
    list became "difficultyBeatmaps", and a difficulty's chart lives in "beatmapDataFilename".
    Without this the reader returned an empty level for every v4 map - the library then showed
    them as blank rows with no difficulties (2026-10, found while building map management).
    Returns None when the file does not look like v4, so the v2/v3 path keeps handling the rest.
    """
    song = raw.get("song")
    if not isinstance(song, dict):
        return None
    audio = raw.get("audio") if isinstance(raw.get("audio"), dict) else {}

    difficulties = []
    for d in raw.get("difficultyBeatmaps") or []:
        if not isinstance(d, dict):
            continue
        authors = d.get("beatmapAuthors") if isinstance(d.get("beatmapAuthors"), dict) else {}
        mappers = [m for m in (authors.get("mappers") or []) if isinstance(m, str)]
        difficulties.append({
            "characteristic": d.get("characteristic") or "",
            "difficulty": d.get("difficulty") or "",
            "label": d.get("difficultyLabel") or "",
            "filename": d.get("beatmapDataFilename") or "",
            "njs": d.get("noteJumpMovementSpeed"),
            "lightshow": d.get("lightshowDataFilename") or "",
            "mappers": mappers,
        })

    # v4 keeps the length in beats; the seconds value the rest of the code expects is derived
    # from the last note of the hardest chart, exactly like the v2/v3 path does.
    bpm = float(audio.get("bpm") or 0) or 0.0
    return {
        "raw": raw,
        "version": raw.get("version") or "",
        "song_name": song.get("title") or "",
        "song_sub_name": song.get("subTitle") or "",
        "song_author": song.get("author") or "",
        "mapper": ", ".join(sorted({m for d in difficulties for m in d.get("mappers", [])})),
        "bpm": bpm,
        "song_length": _length_from_last_note(folder, difficulties, bpm),
        "song_filename": audio.get("songFilename") or raw.get("songPreviewFilename") or "",
        "cover_filename": raw.get("coverImageFilename") or "",
        "environment": (raw.get("environmentNames") or [""])[0],
        "difficulties": difficulties,
    }


def _length_from_last_note(folder: pathlib.Path, difficulties: list[dict],
                           bpm: float) -> float:
    """Song length in seconds from the last note beat of the hardest chart (0 when unknown).

    Shared by the v4 reader; the v2/v3 path computes the same thing inline while walking its
    own difficulty list.
    """
    if bpm <= 0:
        return 0.0
    for diff_info in reversed(difficulties):
        fname = diff_info.get("filename")
        if not fname:
            continue
        diff_path = _find_ci(folder, fname)
        if diff_path is None:
            continue
        try:
            data = json.loads(diff_path.read_bytes().decode("utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        notes = (data.get("_notes") or data.get("notes") or data.get("colorNotes") or [])
        last_beat = 0.0
        for n in notes:
            t = n.get("_time", n.get("time", n.get("b", 0)))
            if t:
                last_beat = max(last_beat, float(t))
        if last_beat > 0:
            return (last_beat / bpm) * 60
    return 0.0


def read_chart_stats(folder: pathlib.Path, info: dict, bpm: float) -> dict:
    """Per-difficulty chart facts for the map detail view: notes, span and file size.

    Counts only the objects a player cuts - color notes plus, when present, bombs, arcs and
    chains - because that is the number BeatSaver shows and the one a player recognises. Only
    ScoreSaber/BeatLeader-style "notes" (the classic `_notes` / `notes` / `colorNotes` array) are
    the minimum; the extra arrays are added when the chart carries them.

    Chart data in v4 can live in a side file: the array entry holds only an index into
    `<name>Data`. A chart whose data is stored that way is reported as unavailable (note count
    would be zero otherwise, which would read as "this difficulty has no notes").
    """
    out: dict[str, dict] = {}
    for d in info.get("difficulties") or []:
        characteristic = d.get("characteristic") or ""
        difficulty = d.get("difficulty") or ""
        key = f"{characteristic}|{difficulty}"
        fname = d.get("filename") or ""
        entry: dict = {"notes": None, "seconds": None, "size": None, "available": False}
        out[key] = entry
        if not fname:
            continue
        path = _find_ci(folder, fname)
        if path is None:
            continue
        try:
            raw = path.read_bytes()
            entry["size"] = len(raw)
            data = json.loads(raw.decode("utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        # inlined data: count the real objects, skip anything referenced by index
        if any(isinstance(data.get(a), list) and data[a] and isinstance(data[a][0], dict)
               for a in ("colorNotes", "notes", "_notes")):
            notes = (data.get("colorNotes") or data.get("notes") or data.get("_notes") or [])
            extra = sum(len(data.get(a) or []) for a in ("bombNotes", "arcs", "chains")
                        if isinstance(data.get(a), list))
            entry["notes"] = len(notes) + extra
            entry["available"] = True
        elif any(isinstance(data.get(a), list) and data[a] and isinstance(data[a][0], int)
                 for a in ("colorNotesData", "_notesData")):
            entry["notes"] = 0      # data lives in a side file: count not available here
            entry["available"] = False
        else:
            entry["notes"] = 0      # a chart that genuinely has no cuttable object
            entry["available"] = True
        if bpm > 0 and entry["available"]:
            first = last = 0.0
            for n in (data.get("colorNotes") or data.get("notes") or data.get("_notes") or []):
                t = n.get("_time", n.get("time", n.get("b", 0)))
                if not t:
                    continue
                t = float(t)
                first = t if first == 0 or t < first else first
                last = t if t > last else last
            if last > 0:
                entry["seconds"] = (last / bpm) * 60
    return out


def read_chart_timeseries(folder: pathlib.Path, filename: str, bpm: float,
                          bucket_seconds: float = 1.0) -> dict:
    """Density over time for ONE chart, bucketed per second (the map detail's chart card).

    Answers "how dense is this chart, and where" without a replay: notes and blocks are placed at
    their own beat time (`t = b * 60 / bpm`), then counted into fixed buckets. Four series come out
    of one pass:

      * ``density``   - every cuttable object (color notes, arcs, chains)
      * ``left`` / ``right`` - the same split by column (<2 = left hand, >=2 = right hand; arcs and
        chains carry a head column, so they land in the hand that starts them)
      * ``bombs``     - bombs, counted separately (they are never cut, so they must not inflate the
        note density; the replay timeline excludes them from its density for the same reason)

    Same units as everywhere else in the app: notes per second, y values are densities (not raw
    counts), so the four series are directly comparable. Buckets run from the first to the last
    object, and a bucket with no object is a real 0 (a gap in the chart), not missing data. Each
    series is then smoothed over +-2 buckets - a single 1s bucket can hold a chord burst, which
    reads as a spike that says nothing about the passage (the replay timeline smooths its per-note
    density the same way, see timeline.DENSITY_SMOOTH).
    """
    out = {"density_t": [], "density": [], "left": [], "right": [], "bombs": [],
           "counts": {"density": 0, "left": 0, "right": 0, "bombs": 0},
           "available": False, "first_beat": 0.0, "last_beat": 0.0}
    if bpm <= 0 or not filename:
        return out
    path = _find_ci(folder, filename)
    if path is None:
        return out
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return out

    def beat(n):
        # a beat of 0 is a REAL position (the chart's first note often sits there), so a missing
        # key must be told apart from a zero value
        for key in ("_time", "time", "b"):
            if key in n and n[key] is not None:
                return float(n[key])
        return 0.0

    def placed(objs) -> list:
        """Objects with a usable position, ignoring the index-only form of a v4 side file."""
        if not objs or not isinstance(objs[0], dict):
            return []
        return [n for n in objs if isinstance(n, dict)]

    colors = (data.get("colorNotes") or data.get("notes") or data.get("_notes") or [])
    if colors and not isinstance(colors[0], dict):
        # v4 chart whose note data lives in a side file: index-only arrays, so nothing to measure
        return out
    cuts = placed(colors) + placed(data.get("arcs")) + placed(data.get("chains"))
    cuts = [n for n in cuts if beat(n) > 0 or "b" in n or "_time" in n or "time" in n]
    if not cuts:
        return out

    total = [beat(n) for n in cuts]
    left = [beat(n) for n in cuts if int(n.get("x", n.get("_lineIndex", 0)) or 0) < 2]
    right = [beat(n) for n in cuts if int(n.get("x", n.get("_lineIndex", 0)) or 0) >= 2]
    bomb_beats = [beat(n) for n in (data.get("bombNotes") or data.get("_bombs") or [])
                  if isinstance(n, dict)]

    first, last = min(total), max(total)
    span_seconds = (last - first) / bpm * 60
    buckets = max(1, int(span_seconds / bucket_seconds) + 1)
    scale = 60.0 / bpm / bucket_seconds         # seconds per beat -> bucket index

    def counts(beats: list) -> list:
        c = [0] * buckets
        for b in beats:
            idx = int((b - first) * scale)
            if idx < 0:
                idx = 0
            elif idx >= buckets:
                idx = buckets - 1
            c[idx] += 1
        return c

    def rates(c: list) -> list:
        """Counts per bucket -> notes/second, smoothed over ±2 buckets."""
        raw = [x / bucket_seconds for x in c]
        if not raw:
            return []
        window = 2
        span = window * 2 + 1
        out_series = []
        for i in range(len(raw)):
            lo, hi = max(0, i - window), min(len(raw) - 1, i + window)
            segment = raw[lo:hi + 1]
            out_series.append(round(sum(segment) / min(span, len(segment)), 3))
        return out_series

    out["density_t"] = [round(first / bpm * 60 + i * bucket_seconds, 3) for i in range(buckets)]
    counts_total, counts_left = counts(total), counts(left)
    counts_right, counts_bombs = counts(right), counts(bomb_beats)
    out["density"] = rates(counts_total)
    out["left"] = rates(counts_left)
    out["right"] = rates(counts_right)
    out["bombs"] = rates(counts_bombs) if bomb_beats else [0.0] * buckets
    # raw per-bucket object counts (before smoothing): what the chart really contains, which is
    # what callers verify against - the smoothed series is a view, not the measurement
    out["bucket_counts"] = {"density": counts_total, "left": counts_left,
                            "right": counts_right, "bombs": counts_bombs}
    out["counts"] = {"density": sum(counts_total), "left": sum(counts_left),
                     "right": sum(counts_right), "bombs": sum(counts_bombs)}
    out["available"] = True
    out["first_beat"], out["last_beat"] = first, last
    return out


def read_level_info(folder: pathlib.Path) -> Optional[dict]:
    """Read info.dat, compatible with V2 (underscore-prefixed), V3 and V4 fields."""
    info_path = _find_ci(folder, "info.dat")
    if info_path is None:
        return None
    try:
        raw = json.loads(info_path.read_bytes().decode("utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return None

    v4 = _read_level_info_v4(folder, raw)
    if v4 is not None:
        return v4

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

        # Before touching the folders: give every row that entered the library without a
        # timestamp its real one. This runs first on purpose - the upsert below stamps
        # added_at with the scan time for rows that have none, which would freeze every
        # existing map at "when the library was migrated" and make the sort useless (measured:
        # 1032 rows inside 11 seconds). A one-time migration cost, then it stays a no-op.
        try:
            stats["added_at_backfilled"] = self._backfill_added_at()
        except Exception as e:  # noqa: BLE001 - a missing sort key must not fail the scan
            print(f"[maps] added_at backfill failed: {e}", flush=True)

        folders = [p for p in self.levels_dir.iterdir() if p.is_dir()]
        sc_cache = self.load_songcore_cache()
        total = len(folders)

        for i, folder in enumerate(folders):
            if progress_cb and (i % 50 == 0 or i == total - 1):
                progress_cb(i + 1, total, folder.name)
            stats["scanned"] += 1
            try:
                self._process_folder(folder, sc_cache, stats, force_recompute)
            except Exception as e:  # noqa: BLE001 - one failing level must not abort the whole pass
                # Counted in stats, but the reason would otherwise be lost: a
                # "3 maps failed to scan" report needs the cause to be actionable.
                print(f"[maps] scan failed for {folder.name}: {e}", flush=True)
                stats["errors"] += 1
        stats["duration_sec"] = round(time.time() - t0, 2)
        return stats

    def _backfill_added_at(self) -> int:
        """Give every map row an added_at value, once. Returns how many rows were filled.

        Skips repositories that do not expose the backfill API (the scan tests use small stand-ins):
        that is a "nothing to do", not an error, and printing a failure for it only made the test
        output look broken.
        """
        pending_of = getattr(self.repo, "maps_without_added_at", None)
        setter = getattr(self.repo, "set_maps_added_at", None)
        if not callable(pending_of) or not callable(setter):
            return 0
        pending = pending_of()
        if not pending:
            return 0
        now = _now()
        values: dict[str, str] = {}
        for row in pending:
            ts = None
            path = row.get("path")
            if path:
                mtime = self._folder_mtime(pathlib.Path(path))
                if mtime:
                    ts = datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
            values[row["map_hash"]] = ts or now
        filled = setter(values)
        print(f"[maps] added_at backfilled for {filled} maps", flush=True)
        return filled

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
            # Try to reuse the DB cache: skip recomputation when the folder is unchanged.
            # A row that stored no song name is never trusted, whatever its timestamps say: it
            # is either a level the reader could not parse back then (an older reader returned
            # an empty level for info v4 maps - 2026-10) or a half-written row, and the folder
            # would otherwise stay blank forever because nothing about it changed.
            existing = self._db_map_by_path(str(folder))
            folder_mtime = self._folder_mtime(folder)
            stored_ok = bool(existing and (existing.get("song_name") or "").strip())
            if (not force_recompute and stored_ok and existing.get("last_scanned")
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
    def resolve(self, map_hash: str, trigger_scan: bool = True) -> Optional[dict]:
        """Look up a local level by the replay's map_hash.

        trigger_scan=False is for BATCH callers (2026-09): a full rescan walks
        every folder under CustomLevels and costs ~14 s on a 1000-map library, so
        letting 400 replays each request one (per 30 s debounce, and once per
        worker process) turned a ~30 s batch into ~4 minutes. Batch paths scan
        ONCE up front and then resolve without rescanning; a replay whose map is
        genuinely absent simply gets no map instead of stalling the batch.
        """
        if not map_hash:
            return None
        key = map_hash.strip().upper()
        row = self.repo.get_map(key)
        if row:
            return row
        if key in self._negative_cache or not trigger_scan:
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
