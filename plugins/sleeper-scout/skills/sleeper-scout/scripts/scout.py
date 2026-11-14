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
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sleeper_scout import engine, league, report, research  # noqa: E402


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


def _load_analysis(a) -> tuple[dict, Path]:
    out = _out_dir(a.out, a.league_id)
    p = out / "analysis.json"
    if not p.exists():
        raise SystemExit(f"{p} not found. Run `analyze {a.league_id}` first.")
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
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
