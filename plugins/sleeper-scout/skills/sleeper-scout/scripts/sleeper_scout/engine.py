"""End-to-end analysis: league -> rosters -> player profiles -> grades.

`run(league_id, adjustments)` is deterministic for a given cache state, so the same
call is used both for the first pass (no research adjustments) and the final report.
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict

from . import api, depth, grading, history
from .league import (NBA_POS, compliance_rules, fetch_positions, last_fantasy_week, player_positions,
                     relevant_positions, roster_compliance, scoring_summary, starting_slots)

# Share of a game's expected points kept, by Sleeper injury status.
WEEK_AVAIL = {"Questionable": 0.9, "GTD": 0.85, "DTD": 0.85, "Day-To-Day": 0.85, "Doubtful": 0.25,
              "Out": 0.0, "IR": 0.0, "PUP": 0.0, "Sus": 0.0, "NA": 0.0, "DNR": 0.0, "COV": 0.0, "NFI": 0.0}
# Expected share of remaining weeks available (before research overrides it with return_week).
ROS_AVAIL = {"Questionable": 0.98, "GTD": 0.98, "DTD": 0.98, "Day-To-Day": 0.98, "Doubtful": 0.93,
             "Out": 0.88, "IR": 0.45, "PUP": 0.5, "NFI": 0.5, "Sus": 0.8, "NA": 0.3, "DNR": 0.3, "COV": 0.9}

W_DVP, W_SPLIT, W_H2H = 0.5, 0.3, 0.2   # matchup multiplier weights
MULT_MIN, MULT_MAX = 0.80, 1.20
ADJ_MIN, ADJ_MAX = 0.0, 1.5
FA_PER_POS = 15  # best unrostered players kept per position for roster-move recommendations


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def _avg(xs):
    return statistics.fmean(xs) if xs else None


def _shrink(raw: float | None, n: int, k: float) -> float:
    if raw is None or n == 0:
        return 1.0
    return 1 + (raw - 1) * n / (n + k)


def _name(p: dict, pid: str) -> str:
    if p.get("position") == "DEF" or (not p.get("first_name") and pid.isalpha()):
        return f"{p.get('team') or pid} D/ST"
    return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or pid


# ------------------------------------------------------------------ projections

def _proj_week(sport, season, week, positions, scoring):
    rows = api.weekly("projections", sport, season, week, positions, ttl=3 * api.HOUR)
    by_pid = defaultdict(list)
    team_games = defaultdict(set)
    for r in rows:
        if not r.get("opponent") or not r.get("team"):
            continue
        g = (r.get("date") or "", r["opponent"])
        team_games[r["team"]].add(g)
        by_pid[str(r["player_id"])].append({"date": g[0], "opp": g[1], "team": r["team"],
                                             "pts": history.score(r.get("stats") or {}, scoring)})
    return by_pid, team_games


def final_games(sport: str, season: int, week: int, statuses=("complete",)) -> set:
    """Games this week with one of `statuses` (default: completed). NFL keys are team
    abbreviations (one game a week); NBA keys are (date, team)."""
    try:
        sched = api.schedule(sport, season)
    except Exception:
        return set()
    out = set()
    for g in sched:
        if g.get("week") != week or g.get("status") not in statuses:
            continue
        for side in ("home", "away"):
            t = g.get(side)
            t = t.get("team") if isinstance(t, dict) else t
            if t:
                out.add(t if sport == "nfl" else (g.get("date"), t))
    return out


# ------------------------------------------------------------------ main

def run(league_id: str, adjustments: dict | None = None, history_seasons: int = 3, progress=print) -> dict:
    adjustments = adjustments or {}
    adj_players = {str(k): v for k, v in (adjustments.get("players") or {}).items()}

    lg = api.league(league_id)
    if not lg:
        raise SystemExit(f"League {league_id} not found. Check the ID (it is the long number in the Sleeper league URL).")
    sport = lg["sport"]
    if sport not in ("nfl", "nba"):
        raise SystemExit(f"Sport '{sport}' is not supported yet (NFL and NBA are).")
    st = api.state(sport)
    season = int(lg["season"])
    cur_season = int(st["league_season"])
    if season != cur_season:
        progress(f"Note: league season {season} differs from Sleeper's current season {cur_season}.")
    in_season = st.get("season_type") == "regular" and season == cur_season
    week = max(1, int(st.get("display_week") or st.get("week") or 1)) if in_season else 1
    last_week = last_fantasy_week(lg, st)
    if sport == "nfl":
        last_week = min(last_week, history.NFL_REGULAR_WEEKS)
    scoring = {k: float(v) for k, v in (lg.get("scoring_settings") or {}).items()}
    slots = starting_slots(lg)
    summary = scoring_summary(lg)
    n_teams = summary["teams"] or len(api.league_rosters(league_id))

    progress(f"League: {lg['name']} ({sport.upper()} {season}, {summary['label']}), week {week}")
    users = {u["user_id"]: u for u in api.league_users(league_id)}
    rosters = api.league_rosters(league_id)
    progress("Loading player database…")
    pdb = api.players(sport)

    positions = fetch_positions(lg)
    rel_pos = relevant_positions(lg)
    seasons = list(range(season - history_seasons + 1, season + 1))
    progress(f"Loading game logs for {seasons[0]}–{seasons[-1]}…")
    logs = history.load_logs(sport, seasons, cur_season, week if season == cur_season else 0, positions, scoring)
    progress(f"  {len(logs):,} player-games")

    dvp = history.defense_vs_position(logs)
    scheme, warnings = {}, []
    if sport == "nfl":
        progress("Loading nflverse defensive scheme data…")
        try:
            from . import schemes_nfl
            scheme, warnings = schemes_nfl.scheme_features(seasons, cur_season)
        except Exception as e:  # never fail the whole run on optional data
            warnings.append(f"Scheme features skipped: {e}")
    style_pos = [p for p in (rel_pos if sport == "nfl" else NBA_POS) if p in {"QB", "RB", "WR", "TE"} | set(NBA_POS)]
    vectors = history.style_vectors(dvp, style_pos, scheme)

    progress(f"Loading projections for weeks {week}–{last_week}…")
    weeks = list(range(week, last_week + 1)) if in_season else list(range(1, last_week + 1))
    proj = dict(zip(weeks, api.pmap(lambda w: _proj_week(sport, season, w, positions, scoring), weeks)))
    finals = final_games(sport, season, week) if in_season else set()
    # games that have kicked off (in progress or final): those players' lineup status is locked
    started = final_games(sport, season, week, ("in_game", "complete")) if in_season else set()
    if finals:
        n_final = len(finals) // 2 if sport == "nfl" else len({d for d, _ in finals})
        unit = "game" if sport == "nfl" else "game day"
        progress(f"  {n_final} {unit}{'s' if n_final != 1 else ''} this week already final; using actual points")

    logs_by_pid = defaultdict(list)
    for r in logs:
        logs_by_pid[r["pid"]].append(r)

    # injured starters by team, from Sleeper's depth chart
    recent, by_team = {}, defaultdict(list)
    for pid_, rows in logs_by_pid.items():
        cur_rows = [x for x in rows if x["season"] == season]
        recent[pid_] = any(x["week"] >= week - 2 for x in cur_rows)
        if cur_rows:
            by_team[cur_rows[-1]["team"]].append((-_avg([x["pts"] for x in cur_rows]), pid_))
    team_rank = {pid_: i + 1 for rows in by_team.values() for i, (_, pid_) in enumerate(sorted(rows))}
    team_inj = depth.team_injuries(pdb, sport, recent, team_rank)

    rostered = {str(pid) for r in rosters for pid in (r.get("players") or [])}
    universe = set(proj[weeks[0]][0]) | rostered if weeks else rostered

    similar_cache: dict = {}

    def similar(opp):
        if opp not in similar_cache:
            similar_cache[opp] = history.similar_defenses(vectors, season, opp)
        return similar_cache[opp]

    # ---------------------------------------------------------- player profiles
    profiles: dict = {}
    for pid in universe:
        p = pdb.get(pid) or {}
        poss = player_positions(p) & set(rel_pos)
        if not poss and pid in rostered:
            poss = player_positions(p)
        if not poss:
            continue
        lg_logs = logs_by_pid.get(pid, [])
        pos = (lg_logs[-1]["pos"] if lg_logs else None) or history.primary_pos(p, sport) or next(iter(poss))
        cur = [g["pts"] for g in lg_logs if g["season"] == season]
        prev = [g["pts"] for g in lg_logs if g["season"] == season - 1]
        last4 = [g["pts"] for g in lg_logs[-4:]]
        parts = []
        if len(last4) >= 2:
            parts.append((0.40, _avg(last4)))
        if cur:
            parts.append((0.35, _avg(cur)))
        if len(prev) >= 4:
            parts.append((0.25, _avg(prev)))
        own = sum(w * v for w, v in parts) / sum(w for w, _ in parts) if parts else None
        overall = _avg([g["pts"] for g in lg_logs])

        status = p.get("injury_status")
        adj = adj_players.get(pid, {})
        week_mult = _clip(float(adj.get("week_mult", 1.0)), ADJ_MIN, ADJ_MAX)
        ros_mult = _clip(float(adj.get("ros_mult", 1.0)), ADJ_MIN, ADJ_MAX)
        return_week = adj.get("return_week")
        if adj.get("out_for_season"):
            return_week = 999

        def game_value(g, w):
            opp = g["opp"]
            dvp_raw, dg = history.dvp_index(dvp, season, opp, pos)
            dvp_i = _shrink(dvp_raw, dg, 3) if dg else dvp_raw
            sim = similar(opp)
            sim_games = [x["pts"] for x in lg_logs if (x["season"], x["opp"]) in sim]
            split_raw = (_avg(sim_games) / overall) if sim_games and overall else None
            split_i = _shrink(split_raw, len(sim_games), 6)
            h2h_games = [x for x in lg_logs if x["opp"] == opp]
            h2h_raw = (_avg([x["pts"] for x in h2h_games]) / overall) if h2h_games and overall else None
            h2h_i = _shrink(h2h_raw, len(h2h_games), 4)
            mult = _clip(1 + W_DVP * (dvp_i - 1) + W_SPLIT * (split_i - 1) + W_H2H * (h2h_i - 1), MULT_MIN, MULT_MAX)
            sp = g.get("pts")
            if sp is not None and own is not None:
                base = 0.5 * sp + 0.5 * own * mult
            elif own is not None:
                base = own * mult
            else:
                base = (sp or 0) * (1 + (mult - 1) * 0.5)
            return base, {"opp": opp, "date": g.get("date"), "sleeper_proj": sp, "mult": round(mult, 3),
                          "dvp": round(dvp_i, 3), "split": round(split_i, 3), "split_n": len(sim_games),
                          "h2h": round(h2h_i, 3), "h2h_n": len(h2h_games),
                          "h2h_avg": round(_avg([x["pts"] for x in h2h_games]), 2) if h2h_games else None,
                          "sim_avg": round(_avg(sim_games), 2) if sim_games else None}

        def games_for(w):
            by_pid, team_games = proj.get(w, ({}, {}))
            rows = by_pid.get(pid)
            if rows:
                return rows
            team = p.get("team")
            # no Sleeper projection: use the team's schedule with our own estimate only
            return [{"date": d, "opp": o, "pts": None} for d, o in sorted(team_games.get(team, []))] if team else []

        def game_key(g):
            team = g.get("team") or p.get("team")
            return team if sport == "nfl" else (g.get("date"), team)

        def is_final(g):
            return game_key(g) in finals

        week_logs = [x for x in lg_logs if x["season"] == season and x["week"] == week]
        weekly_pts, detail = 0.0, []
        ros_by_week, ros_games = {}, {}
        for w in weeks:
            tot, actual, started_v = 0.0, 0.0, 0.0
            ros_games[w] = len(games_for(w))
            for g in games_for(w):
                v, info = game_value(g, w)
                if w == weeks[0] and is_final(g):
                    # game already played: actual points replace the projection (0 if he didn't play)
                    pts = next((x["pts"] for x in week_logs
                                if x["opp"] == g["opp"] and (sport == "nfl" or x["date"] == g.get("date"))), 0.0)
                    info.update(final=True, actual=pts)
                    actual += pts
                else:
                    tot += v
                if w == weeks[0]:
                    info["started"] = game_key(g) in started or bool(info.get("final"))
                    if info["started"] and not info.get("final"):
                        started_v += v  # in progress: no live stats, so its projection
                    detail.append(info)
            if w == weeks[0]:
                avail = WEEK_AVAIL.get(status, 1.0) if status else 1.0
                if return_week is not None and w < int(return_week):
                    avail = 0.0
                tot = tot * avail * week_mult + actual
                weekly_pts = tot
                locked_pts = started_v * avail * week_mult + actual
            else:
                avail = ROS_AVAIL.get(status, 1.0) if status else 1.0
                if return_week is not None:
                    avail = 0.0 if w < int(return_week) else 0.95
                tot *= avail * ros_mult
            ros_by_week[w] = round(tot, 2)

        sleeper_week = sum((g.get("sleeper_proj") or 0) for g in detail) if detail else None
        profiles[pid] = {
            "pid": pid, "name": _name(p, pid), "pos": pos, "positions": sorted(poss),
            "team": p.get("team"), "age": p.get("age"), "years_exp": p.get("years_exp"),
            "injury": {"status": status, "body_part": p.get("injury_body_part"), "notes": p.get("injury_notes"),
                       "practice": p.get("practice_participation"), "start": p.get("injury_start_date")} if status else None,
            "games_this_season": len(cur), "ppg_season": round(_avg(cur), 2) if cur else None,
            "ppg_last4": round(_avg(last4), 2) if last4 else None,
            "ppg_prev_season": round(_avg(prev), 2) if prev else None,
            "floor": round(sorted(cur)[int(0.2 * (len(cur) - 1))], 2) if len(cur) >= 3 else None,
            "ceiling": round(sorted(cur)[int(0.8 * (len(cur) - 1))], 2) if len(cur) >= 3 else None,
            "stdev": round(statistics.pstdev(cur), 2) if len(cur) >= 3 else None,
            "own_ppg": round(own, 2) if own is not None else None,
            "sleeper_week": round(sleeper_week, 2) if sleeper_week is not None else None,
            "week_games": detail,
            # played or playing this week: his starter/bench status can't change any more
            "locked": any(g["started"] for g in detail),
            "week_pts_locked": round(locked_pts, 2),
            "week_pts": round(weekly_pts, 2),
            "ros_by_week": ros_by_week,
            "ros_games_by_week": ros_games,  # per-game normalisation for moves.robust_weeks
            "ros_pts": round(sum(ros_by_week.values()), 2),
            "teammate_injuries": depth.impacts(pid, poss | {pos}, p.get("team"), team_inj, sport),
            "adjustment": adj or None,
            "rostered": pid in rostered,
        }

    # ---------------------------------------------------------- team scoring
    pos_map = {pid: set(pr["positions"]) for pid, pr in profiles.items()}
    week_points_all = {pid: pr["week_pts"] for pid, pr in profiles.items()}
    week_bench = grading.benchmark_lineup(week_points_all, pos_map, slots, n_teams)
    ros_bench_by_week = {w: grading.benchmark_lineup({pid: pr["ros_by_week"].get(w, 0) for pid, pr in profiles.items()},
                                                     pos_map, slots, n_teams) for w in weeks}
    ros_bench = round(sum(ros_bench_by_week.values()), 2)

    matchups = api.league_matchups(league_id, week) if in_season else []
    mid_by_roster = {m["roster_id"]: m.get("matchup_id") for m in matchups}

    teams = []
    for r in rosters:
        rid = r["roster_id"]
        owner = users.get(r.get("owner_id") or "", {})
        taxi = set(map(str, r.get("taxi") or []))
        reserve = set(map(str, r.get("reserve") or []))
        slot_starters = [str(x) if x and str(x) != "0" else None for x in (r.get("starters") or [])]
        starters = [x for x in slot_starters if x]
        pids = [str(x) for x in (r.get("players") or []) if str(x) in profiles and str(x) not in taxi]
        # Hard rule: games that have started keep each player's actual Sleeper status (see
        # grading.lineup_with_locks). The current week of ROS equals the week score.
        locked = {pid for pid in pids if profiles[pid]["locked"]}
        week_score, ros_score, week_lineup, locked_base = grading.team_value(
            pids, reserve, profiles, pos_map, slots, slot_starters, weeks, sport)
        set_score = round(sum(profiles[s]["week_pts"] for s in starters if s in profiles), 2)
        settings = r.get("settings") or {}
        teams.append({
            "roster_id": rid,
            "team_name": (owner.get("metadata") or {}).get("team_name") or owner.get("display_name") or f"Team {rid}",
            "owner": owner.get("display_name") or "(open)",
            "owner_id": r.get("owner_id"),
            "co_owners": r.get("co_owners") or [],
            "slot_starters": slot_starters,
            "waiver": {"position": settings.get("waiver_position"),
                       "budget_used": int(settings.get("waiver_budget_used") or 0)},
            "record": f"{settings.get('wins', 0)}-{settings.get('losses', 0)}" + (f"-{settings['ties']}" if settings.get("ties") else ""),
            "points_for": round((settings.get("fpts") or 0) + (settings.get("fpts_decimal") or 0) / 100, 2),
            "week_score": week_score,
            "set_lineup_score": set_score,
            "ros_score": round(ros_score, 2),
            "week_lineup": [{"slot": s, "pid": pid, "pts": pts, "locked": sport == "nfl" and pid in locked}
                            for s, pid, pts in week_lineup],
            "locked_bench": sorted(locked - set(starters)),
            "locked_points": locked_base,  # NBA: points already banked by current starters' started games
            "starters": starters,
            "players": pids,
            "reserve": sorted(reserve & set(pids)),
            "taxi": sorted(taxi),
            "matchup_id": mid_by_roster.get(rid),
            "compliance": roster_compliance(lg, r, pdb),
        })

    wk_curve = grading.curved_grades({t["roster_id"]: t["week_score"] for t in teams})
    ros_curve = grading.curved_grades({t["roster_id"]: t["ros_score"] for t in teams})
    by_mid = defaultdict(list)
    for t in teams:
        t["grades"] = {
            "week_abs": grading.absolute_grade(t["week_score"], week_bench),
            "week_curve": wk_curve[t["roster_id"]],
            "ros_abs": grading.absolute_grade(t["ros_score"], ros_bench),
            "ros_curve": ros_curve[t["roster_id"]],
        }
        t["week_index"] = round(t["week_score"] / week_bench, 3) if week_bench else None
        t["ros_index"] = round(t["ros_score"] / ros_bench, 3) if ros_bench else None
        if t["matchup_id"]:
            by_mid[t["matchup_id"]].append(t)
    for pair in by_mid.values():
        if len(pair) == 2:
            a, b = pair
            pa = grading.win_probability(a["week_score"], b["week_score"], sport)
            a["opponent"] = {"roster_id": b["roster_id"], "team_name": b["team_name"], "score": b["week_score"], "win_prob": pa}
            b["opponent"] = {"roster_id": a["roster_id"], "team_name": a["team_name"], "score": a["week_score"], "win_prob": round(1 - pa, 3)}
    teams.sort(key=lambda t: -t["week_score"])

    # ---------------------------------------------------------- research queue
    research = _research_targets(teams, profiles, sport)
    opp_keys = sorted({g["opp"] for pid in research["players"] for g in profiles[pid]["week_games"]})
    defenses = {}
    for o in opp_keys:
        feats = scheme.get((season, o)) or scheme.get((season - 1, o)) or {}
        sims = sorted(t for (s, t) in similar(o) if s == season and t != o)
        ctx = {}
        if scheme:
            from . import schemes_nfl
            ctx = schemes_nfl.context(scheme, season, o)
        defenses[o] = {"scheme": feats, "scheme_context": ctx, "similar_this_season": sims,
                       "injuries": depth.defense_injuries(o, team_inj, sport),
                       "allowed_index": {p: round(history.dvp_index(dvp, season, o, p)[0], 3) for p in style_pos}}

    rostered_profiles = {pid: pr for pid, pr in profiles.items() if pr["rostered"]}
    # unrostered players, for the owner-only roster-move step (moves.py). The report never reads this.
    fa_by_pos = defaultdict(list)
    for pid, pr in profiles.items():
        if not pr["rostered"] and pr["ros_pts"] > 0:
            fa_by_pos[pr["pos"]].append(pr)
    free_agents = {pr["pid"]: pr for rows in fa_by_pos.values()
                   for pr in sorted(rows, key=lambda x: -x["ros_pts"])[:FA_PER_POS]}
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="minutes"),
        "league": {"league_id": league_id, "name": lg["name"], "sport": sport, "season": season, "week": week,
                   "last_week": last_week, "in_season": in_season, "status": lg.get("status"), **summary,
                   "scoring_settings": scoring, "roster_positions": lg.get("roster_positions"),
                   "roster_rules": compliance_rules(lg)},
        "benchmarks": {"week": week_bench, "ros": ros_bench},
        "teams": teams,
        "players": rostered_profiles,
        "free_agents": free_agents,
        "weeks": weeks,
        "defenses": defenses,
        "research": research,
        "warnings": warnings,
        "adjustments": adjustments,
    }


def _research_targets(teams, profiles, sport) -> dict:
    """Players whose outlook most depends on things the data can't see: their own injuries,
    injured starters at positions that change their role, big matchup swings, and each team's
    most important starters."""
    pri: dict = {}
    why: dict = {}
    for t in teams:
        starters = [x["pid"] for x in t["week_lineup"] if x["pid"]]
        key = sorted(starters, key=lambda pid: -profiles[pid]["ros_pts"])[:3]
        for pid in t["players"]:
            pr = profiles[pid]
            score, reasons = 0.0, []
            if pr["injury"]:
                # injuries matter in proportion to how much the player would otherwise contribute
                score += 3 if pid in starters else 1 + min(2, pr["ros_pts"] / 100)
                reasons.append("injury")
            if pid in key:
                score += 2
                reasons.append("key starter")
            open_games = any(not g.get("final") for g in pr["week_games"])
            # starters: any injured starter that shifts their role; bench: only an opening likely to happen
            hits = [e for e in pr.get("teammate_injuries") or []
                    if open_games and (pid in starters or (e["effect"] == "+" and e["likely_out"]))]
            if hits:
                # a starter whose role shifts, or a bench player who may inherit one
                score += 1.5 if pid in starters else 1
                reasons += [f"teammate injury: {e['depth']} {e['name']} ({e['status']}, {e['effect']})" for e in hits[:3]]
            if pid in starters and any(abs(g["mult"] - 1) >= 0.08 and not g.get("final") for g in pr["week_games"]):
                score += 1
                reasons.append("matchup swing")
            if score:
                pri[pid], why[pid] = score, reasons
    ordered = sorted(pri, key=lambda pid: (-pri[pid], -profiles[pid]["ros_pts"]))
    cap = 45 if sport == "nfl" else 40
    return {"players": ordered[:cap], "why": {pid: why[pid] for pid in ordered[:cap]}}
