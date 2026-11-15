"""Owner-only roster-move recommendations: drops, waiver claims and trade ideas.

Everything here works on a finished analysis.json (no HTTP) and re-scores rosters with
grading.team_value, the same function the engine grades teams with. The output is private to
the owner who asked: it is written to moves.json and never feeds report.html or adjustments.

Values are rest-of-season points (the current week included) from the researched projections, with
single-week outliers in later weeks capped (robust_weeks).
"""
from __future__ import annotations

import statistics
from itertools import combinations

from .grading import team_value

WAIVER_DROP_POOL = 5     # lowest-value droppable players each waiver add is paired with
TRADE_SINGLES = 12       # top players per side considered for 1-for-1 trades
TRADE_PACKAGE = 8        # top players per side combined into 2-player packages
TRADE_TOL = 0.005        # partner may lose up to 0.5% of their ROS score and still plausibly accept
MIN_GAIN = 0.005         # a move must add at least 0.5% to the owner's ROS score...
MIN_GAIN_PTS = 1.0       # ...and at least this many points
# Market sanity check, so the partner doesn't give up a star for a bench upgrade just because the star
# sits on their bench. A player's market value is his ROS points over replacement (the best free agent
# at his position, so kickers and streamable defenses are worth little), raised to MARKET_EXP so stars
# beat the sum of depth pieces. The partner's outgoing value may exceed their incoming by MARKET_SLACK.
MARKET_EXP, MARKET_SLACK = 1.5, 0.15
PER_PARTNER, MAX_TRADES, MAX_WAIVERS, MAX_DROPS = 2, 8, 10, 6
WAIVERS_PER_POS = 3      # a few alternatives per position, not ten interchangeable TEs
# A future week's projection may be at most OUTLIER_MULT x the player's typical per-game value. One bad
# Sleeper projection (e.g. a kicker projected 75 for a single week) otherwise swings every move he's in.
OUTLIER_MULT = 2.0
OUTLIER_MIN_WEEKS = 3    # future weeks with a value needed for a median; fewer falls back to own_ppg


def robust_weeks(pr: dict, current_week: int) -> tuple[dict, list[dict]]:
    """(ros_by_week with future-week outliers capped, flags). The current week holds actual points,
    locks and researched multipliers, so only later weeks are checked. Values are compared per game
    so an NBA 4-game week isn't mistaken for an outlier against 2-game weeks."""
    rbw = pr.get("ros_by_week") or {}
    games = {int(k): v for k, v in (pr.get("ros_games_by_week") or {}).items()}
    future = {k: v for k, v in rbw.items() if int(k) > current_week}
    per_game = {k: v / max(1, games.get(int(k), 1)) for k, v in future.items()}
    vals = [v for v in per_game.values() if v > 0]
    if len(vals) >= OUTLIER_MIN_WEEKS:
        ref = statistics.median(vals)
    else:
        ref = pr.get("own_ppg") or 0.0
    if ref <= 0:
        return rbw, []
    out, flags = dict(rbw), []
    for k, v in future.items():
        cap = round(max(1, games.get(int(k), 1)) * ref * OUTLIER_MULT, 2)
        if v > cap:
            out[k] = cap
            flags.append({"pid": pr.get("pid"), "name": pr.get("name"), "pos": pr.get("pos"),
                          "week": int(k), "raw": v, "capped": cap})
    return out, flags


class Ctx:
    """Scoring context shared by every roster evaluation."""

    def __init__(self, analysis: dict):
        L = analysis["league"]
        self.sport = L["sport"]
        self.week = L["week"]
        self.weeks = [int(w) for w in analysis.get("weeks") or range(L["week"], L["last_week"] + 1)]
        self.slots = [s for s in L.get("roster_positions") or [] if s not in {"BN", "IR", "TAXI", "RESERVE"}]
        self.rules = L.get("roster_rules") or {}
        self.P, self.outliers = {}, []
        for pid, pr in {**(analysis.get("free_agents") or {}), **analysis["players"]}.items():
            rbw, flags = robust_weeks(pr, self.week)
            if flags:  # a capped copy; analysis.json and the graded profiles stay as they are
                pr = {**pr, "ros_by_week": rbw, "ros_pts": round(sum(rbw.values()), 2)}
                self.outliers += flags
            self.P[pid] = pr
        self.pos = {pid: set(pr.get("positions") or [pr.get("pos")]) for pid, pr in self.P.items()}
        self.replacement: dict = {}
        for pid in analysis.get("free_agents") or {}:
            pr = self.P[pid]
            k = pr.get("pos")
            self.replacement[k] = max(self.replacement.get(k, 0.0), pr.get("ros_pts") or 0.0)

    def value(self, team: dict, pids, weeks=None) -> tuple[float, float]:
        pids = [p for p in pids if p in self.P]
        w, ros, _, _ = team_value(pids, team.get("reserve") or [], self.P, self.pos, self.slots,
                                  team.get("slot_starters") or [], weeks or self.weeks, self.sport)
        return w, ros

    def locked(self, pid) -> bool:
        return bool(self.P.get(pid, {}).get("locked"))

    def droppable(self, pid) -> bool:
        # Sleeper won't drop an NFL player once his game has kicked off
        return not (self.sport == "nfl" and self.locked(pid))


def _active(team: dict) -> list[str]:
    reserve = set(team.get("reserve") or [])
    return [p for p in team["players"] if p not in reserve]


def _open_spots(ctx: Ctx, team: dict) -> int:
    spots = ctx.rules.get("roster_spots") or 0
    return spots - len(_active(team)) if spots else 0


def _pos_ok(ctx: Ctx, team: dict, roster) -> bool:
    """Position limits count active players only (Sleeper excludes IR and taxi)."""
    limits = ctx.rules.get("position_limits") or {}
    if not limits:
        return True
    reserve = set(team.get("reserve") or [])
    counts: dict = {}
    for p in roster:
        if p in reserve:
            continue
        k = (ctx.P[p].get("pos") or "").upper()
        counts[k] = counts.get(k, 0) + 1
    return all(counts.get(pos, 0) <= lim for pos, lim in limits.items())


def _brief(ctx: Ctx, pid) -> dict:
    pr = ctx.P.get(pid) or {}
    inj = pr.get("injury") or {}
    return {"pid": pid, "name": pr.get("name"), "pos": pr.get("pos"), "team": pr.get("team"),
            "status": inj.get("status"), "week_pts": pr.get("week_pts"), "ros_pts": pr.get("ros_pts")}


def marginals(ctx: Ctx, team: dict, base=None) -> dict:
    """{pid: (week_loss, ros_loss)} from removing each active player."""
    base = base or ctx.value(team, team["players"])
    out = {}
    for p in _active(team):
        w, r = ctx.value(team, [x for x in team["players"] if x != p])
        out[p] = (round(base[0] - w, 2), round(base[1] - r, 2))
    return out


# ------------------------------------------------------------------ drops

def drop_candidates(ctx: Ctx, team: dict, marg: dict) -> list[dict]:
    ir_ok = {s.lower() for s in ctx.rules.get("ir_statuses") or []}
    ir_free = (ctx.rules.get("ir_slots") or 0) - len(team.get("reserve") or [])
    issues = {i["rule"]: i for i in team.get("compliance") or []}
    order = sorted((p for p in marg if ctx.droppable(p)), key=lambda p: (marg[p][1], marg[p][0]))

    must: set = set()
    if "roster_size" in issues:
        must |= set(order[:max(0, -_open_spots(ctx, team))])
    if "position_limit" in issues:
        limits = ctx.rules.get("position_limits") or {}
        for pos, lim in limits.items():
            at = [p for p in order if (ctx.P[p].get("pos") or "").upper() == pos]
            if len(at) > lim:
                must |= set(at[:len(at) - lim])

    out = []
    for p in order:
        status = ((ctx.P[p].get("injury") or {}).get("status") or "").lower()
        tags = []
        if p in must:
            tags.append("must_drop")
        if ir_free > 0 and status and status in ir_ok:
            tags.append("ir_instead")  # move him to IR rather than dropping him
        out.append({**_brief(ctx, p), "week_loss": marg[p][0], "ros_loss": marg[p][1], "tags": tags})
    keep = [d for d in out if d["tags"]]
    return keep + [d for d in out if not d["tags"]][:max(0, MAX_DROPS - len(keep))]


# ------------------------------------------------------------------ waivers

def faab_hint(gain_pct: float, remaining: int) -> list[int] | None:
    """Suggested FAAB bid range [lo, hi] from the ROS gain as a share of the owner's ROS score."""
    if remaining <= 0:
        return None
    for cut, lo, hi in ((0.06, 0.25, 0.40), (0.03, 0.10, 0.20), (0.015, 0.04, 0.10)):
        if gain_pct >= cut:
            return [max(1, round(remaining * lo)), max(1, round(remaining * hi))]
    return [0, max(1, round(remaining * 0.02))]


def waiver_claims(ctx: Ctx, team: dict, marg: dict, base, free_agents, faab_remaining: int | None) -> list[dict]:
    pool = sorted((p for p in marg if ctx.droppable(p)), key=lambda p: marg[p][1])[:WAIVER_DROP_POOL]
    drops: list = ([None] if _open_spots(ctx, team) > 0 else []) + pool
    best: dict = {}
    for fa in free_agents:
        if fa not in ctx.P or fa in team["players"]:
            continue
        for d in drops:
            roster = [p for p in team["players"] if p != d] + [fa]
            if not _pos_ok(ctx, team, roster):
                continue
            w, r = ctx.value(team, roster)
            gain = (round(w - base[0], 2), round(r - base[1], 2))
            if fa not in best or gain[1] > best[fa][1][1]:
                best[fa] = (d, gain)
    floor = max(MIN_GAIN_PTS, MIN_GAIN * base[1])
    out, per_pos = [], {}
    for fa, (d, (wg, rg)) in sorted(best.items(), key=lambda kv: -kv[1][1][1]):
        pos = ctx.P[fa].get("pos")
        if rg < floor or per_pos.get(pos, 0) >= WAIVERS_PER_POS:
            continue
        per_pos[pos] = per_pos.get(pos, 0) + 1
        pct = rg / base[1] if base[1] else 0.0
        out.append({"add": _brief(ctx, fa), "drop": _brief(ctx, d) if d else None,
                    "week_gain": wg, "ros_gain": rg, "ros_gain_pct": round(pct, 4),
                    "faab_bid": faab_hint(pct, faab_remaining) if faab_remaining is not None else None})
        if len(out) == MAX_WAIVERS:
            break
    return out


# ------------------------------------------------------------------ trades

def trade_window(ctx: Ctx, league: dict) -> str | None:
    """Why trades are closed this week, or None when they are open."""
    dl = league.get("trade_deadline") or 0
    if dl and dl < 99 and ctx.week > dl:
        return f"The trade deadline (week {dl}) has passed."
    pws = league.get("playoff_week_start") or 0
    if pws and ctx.week >= pws:
        return "The league is in its playoffs."
    return None


def _apply(ctx: Ctx, team: dict, out_: tuple, in_: tuple, marg: dict) -> tuple[list, str | None]:
    """Roster after a trade, with the lowest-value droppable player cut when it's over the roster
    size. A freed spot stays empty: trades are judged without counting on a waiver pickup.
    Returns (roster, dropped pid or None)."""
    roster = [p for p in team["players"] if p not in out_] + list(in_)
    over = len([p for p in roster if p not in set(team.get("reserve") or [])]) - (ctx.rules.get("roster_spots") or 10**6)
    dropped = None
    if over > 0:
        cands = sorted((p for p in roster if p not in in_ and p in marg and ctx.droppable(p)),
                       key=lambda p: marg[p][1])
        for p in cands[:over]:
            roster.remove(p)
            dropped = p
    return roster, dropped


def _market(ctx: Ctx, pids) -> float:
    return sum(max(0.0, (ctx.P[p].get("ros_pts") or 0.0) - ctx.replacement.get(ctx.P[p].get("pos"), 0.0))
               ** MARKET_EXP for p in pids)


def trade_ideas(ctx: Ctx, me: dict, teams: list[dict], margs: dict, bases: dict) -> list[dict]:
    def tradeable(t):
        reserve = set(t.get("reserve") or [])
        # NFL players whose game has started can't move this week
        return [p for p in t["players"] if p not in reserve and ctx.droppable(p) and p in margs[t["roster_id"]]]

    def top(t, n):
        m = margs[t["roster_id"]]
        return sorted(tradeable(t), key=lambda p: -m[p][1])[:n]

    def packages(t):
        singles = [(p,) for p in top(t, TRADE_SINGLES)]
        return singles, singles + list(combinations(top(t, TRADE_PACKAGE), 2))

    my_singles, my_all = packages(me)
    my_floor = max(MIN_GAIN_PTS, MIN_GAIN * bases[me["roster_id"]][1])
    ideas = []
    for t in teams:
        if t["roster_id"] == me["roster_id"]:
            continue
        their_singles, their_all = packages(t)
        tol = TRADE_TOL * bases[t["roster_id"]][1]
        found = []
        for give in my_all:
            for get in (their_all if len(give) == 1 else their_singles):
                if len(give) == 2 and len(get) == 2:
                    continue
                if _market(ctx, get) > _market(ctx, give) * (1 + MARKET_SLACK):
                    continue  # they'd be giving up clearly more player value than they get
                weeks = ctx.weeks
                if any(ctx.locked(p) for p in give + get) and len(ctx.weeks) > 1:
                    weeks = ctx.weeks[1:]  # NBA: value from next week when a traded player has played
                mine, my_drop = _apply(ctx, me, give, get, margs[me["roster_id"]])
                theirs, their_drop = _apply(ctx, t, get, give, margs[t["roster_id"]])
                if not _pos_ok(ctx, me, mine) or not _pos_ok(ctx, t, theirs):
                    continue
                mb = ctx.value(me, me["players"], weeks) if weeks is not ctx.weeks else bases[me["roster_id"]]
                tb = ctx.value(t, t["players"], weeks) if weeks is not ctx.weeks else bases[t["roster_id"]]
                mw, mr = ctx.value(me, mine, weeks)
                tw, tr = ctx.value(t, theirs, weeks)
                my_gain, their_gain = round(mr - mb[1], 2), round(tr - tb[1], 2)
                if my_gain < my_floor or their_gain < -tol:
                    continue
                found.append({
                    "partner": {"roster_id": t["roster_id"], "team_name": t["team_name"], "owner": t["owner"]},
                    "give": [_brief(ctx, p) for p in give], "get": [_brief(ctx, p) for p in get],
                    "my_gain_ros": my_gain, "my_gain_week": round(mw - mb[0], 2),
                    "their_gain_ros": their_gain, "their_gain_week": round(tw - tb[0], 2),
                    "my_drop": _brief(ctx, my_drop) if my_drop else None,
                    "their_drop": _brief(ctx, their_drop) if their_drop else None,
                    "_rank": (min(my_gain, their_gain + tol), my_gain),
                })
        found.sort(key=lambda x: x["_rank"], reverse=True)
        picked, used = [], set()
        for f in found:  # distinct ideas per partner: don't repeat the same player I'd get
            key = tuple(sorted(p["pid"] for p in f["get"]))
            if key in used:
                continue
            used.add(key)
            picked.append(f)
            if len(picked) == PER_PARTNER:
                break
        ideas += picked
    ideas.sort(key=lambda x: x["_rank"], reverse=True)
    for i in ideas:
        i.pop("_rank")
    return ideas[:MAX_TRADES]


# ------------------------------------------------------------------ entry point

def find_team(analysis: dict, roster_id: int | None = None, user_id: str | None = None) -> dict:
    for t in analysis["teams"]:
        if roster_id is not None and int(t["roster_id"]) == int(roster_id):
            return t
        if user_id and (t.get("owner_id") == user_id or user_id in (t.get("co_owners") or [])):
            return t
    raise SystemExit("That team isn't in this league. Teams: " +
                     ", ".join(f"{t['roster_id']} {t['team_name']} ({t['owner']})" for t in analysis["teams"]))


def recommend(analysis: dict, roster_id: int, now_rostered: set | None = None) -> dict:
    """Drops, waiver claims and trade ideas for one roster. now_rostered (optional): every player
    rostered in the league right now, so free agents claimed since the analysis are skipped."""
    ctx = Ctx(analysis)
    L = analysis["league"]
    me = find_team(analysis, roster_id=roster_id)
    free_agents = [p for p in (analysis.get("free_agents") or {}) if not now_rostered or p not in now_rostered]
    bases = {t["roster_id"]: ctx.value(t, t["players"]) for t in analysis["teams"]}
    margs = {t["roster_id"]: marginals(ctx, t, bases[t["roster_id"]]) for t in analysis["teams"]}
    base, marg = bases[me["roster_id"]], margs[me["roster_id"]]

    faab = None
    if L.get("waiver_type") == 2 and L.get("waiver_budget"):
        faab = max(0, L["waiver_budget"] - (me.get("waiver") or {}).get("budget_used", 0))
    notes = []
    closed = trade_window(ctx, L)
    if closed:
        notes.append(closed + " No trade ideas.")
    if "Redraft" not in (L.get("tags") or []):
        notes.append("Values are this season only; dynasty or keeper value (age, future years) isn't modelled.")
    if ctx.sport == "nfl" and any(ctx.locked(p) for p in me["players"]):
        notes.append("Players whose game has started this week can't be dropped or traded and are left out.")
    if not analysis.get("free_agents"):
        notes.append("No free-agent data in analysis.json; re-run `report` to add it.")

    drops = drop_candidates(ctx, me, marg)
    waivers = waiver_claims(ctx, me, marg, base, free_agents, faab)
    trades = [] if closed else trade_ideas(ctx, me, analysis["teams"], margs, bases)
    shown = {d["pid"] for d in drops} | set(me["players"])
    for w in waivers:
        shown |= {x["pid"] for x in (w["add"], w["drop"]) if x}
    for t in trades:
        shown |= {x["pid"] for x in t["give"] + t["get"] + [t["my_drop"], t["their_drop"]] if x}
    for o in ctx.outliers:
        if o["pid"] in shown:
            notes.append(f"Capped {o['name']}'s week {o['week']} projection ({o['raw']:.1f} -> {o['capped']:.1f}): "
                         "outlier vs his other weeks.")

    return {
        "league": {k: L.get(k) for k in ("league_id", "name", "sport", "season", "week", "last_week", "label",
                                         "waiver", "waiver_budget", "trade_deadline")},
        "team": {"roster_id": me["roster_id"], "team_name": me["team_name"], "owner": me["owner"],
                 "week_score": base[0], "ros_score": base[1], "grades": me.get("grades"),
                 "open_spots": _open_spots(ctx, me), "faab_remaining": faab,
                 "waiver_position": (me.get("waiver") or {}).get("position"),
                 "compliance": me.get("compliance") or []},
        "drops": drops,
        "waivers": waivers,
        "trades": trades,
        "outliers": ctx.outliers,
        "notes": notes,
    }
