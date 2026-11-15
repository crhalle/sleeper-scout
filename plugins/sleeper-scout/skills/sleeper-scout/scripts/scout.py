# /// script
# requires-python = ">=3.10"
# dependencies = ["polars>=1.0"]
# ///
"""Sleeper Scout CLI.

  uv run scout.py leagues <username>                 list the user's NFL + NBA leagues this season
  uv run scout.py analyze <league_id> [--out DIR]    pull data, build profiles, baseline grades
  uv run scout.py plan-research  <league_id> [--batches N]   split the research queue for research agents
  uv run scout.py merge-research <league_id>                 merge research/result-*.json -> adjustments.json
  uv run scout.py report  <league_id> [--out DIR] [--adjustments FILE]
                                                     re-grade with research adjustments, write report.html
  uv run scout.py moves   <league_id> (--roster ID | --owner USERNAME)
                                                     owner-only drops / waiver claims / trades -> moves.json
                                                     (needs this week's finished analysis; never in report.html)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sleeper_scout import api, engine, league, moves, report, research  # noqa: E402


def _out_dir(arg: str | None, league_id: str) -> Path:
    d = Path(arg).expanduser() if arg else Path.home() / "sleeper-scout" / league_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cmd_leagues(a):
    rows = league.find_leagues(a.username, a.season)
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print(f"No NFL or NBA leagues found for {a.username} this season.")
        return
    for r in rows:
        print(f"{r['league_id']}  {r['sport'].upper()} {r['season']}  {r['name']}  "
              f"({r['teams']} teams, {r['format']}, {r['status']})")


def _summary(res: dict) -> str:
    L = res["league"]
    lines = [
        f"# {L['name']} — {L['sport'].upper()} {L['season']}, week {L['week']} (ROS through week {L['last_week']})",
        f"Format: {L['label']} | {' · '.join(L['tags'])} | {L['teams']} teams | Lineup: {L['lineup']} + {L['bench']} BN",
    ]
    lines += [f"  - {n}" for n in L["notes"]]
    b = res["benchmarks"]
    lines.append(f"Benchmark lineup: {b['week']} pts this week, {b['ros']} pts rest of season")
    lines.append("")
    lines.append("Team | Owner | Record | Week pts (abs/curve) | ROS pts (abs/curve) | Matchup")
    for t in res["teams"]:
        g = t["grades"]
        opp = t.get("opponent")
        m = f"vs {opp['team_name']} {t['week_score']:.1f}-{opp['score']:.1f} ({opp['win_prob']:.0%})" if opp else ""
        lines.append(f"{t['team_name']} | {t['owner']} | {t['record']} | {t['week_score']:.1f} ({g['week_abs']}/{g['week_curve']}) | "
                     f"{t['ros_score']:.1f} ({g['ros_abs']}/{g['ros_curve']}) | {m}")
    lines.append("")
    flagged = [t for t in res["teams"] if t.get("compliance")]
    lines.append(f"## Roster compliance: {len(flagged)} of {len(res['teams'])} rosters break a league rule"
                 if flagged else "## Roster compliance: every roster complies")
    for t in flagged:
        for i in t["compliance"]:
            lines.append(f"{t['team_name']} | {i['rule']} | {i['message']}")
    lines.append("")
    lines.append("## Research queue (pid | player | pos | team | this week | why | status)")
    P = res["players"]
    for pid in res["research"]["players"]:
        p = P[pid]
        opp = ", ".join(f"{g['opp']} x{g['mult']:.2f}" for g in p["week_games"]) or "no game"
        inj = p["injury"]
        status = f"{inj['status']} ({inj.get('body_part') or '?'})" if inj else "healthy"
        lines.append(f"{pid} | {p['name']} | {p['pos']} | {p['team']} | {opp} | {', '.join(res['research']['why'][pid])} | {status}")
    if res["defenses"]:
        lines.append("")
        lines.append("## Opposing defenses this week (scheme features, positions they leak to)")
        for o, d in res["defenses"].items():
            leaks = ", ".join(f"{k} {v:.2f}" for k, v in d["allowed_index"].items())
            sch = ", ".join(f"{k} {v:.2f}" for k, v in d["scheme"].items()) if d["scheme"] else "n/a"
            lines.append(f"{o}: allowed-index {leaks} | scheme {sch} | similar: {', '.join(d['similar_this_season'][:5])}")
    for w in res["warnings"]:
        lines.append(f"! {w}")
    return "\n".join(lines)


def cmd_analyze(a):
    adj = json.loads(Path(a.adjustments).read_text()) if a.adjustments else None
    res = engine.run(a.league_id, adj, history_seasons=a.seasons, progress=_log)
    out = _out_dir(a.out, a.league_id)
    (out / "analysis.json").write_text(json.dumps(res, indent=1, default=str))
    print(_summary(res))
    print(f"\nFull data: {out / 'analysis.json'}")
    return res, out


def cmd_report(a):
    res, out = cmd_analyze(a)
    html = report.render(res)
    path = out / "report.html"
    path.write_text(html, encoding="utf-8")
    print(f"Report: {path}")


def _load_analysis(a, missing: str | None = None) -> tuple[dict, Path]:
    out = _out_dir(a.out, a.league_id)
    p = out / "analysis.json"
    if not p.exists():
        raise SystemExit(missing or f"{p} not found. Run `analyze {a.league_id}` first.")
    return json.loads(p.read_text()), out


def cmd_plan_research(a):
    analysis, out = _load_analysis(a)
    plan = research.plan_batches(analysis, n=a.batches)
    rdir = out / "research"
    rdir.mkdir(exist_ok=True)
    for old in rdir.glob("*.json"):  # results from a previous run would be merged by mistake
        old.unlink()
    sport = analysis["league"]["sport"]
    print(f"mode: {plan['mode']}  sport: {sport}  agent: sleeper-scout:{sport}-researcher  "
          f"queue: {len(analysis['research']['players'])} players")
    for b in plan["batches"]:
        bp = rdir / f"batch-{b['batch']}.json"
        b["result_path"] = str(rdir / f"result-{b['batch']}.json")
        bp.write_text(json.dumps(b, indent=1, default=str))
        print(f"batch {b['batch']}: {bp}  ({len(b['players'])} players; {', '.join(b['games'])})")


def cmd_merge_research(a):
    analysis, out = _load_analysis(a)
    results = []
    for rp in sorted((out / "research").glob("result-*.json")):
        try:
            results.append(json.loads(rp.read_text()))
        except json.JSONDecodeError as e:
            print(f"! {rp.name}: invalid JSON ({e}); skipped")
    if not results:
        raise SystemExit(f"No research results in {out / 'research'}. Run the research step first.")
    adj_path = out / "adjustments.json"
    existing = json.loads(adj_path.read_text()) if adj_path.exists() else None
    adj, warnings, missing = research.merge(results, analysis, existing)
    adj_path.write_text(json.dumps(adj, indent=1))
    changed = sum(1 for e in adj["players"].values() if any(k in e for k in ("week_mult", "ros_mult", "return_week", "out_for_season")))
    print(f"Merged {len(results)} result files: {len(adj['players'])} player profiles "
          f"({changed} with adjustments), {len(adj['defenses'])} defense notes -> {adj_path}")
    for w in warnings:
        print(f"! {w}")
    if missing:
        names = ", ".join(f"{pid} {analysis['players'][pid]['name']}" for pid in missing)
        print(f"No result for {len(missing)} queued players: {names}")


GATE_MSG = ("Roster moves need this week's finished league analysis. Run the weekly analysis first "
            "(`/sleeper-scout:scout`: analyze, research, merge-research, report), then ask again.")


def moves_gate(analysis: dict, current_week: int | None) -> str | None:
    """Why roster moves aren't available yet, or None. They need the researched analysis
    (report run with merged adjustments) for the current week."""
    adj = analysis.get("adjustments") or {}
    if not (adj.get("players") or adj.get("league_notes")):
        return GATE_MSG + " (No research adjustments in analysis.json: `report` hasn't been run with them.)"
    if "free_agents" not in analysis:
        return GATE_MSG + " (analysis.json predates roster-move support.)"
    L = analysis["league"]
    if L.get("in_season") and current_week is not None and int(L["week"]) != int(current_week):
        return GATE_MSG + f" (The analysis is for week {L['week']}; Sleeper is now on week {current_week}.)"
    return None


def _print_moves(m: dict) -> None:
    T, L = m["team"], m["league"]
    print(f"# Roster moves for {T['team_name']} ({T['owner']}) — {L['name']}, week {L['week']} (private; not in the report)")
    print(f"Week {T['week_score']:.1f} pts, ROS {T['ros_score']:.1f} pts | open roster spots: {T['open_spots']} | "
          f"waivers: {L['waiver']}" + (f", FAAB left ${T['faab_remaining']}" if T["faab_remaining"] is not None else "")
          + (f", waiver position {T['waiver_position']}" if T.get("waiver_position") else ""))
    for i in T["compliance"]:
        print(f"! {i['rule']}: {i['message']}")

    def nm(p):
        return f"{p['name']} ({p['pos']}, {p['team'] or 'FA'}{', ' + p['status'] if p.get('status') else ''})"
    print("\n## Drop candidates (lowest value to your roster first; pid | player | week loss | ROS loss | tags)")
    for d in m["drops"]:
        print(f"{d['pid']} | {nm(d)} | {d['week_loss']:.1f} | {d['ros_loss']:.1f} | {', '.join(d['tags']) or '-'}")
    print("\n## Waiver claims (add | drop | week gain | ROS gain | FAAB bid)")
    for w in m["waivers"] or []:
        bid = f"${w['faab_bid'][0]}-{w['faab_bid'][1]}" if w.get("faab_bid") else "-"
        drop = nm(w["drop"]) if w["drop"] else "(open spot)"
        print(f"{w['add']['pid']} {nm(w['add'])} | {drop} | {w['week_gain']:+.1f} | {w['ros_gain']:+.1f} "
              f"({w['ros_gain_pct']:.1%}) | {bid}")
    if not m["waivers"]:
        print("(no free agent improves this roster)")
    print("\n## Trade ideas (partner | you give | you get | your ROS gain | their ROS gain)")
    for t in m["trades"]:
        extra = []
        if t["my_drop"]:
            extra.append(f"you drop {nm(t['my_drop'])}")
        if t["their_drop"]:
            extra.append(f"they drop {nm(t['their_drop'])}")
        print(f"{t['partner']['team_name']} ({t['partner']['owner']}) | {', '.join(nm(p) for p in t['give'])} | "
              f"{', '.join(nm(p) for p in t['get'])} | {t['my_gain_ros']:+.1f} (week {t['my_gain_week']:+.1f}) | "
              f"{t['their_gain_ros']:+.1f}" + (f" | {'; '.join(extra)}" if extra else ""))
    if not m["trades"]:
        print("(no trade ideas)")
    for n in m["notes"]:
        print(f"! {n}")
    if m.get("outliers"):
        print(f"({len(m['outliers'])} future-week projection outlier(s) capped league-wide; see moves.json)")


def cmd_moves(a):
    analysis, out = _load_analysis(a, missing=GATE_MSG)
    L = analysis["league"]
    try:
        st = api.state(L["sport"])
        current = max(1, int(st.get("display_week") or st.get("week") or 1))
    except Exception:
        current = None
    why = moves_gate(analysis, current)
    if why:
        raise SystemExit(why)
    user_id = None
    if a.owner:
        u = api.user(a.owner)
        if not u:
            raise SystemExit(f"No Sleeper user found for '{a.owner}'.")
        user_id = u["user_id"]
    team = moves.find_team(analysis, roster_id=a.roster, user_id=user_id)
    now_rostered = None
    try:  # skip free agents claimed since the analysis ran
        rosters = api.league_rosters(L["league_id"])
        now_rostered = {str(p) for r in rosters for p in (r.get("players") or [])}
        mine = next((r for r in rosters if r["roster_id"] == team["roster_id"]), None)
        if mine is not None:
            cur = {str(p) for p in mine.get("players") or []} - set(map(str, mine.get("taxi") or []))
            before = set(team["players"])
            if before - cur or (cur - before) & (set(analysis["players"]) | set(analysis["free_agents"])):
                print("! Your roster changed since the analysis ran; re-run `report` for exact numbers.")
    except Exception:
        pass
    res = moves.recommend(analysis, team["roster_id"], now_rostered)
    path = out / "moves.json"
    path.write_text(json.dumps(res, indent=1, default=str))
    _print_moves(res)
    print(f"\nFull data (private): {path}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="scout", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("leagues", help="list a user's leagues")
    p.add_argument("username")
    p.add_argument("--season")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_leagues)
    for name, fn in (("analyze", cmd_analyze), ("report", cmd_report)):
        p = sub.add_parser(name)
        p.add_argument("league_id")
        p.add_argument("--out", help="output directory (default ~/sleeper-scout/<league_id>)")
        p.add_argument("--adjustments", help="research adjustments JSON")
        p.add_argument("--seasons", type=int, default=3, help="seasons of history to use (default 3)")
        p.set_defaults(fn=fn)
    p = sub.add_parser("plan-research", help="split the research queue into batches for research agents")
    p.add_argument("league_id")
    p.add_argument("--out")
    p.add_argument("--batches", type=int, help="force a batch count (1 = inline)")
    p.set_defaults(fn=cmd_plan_research)
    p = sub.add_parser("merge-research", help="merge research results into adjustments.json")
    p.add_argument("league_id")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_merge_research)
    p = sub.add_parser("moves", help="owner-only roster moves (drops, waiver claims, trades) for one team")
    p.add_argument("league_id")
    p.add_argument("--out")
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--roster", type=int, help="roster_id of the owner's team")
    who.add_argument("--owner", help="the owner's Sleeper username")
    p.set_defaults(fn=cmd_moves)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
