"""Fill in map files that are missing locally ("补全丢失谱面").

A replay carries exactly one level identity: `replays.map_hash` (BSOR's `mapHash`). Its value
tells the two worlds apart, and each has its own source:

* 40 hex characters — a custom level, hashed by SongCore. Source: BeatSaver, which exposes the
  archive URL for a hash through its API (`versions[].downloadURL`, a `r2cdn.beatsaver.com`
  object). The URL is read from the API on purpose: guessing the host from the hash looked
  right until measured (HEAD on that host answers 404 for every object, as do range probes
  against a wrong name).
* anything else — an official (base game / DLC) level id such as `CRABRAVE` or `100BILLS`.
  Those levels ship as packed game assets with no info.dat, so they can never be hashed or
  scanned locally. Source: BeatLeader's official-level CDN, where the archive is named after
  the level id with every non-alphanumeric character dropped (`BE THERE FOR YOU` ->
  `BeThereForYou.zip`). Measured 2026-10: 18 of the 29 official ids in a real library exist
  there; the rest are paid DLC whose audio cannot be redistributed, and they are reported as
  "not downloadable" rather than retried forever.

Downloads are deliberately gentle on both hosts: at most one request per host at a time, a
minimum gap between them, and a small number of attempts with `Retry-After` honoured — these
are community-funded mirrors, not a CDN we own.

Nothing is trusted from the network: the archive must start with the zip magic, extract inside
the size budget without escaping the target directory, contain a readable level, and — for a
custom level — hash back to the exact identity that was requested.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import pathlib
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from ..config import PROJECT_ROOT
from ..db.repository import Repository
from .resolver import (MapResolver, is_custom_hash, normalize_level_id,
                       read_level_info)

#: Where downloaded levels live: SaberLab's own data directory, never the user's game folder
#: (product decision 2026-10 — the game install stays exactly as the player left it).
LEVELS_DIR = PROJECT_ROOT / "data" / "levels"

BEATSAVER_API = "api.beatsaver.com"
BEATLEADER_CDN = "cdn.songs.beatleader.xyz"
_UA = "SaberLab/2.3 (+local analysis app)"

#: Hard limits. Real archives measured 2–8.5 MB; the caps exist so a hostile or broken mirror
#: cannot fill the disk or the memory.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 4000
#: Filename sanitizer for the two things we name ourselves: the temp folder and the level
#: folder. Level ids/hashes are already [0-9A-Za-z], so this only guards against a surprise.
_SAFE_NAME = re.compile(r"[^0-9A-Za-z._-]")

#: Per-host politeness: one request at a time, and a minimum gap between two of them.
_HOST_MIN_GAP_SEC = 0.4
_RETRY_DELAYS = (1.0, 3.0)          # two extra attempts after the first
_REASON_RETRY_AFTER = "Retry-After"

_host_locks: dict[str, threading.Lock] = {}
_host_locks_guard = threading.Lock()
_host_last_request: dict[str, float] = {}


def _host_lock(host: str) -> threading.Lock:
    with _host_locks_guard:
        return _host_locks.setdefault(host, threading.Lock())


def _wait_for_slot(host: str) -> None:
    """Sleep so that two requests to the same host never overlap and stay `_HOST_MIN_GAP_SEC` apart."""
    last = _host_last_request.get(host, 0.0)
    gap = _HOST_MIN_GAP_SEC - (time.time() - last)
    if gap > 0:
        time.sleep(gap)


def _mark_request(host: str) -> None:
    _host_last_request[host] = time.time()


class DownloadError(Exception):
    """Raised when an archive could not be fetched. `reason` is a stable, user-facing code."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def sanitize_name(value: str, fallback: str = "level") -> str:
    """Make a string safe to use as a single path component."""
    cleaned = _SAFE_NAME.sub("", (value or "").strip())
    cleaned = cleaned.strip(". ")
    return cleaned[:96] or fallback


# ---------------------------------------------------------------- official level naming


def official_zip_names(level_id: str, song_name: str = "") -> list[str]:
    """Candidate archive names for an official level id, most likely first.

    The CDN holds these archives under a title derived from the SONG name with punctuation
    dropped (`Be There For You` -> `BeThereForYou`, `$100 Bills` -> `100Bills`), so that spelling
    is tried first. The level id is only a fallback: a single-word id spells the same
    (`CRABRAVE` -> `CrabRave`), while a multi-word title glued into one id cannot be split back
    (`BETHEREFORYOU` -> `Bethereforyou`, which is NOT the archive name — measured 2026-10), and
    that is exactly why the song name has to lead.
    """
    out: list[str] = []
    name = re.sub(r"[^0-9A-Za-z]", "", song_name or "")
    if name:
        out.append(name)
    key = (level_id or "").strip()
    if key:
        out.append(key[:1].upper() + key[1:].lower())
        out.append(key)
    seen: set[str] = set()
    return [x for x in out if x and not (x in seen or seen.add(x))]


# ---------------------------------------------------------------- networking


def _open_https(url: str, proxy: str = "", timeout: float = 60.0):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise DownloadError("bad_url", url)
    conn = http.client.HTTPSConnection(parsed.hostname, timeout=timeout)
    if proxy:
        p = urllib.parse.urlsplit(proxy if "://" in proxy else f"http://{proxy}")
        conn.set_tunnel(p.hostname, p.port or 443)
    return conn, parsed.path + (f"?{parsed.query}" if parsed.query else "")


def http_get(url: str, *, proxy: str = "", timeout: float = 60.0,
             max_bytes: int = MAX_ARCHIVE_BYTES) -> bytes:
    """GET an URL with per-host serialisation and bounded retries.

    Raises DownloadError with a stable reason so the UI can say what happened.
    Returns the body (at most `max_bytes`; more is refused, not truncated).
    """
    host = urllib.parse.urlsplit(url).hostname or ""
    attempts = len(_RETRY_DELAYS) + 1
    last: Optional[DownloadError] = None
    for attempt in range(attempts):
        with _host_lock(host):
            _wait_for_slot(host)
            conn = None
            try:
                conn, target = _open_https(url, proxy=proxy, timeout=timeout)
                conn.request("GET", target, headers={"User-Agent": _UA, "Accept": "*/*"})
                resp = conn.getresponse()
                _mark_request(host)
                status = resp.status
                if status == 404:
                    raise DownloadError("not_found", f"{status}")
                if status in (429, 500, 502, 503, 504):
                    # Community mirrors throttle; wait as long as they ask, then try again.
                    retry_after = resp.getheader(_REASON_RETRY_AFTER) or ""
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    if retry_after.isdigit():
                        delay = min(float(retry_after), 30.0)
                    resp.read(0)
                    last = DownloadError("busy", f"{status}")
                    time.sleep(delay)
                    continue
                if status != 200:
                    raise DownloadError("http_error", f"{status}")
                body = resp.read(max_bytes + 1)
            except DownloadError:
                raise
            except (OSError, http.client.HTTPException, ValueError) as e:
                last = DownloadError("network", type(e).__name__)
                if attempt + 1 < attempts:
                    time.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
                    continue
                raise last from e
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except OSError:
                        pass
        if len(body) > max_bytes:
            raise DownloadError("too_large", f"{len(body)} bytes")
        return body
    raise last or DownloadError("network", "unreachable")


def resolve_download_url(level_key: str, song_name: str = "", *, proxy: str = "",
                         timeout: float = 60.0) -> tuple[str, str]:
    """Return (url, source) for a level identity, or raise DownloadError('not_found').

    `source` is `beatsaver` (custom level) or `beatleader` (official level).
    """
    key = normalize_level_id(level_key)
    if not key:
        raise DownloadError("bad_id")
    if is_custom_hash(key):
        lower = key.lower()
        body = http_get(f"https://{BEATSAVER_API}/maps/hash/{lower}", proxy=proxy,
                        timeout=timeout, max_bytes=4 * 1024 * 1024)
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except json.JSONDecodeError as e:
            raise DownloadError("bad_response", "beatsaver json") from e
        versions = data.get("versions") or []
        for version in versions:
            url = (version or {}).get("downloadURL") or ""
            if url:
                return url, "beatsaver"
        raise DownloadError("not_found", "beatsaver has no archive for this hash")

    # Official level: try the candidate archive names, cheapest first.
    tried: list[str] = []
    for name in official_zip_names(key, song_name):
        url = f"https://{BEATLEADER_CDN}/{urllib.parse.quote(name)}.zip"
        try:
            http_get(url, proxy=proxy, timeout=timeout, max_bytes=MAX_ARCHIVE_BYTES)
        except DownloadError as e:
            if e.reason == "not_found":
                tried.append(name)
                continue
            raise
        return url, "beatleader"
    raise DownloadError("not_found", "official archive absent: " + ", ".join(tried))


# ---------------------------------------------------------------- archive handling


def _safe_zip_member(name: str) -> bool:
    """Reject everything that could write outside the target folder.

    A zip from the internet is untrusted input: no absolute paths, no drive letters, no `..`
    segments, no backslash tricks (Windows treats `\\` as a separator, so a member like
    `..\\evil` is the same escape as `../evil`).
    """
    if not name or name.endswith("/"):
        return False
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized.split("/")[0]:
        return False
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return False
    return True


def extract_archive(blob: bytes, dest: pathlib.Path) -> int:
    """Extract a zip into `dest` (created empty by the caller). Returns the extracted bytes.

    Guards: magic number, per-archive entry count, per-entry and total uncompressed size, and
    member paths that stay inside `dest`.
    """
    if not blob.startswith(b"PK\x03\x04") and not blob.startswith(b"PK\x05\x06"):
        raise DownloadError("not_zip", f"magic={blob[:4]!r}")
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as e:
        raise DownloadError("not_zip", str(e)[:80]) from e
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise DownloadError("too_many_entries", str(len(infos)))
    total = 0
    written: list[tuple[zipfile.ZipInfo, pathlib.Path]] = []
    for info in infos:
        if info.is_dir():
            continue
        if not _safe_zip_member(info.filename):
            raise DownloadError("unsafe_member", info.filename[:80])
        total += info.file_size
        if info.file_size > MAX_EXTRACTED_BYTES or total > MAX_EXTRACTED_BYTES:
            raise DownloadError("too_large_extracted", str(total))
        target = dest / info.filename.replace("\\", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out, 1024 * 256)
        written.append((info, target))
    if not written:
        raise DownloadError("empty_archive")
    return total


def _install_dir(src: pathlib.Path, dest: pathlib.Path, replace: bool) -> None:
    """Move an extracted folder into place.

    A target that is already there is replaced through a renamed staging folder, so a reader
    (the covering endpoint, the viewer fetching the package) never sees a half-populated level
    folder: the old one is only dropped once the new one is complete.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        src.replace(dest)
        return
    if not replace:
        raise DownloadError("already_present", str(dest))
    stale = dest.with_name(dest.name + ".old-" + str(int(time.time())))
    dest.replace(stale)
    try:
        src.replace(dest)
    except OSError:
        stale.replace(dest)          # put the old one back; the new one is discarded by the caller
        raise
    shutil.rmtree(stale, ignore_errors=True)


# ---------------------------------------------------------------- the task itself


def verify_level(folder: pathlib.Path, level_key: str, song_name: str = "",
                 mapper: str = "") -> dict:
    """Read the extracted level and check it is the level that was asked for.

    A custom level is normalised by its SongCore hash — that is the only hard proof the archive
    belongs to this replay. The hash may still differ legitimately: BeatSaver serves the CURRENT
    version of a map, and a map that was re-uploaded since the replay was recorded hashes
    differently (measured 2026-10: `052AAA3A…` came back as `82D8B81F…`). A mismatch is therefore
    accepted when the name and mapper agree, and recorded as `version_changed` so the choice is
    visible instead of silent. Official archives carry no id we could recompute (the game
    resolves those levels through packed assets), so their check is structural plus the song name.
    """
    from .resolver import compute_level_hash      # local import: resolver imports this module's constants only

    info = read_level_info(folder)
    if info is None:
        raise DownloadError("no_level", "info.dat missing or unreadable")
    key = (level_key or "").strip().upper()
    result = {"song_name": info.get("song_name") or "", "difficulties": len(info.get("difficulties") or [])}
    if not info.get("difficulties"):
        raise DownloadError("no_difficulties", str(folder))

    def norm(value: str) -> str:
        return re.sub(r"[^0-9a-z]", "", (value or "").lower())

    def name_match() -> bool:
        """Whether the archive is the same song.

        The song name comes from the same source on both sides (a map's info.dat carries it, and
        the replay recorded it from that level), so the two agree exactly in practice — measured
        across the local library. A re-upload may still retitle the map
        ("3 BIG SHOTS" -> "3 BIG SHOTS FROM [KITCHEN GUN]"), which is why one name containing the
        other counts as the same song too.
        """
        expected, actual = norm(song_name), norm(info.get("song_name"))
        if not expected or not actual:
            return True                       # nothing to compare: the structure check stands
        return expected == actual or expected.startswith(actual) or actual.startswith(expected)

    if is_custom_hash(key):
        actual = compute_level_hash(folder, info)
        result["downloaded_hash"] = actual or ""
        if actual == key:
            result["verified"] = "hash"
            return result
        if name_match():
            # Same song from the same source, another revision - usable, and worth reporting.
            result["verified"] = "version_changed"
            return result
        raise DownloadError("hash_mismatch", f"{actual} != {key}")
    expected = norm(song_name)
    actual = norm(info.get("song_name"))
    result["verified"] = "name" if (expected and actual and (expected == actual
                                                            or expected.startswith(actual)
                                                            or actual.startswith(expected))) \
        else "unverified"
    return result


def _cleanup(path: pathlib.Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except OSError:
        pass


def fill_one(level_key: str, song_name: str, *, repo: Repository, resolver: Optional[MapResolver],
             mapper: str = '', 
             levels_dir: pathlib.Path = LEVELS_DIR, proxy: str = "", timeout: float = 60.0,
             replace: bool = False) -> dict:
    """Download and install one missing level. Never raises: returns a result dict."""
    key = normalize_level_id(level_key)
    out: dict = {"map_hash": key, "song_name": song_name, "status": "failed", "reason": "",
                 "source": "", "detail": ""}
    # The download root may not exist yet (nothing downloaded so far, or a fresh install):
    # this is also the entry point of the single-level path, which has no batch to create it.
    try:
        levels_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        out.update(reason="io_error", detail=f"{type(e).__name__}: {e}"[:200])
        return out
    target = levels_dir / sanitize_name(key)
    existing = resolver.downloaded_folder(key) if resolver else None
    if existing is not None and not replace:
        out.update(status="skipped", reason="already_local", detail=str(existing))
        return out
    staging = pathlib.Path(tempfile.mkdtemp(prefix="sl-fill-", dir=str(levels_dir)))
    try:
        url, source = resolve_download_url(key, song_name, proxy=proxy, timeout=timeout)
        out["source"] = source
        blob = http_get(url, proxy=proxy, timeout=timeout)
        extract_archive(blob, staging)
        verified = verify_level(staging, key, song_name, mapper)
        out["verified"] = verified.get("verified", "")
        out["song_name"] = verified.get("song_name") or song_name
        out["difficulties"] = verified.get("difficulties", 0)
        out["downloaded_hash"] = verified.get("downloaded_hash", "")
        _install_dir(staging, target, replace=replace)
        if resolver is not None:
            resolver.store_map_folder(target, key, "downloaded")
        out.update(status="ok", reason="", detail=str(target))
    except DownloadError as e:
        out.update(reason=e.reason, detail=e.detail or str(e))
    except OSError as e:
        out.update(reason="io_error", detail=f"{type(e).__name__}: {e}"[:200])
    except Exception as e:  # noqa: BLE001 - one level must not kill the whole batch
        out.update(reason="unexpected", detail=f"{type(e).__name__}: {e}"[:200])
    finally:
        _cleanup(staging)
    return out


def missing_levels(repo: Repository) -> dict:
    """Every level a replay refers to but that is not in the library, grouped by kind.

    `official` ids are the game's own levels (they are never in CustomLevels); `custom` are
    hashes whose folder the player deleted or never had. Both are reported with the play count
    and one example song name so the UI can say what it is about to fetch.
    """
    rows = repo.list_replays_for_missing_levels()
    known: set[str] = set()
    for m in repo.list_maps():
        key = (m.get("map_hash") or "").strip().upper()
        if key:
            known.add(key)
    grouped: dict[str, dict] = {}
    for r in rows:
        key = (r.get("map_hash") or "").strip().upper()
        if not key or key in known:
            continue
        entry = grouped.setdefault(key, {
            "map_hash": key,
            "song_name": r.get("song_name") or "",
            "mapper": r.get("mapper") or "",
            "plays": 0,
            "official": not is_custom_hash(key),
        })
        # The query already grouped by identity: `plays` is how many replays point at this
        # level, and it is what orders the work (busiest level first).
        entry["plays"] += int(r.get("plays") or 1)
        if not entry["song_name"] and r.get("song_name"):
            entry["song_name"] = r["song_name"]
        if not entry["mapper"] and r.get("mapper"):
            entry["mapper"] = r["mapper"]
    items = sorted(grouped.values(), key=lambda x: (-x["plays"], x["map_hash"]))
    official = [i for i in items if i["official"]]
    custom = [i for i in items if not i["official"]]
    return {
        "items": items,
        "total": len(items),
        "official": official,
        "custom": custom,
        "plays": sum(i["plays"] for i in items),
    }


def fill_missing(repo: Repository, resolver: Optional[MapResolver], *,
                 levels_dir: pathlib.Path = LEVELS_DIR, proxy: str = "",
                 timeout: float = 60.0, concurrency: int = 3,
                 progress_cb: Optional[Callable[[int, int, str], None]] = None,
                 replace: bool = False) -> dict:
    """Fetch every missing level, `concurrency` at a time (each host is still serialised).

    Returns a summary: counts by outcome plus the per-level results, so the report can name
    what could not be fetched and why instead of hiding it behind a single number.
    """
    levels_dir.mkdir(parents=True, exist_ok=True)
    report = missing_levels(repo)
    items: list[dict] = list(report["items"])
    total = len(items)
    results: list[dict] = []
    done = 0
    lock = threading.Lock()

    def work(item: dict) -> dict:
        return fill_one(item["map_hash"], item["song_name"], mapper=item.get("mapper", ""),
                        repo=repo, resolver=resolver, levels_dir=levels_dir, proxy=proxy,
                        timeout=timeout, replace=replace)

    workers = max(1, min(int(concurrency or 1), 8, total or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(work, items):
            with lock:
                done += 1
                results.append(res)
                if progress_cb:
                    progress_cb(done, total, (res.get("song_name") or res.get("map_hash") or "")[:40])

    ok = [r for r in results if r["status"] == "ok"]
    skipped = [r for r in results if r["status"] == "skipped"]
    failed = [r for r in results if r["status"] == "failed"]
    return {
        "total": len(results),
        "ok": len(ok),
        "skipped": len(skipped),
        "failed": len(failed),
        "unavailable": [r for r in failed if r["reason"] == "not_found"],
        "results": results,
        "missing": report,
    }


def archive_sha256(blob: bytes) -> str:
    """Digest helper used by the tests and by manual verification of a downloaded archive."""
    return hashlib.sha256(blob).hexdigest()
