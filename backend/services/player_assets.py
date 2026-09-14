"""Player asset cache: avatar + country flag (sidebar player card, 2026-09).

The sidebar card needs two remote images. Both follow the same policy, which
is why this module is generic rather than a pile of one-off helpers:

1. **Local-first**: images are downloaded once during a cloud sync
   (`_cloud_page_refresh`) and served from local disk afterwards, so the card
   works offline and the WebView never talks to a CDN directly.
2. **A failed download never breaks anything**: the download helpers report
   failure by returning None and the previously cached file (if any) is kept.
   The profile snapshot stays intact, so the card still renders name/ranks.
3. **Reads never touch the network**: `read_asset` is pure local I/O, so the
   endpoints can be called on every page load regardless of connectivity.

Sources (verified 2026-09-10):
- avatar  ScoreSaber: `profile.playerPicture` -> https://cdn.scoresaber.com/avatars/<id>.jpg
          BeatLeader:  `player.avatar`        -> https://avatars.steamstatic.com/<hash>_full.jpg
- flag    https://flagcdn.com/w40/<cc>.png, derived from the profile's country
          code (works for both providers; BeatLeader exposes no flag URL)

Why a flag image instead of the flag emoji: WebView2 runs on Chromium/Windows
and does NOT render regional-indicator flag glyphs — the emoji shows up as the
bare letters "CN" (verified on the real window 2026-09-10). The image keeps the
card looking like the ScoreSaber profile it mirrors.

Cache file names are derived (`<kind>_<key>`), deliberately NOT stored in the
profile snapshot: a local cache path is infrastructure state, not part of the
API payload, and the derivation keeps the snapshot re-fetchable. No file
extension either — bytes are opaque and the MIME type is sniffed when serving,
so a provider switching format (jpeg -> png) cannot leave a stale second file.
"""
from __future__ import annotations

import http.client
import pathlib
import urllib.parse
from typing import NamedTuple

UA = "SaberLab/2.2.0"
_TIMEOUT = 30.0
_AVATAR_MAX_BYTES = 4 * 1024 * 1024     # avatars are ~20KB; refuse anything absurd
_FLAG_MAX_BYTES = 512 * 1024            # flags are ~0.2-1KB
FLAG_URL_TEMPLATE = "https://flagcdn.com/w40/{cc}.png"

# Magic-byte sniffing: the Content-Type header is not trusted (an error page
# served with 200 would otherwise be cached as an "image").
_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_mime(head: bytes) -> str | None:
    """Return the image MIME type for `head`, or None when it is not an image."""
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            return mime
    # WebP: RIFF....WEBP
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _safe(key: str) -> str:
    """Filesystem-safe fragment (path traversal / separators cannot escape)."""
    return "".join(ch for ch in str(key) if ch.isalnum() or ch in "-_")


def asset_path(cfg, kind: str, key: str) -> pathlib.Path:
    """Deterministic cache path for one asset (kind = avatar | flag)."""
    return pathlib.Path(cfg.data_dir) / "assets" / f"{kind}_{_safe(key)}"


def download_asset(cfg, kind: str, key: str, url: str,
                   max_bytes: int = _AVATAR_MAX_BYTES) -> str | None:
    """Download `url` into the local asset cache, atomically.

    Returns the stored path as a string, or None when nothing usable arrived
    (missing key/url, network failure, non-200, oversized or non-image
    payload). Callers treat None as "keep whatever is cached already" — never
    as a fatal error.
    """
    url = (url or "").strip()
    # key is sanitized into the file name, so a blank key would produce a
    # nameless file — refuse it instead.
    if not url or not str(key).strip():
        return None

    parsed = urllib.parse.urlsplit(url)
    # https only: both providers serve images over https, and the client below is
    # an HTTPSConnection (a plain-http URL would break it). Other schemes
    # (file/, data/, ftp/) are refused outright.
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    target = parsed.path + (f"?{parsed.query}" if parsed.query else "")

    conn = None
    try:
        conn = http.client.HTTPSConnection(parsed.hostname, timeout=_TIMEOUT)
        if cfg.proxy:
            proxy = urllib.parse.urlsplit(cfg.proxy if "://" in cfg.proxy
                                          else f"http://{cfg.proxy}")
            conn.set_tunnel(proxy.hostname, proxy.port or 443)
        conn.request("GET", target, headers={"User-Agent": UA, "Accept": "image/*"})
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        body = resp.read(max_bytes + 1)
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    if not body or len(body) > max_bytes or sniff_mime(body[:16]) is None:
        return None

    dest = asset_path(cfg, kind, key)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(body)
        # os.replace cannot overwrite an existing file on Windows while another
        # handle is open on it (the serving endpoint), so drop the old one
        # first: worst case a concurrent duplicate GET returns 404 for one poll.
        if dest.exists():
            dest.unlink()
        tmp.replace(dest)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return None
    return str(dest)


def read_asset(cfg, kind: str, key: str) -> tuple[bytes, str, float] | None:
    """Return (bytes, mime, mtime) for a cached asset, or None when absent.

    Read-only: never touches the network. A stale file that is no longer a
    valid image is deleted (self-healing) rather than served.
    """
    if not key:
        return None
    path = asset_path(cfg, kind, key)
    try:
        if not path.is_file():
            return None
        data = path.read_bytes()
        mtime = path.stat().st_mtime
    except OSError:
        return None
    mime = sniff_mime(data[:16])
    if mime is None:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return data, mime, mtime


# ---------- avatar ----------

def avatar_url(platform: str, profile: dict) -> str:
    """Extract the avatar URL from a cached profile snapshot ("" when absent).

    Both providers keep it under different keys, so the lookup lives here
    instead of being duplicated across the provider clients.
    """
    if not isinstance(profile, dict):
        return ""
    if platform == "beatleader":
        url = profile.get("avatarUrl") or profile.get("avatar") or ""
    else:
        url = profile.get("profilePicture") or profile.get("avatar") or ""
    return str(url).strip()


def download_avatar(cfg, platform: str, player_id: str, url: str) -> str | None:
    return download_asset(cfg, f"avatar_{platform}", player_id, url,
                          _AVATAR_MAX_BYTES)


def read_avatar(cfg, platform: str, player_id: str) -> tuple[bytes, str, float] | None:
    return read_asset(cfg, f"avatar_{platform}", player_id)


# ---------- country flag ----------

def flag_url(country: str) -> str:
    """CDN URL for an ISO 3166-1 alpha-2 country code ("" when not a code)."""
    cc = (country or "").strip().lower()
    if len(cc) != 2 or not cc.isalpha():
        return ""
    return FLAG_URL_TEMPLATE.format(cc=cc)


def download_flag(cfg, country: str) -> str | None:
    return download_asset(cfg, "flag", country, flag_url(country), _FLAG_MAX_BYTES)


def read_flag(cfg, country: str) -> tuple[bytes, str, float] | None:
    return read_asset(cfg, "flag", country)


def card_payload(cfg, platform: str, player_id: str, profile: dict) -> dict | None:
    """Card fields for the sidebar player block, from the cached profile snapshot.

    Returns None when there is no usable snapshot, which the frontend renders as
    "no card" (the block stays hidden) - never as an error state: not having
    synced cloud data yet is a normal state for a fresh install.

    `rank` / `countryRank` are passed through as reported by the provider (0 /
    None means "unranked on that platform"); the frontend prints a dash for them.
    Each image URL carries its file mtime (`?v=`) so the WebView can cache the
    bytes hard while a changed image still busts the cache; an empty URL means
    "not cached yet", and the frontend falls back to the name initial / country
    code instead of requesting a missing file.
    """
    if not isinstance(profile, dict) or not profile:
        return None
    name = str(profile.get("name") or "").strip()
    country = str(profile.get("country") or "").strip().upper()
    if not name and not country:
        return None
    avatar = read_avatar(cfg, platform, player_id)
    flag = read_flag(cfg, country)
    return {
        "platform": platform,
        "player_id": str(player_id),
        "name": name,
        "country": country[:2],
        "rank": profile.get("rank"),
        "countryRank": profile.get("countryRank"),
        "avatar_url": (f"/api/player/avatar?v={int(avatar[2])}" if avatar else ""),
        "flag_url": (f"/api/player/flag?v={int(flag[2])}" if flag else ""),
    }


# ---------- sync helper ----------

class AssetFetch(NamedTuple):
    """Outcome of one best-effort asset download.

    `attempted` distinguishes "no source URL" (nothing to do, e.g. a player
    without a country code) from "tried and failed" (worth a log line).
    """
    attempted: bool
    stored: str | None


def sync_player_assets(cfg, platform: str, player_id: str, profile: dict) -> dict:
    """Cache the avatar + flag for one synced profile (best effort).

    Called by the cloud sync. Never raises: a CDN failure only means the card
    keeps the previous image (or falls back to the initial / country code).
    """
    profile = profile or {}
    avatar_src = avatar_url(platform, profile)
    flag_src = flag_url(str(profile.get("country") or ""))
    return {
        "avatar": AssetFetch(
            bool(avatar_src),
            download_avatar(cfg, platform, player_id, avatar_src) if avatar_src else None),
        "flag": AssetFetch(
            bool(flag_src),
            download_flag(cfg, str(profile.get("country") or "")) if flag_src else None),
    }
