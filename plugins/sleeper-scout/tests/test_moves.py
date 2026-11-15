"""Offline tests for owner-only roster-move recommendations (moves.py) and their privacy."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "sleeper-scout" / "scripts"))

import scout  # noqa: E402
from sleeper_scout import moves, report  # noqa: E402

WEEKS = [10, 11, 12]


def _p(pid, pos, pts, locked=False, status=None, rostered=True):
    return {"pid": pid, "name": pid.upper(), "pos": pos, "positions": [pos], "team": "KC",
            "injury": {"status": status} if status else None, "week_pts": pts, "week_pts_locked": pts if locked else 0.0,
            "locked": locked, "ros_by_week": {str(w): pts for w in WEEKS}, "ros_pts": pts * len(WEEKS),
            "week_games": [], "rostered": rostered, "own_ppg": pts, "sleeper_week": pts, "adjustment": None,
            "teammate_injuries": [], "ppg_season": pts, "ppg_last4": pts, "floor": None, "ceiling": None}


def _team(rid, pids, reserve=(), compliance=()):
    return {"roster_id": rid, "team_name": f"Team {rid}", "owner": f"o{rid}", "owner_id": f"u{rid}", "co_owners": [],
            "players": list(pids), "reserve": list(reserve), "slot_starters": [], "taxi": [],
            "compliance": list(compliance), "waiver": {"position": 3, "budget_used": 20},
            "week_score": 0, "ros_score": 0, "week_lineup": [], "starters": [], "grades": {}}


def _analysis(players, teams, free_agents=(), limits=None, spots=4, **league):
    L = {"league_id": "1", "name": "Test", "sport": "nfl", "season": 2026, "week": 10, "last_week": 12,
         "in_season": True, "label": "PPR", "tags": ["Redraft"], "teams": len(teams), "lineup": "", "bench": 1,
         "notes": [], "waiver": "FAAB", "waiver_type": 2, "waiver_budget": 100, "trade_deadline": 12,
         "playoff_week_start": 15, "roster_positions": ["QB", "RB", "WR", "BN"],
         "roster_rules": {"roster_spots": spots, "ir_slots": 1, "ir_statuses": ["IR", "Out"], "taxi_slots": 0,
                          "position_limits": limits or {}}, **league}
    return {"generated_at": "now", "league": L, "weeks": WEEKS, "benchmarks": {"week": 50, "ros": 150},
            "teams": teams, "players": {p["pid"]: p for p in players},
            "free_agents": {p["pid"]: p for p in free_agents}, "defenses": {}, "research": {"players": [], "why": {}},
            "warnings": [], "adjustments": {"players": {}, "league_notes": "notes"}}


def _base_league(**kw):
    players = [_p("qa", "QB", 20), _p("ra", "RB", 15), _p("wa", "WR", 14), _p("rb2", "RB", 13),  # team 1: RB surplus
               _p("qb", "QB", 19), _p("rb", "RB", 8), _p("wb", "WR", 16), _p("wb2", "WR", 15)]  # team 2: WR surplus
    teams = [_team(1, ["qa", "ra", "wa", "rb2"]), _team(2, ["qb", "rb", "wb", "wb2"])]
    return _analysis(players, teams, **kw)


def test_drop_is_lowest_marginal_and_locked_players_are_never_dropped():
    a = _base_league()
    a["players"]["rb2"]["locked"] = True  # his game kicked off: Sleeper won't drop him
    res = moves.recommend(a, 1)
    pids = [d["pid"] for d in res["drops"]]
    assert "rb2" not in pids
    losses = [d["ros_loss"] for d in res["drops"]]
    assert losses == sorted(losses) and pids[0] == "ra"  # rb2 locked, so the RB1 slot's backup is gone


def test_waiver_upgrade_beats_depth_add_and_gets_faab_hint():
    fa = [_p("wfa", "WR", 25, rostered=False), _p("rfa", "RB", 9, rostered=False)]
    res = moves.recommend(_base_league(free_agents=fa), 1)
    top = res["waivers"][0]
    assert top["add"]["pid"] == "wfa" and top["drop"]["pid"] == "rb2"  # bench RB makes room for a WR1
    assert top["ros_gain"] == (25 - 14) * 3
    assert all(w["add"]["pid"] != "rfa" for w in res["waivers"])  # depth that never starts adds nothing
    assert res["team"]["faab_remaining"] == 80 and top["faab_bid"][0] >= 20


def test_position_limit_blocks_illegal_add():
    fa = [_p("wfa", "WR", 25, rostered=False)]
    a = _base_league(free_agents=fa, limits={"WR": 1})
    res = moves.recommend(a, 1)
    # the only legal way to add a WR is dropping the current WR
    assert all(w["drop"]["pid"] == "wa" for w in res["waivers"] if w["add"]["pid"] == "wfa")


def test_must_drop_when_roster_is_over_size():
    a = _base_league(spots=3)
    a["teams"][0]["compliance"] = [{"rule": "roster_size", "message": "over", "pids": []}]
    res = moves.recommend(a, 1)
    assert [d["pid"] for d in res["drops"] if "must_drop" in d["tags"]] == ["rb2"]


def test_ir_instead_of_drop_when_eligible_and_slot_free():
    a = _base_league()
    a["players"]["rb2"]["injury"] = {"status": "Out"}
    res = moves.recommend(a, 1)
    assert "ir_instead" in next(d for d in res["drops"] if d["pid"] == "rb2")["tags"]


def test_fair_trade_from_positional_surplus_is_found():
    res = moves.recommend(_base_league(), 1)
    t = res["trades"][0]
    assert t["partner"]["roster_id"] == 2
    assert "rb2" in [p["pid"] for p in t["give"]] and [p["pid"] for p in t["get"]] in (["wb"], ["wb2"])
    assert t["my_gain_ros"] > 0 and t["their_gain_ros"] >= 0


def test_lopsided_trade_is_rejected():
    a = _base_league()
    ctx = moves.Ctx(a)
    their_base = ctx.value(a["teams"][1], a["teams"][1]["players"])[1]
    res = moves.recommend(a, 1)
    assert res["trades"]
    for t in res["trades"]:
        assert t["their_gain_ros"] >= -moves.TRADE_TOL * their_base
        assert [p["pid"] for p in t["get"]] != ["qb"]  # nobody hands over their only QB for a bench player


def test_two_for_one_forces_receiver_to_drop():
    players = [_p("q1", "QB", 20), _p("r1", "RB", 10), _p("w1", "WR", 9), _p("w1b", "WR", 8),
               _p("q2", "QB", 18), _p("r2", "RB", 30), _p("r2b", "RB", 25), _p("w2", "WR", 2)]
    teams = [_team(1, ["q1", "r1", "w1", "w1b"]), _team(2, ["q2", "r2", "r2b", "w2"])]
    a = _analysis(players, teams)
    ctx = moves.Ctx(a)
    marg = moves.marginals(ctx, teams[1])
    roster, dropped = moves._apply(ctx, teams[1], ("r2b",), ("w1", "w1b"), marg)
    assert len(roster) == 4 and dropped == ["w2"]


def test_partner_never_gives_up_a_star_for_a_bench_upgrade():
    # team 2's RB2 (30 pts) is stuck on the bench behind r2; its marginal value to them is ~0, but no
    # owner hands a 30-point back over for a 1-point kicker-style upgrade
    players = [_p("q1", "QB", 20), _p("r1", "RB", 10), _p("w1", "WR", 9), _p("w1b", "WR", 3),
               _p("q2", "QB", 18), _p("r2", "RB", 31), _p("r2b", "RB", 30), _p("w2", "WR", 2)]
    teams = [_team(1, ["q1", "r1", "w1", "w1b"]), _team(2, ["q2", "r2", "r2b", "w2"])]
    res = moves.recommend(_analysis(players, teams), 1)
    assert all("r2b" not in [p["pid"] for p in t["get"]] or len(t["give"]) == 2 for t in res["trades"])
    assert not any([p["pid"] for p in t["give"]] == ["w1b"] for t in res["trades"])


def test_no_trades_after_deadline():
    res = moves.recommend(_base_league(trade_deadline=9), 1)
    assert res["trades"] == [] and any("deadline" in n for n in res["notes"])


def test_gate_requires_researched_analysis_for_current_week():
    a = _base_league()
    assert scout.moves_gate(a, 10) is None
    assert "week 10" in scout.moves_gate(a, 11)
    a["adjustments"] = {}
    assert "Run the weekly analysis first" in scout.moves_gate(a, 10)
    b = _base_league()
    del b["free_agents"]
    assert scout.moves_gate(b, 10)


def test_report_never_shows_free_agents_or_moves():
    fa = [_p("wfa", "WR", 25, rostered=False)]
    a = _base_league(free_agents=fa)
    for t in a["teams"]:
        t.update(record="1-0", points_for=0, week_index=1, ros_index=1, set_lineup_score=0, locked_bench=[],
                 locked_points=0, matchup_id=None,
                 grades={"week_abs": "C", "week_curve": "C", "ros_abs": "C", "ros_curve": "C"})
    a["league"].update(teams=2, roster_rules=a["league"]["roster_rules"])
    with_fa = report.render(a)
    a["free_agents"] = {}
    assert report.render(a) == with_fa
    assert "WFA" not in with_fa


# ------------------------------------------------------------------ projection outliers

def _weeks(pr, vals, games=None):
    pr["ros_by_week"] = {str(w): v for w, v in zip(WEEKS_LONG, vals)}
    pr["ros_pts"] = sum(vals)
    if games:
        pr["ros_games_by_week"] = {str(w): n for w, n in zip(WEEKS_LONG, games)}
    return pr


WEEKS_LONG = [10, 11, 12, 13, 14, 15]


def test_single_week_spike_is_capped_and_input_untouched():
    k = _weeks(_p("k1", "K", 7), [7, 7, 7, 7, 7, 41.5])
    capped, flags = moves.robust_weeks(k, 10)
    assert capped["15"] == 14.0 and flags[0]["week"] == 15 and flags[0]["raw"] == 41.5
    assert k["ros_by_week"]["15"] == 41.5  # caller's profile isn't mutated


def test_current_week_is_never_capped():
    p = _weeks(_p("r1", "RB", 8), [40, 8, 8, 8, 8, 8])  # week 10 = actual big game
    capped, flags = moves.robust_weeks(p, 10)
    assert capped["10"] == 40 and not flags


def test_nba_game_count_is_normalised():
    p = _weeks(_p("g1", "PG", 40), [80, 80, 160, 80, 120, 80], games=[2, 2, 4, 2, 3, 2])
    assert moves.robust_weeks(p, 10)[1] == []


def test_few_weeks_fall_back_to_own_ppg_and_no_reference_means_no_cap():
    p = _weeks(_p("r1", "RB", 8), [8, 0, 0, 0, 0, 30])
    capped, flags = moves.robust_weeks(p, 10)
    assert capped["15"] == 16.0 and flags  # own_ppg 8 x 2
    p["own_ppg"] = None
    assert moves.robust_weeks(p, 10) == (p["ros_by_week"], [])


def test_outlier_kicker_no_longer_blocks_a_better_waiver_kicker():
    players = [_p("qa", "QB", 20), _p("ra", "RB", 15), _p("wa", "WR", 14), _p("ka", "K", 7)]
    teams = [_team(1, ["qa", "ra", "wa", "ka"]), _team(2, [])]
    fa = [_p("kfa", "K", 9, rostered=False)]
    a = _analysis(players, teams, free_agents=fa)
    a["league"]["roster_positions"] = ["QB", "RB", "WR", "K", "BN"]
    a["weeks"] = WEEKS_LONG
    a["league"]["last_week"] = 15
    _weeks(a["players"]["ka"], [7, 7, 7, 7, 7, 41.5])
    _weeks(a["free_agents"]["kfa"], [9] * 6)
    res = moves.recommend(a, 1)
    assert res["waivers"] and res["waivers"][0]["add"]["pid"] == "kfa" and res["waivers"][0]["drop"]["pid"] == "ka"
    assert res["outliers"][0]["pid"] == "ka" and any("Capped KA" in n for n in res["notes"])
    assert a["players"]["ka"]["ros_by_week"]["15"] == 41.5


# ------------------------------------------------------------------ review fixes

def test_span_without_current_week_uses_that_weeks_projection_not_this_weeks():
    a = _base_league()
    a["players"]["ra"]["week_pts"] = 100  # this week's points must not leak into a next-week valuation
    ctx = moves.Ctx(a)
    team = a["teams"][0]
    w, ros = ctx.value(team, team["players"], [11, 12])
    assert w == 20 + 15 + 14 and ros == 2 * (20 + 15 + 14)


def test_gate_rejects_preseason_or_other_season_and_accepts_defense_only_research():
    a = _base_league()
    a["league"]["in_season"] = False
    assert "before the season" in scout.moves_gate(a, 10, {"season_type": "regular", "season": "2026"})
    a["league"]["in_season"] = True
    assert "season" in scout.moves_gate(a, 10, {"season_type": "regular", "season": "2027"})
    a["adjustments"] = {"players": {}, "defenses": {"KC": {"note": "x"}}}
    assert scout.moves_gate(a, 10, {"season_type": "regular", "season": "2026"}) is None


def test_locked_nfl_free_agent_is_valued_from_next_week():
    fa = [_p("wfa", "WR", 25, locked=True, rostered=False)]
    res = moves.recommend(_base_league(free_agents=fa), 1)
    top = res["waivers"][0]
    assert top["add"]["pid"] == "wfa" and top["after_lock"] and top["week_gain"] == 0
    assert top["ros_gain"] == (25 - 14) * 2  # weeks 11-12 only


def test_ir_eligible_player_goes_to_ir_instead_of_being_dropped_for_a_claim():
    fa = [_p("wfa", "WR", 25, rostered=False)]
    a = _base_league(free_agents=fa)
    a["players"]["rb2"]["injury"] = {"status": "Out"}
    res = moves.recommend(a, 1)
    top = res["waivers"][0]
    assert top["add"]["pid"] == "wfa" and top["drop"] is None and top["move_to_ir"]["pid"] == "rb2"


def test_trade_needing_more_cuts_than_possible_is_skipped_and_all_cuts_listed():
    players = [_p("q1", "QB", 20), _p("r1", "RB", 10), _p("w1", "WR", 9), _p("w1b", "WR", 8),
               _p("q2", "QB", 18), _p("r2", "RB", 30), _p("r2b", "RB", 25), _p("w2", "WR", 2)]
    teams = [_team(1, ["q1", "r1", "w1", "w1b"]), _team(2, ["q2", "r2", "r2b", "w2"])]
    a = _analysis(players, teams, spots=3)  # team 2 already one over, so a 2-for-1 needs two cuts
    ctx = moves.Ctx(a)
    marg = moves.marginals(ctx, teams[1])
    roster, dropped = moves._apply(ctx, teams[1], ("r2b",), ("w1", "w1b"), marg)
    assert len(roster) == 3 and len(dropped) == 2
    for p in ("q2", "r2", "w2"):
        a["players"][p]["locked"] = True  # nobody left who can be cut
    assert moves._apply(moves.Ctx(a), teams[1], ("r2b",), ("w1", "w1b"), marg) is None


def test_replacement_level_ignores_free_agents_claimed_since_analysis():
    fa = [_p("wfa", "WR", 25, rostered=False), _p("wfb", "WR", 5, rostered=False)]
    a = _base_league(free_agents=fa)
    assert moves.Ctx(a).replacement["WR"] == 75
    assert moves.Ctx(a, now_rostered={"wfa"}).replacement["WR"] == 15
