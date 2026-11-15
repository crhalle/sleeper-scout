"""Split the research queue into batches for the sport-specific research agents,
and merge their results into one validated adjustments.json.

Batching is deterministic: players are grouped by real game (NFL: both teams of a
matchup; NBA: the player's own team, since a week has several opponents), so a batch
holds both sides' injuries plus the opposing defense and scheme research isn't duplicated.
"""
from __future__ import annotations

import math

from .engine import ADJ_MAX, ADJ_MIN

MIN_PARALLEL = 12      # below this many queued players, research stays in the main session
PER_BATCH = 10
MIN_BATCHES, MAX_BATCHES = 2, 5

PROFILE_FIELDS = ("name", "pos", "team", "injury", "games_this_season", "ppg_season", "ppg_last4",
                  "ppg_prev_season", "own_ppg", "floor", "ceiling", "sleeper_week", "week_pts", "ros_pts",
                  "week_games", "teammate_injuries")
PLAYER_KEYS = ("week_mult", "ros_mult", "return_week", "out_for_season", "status_note", "profile", "sources")


def _group_key(p: dict, sport: str) -> str:
    team = p.get("team") or "FA"
    if sport == "nba":
        return team
    games = p.get("week_games") or []
    if not games:
        return f"{team} (bye)"
    return " vs ".join(sorted({team, games[0]["opp"]}))


def plan_batches(analysis: dict, n: int | None = None, min_parallel: int = MIN_PARALLEL) -> dict:
    sport = analysis["league"]["sport"]
    queue = analysis["research"]["players"]
    P = analysis["players"]
    why = analysis["research"].get("why", {})

    groups: dict[str, list[str]] = {}
    for pid in queue:  # queue is already priority-ordered; keep that order inside groups
        groups.setdefault(_group_key(P[pid], sport), []).append(pid)

    if n is None:
        if len(queue) < min_parallel:
            n = 1
        else:
            n = max(MIN_BATCHES, min(MAX_BATCHES, math.ceil(len(queue) / PER_BATCH)))
    n = max(1, min(n, len(groups) or 1))
    mode = "inline" if n == 1 else "parallel"

    # greedy bin packing: biggest group first into the lightest batch
    bins: list[list[str]] = [[] for _ in range(n)]
    for key in sorted(groups, key=lambda k: (-len(groups[k]), k)):
        min(bins, key=lambda b: sum(len(groups[g]) for g in b)).append(key)

    L = analysis["league"]
    batches = []
    for i, keys in enumerate(b for b in bins if b):
        pids = [pid for k in keys for pid in groups[k]]
        opps = sorted({g["opp"] for pid in pids for g in P[pid].get("week_games") or []})
        batches.append({
            "batch": i + 1,
            "sport": sport,
            "league": {k: L.get(k) for k in ("name", "season", "week", "last_week", "label", "tags", "notes", "lineup")},
            "games": keys,
            "players": [{"pid": pid, "why": why.get(pid, []), **{f: P[pid].get(f) for f in PROFILE_FIELDS}} for pid in pids],
            "defenses": {o: analysis["defenses"][o] for o in opps if o in analysis.get("defenses", {})},
        })
    return {"mode": mode, "batches": batches}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _clean_player(pid: str, e: dict, warnings: list[str]) -> dict:
    out = {k: e[k] for k in PLAYER_KEYS if k in e and e[k] is not None}
    sources = [s for s in (out.get("sources") or []) if isinstance(s, str) and s.strip()]
    out["sources"] = sources
    for k in ("week_mult", "ros_mult"):
        if k in out:
            v = _num(out[k])
            if v is None:
                warnings.append(f"{pid}: dropped non-numeric {k}={out[k]!r}")
                out.pop(k)
            elif not ADJ_MIN <= v <= ADJ_MAX:
                warnings.append(f"{pid}: {k} {v} clamped to [{ADJ_MIN}, {ADJ_MAX}]")
                out[k] = max(ADJ_MIN, min(ADJ_MAX, v))
            else:
                out[k] = v
    if "return_week" in out:
        try:
            out["return_week"] = int(out["return_week"])
        except (TypeError, ValueError):
            warnings.append(f"{pid}: dropped invalid return_week={out['return_week']!r}")
            out.pop("return_week")
    changes = (out.get("week_mult", 1.0) != 1.0 or out.get("ros_mult", 1.0) != 1.0
               or "return_week" in out or out.get("out_for_season"))
    if changes and not sources:
        warnings.append(f"{pid}: adjustment ignored because it has no sources")
        for k in ("week_mult", "ros_mult", "return_week", "out_for_season"):
            out.pop(k, None)
    return out


def merge(results: list[dict], analysis: dict, existing: dict | None = None) -> tuple[dict, list[str], list[str]]:
    """Returns (adjustments, warnings, queued players with no result)."""
    warnings: list[str] = []
    known = set(analysis["players"])
    players: dict = {}
    defenses: dict = {}
    for res in results:
        for pid, e in (res.get("players") or {}).items():
            pid = str(pid)
            if pid not in known:
                warnings.append(f"{pid}: not a rostered player in this league; dropped")
                continue
            if not isinstance(e, dict):
                continue
            e = _clean_player(pid, e, warnings)
            prev = players.get(pid)
            if prev is None or len(e["sources"]) > len(prev["sources"]):
                players[pid] = e
        for team, d in (res.get("defenses") or {}).items():
            if not isinstance(d, dict) or not d.get("note"):
                continue
            prev = defenses.get(team)
            if prev is None:
                defenses[team] = {"note": d["note"], "sources": list(d.get("sources") or [])}
            elif d["note"] not in prev["note"]:
                prev["note"] = f"{prev['note']} {d['note']}"
                prev["sources"] += [s for s in d.get("sources") or [] if s not in prev["sources"]]
    adj = {"players": players, "defenses": defenses}
    if existing and existing.get("league_notes"):
        adj["league_notes"] = existing["league_notes"]
    missing = [pid for pid in analysis["research"]["players"] if pid not in players]
    return adj, warnings, missing
