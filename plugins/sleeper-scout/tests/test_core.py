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
