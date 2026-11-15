"""Depth-chart injury context: whose injuries change a player's role this week.

A player's outlook depends on more than his own status. A starting QB out lowers his
receivers; a lead RB out lifts the next back; a starting CB out lifts the WRs facing that
defense. This module finds injured *starters* from Sleeper's depth chart and maps each one
to the positions it affects, for teammates (`impacts`) and opponents (`defense_injuries`).

The engine doesn't change projections from this on its own (Sleeper's half of the
projection already reacts to depth-chart news); it flags the affected players for research
and hands the research agents the context, and they set cited multipliers.
"""
from __future__ import annotations

from collections import defaultdict

# Statuses that matter for this week. Long-term statuses count only when the absence is new.
WEEK_STATUSES = {"Questionable", "Doubtful", "Out", "GTD", "DTD", "Day-To-Day", "Sus"}
LONG_STATUSES = {"IR", "PUP", "NFI", "NA", "DNR", "COV"}
LIKELY_OUT = {"Doubtful", "Out", "Sus"} | LONG_STATUSES

OL_MIN_OUT = 2           # linemen likely out before the line counts as weakened
NFL_OL = {"OL", "T", "G", "C", "OT", "OG", "LT", "RT", "LG", "RG"}
NFL_DEF = {"DL": {"DL", "DE", "DT", "NT", "LDE", "RDE", "LDT", "RDT", "EDGE"},
           "LB": {"LB", "OLB", "ILB", "MLB", "LOLB", "ROLB", "LILB", "RILB", "WLB", "SLB"},
           "CB": {"CB", "LCB", "RCB", "NB", "DB"},
           "S": {"S", "FS", "SS"}}

# injured teammate's position group -> {affected position: expected direction}
NFL_IMPACT = {
    "QB": {"WR": "-", "TE": "-", "RB": "-", "K": "-"},
    "RB": {"RB": "+"},
    "WR": {"WR": "+", "TE": "+", "QB": "-"},
    "TE": {"TE": "+", "WR": "+"},
    "OL": {"QB": "-", "RB": "-"},
}
# injured opposing defender's group -> offensive positions it helps
NFL_DEF_HELPS = {"CB": ["WR"], "S": ["WR", "TE"], "LB": ["RB", "TE"], "DL": ["RB", "QB"]}

NBA_IMPACT = {
    "PG": {"PG": "+", "SG": "+"},
    "SG": {"SG": "+", "PG": "+", "SF": "+"},
    "SF": {"SF": "+", "SG": "+", "PF": "+"},
    "PF": {"PF": "+", "SF": "+", "C": "+"},
    "C": {"C": "+", "PF": "+"},
}
NBA_STARS_PER_TEAM = 2   # an injured top-2 scorer frees usage for every teammate


def _group(p: dict, sport: str) -> str | None:
    pos = p.get("position")
    dpos = p.get("depth_chart_position") or pos
    if sport == "nba":
        return dpos if dpos in NBA_IMPACT else (pos if pos in NBA_IMPACT else None)
    if pos in NFL_OL or dpos in NFL_OL:
        return "OL"
    if pos in NFL_IMPACT:
        return pos
    for g, names in NFL_DEF.items():
        if pos in names or dpos in names:
            return g
    return None


def _name(p: dict) -> str:
    return p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or "?"


def team_injuries(pdb: dict, sport: str, recent: dict[str, bool] | None = None,
                  team_rank: dict[str, int] | None = None) -> dict[str, list[dict]]:
    """Injured starters by team.

    recent: pid -> True if he played in the last two weeks (a new absence). Players with no
        game logs (OL, defenders) are unknown, so only their week-to-week statuses count.
    team_rank: pid -> rank of his fantasy points per game on his team this season (1 = best).
    """
    recent = recent or {}
    team_rank = team_rank or {}
    out = defaultdict(list)
    for pid, p in pdb.items():
        status = p.get("injury_status")
        team = p.get("team")
        if not status or not team:
            continue
        group = _group(p, sport)
        if not group:
            continue
        new = recent.get(str(pid))
        if status in LONG_STATUSES and not new:
            continue  # long-term absence already reflected in recent games
        order = p.get("depth_chart_order")
        rank = team_rank.get(str(pid))
        if sport == "nfl":
            # Sleeper has no depth order for linemen, so keep only the ones likely out;
            # `impacts` flags the line only when several are missing
            starter = order == 1 or (group == "OL" and status in LIKELY_OUT)
            star = False
        else:
            star = rank is not None and rank <= NBA_STARS_PER_TEAM
            starter = order == 1 or star
        if not starter:
            continue
        out[team].append({
            "pid": str(pid), "name": _name(p), "pos": p.get("position"), "group": group,
            "depth": f"{p.get('depth_chart_position') or group}{order or ''}",
            "status": status, "body_part": p.get("injury_body_part"),
            "practice": p.get("practice_participation"), "new_absence": new,
            "likely_out": status in LIKELY_OUT, "star": star,
        })
    for team in out:
        out[team].sort(key=lambda e: (not e["likely_out"], e["group"], e["name"]))
    return dict(out)


def impacts(pid: str, positions: set[str], team: str | None, inj: dict[str, list[dict]], sport: str) -> list[dict]:
    """Injured teammates whose absence changes this player's role, with expected direction."""
    if not team:
        return []
    table = NFL_IMPACT if sport == "nfl" else NBA_IMPACT
    out = []
    ol = [e for e in inj.get(team, []) if e["group"] == "OL"]
    if sport == "nfl" and len(ol) >= OL_MIN_OUT and positions & set(NFL_IMPACT["OL"]):
        out.append({"name": ", ".join(e["name"] for e in ol), "pos": "OL", "depth": f"OL ({len(ol)} out)",
                    "status": "Out/Doubtful", "practice": None, "new_absence": None, "likely_out": True,
                    "effect": "-", "note": "starter status unconfirmed; check the depth chart"})
    for e in inj.get(team, []):
        if e["pid"] == pid or e["group"] == "OL":
            continue
        effect = next((table.get(e["group"], {}).get(pos) for pos in positions if table.get(e["group"], {}).get(pos)), None)
        if effect is None and sport == "nba" and e["star"]:
            effect = "+"
        if effect:
            out.append({"name": e["name"], "pos": e["pos"], "depth": e["depth"], "status": e["status"],
                        "practice": e["practice"], "new_absence": e["new_absence"],
                        "likely_out": e["likely_out"], "effect": effect})
    return out


def defense_injuries(team: str, inj: dict[str, list[dict]], sport: str) -> list[dict]:
    """The opponent's injured starters that make it easier to score against them."""
    out = []
    for e in inj.get(team, []):
        if sport == "nfl":
            helps = NFL_DEF_HELPS.get(e["group"])
        else:
            helps = [e["group"]]
        if helps:
            out.append({"name": e["name"], "pos": e["pos"], "depth": e["depth"], "status": e["status"],
                        "practice": e["practice"], "helps": helps})
    return out
