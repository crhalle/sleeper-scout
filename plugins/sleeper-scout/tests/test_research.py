"""Offline tests for research batching and merging."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "sleeper-scout" / "scripts"))

from sleeper_scout import research  # noqa: E402


def _analysis(games, sport="nfl"):
    """games: list of (team, opp, n_players). Builds a minimal analysis dict."""
    players, queue = {}, []
    for team, opp, n in games:
        for i in range(n):
            pid = f"{team}{i}"
            wg = [{"opp": opp, "mult": 1.0}] if opp else []
            players[pid] = {"name": pid, "pos": "WR", "team": team, "week_games": wg, "injury": None}
            queue.append(pid)
    return {
        "league": {"sport": sport, "name": "L", "season": 2026, "week": 10},
        "players": players,
        "research": {"players": queue, "why": {pid: ["key starter"] for pid in queue}},
        "defenses": {opp: {"allowed_index": {}} for _, opp, _ in games if opp},
    }


def test_small_queue_stays_inline():
    plan = research.plan_batches(_analysis([("KC", "BUF", 3), ("BUF", "KC", 3)]))
    assert plan["mode"] == "inline" and len(plan["batches"]) == 1


def test_both_teams_of_a_game_share_a_batch():
    games = [("KC", "BUF", 4), ("BUF", "KC", 4), ("DET", "GB", 4), ("GB", "DET", 4), ("SF", "DAL", 3), ("DAL", "SF", 3)]
    plan = research.plan_batches(_analysis(games))
    assert plan["mode"] == "parallel"
    for b in plan["batches"]:
        teams = {p["team"] for p in b["players"]}
        for a, o, _ in games:
            assert (a in teams) == (o in teams)
    # defenses attached for every opponent in the batch
    for b in plan["batches"]:
        assert set(b["defenses"]) == {g["opp"] for p in b["players"] for g in p["week_games"]}


def test_batch_count_is_clamped():
    games = [(f"T{i}", f"O{i}", 3) for i in range(30)]  # 90 players, 30 games
    plan = research.plan_batches(_analysis(games))
    assert len(plan["batches"]) == research.MAX_BATCHES
    sizes = [len(b["players"]) for b in plan["batches"]]
    assert max(sizes) - min(sizes) <= 3  # balanced


def test_nba_groups_by_team():
    a = _analysis([("LAL", "BOS", 7), ("BOS", "LAL", 7)], sport="nba")
    plan = research.plan_batches(a)
    assert plan["mode"] == "parallel"
    assert sorted(g for b in plan["batches"] for g in b["games"]) == ["BOS", "LAL"]


def test_merge_validates_and_resolves_duplicates():
    a = _analysis([("KC", "BUF", 2)])
    r1 = {"players": {
        "KC0": {"week_mult": 3, "profile": "p", "sources": ["https://a"]},       # clamped
        "KC1": {"week_mult": 0, "return_week": 14, "profile": "no source"},     # unsourced -> neutral
        "ZZZ": {"profile": "not in league"}},                                   # dropped
        "defenses": {"BUF": {"note": "Two-high shell.", "sources": ["https://x"]}}}
    r2 = {"players": {"KC0": {"week_mult": 0.9, "profile": "better", "sources": ["https://a", "https://b"]}},
          "defenses": {"BUF": {"note": "CB1 out.", "sources": ["https://y"]}}}
    adj, warnings, missing = research.merge([r1, r2], a, existing={"league_notes": "keep me"})
    assert adj["players"]["KC0"]["week_mult"] == 0.9 and adj["players"]["KC0"]["profile"] == "better"
    assert "week_mult" not in adj["players"]["KC1"] and "return_week" not in adj["players"]["KC1"]
    assert adj["players"]["KC1"]["profile"] == "no source"
    assert "ZZZ" not in adj["players"]
    assert adj["defenses"]["BUF"]["note"] == "Two-high shell. CB1 out."
    assert adj["league_notes"] == "keep me"
    assert any("clamped" in w for w in warnings) and any("no sources" in w for w in warnings)
    assert missing == []
