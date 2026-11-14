"""League discovery, roster-slot rules and scoring-format detection."""
from __future__ import annotations

import math

from . import api

SPORTS = ("nfl", "nba")

# Which base positions may fill each lineup slot.
SLOT_ELIGIBILITY = {
    # NFL
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"}, "DEF": {"DEF"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "DL": {"DL", "DE", "DT"}, "LB": {"LB"}, "DB": {"DB", "CB", "S"},
    "IDP_FLEX": {"DL", "DE", "DT", "LB", "DB", "CB", "S"},
    # NBA
    "PG": {"PG"}, "SG": {"SG"}, "SF": {"SF"}, "PF": {"PF"}, "C": {"C"},
    "G": {"PG", "SG"}, "F": {"SF", "PF"},
    "UTIL": {"PG", "SG", "SF", "PF", "C"},
}
NON_STARTING = {"BN", "IR", "TAXI", "RESERVE"}

NFL_SKILL = ["QB", "RB", "WR", "TE", "K", "DEF"]
NFL_IDP = ["DL", "LB", "DB"]
NBA_POS = ["PG", "SG", "SF", "PF", "C"]

NFL_REGULAR_WEEKS = 18


def find_leagues(username_or_id: str, season: str | None = None) -> list[dict]:
    u = api.user(username_or_id)
    if not u:
        raise SystemExit(f"No Sleeper user found for '{username_or_id}'. Check the spelling, or pass a league ID instead.")
    out = []
    for sport in SPORTS:
        s = season or api.state(sport)["league_season"]
        for lg in api.user_leagues(u["user_id"], sport, s):
            out.append({
                "league_id": lg["league_id"], "name": lg["name"], "sport": sport, "season": lg["season"],
                "status": lg["status"], "teams": lg["total_rosters"],
                "format": scoring_summary(lg)["label"],
            })
    return out


def starting_slots(lg: dict) -> list[str]:
    return [s for s in lg.get("roster_positions", []) if s not in NON_STARTING]


def relevant_positions(lg: dict) -> list[str]:
    """Base positions the league can start (drives which stats we fetch)."""
    pos: set[str] = set()
    for s in starting_slots(lg):
        pos |= SLOT_ELIGIBILITY.get(s, {s})
    if lg["sport"] == "nfl":
        order = NFL_SKILL + ["DL", "DE", "DT", "LB", "DB", "CB", "S"]
    else:
        order = NBA_POS
    return [p for p in order if p in pos]


def fetch_positions(lg: dict) -> list[str]:
    """Position filter for the stats endpoint (NFL needs it; NBA returns all players)."""
    if lg["sport"] != "nfl":
        return []
    pos = relevant_positions(lg)
    idp = {"DL", "DE", "DT", "LB", "DB", "CB", "S"} & set(pos)
    base = [p for p in pos if p in NFL_SKILL]
    if idp:
        base += ["DL", "LB", "DB"]
    return base


def player_positions(p: dict) -> set[str]:
    fp = p.get("fantasy_positions") or ([p["position"]] if p.get("position") else [])
    return set(fp)



def last_fantasy_week(lg: dict, sport_state: dict) -> int:
    st = lg.get("settings", {})
    pws = int(st.get("playoff_week_start") or 0)
    teams = int(st.get("playoff_teams") or 0)
    if pws and teams:
        rounds = max(1, math.ceil(math.log2(teams)))
        extra = 1 if int(st.get("playoff_round_type") or 0) == 2 else 0  # two-week championship
        return pws + rounds - 1 + extra
    if lg["sport"] == "nfl":
        return 17
    return int(sport_state.get("week") or 1) + 20


def scoring_summary(lg: dict) -> dict:
    """Human-readable description of the league's format and scoring framework."""
    sc = lg.get("scoring_settings") or {}
    st = lg.get("settings") or {}
    slots = starting_slots(lg)
    notes: list[str] = []
    tags: list[str] = []
    kind = {0: "Redraft", 1: "Keeper", 2: "Dynasty", 3: "Guillotine"}.get(int(st.get("type") or 0), "Redraft")
    tags.append(kind)
    if st.get("best_ball"):
        tags.append("Best Ball")

    if lg["sport"] == "nfl":
        rec = float(sc.get("rec", 0))
        label = "PPR" if rec >= 1 else "Half-PPR" if rec >= 0.5 else "Standard" if rec == 0 else f"{rec:g}-PPR"
        if sc.get("bonus_rec_te"):
            label += f" + TE premium (+{sc['bonus_rec_te']:g}/rec)"
            tags.append("TE Premium")
        if slots.count("QB") >= 2:
            tags.append("2QB")
        if "SUPER_FLEX" in slots:
            tags.append("Superflex")
        if {"DL", "LB", "DB", "IDP_FLEX"} & set(slots):
            tags.append("IDP")
        pt = sc.get("pass_td", 4)
        if pt != 4:
            notes.append(f"{pt:g}-point passing TDs")
        if sc.get("pass_int", -2) != -2:
            notes.append(f"Interceptions {sc.get('pass_int', 0):g}")
        fd = [k for k in ("pass_fd", "rush_fd", "rec_fd") if sc.get(k)]
        if fd:
            notes.append("First-down scoring: " + ", ".join(f"{k.split('_')[0]} {sc[k]:g}" for k in fd))
        bonuses = sorted(k for k, v in sc.items() if k.startswith("bonus_") and v and k != "bonus_rec_te")
        if bonuses:
            notes.append("Bonuses: " + ", ".join(f"{k.removeprefix('bonus_')} {sc[k]:g}" for k in bonuses[:8]))
        if sc.get("pass_yd") and abs(sc["pass_yd"] - 0.04) > 1e-9:
            notes.append(f"{sc['pass_yd']:g} pts per passing yard")
        if sc.get("fum_lost", -2) != -2:
            notes.append(f"Fumble lost {sc.get('fum_lost', 0):g}")
    else:
        weights = {k: sc[k] for k in ("pts", "reb", "ast", "stl", "blk", "to", "tpm", "dd", "td") if sc.get(k)}
        label = "Points (" + ", ".join(f"{k} {v:g}" for k, v in weights.items()) + ")" if weights else "Custom points"
        if not any(sc.get(k) for k in ("pts", "reb", "ast")):
            notes.append("Scoring settings look unusual (no points/rebounds/assists weights); grades use whatever weights the league defines.")
        bonuses = sorted(k for k, v in sc.items() if k.startswith("bonus_") and v)
        if bonuses:
            notes.append("Bonuses: " + ", ".join(f"{k.removeprefix('bonus_')} {sc[k]:g}" for k in bonuses))

    slot_counts: dict[str, int] = {}
    for s in slots:
        slot_counts[s] = slot_counts.get(s, 0) + 1
    lineup = " · ".join(f"{n} {s}" if n > 1 else s for s, n in slot_counts.items())
    bench = sum(1 for s in lg.get("roster_positions", []) if s == "BN")
    return {
        "label": label,
        "tags": tags,
        "notes": notes,
        "lineup": lineup,
        "bench": bench,
        "teams": int(st.get("num_teams") or lg.get("total_rosters") or 0),
        "playoff_week_start": int(st.get("playoff_week_start") or 0),
        "playoff_teams": int(st.get("playoff_teams") or 0),
        "trade_deadline": int(st.get("trade_deadline") or 0),
        "waiver": {0: "Rolling waivers", 1: "Reverse standings", 2: "FAAB"}.get(int(st.get("waiver_type") or 0), "Waivers"),
    }
