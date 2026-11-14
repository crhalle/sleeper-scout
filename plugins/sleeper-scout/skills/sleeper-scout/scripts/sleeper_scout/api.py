"""HTTP access to the public Sleeper API and nflverse releases, with an on-disk cache.

Sleeper's API is read-only and needs no authentication. Everything fetched is
cached under ~/.cache/sleeper-scout (override with SLEEPER_SCOUT_CACHE) so that
re-running an analysis is fast and deterministic.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

APP = "https://api.sleeper.app/v1"
COM = "https://api.sleeper.com"
NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"

FOREVER = None
MINUTE = 60
HOUR = 3600
DAY = 86400

USER_AGENT = "sleeper-scout/0.1 (+claude-code-plugin)"


def cache_dir() -> Path:
    d = Path(os.environ.get("SLEEPER_SCOUT_CACHE", Path.home() / ".cache" / "sleeper-scout"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(url: str, suffix: str) -> Path:
    h = hashlib.sha1(url.encode()).hexdigest()[:20]
    sub = cache_dir() / h[:2]
    sub.mkdir(exist_ok=True)
    return sub / f"{h}{suffix}"


def _fresh(p: Path, ttl: float | None) -> bool:
    if not p.exists():
        return False
    return ttl is None or (time.time() - p.stat().st_mtime) < ttl


def _download(url: str, timeout: float = 60) -> bytes:
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def get_json(url: str, ttl: float | None = HOUR):
    p = _cache_path(url, ".json")
    if _fresh(p, ttl):
        return json.loads(p.read_bytes())
    try:
        body = _download(url)
    except Exception:
        if p.exists():  # stale cache beats no data
            return json.loads(p.read_bytes())
        raise
    p.write_bytes(body)
    return json.loads(body)


def get_file(url: str, ttl: float | None = DAY, suffix: str = ".bin") -> Path | None:
    """Download a binary file (e.g. parquet). Returns None on 404."""
    p = _cache_path(url, suffix)
    if _fresh(p, ttl):
        return p
    try:
        body = _download(url, timeout=180)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return p if p.exists() else None
        raise
    except Exception:
        if p.exists():
            return p
        raise
    p.write_bytes(body)
    return p


def pmap(fn, items, workers: int = 8):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


# ---------------------------------------------------------------- Sleeper endpoints

def state(sport: str):
    return get_json(f"{APP}/state/{sport}", ttl=15 * MINUTE)


def user(username_or_id: str):
    return get_json(f"{APP}/user/{urllib.parse.quote(username_or_id)}", ttl=DAY)


def user_leagues(user_id: str, sport: str, season: str):
    return get_json(f"{APP}/user/{user_id}/leagues/{sport}/{season}", ttl=10 * MINUTE) or []


def league(league_id: str):
    return get_json(f"{APP}/league/{league_id}", ttl=10 * MINUTE)


def league_users(league_id: str):
    return get_json(f"{APP}/league/{league_id}/users", ttl=HOUR) or []


def league_rosters(league_id: str):
    return get_json(f"{APP}/league/{league_id}/rosters", ttl=10 * MINUTE) or []


def league_matchups(league_id: str, week: int):
    return get_json(f"{APP}/league/{league_id}/matchups/{week}", ttl=10 * MINUTE) or []


def players(sport: str):
    """Full player dictionary (~15 MB for NFL). Sleeper asks clients to fetch it at most daily."""
    return get_json(f"{APP}/players/{sport}", ttl=DAY)


def schedule(sport: str, season: int):
    """Games with status 'pre_game' | 'in_game' | 'complete'. NBA home/away are {team, record} dicts."""
    data = get_json(f"{COM}/schedule/{sport}/regular/{season}", ttl=10 * MINUTE)
    return data if isinstance(data, list) else []


def _pos_query(positions):
    return "&".join(f"position%5B%5D={urllib.parse.quote(p)}" for p in positions)


def weekly(kind: str, sport: str, season: int, week: int, positions, ttl):
    """kind = 'stats' | 'projections'. Returns a list of per-player (NFL) or per-game (NBA) rows
    with team/opponent attached."""
    q = f"season_type=regular"
    if positions:
        q += "&" + _pos_query(positions)
    data = get_json(f"{COM}/{kind}/{sport}/{season}/{week}?{q}", ttl=ttl)
    return data if isinstance(data, list) else []
