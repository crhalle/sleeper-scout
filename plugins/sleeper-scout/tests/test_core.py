"""Offline unit tests for scoring, lineup optimisation, grading and style similarity.

Run:  uv run --with pytest --with polars pytest plugins/sleeper-scout/tests -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "sleeper-scout" / "scripts"))

from sleeper_scout import grading, history, league  # noqa: E402


def test_score_is_dot_product_including_defense_buckets():
    scoring = {"rec": 1, "rec_yd": 0.1, "rec_td": 6, "pts_allow_7_13": 4, "sack": 1}
    assert history.score({"rec": 5, "rec_yd": 72, "rec_td": 1, "unused": 99}, scoring) == 18.2
    assert history.score({"pts_allow_7_13": 1, "sack": 3}, scoring) == 7


def test_played_handles_nba_seconds_only_rows():
    assert history.played({"stats": {"sp": 1200}}, "nba")
    assert not history.played({"stats": {"pts": 0}}, "nba")
    assert not history.played({"stats": {"gms_active": 1}}, "nfl")


def test_optimal_lineup_fills_restrictive_slots_first():
    pts = {"rb1": 20, "rb2": 15, "wr1": 18, "te1": 9, "wr2": 14}
    pos = {"rb1": {"RB"}, "rb2": {"RB"}, "wr1": {"WR"}, "te1": {"TE"}, "wr2": {"WR"}}
    total, lineup = grading.optimal_lineup(pts, pos, ["FLEX", "RB", "WR", "TE"])
    by_slot = {s: p for s, p, _ in lineup}
    assert by_slot["RB"] == "rb1" and by_slot["WR"] == "wr1" and by_slot["TE"] == "te1"
    assert by_slot["FLEX"] == "rb2"
    assert total == 62


def test_empty_slot_scores_zero():
    total, lineup = grading.optimal_lineup({"q": 20}, {"q": {"QB"}}, ["QB", "K"])
    assert total == 20
    assert ("K", None, 0.0) in lineup


def test_benchmark_is_average_starter_when_talent_spread_evenly():
    pts = {f"p{i}": float(10 - i) for i in range(6)}  # 10..5
    pos = {k: {"QB"} for k in pts}
    assert grading.benchmark_lineup(pts, pos, ["QB"], 2) == 9.5  # mean of top two


def test_absolute_ladder():
    assert grading.absolute_grade(100, 100) == "C+"
    assert grading.absolute_grade(100 * (1 + grading.ABS_STEP), 100) == "B-"
    assert grading.absolute_grade(200, 100) == "A+"
    assert grading.absolute_grade(10, 100) == "F"


def test_curved_grades_are_symmetric_around_c_plus():
    g = grading.curved_grades({"a": 90, "b": 100, "c": 110})
    assert g["b"] == "C+"
    assert grading.LADDER.index(g["c"]) - grading.LADDER.index("C+") == grading.LADDER.index("C+") - grading.LADDER.index(g["a"])


def test_win_probability_bounds():
    assert grading.win_probability(100, 100, "nfl") == 0.5
    assert grading.win_probability(140, 100, "nfl") > 0.8


def test_similar_defenses_finds_closest_style():
    vecs = {
        (2026, "AAA"): {"x": 1.0, "y": 1.0, "z": 1.0},
        (2026, "BBB"): {"x": 1.1, "y": 0.9, "z": 1.0},
        (2026, "CCC"): {"x": -1.0, "y": -1.0, "z": -1.0},
        (2025, "DDD"): {"x": 0.9, "y": 1.1, "z": 1.0},
        (2025, "EEE"): {"x": -2.0, "y": 0.0, "z": -1.0},
        (2025, "FFF"): {"x": 0.0, "y": -2.0, "z": 0.0},
    }
    sim = history.similar_defenses(vecs, 2026, "AAA", frac=0.5)
    assert {(2026, "BBB"), (2025, "DDD")} <= sim
    assert (2026, "CCC") not in sim


def test_scoring_summary_detects_formats():
    lg = {"sport": "nfl", "scoring_settings": {"rec": 0.5, "bonus_rec_te": 0.5, "pass_td": 6},
          "settings": {"type": 2, "num_teams": 12},
          "roster_positions": ["QB", "RB", "WR", "TE", "SUPER_FLEX", "BN"]}
    s = league.scoring_summary(lg)
    assert s["label"].startswith("Half-PPR") and "TE Premium" in s["tags"]
    assert "Dynasty" in s["tags"] and "Superflex" in s["tags"]
    assert any("6-point passing" in n for n in s["notes"])


def test_scheme_context_labels_relative_to_league():
    from sleeper_scout import schemes_nfl
    scheme = {(2026, f"T{i}"): {"blitz_rate": 0.20 + i * 0.02} for i in range(12)}  # 0.20 .. 0.42
    ctx = schemes_nfl.context(scheme, 2026, "T0")["blitz_rate"]
    assert ctx["label"] == "low" and ctx["percentile"] == 0
    assert schemes_nfl.context(scheme, 2026, "T11")["blitz_rate"]["label"] == "high"
    mid = schemes_nfl.context(scheme, 2026, "T6")["blitz_rate"]
    assert mid["label"] == "average" and abs(mid["league_avg"] - 0.31) < 1e-9
    # falls back to prior season when the team has no current-season data
    assert schemes_nfl.context({(2025, "X"): {"blitz_rate": 0.3}, **{(2025, f"T{i}"): {"blitz_rate": 0.3} for i in range(8)}}, 2026, "X")["blitz_rate"]["season"] == 2025


def _pdb():
    return {
        "1": {"full_name": "Starting QB", "team": "AAA", "position": "QB", "depth_chart_position": "QB",
              "depth_chart_order": 1, "injury_status": "Out"},
        "2": {"full_name": "Wideout", "team": "AAA", "position": "WR", "depth_chart_position": "LWR", "depth_chart_order": 1},
        "3": {"full_name": "Backup RB", "team": "AAA", "position": "RB", "depth_chart_position": "RB", "depth_chart_order": 2},
        "4": {"full_name": "Lead RB", "team": "AAA", "position": "RB", "depth_chart_position": "RB",
              "depth_chart_order": 1, "injury_status": "Questionable"},
        "5": {"full_name": "Old IR", "team": "AAA", "position": "TE", "depth_chart_position": "TE",
              "depth_chart_order": 1, "injury_status": "IR"},
        "6": {"full_name": "Tackle A", "team": "AAA", "position": "OL", "depth_chart_position": "OL", "injury_status": "Out"},
        "7": {"full_name": "Tackle B", "team": "AAA", "position": "OL", "depth_chart_position": "OL", "injury_status": "Doubtful"},
        "8": {"full_name": "Corner", "team": "BBB", "position": "CB", "depth_chart_position": "LCB",
              "depth_chart_order": 1, "injury_status": "Out"},
        "9": {"full_name": "Backup QB", "team": "AAA", "position": "QB", "depth_chart_position": "QB",
              "depth_chart_order": 2, "injury_status": "Questionable"},
    }


def test_team_injuries_keeps_starters_and_new_absences_only():
    from sleeper_scout import depth
    inj = depth.team_injuries(_pdb(), "nfl", recent={"5": False})
    names = {e["name"] for e in inj["AAA"]}
    assert names == {"Starting QB", "Lead RB", "Tackle A", "Tackle B"}  # not the backup QB or the old IR
    assert "Old IR" in {e["name"] for e in depth.team_injuries(_pdb(), "nfl", recent={"5": True})["AAA"]}


def test_qb_injury_ripples_to_receivers_and_rb1_to_backup():
    from sleeper_scout import depth
    inj = depth.team_injuries(_pdb(), "nfl")
    wr = depth.impacts("2", {"WR"}, "AAA", inj, "nfl")
    assert [(e["name"], e["effect"]) for e in wr] == [("Starting QB", "-")]
    rb2 = {e["name"]: e["effect"] for e in depth.impacts("3", {"RB"}, "AAA", inj, "nfl")}
    assert rb2["Lead RB"] == "+" and rb2["Starting QB"] == "-"
    assert any(e["pos"] == "OL" and e["effect"] == "-" for e in depth.impacts("3", {"RB"}, "AAA", inj, "nfl"))
    assert all(e["name"] != "Starting QB" for e in depth.impacts("1", {"QB"}, "AAA", inj, "nfl"))  # never himself
    assert depth.defense_injuries("BBB", inj, "nfl")[0]["helps"] == ["WR"]


def test_nba_star_absence_lifts_every_teammate():
    from sleeper_scout import depth
    pdb = {"a": {"full_name": "Star", "team": "T", "position": "SF", "depth_chart_position": "SF",
                 "depth_chart_order": 1, "injury_status": "Out"}}
    inj = depth.team_injuries(pdb, "nba", team_rank={"a": 1})
    assert depth.impacts("b", {"C"}, "T", inj, "nba")[0]["effect"] == "+"


def _league(**settings):
    return {"roster_positions": ["QB", "WR", "WR", "FLEX", "BN", "BN"],
            "settings": {"reserve_slots": 1, "taxi_slots": 0, "reserve_allow_out": 1, **settings}}


def test_compliant_roster_has_no_issues():
    pdb = {"q": {"position": "QB"}, "w1": {"position": "WR"}, "w2": {"position": "WR"},
           "ir": {"position": "WR", "injury_status": "IR"}}
    roster = {"players": ["q", "w1", "w2", "ir"], "reserve": ["ir"]}
    assert league.roster_compliance(_league(position_limit_wr=2), roster, pdb) == []  # IR WR doesn't count


def test_compliance_flags_size_position_and_ir_rules():
    pdb = {f"w{i}": {"position": "WR", "full_name": f"WR {i}"} for i in range(7)}
    pdb["w5"]["injury_status"] = "Questionable"
    pdb["w6"]["injury_status"] = "Out"
    roster = {"players": list(pdb), "reserve": ["w5", "w6"]}
    issues = league.roster_compliance(_league(position_limit_wr=4), roster, pdb)
    rules = sorted(i["rule"] for i in issues)
    assert rules == ["ir_eligibility", "ir_slots", "position_limit"]
    assert any("WR 5" in i["message"] and "Questionable" in i["message"] for i in issues)  # Out is allowed, Q isn't
    roster = {"players": list(pdb) + ["x"], "reserve": []}
    pdb["x"] = {"position": "QB"}
    assert "roster_size" in {i["rule"] for i in league.roster_compliance(_league(), roster, pdb)}


def test_ir_out_toggle_controls_eligibility():
    pdb = {"a": {"position": "RB", "injury_status": "Out"}}
    roster = {"players": ["a"], "reserve": ["a"]}
    assert league.roster_compliance(_league(), roster, pdb) == []
    assert league.roster_compliance(_league(reserve_allow_out=0), roster, pdb)[0]["rule"] == "ir_eligibility"


def test_started_players_keep_their_lineup_status():
    pts = {"qb": 30, "rbA": 5, "rbB": 25, "rbC": 12}
    pos = {"qb": {"QB"}, "rbA": {"RB"}, "rbB": {"RB"}, "rbC": {"RB"}}
    slots = ["QB", "RB"]
    # rbA started (game kicked off) and rbB played from the bench: neither can move
    total, lineup = grading.optimal_lineup(pts, pos, slots, fixed={1: "rbA"}, exclude={"rbA", "rbB"})
    assert {s: p for s, p, _ in lineup} == {"QB": "qb", "RB": "rbA"} and total == 35
    # a locked bench player is never pulled in, even into an open slot
    total, lineup = grading.optimal_lineup(pts, pos, slots, exclude={"rbB"})
    assert {s: p for s, p, _ in lineup}["RB"] == "rbC"


def test_lineup_with_locks_nfl_locks_the_whole_week():
    pts = {"a": 2.0, "b": 25.0, "c": 10.0}
    pos = {k: {"RB"} for k in pts}
    # a started (scored 2) and b played from the bench (25): only c is still free, but a holds the slot
    total, lineup, base = grading.lineup_with_locks(pts, {"a": 2.0, "b": 25.0, "c": 0.0}, {"a", "b"}, pos, ["RB"], ["a"], "nfl")
    assert lineup == [("RB", "a", 2.0)] and total == 2.0 and base == 0.0


def test_lineup_with_locks_nba_locks_only_started_games():
    pts = {"start": 40.0, "bench": 60.0}           # week totals
    done = {"start": 20.0, "bench": 30.0}          # from games already started
    pos = {k: {"PG"} for k in pts}
    total, lineup, base = grading.lineup_with_locks(pts, done, {"start", "bench"}, pos, ["PG"], ["start"], "nba")
    # starter banks 20; the bench player's 30 doesn't count, but his remaining 30 can be started
    assert base == 20.0 and lineup == [("PG", "bench", 30.0)] and total == 50.0
