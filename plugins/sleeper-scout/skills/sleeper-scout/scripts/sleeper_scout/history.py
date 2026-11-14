"""Game logs (scored with the league's own settings), defense-vs-position, and
defensive style vectors used to find "similar defenses"."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

from . import api
from .league import NBA_POS, NFL_REGULAR_WEEKS

NBA_MAX_WEEKS = 26


def score(stats: dict, scoring: dict) -> float:
    """Fantasy points for one stat line. Sleeper stat keys match scoring_settings keys,
    including defense points-allowed buckets, so a dot product is exact."""
    return round(sum(float(stats.get(k, 0) or 0) * float(v) for k, v in scoring.items()), 2)


def played(row: dict, sport: str) -> bool:
    s = row.get("stats") or {}
    if sport == "nba":
        # older seasons only record seconds played ("sp"); newer ones also have "min"
        return (s.get("min") or 0) > 0 or (s.get("sp") or 0) > 0 or (s.get("gp") or 0) > 0
    return (s.get("gp") or 0) > 0


def primary_pos(row_player: dict, sport: str) -> str | None:
    pos = row_player.get("position")
    fps = row_player.get("fantasy_positions") or []
    if sport == "nfl":
        if pos in ("DE", "DT"):
            return "DL"
        if pos in ("CB", "S"):
            return "DB"
        return pos or (fps[0] if fps else None)
    for p in fps:
        if p in NBA_POS:
            return p
    return pos if pos in NBA_POS else None


def _ttl(season: int, week: int, cur_season: int, cur_week: int):
    if season < cur_season or week < cur_week - 1:
        return api.FOREVER
    return api.HOUR


def load_logs(sport: str, seasons: list[int], cur_season: int, cur_week: int, positions: list[str], scoring: dict) -> list[dict]:
    """Per-game rows: pid, season, week, date, team, opp, pos, pts."""
    jobs = []
    for s in seasons:
        last = cur_week if s == cur_season else (NFL_REGULAR_WEEKS if sport == "nfl" else NBA_MAX_WEEKS)
        jobs += [(s, w) for w in range(1, last + 1)]

    def fetch(job):
        s, w = job
        try:
            return s, w, api.weekly("stats", sport, s, w, positions, _ttl(s, w, cur_season, cur_week))
        except Exception:
            return s, w, []

    out = []
    for s, w, rows in api.pmap(fetch, jobs):
        for r in rows:
            if not played(r, sport) or not r.get("opponent"):
                continue
            pos = primary_pos(r.get("player") or {}, sport)
            if not pos:
                continue
            out.append({
                "pid": str(r["player_id"]), "season": s, "week": w, "date": r.get("date"),
                "team": r.get("team"), "opp": r.get("opponent"), "pos": pos,
                "pts": score(r.get("stats") or {}, scoring),
            })
    out.sort(key=lambda x: (x["season"], x["week"], x["date"] or ""))
    return out


# ------------------------------------------------------------- defense vs position

def defense_vs_position(logs: list[dict]) -> dict:
    """{(season, opp, pos): avg points allowed per game} plus {(season, pos): league avg}
    and {(season, opp): games}. Points are summed per game across all players at a position."""
    per_game = defaultdict(float)  # (season, opp, game_key, pos) -> pts
    games = defaultdict(set)
    for r in logs:
        gk = (r["week"], r["date"])
        per_game[(r["season"], r["opp"], gk, r["pos"])] += r["pts"]
        games[(r["season"], r["opp"])].add(gk)
    allowed = defaultdict(float)
    for (s, opp, gk, pos), pts in per_game.items():
        allowed[(s, opp, pos)] += pts
    avg = {}
    for (s, opp, pos), tot in allowed.items():
        avg[(s, opp, pos)] = tot / max(1, len(games[(s, opp)]))
    league = defaultdict(list)
    for (s, opp, pos), v in avg.items():
        league[(s, pos)].append(v)
    league_avg = {k: statistics.fmean(v) for k, v in league.items() if v}
    n_games = {k: len(v) for k, v in games.items()}
    return {"allowed": avg, "league": league_avg, "games": n_games}


def dvp_index(dvp: dict, season: int, opp: str, pos: str) -> tuple[float, int]:
    """Opponent's points allowed to `pos` relative to league average, blending in the
    prior season when the current sample is small. Returns (index, games)."""
    def one(s):
        a = dvp["allowed"].get((s, opp, pos))
        lg = dvp["league"].get((s, pos))
        g = dvp["games"].get((s, opp), 0)
        if a is None or not lg:
            return None, 0
        return a / lg, g
    cur, g = one(season)
    prev, gp = one(season - 1)
    if cur is None and prev is None:
        return 1.0, 0
    if cur is None:
        return 1 + (prev - 1) * 0.5, 0
    if prev is None or g >= 8:
        return cur, g
    w = g / 8
    return cur * w + prev * (1 - w), g


# ------------------------------------------------------------- style vectors

def style_vectors(dvp: dict, positions: list[str], scheme: dict | None = None) -> dict:
    """{(season, team): {feature: z-score}}. Features: share of fantasy points allowed to each
    position, total allowed per game, plus any scheme features (NFL: blitz/box/man/EPA)."""
    raw: dict = defaultdict(dict)
    teams = {(s, t) for (s, t) in dvp["games"]}
    for s, t in teams:
        vals = {p: dvp["allowed"].get((s, t, p), 0.0) for p in positions}
        tot = sum(vals.values())
        if tot <= 0:
            continue
        for p in positions:
            raw[(s, t)][f"share_{p}"] = vals[p] / tot
        raw[(s, t)]["allowed_total"] = tot
        for k, v in ((scheme or {}).get((s, t)) or {}).items():
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                raw[(s, t)][k] = v
    # z-score each feature within season so different seasons are comparable
    by_season = defaultdict(lambda: defaultdict(list))
    for (s, t), feats in raw.items():
        for k, v in feats.items():
            by_season[s][k].append(v)
    stats = {s: {k: (statistics.fmean(v), statistics.pstdev(v) or 1.0) for k, v in fs.items() if len(v) >= 4}
             for s, fs in by_season.items()}
    out = {}
    for (s, t), feats in raw.items():
        out[(s, t)] = {k: (v - stats[s][k][0]) / stats[s][k][1] for k, v in feats.items() if k in stats.get(s, {})}
    return out


def similar_defenses(vectors: dict, season: int, team: str, frac: float = 0.25) -> set:
    """The (season, team) defenses closest in style to `team`'s current-season profile
    (falls back to its prior season early in the year)."""
    target = vectors.get((season, team)) or vectors.get((season - 1, team))
    if not target:
        return set()
    dists = []
    for key, v in vectors.items():
        common = [k for k in target if k in v]
        if len(common) < 3:
            continue
        d = math.sqrt(sum((target[k] - v[k]) ** 2 for k in common) / len(common))
        dists.append((d, key))
    dists.sort()
    k = max(4, int(len(dists) * frac))
    return {key for _, key in dists[:k]}
