"""Render an analysis result as a single self-contained HTML page.

The page has no external scripts, renders in light and dark themes, and is written as a
body fragment (title + style + content) so it can be opened locally or published as a
claude.ai Artifact unchanged.
"""
from __future__ import annotations

from html import escape

from .grading import ABS_STEP, CURVE_STEP, LADDER

CSS = """
/* Layout: a scoreboard header, one grade board, then collapsible team cards. */
:root {
  --bg: #f5f6f8; --surface: #ffffff; --ink: #141a24; --muted: #5d6675; --line: #dde1e7;
  --accent: #2552c4; --accent-soft: #e6ecfb;
  --g-a: #127a4a; --g-a-bg: #dcf3e6; --g-b: #1c6b8c; --g-b-bg: #dcedf5; --g-c: #7a5d00; --g-c-bg: #f8eecb;
  --g-d: #a8490f; --g-d-bg: #fbe3d2; --g-f: #b3262e; --g-f-bg: #f9dcde;
  --up: #127a4a; --down: #b3262e;
  --display: "Barlow Condensed", "Arial Narrow", "Roboto Condensed", sans-serif;
  --body: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #0f131a; --surface: #171c25; --ink: #e8ebf1; --muted: #98a1b0; --line: #2a313d;
    --accent: #8fb0ff; --accent-soft: #1d2740;
    --g-a: #6fdca2; --g-a-bg: #133324; --g-b: #7cc8e6; --g-b-bg: #132d38; --g-c: #e8c65a; --g-c-bg: #332b10;
    --g-d: #f2a067; --g-d-bg: #3a2414; --g-f: #f48a8f; --g-f-bg: #3d1a1d; --up: #6fdca2; --down: #f48a8f;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #0f131a; --surface: #171c25; --ink: #e8ebf1; --muted: #98a1b0; --line: #2a313d;
  --accent: #8fb0ff; --accent-soft: #1d2740;
  --g-a: #6fdca2; --g-a-bg: #133324; --g-b: #7cc8e6; --g-b-bg: #132d38; --g-c: #e8c65a; --g-c-bg: #332b10;
  --g-d: #f2a067; --g-d-bg: #3a2414; --g-f: #f48a8f; --g-f-bg: #3d1a1d; --up: #6fdca2; --down: #f48a8f;
}
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--ink); font: 15px/1.5 var(--body); margin: 0;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.wrap { max-width: 1120px; margin: 0 auto; padding-block: 28px 64px; display: grid; gap: 28px; }
h1, h2, h3 { font-family: var(--display); font-weight: 700; letter-spacing: .01em; text-wrap: balance; margin: 0; line-height: 1.1; }
h1 { font-size: clamp(2rem, 5vw, 3rem); }
h2 { font-size: 1.6rem; }
h3 { font-size: 1.25rem; }
p { margin: 0; max-width: 70ch; }
a { color: var(--accent); }
a:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
.eyebrow { font-family: var(--display); text-transform: uppercase; letter-spacing: .12em; font-weight: 600; color: var(--muted); font-size: .9rem; }
.muted { color: var(--muted); }
.small { font-size: .85rem; }
header { display: grid; gap: 10px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: .78rem; font-weight: 600; padding: 2px 9px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); }
.section { display: grid; gap: 14px; }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; white-space: nowrap; }
th { font-family: var(--display); text-transform: uppercase; letter-spacing: .08em; font-size: .8rem; color: var(--muted); font-weight: 600; }
td.num, th.num { text-align: right; }
tbody tr:last-child td { border-bottom: 0; }
.group th { border-bottom: 0; padding-bottom: 0; }
.team-cell { white-space: normal; min-width: 180px; }
.team-name { font-weight: 650; }
.grade { display: inline-block; min-width: 2.4em; text-align: center; font-family: var(--display); font-weight: 700; font-size: 1.05rem;
  padding: 1px 6px; border-radius: 6px; }
.g-a { color: var(--g-a); background: var(--g-a-bg); } .g-b { color: var(--g-b); background: var(--g-b-bg); }
.g-c { color: var(--g-c); background: var(--g-c-bg); } .g-d { color: var(--g-d); background: var(--g-d-bg); }
.g-f { color: var(--g-f); background: var(--g-f-bg); }
.pair { display: inline-flex; gap: 4px; align-items: center; }
.bar { display: inline-block; width: 64px; height: 6px; border-radius: 3px; background: var(--line); vertical-align: middle; overflow: hidden; }
.bar > i { display: block; height: 100%; background: var(--accent); }
.up { color: var(--up); } .down { color: var(--down); }
.status { font-size: .75rem; font-weight: 700; padding: 1px 7px; border-radius: 999px; }
.st-q { color: var(--g-c); background: var(--g-c-bg); } .st-o { color: var(--g-f); background: var(--g-f-bg); }
.notes { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; display: grid; gap: 8px; }
.ladder { display: flex; flex-wrap: wrap; gap: 4px; }
.method { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
.method > div { display: grid; gap: 6px; align-content: start; }
details.team { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; }
details.team > summary { cursor: pointer; list-style: none; padding: 14px 16px; display: flex; flex-wrap: wrap; gap: 8px 18px; align-items: baseline; }
details.team > summary::-webkit-details-marker { display: none; }
details.team > summary::before { content: "▸"; color: var(--muted); }
details.team[open] > summary::before { content: "▾"; }
details.team > .body { padding: 0 16px 16px; display: grid; gap: 12px; }
.summary-grades { display: inline-flex; gap: 14px; flex-wrap: wrap; margin-left: auto; }
.summary-grades span.lbl { font-size: .75rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin-right: 4px; }
tr.note td { white-space: normal; padding-top: 0; color: var(--muted); font-size: .86rem; }
tr.bench td:first-child { color: var(--muted); }
.defs { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }
.def { padding: 14px 16px; display: grid; gap: 6px; align-content: start; }
.def .idx { display: flex; flex-wrap: wrap; gap: 6px; font-variant-numeric: tabular-nums; font-size: .85rem; }
footer { color: var(--muted); font-size: .82rem; display: grid; gap: 6px; }
@media (prefers-reduced-motion: no-preference) { details.team > summary { transition: background .15s; } }
details.team > summary:hover { background: var(--accent-soft); border-radius: 10px; }
"""


def _e(x) -> str:
    return escape("" if x is None else str(x))


def _gclass(g: str) -> str:
    return {"A": "g-a", "B": "g-b", "C": "g-c", "D": "g-d", "F": "g-f"}.get(g[:1], "g-c")


def _grade(g: str, title: str = "") -> str:
    return f'<span class="grade {_gclass(g)}" title="{_e(title)}">{_e(g)}</span>'


def _num(x, d=1) -> str:
    return "–" if x is None else f"{x:.{d}f}"


def _mult(m) -> str:
    if m is None:
        return "–"
    cls = "up" if m >= 1.03 else "down" if m <= 0.97 else ""
    return f'<span class="{cls}">×{m:.2f}</span>'


def _status(inj) -> str:
    if not inj:
        return ""
    s = inj["status"]
    cls = "st-q" if s in ("Questionable", "GTD", "DTD", "Day-To-Day") else "st-o"
    part = f" · {inj['body_part']}" if inj.get("body_part") else ""
    return f'<span class="status {cls}">{_e(s)}{_e(part)}</span>'


def _sources(srcs) -> str:
    if not srcs:
        return ""
    links = []
    for i, s in enumerate(srcs[:4], 1):
        if isinstance(s, str) and s.startswith(("http://", "https://")):
            links.append(f'<a href="{_e(s)}" target="_blank" rel="noopener">[{i}]</a>')
        else:
            links.append(_e(s))
    return " " + " ".join(links)


def _player_rows(pid, slot, P, bench=False) -> str:
    p = P.get(pid)
    if not p:
        return f'<tr><td>{_e(slot)}</td><td colspan="10" class="muted">empty</td></tr>'
    g = p["week_games"]
    opp = ", ".join(_e(g_["opp"]) + (f' <span class="muted small">final {g_["actual"]:.1f}</span>' if g_.get("final") else "")
                    for g_ in g) or "bye"
    sp = p.get("sleeper_week")
    mult = g[0]["mult"] if len(g) == 1 else (sum(x["mult"] for x in g) / len(g) if g else None)
    h2h = next((x for x in g if x["h2h_n"]), None)
    sim = next((x for x in g if x["split_n"]), None)
    h2h_txt = f'{_num(h2h["h2h_avg"])} <span class="muted small">({h2h["h2h_n"]}g)</span>' if h2h else "–"
    sim_txt = f'{_num(sim["sim_avg"])} <span class="muted small">({sim["split_n"]}g)</span>' if sim else "–"
    row = (f'<tr class="{"bench" if bench else ""}"><td>{_e(slot)}</td>'
           f'<td class="team-cell"><span class="team-name">{_e(p["name"])}</span> '
           f'<span class="muted small">{_e(p["pos"])} · {_e(p["team"] or "FA")}</span> {_status(p["injury"])}</td>'
           f'<td>{opp}</td><td class="num">{_num(sp)}</td><td class="num">{_num(p["own_ppg"])}</td>'
           f'<td class="num">{_mult(mult)}</td><td class="num">{h2h_txt}</td><td class="num">{sim_txt}</td>'
           f'<td class="num"><b>{_num(p["week_pts"])}</b></td><td class="num">{_num(p["ros_pts"], 0)}</td></tr>')
    adj = p.get("adjustment") or {}
    bits = []
    if adj.get("profile"):
        bits.append(_e(adj["profile"]))
    if adj.get("status_note"):
        bits.append("<b>Status:</b> " + _e(adj["status_note"]))
    elif p["injury"] and p["injury"].get("notes"):
        bits.append("<b>Injury:</b> " + _e(p["injury"]["notes"]))
    if p.get("teammate_injuries"):
        arrow = {"+": "may help", "-": "may hurt"}
        bits.append("<b>Teammates:</b> " + _e("; ".join(
            f'{e["depth"]} {e["name"]} {e["status"]} ({arrow.get(e["effect"], "")})' for e in p["teammate_injuries"])))
    adj_bits = []
    if adj.get("week_mult") not in (None, 1, 1.0):
        adj_bits.append(f"this week ×{float(adj['week_mult']):.2f}")
    if adj.get("ros_mult") not in (None, 1, 1.0):
        adj_bits.append(f"rest of season ×{float(adj['ros_mult']):.2f}")
    if adj.get("return_week"):
        adj_bits.append(f"expected back week {adj['return_week']}")
    if adj.get("out_for_season"):
        adj_bits.append("out for season")
    if adj_bits:
        bits.append("<b>Research adjustment:</b> " + ", ".join(adj_bits))
    if bits:
        row += f'<tr class="note"><td></td><td colspan="9">{" · ".join(bits)}{_sources(adj.get("sources"))}</td></tr>'
    return row


ROSTER_HEAD = ('<thead><tr><th>Slot</th><th>Player</th><th>Opp</th><th class="num" title="Sleeper projection, league scoring">Sleeper</th>'
               '<th class="num" title="Independent baseline: recent, season and prior-season points per game">Own PPG</th>'
               '<th class="num" title="Matchup multiplier from defense-vs-position, similar-defense split and head-to-head">Matchup</th>'
               '<th class="num" title="Average vs this opponent (games)">H2H</th>'
               '<th class="num" title="Average vs defenses with a similar style (games)">Sim. D</th>'
               '<th class="num">Week</th><th class="num">ROS</th></tr></thead>')


def _team_card(t, P, rank, sport) -> str:
    g = t["grades"]
    starters = {x["pid"] for x in t["week_lineup"] if x["pid"]}
    rows = "".join(_player_rows(x["pid"], x["slot"], P) for x in t["week_lineup"])
    bench_ids = [pid for pid in t["players"] if pid not in starters]
    bench_ids.sort(key=lambda pid: -P[pid]["ros_pts"] if pid in P else 0)
    bench = "".join(_player_rows(pid, "IR" if pid in t["reserve"] else "BN", P, bench=True) for pid in bench_ids)
    eff = (t["set_lineup_score"] / t["week_score"]) if t["week_score"] else None
    opp = t.get("opponent")
    opp_line = (f'<p>Matchup: vs <b>{_e(opp["team_name"])}</b>, projected {t["week_score"]:.1f}–{opp["score"]:.1f}, '
                f'win chance {opp["win_prob"]:.0%}.</p>') if opp else ""
    eff_line = (f'<p class="muted small">Lineup as currently set projects {t["set_lineup_score"]:.1f} '
                f'({eff:.0%} of the best possible lineup below).</p>') if eff is not None and t["starters"] else ""
    return f"""
<details class="team" id="team-{t['roster_id']}">
  <summary>
    <span class="muted">#{rank}</span>
    <h3>{_e(t['team_name'])}</h3>
    <span class="muted small">{_e(t['owner'])} · {_e(t['record'])}</span>
    <span class="summary-grades">
      <span><span class="lbl">Week</span><span class="pair">{_grade(g['week_abs'], 'absolute')}{_grade(g['week_curve'], 'curved')}</span></span>
      <span><span class="lbl">ROS</span><span class="pair">{_grade(g['ros_abs'], 'absolute')}{_grade(g['ros_curve'], 'curved')}</span></span>
    </span>
  </summary>
  <div class="body">
    {opp_line}{eff_line}
    <div class="scroll"><table>{ROSTER_HEAD}
      <tbody><tr class="group"><th colspan="10">Best lineup this week</th></tr>{rows}
      <tr class="group"><th colspan="10">Bench and reserve</th></tr>{bench}</tbody></table></div>
  </div>
</details>"""


def _board(res) -> str:
    rows = []
    for i, t in enumerate(res["teams"], 1):
        g = t["grades"]
        opp = t.get("opponent")
        m = (f'<span class="small">vs {_e(opp["team_name"])}</span><br>'
             f'<span class="bar" aria-hidden="true"><i style="width:{opp["win_prob"] * 100:.0f}%"></i></span> '
             f'<span class="small">{opp["win_prob"]:.0%}</span>') if opp else '<span class="muted small">–</span>'
        rows.append(
            f'<tr><td class="muted">{i}</td>'
            f'<td class="team-cell"><a href="#team-{t["roster_id"]}" class="team-name">{_e(t["team_name"])}</a><br>'
            f'<span class="muted small">{_e(t["owner"])} · {_e(t["record"])}</span></td>'
            f'<td class="num">{t["week_score"]:.1f}</td><td>{_grade(g["week_abs"], "absolute")}</td><td>{_grade(g["week_curve"], "curved")}</td>'
            f'<td class="num">{t["ros_score"]:.0f}</td><td>{_grade(g["ros_abs"], "absolute")}</td><td>{_grade(g["ros_curve"], "curved")}</td>'
            f'<td>{m}</td></tr>')
    return ('<div class="panel scroll"><table><thead>'
            '<tr class="group"><th></th><th></th><th colspan="3">This week</th><th colspan="3">Rest of season</th><th></th></tr>'
            '<tr><th>#</th><th>Team</th><th class="num">Proj</th><th>Abs</th><th>Curve</th>'
            '<th class="num">Proj</th><th>Abs</th><th>Curve</th><th>Fantasy matchup</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _defenses(res) -> str:
    adj = (res.get("adjustments") or {}).get("defenses") or {}
    cards = []
    for team, d in res["defenses"].items():
        idx = " ".join(f'<span>{_e(k)} {_mult(v)}</span>' for k, v in d["allowed_index"].items())
        sch = d.get("scheme") or {}
        ctx = d.get("scheme_context") or {}

        def lbl(k):
            c = ctx.get(k)
            return f" ({c['label']})" if c else ""

        sch_txt = []
        if sch.get("blitz_rate") is not None:
            sch_txt.append(f"blitz {sch['blitz_rate']:.0%}{lbl('blitz_rate')}")
        if sch.get("man_rate") is not None:
            sch_txt.append(f"man {sch['man_rate']:.0%}{lbl('man_rate')}")
        if sch.get("heavy_box_rate") is not None:
            sch_txt.append(f"8+ box {sch['heavy_box_rate']:.0%} of runs{lbl('heavy_box_rate')}")
        if sch.get("pass_epa") is not None:
            sch_txt.append(f"EPA/dropback {sch['pass_epa']:+.2f}")
        if sch.get("rush_epa") is not None:
            sch_txt.append(f"EPA/rush {sch['rush_epa']:+.2f}")
        note = adj.get(team, {})
        cards.append(
            f'<div class="panel def"><h3>{_e(team)}</h3>'
            f'<div class="idx"><span class="muted">Allows vs avg:</span> {idx}</div>'
            + (f'<p class="small muted">{_e(", ".join(sch_txt))}</p>' if sch_txt else "")
            + (f'<p class="small muted">Similar style: {_e(", ".join(d["similar_this_season"][:6]))}</p>' if d["similar_this_season"] else "")
            + (f'<p class="small"><b>Injured starters:</b> ' + _e("; ".join(
                f'{e["depth"]} {e["name"]} {e["status"]} (helps {"/".join(e["helps"])})' for e in d["injuries"])) + "</p>"
               if d.get("injuries") else "")
            + (f'<p class="small">{_e(note.get("note"))}{_sources(note.get("sources"))}</p>' if note.get("note") else "")
            + "</div>")
    if not cards:
        return ""
    label = "Opposing defenses" if res["league"]["sport"] == "nfl" else "Opponents this week"
    return f'<section class="section"><h2>{label}</h2><div class="defs">{"".join(cards)}</div></section>'


def render(res: dict) -> str:
    L = res["league"]
    sport = L["sport"]
    adj = res.get("adjustments") or {}
    P = res["players"]
    title = f"{L['name']} Week {L['week']} Scout"
    chips = "".join(f'<span class="chip">{_e(c)}</span>' for c in [L["label"], *L["tags"], f"{L['teams']} teams", L["waiver"]])
    notes = "".join(f"<li>{_e(n)}</li>" for n in L["notes"])
    scout_notes = adj.get("league_notes")
    ladder = "".join(_grade(g) for g in LADDER)
    b = res["benchmarks"]
    teams = "".join(_team_card(t, P, i, sport) for i, t in enumerate(res["teams"], 1))
    warn = "".join(f"<p>{_e(w)}</p>" for w in res.get("warnings") or [])
    sources = ("Sleeper API (league, rosters, injuries, stats, projections); nflverse play-by-play and FTN charting "
               "(blitz, box, EPA); web research where cited." if sport == "nfl"
               else "Sleeper API (league, rosters, injuries, game logs, projections); web research where cited.")
    return f"""<meta charset="utf-8">
<title>{_e(title)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Inter:wght@400;600;700&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header>
  <span class="eyebrow">{_e(sport.upper())} {_e(L['season'])} · Week {_e(L['week'])} · Rest of season through week {_e(L['last_week'])}</span>
  <h1>{_e(L['name'])}</h1>
  <div class="chips">{chips}</div>
  <p class="muted small">Lineup: {_e(L['lineup'])} + {_e(L['bench'])} bench. Generated {_e(res['generated_at'])}.</p>
  {f'<ul class="muted small">{notes}</ul>' if notes else ''}
</header>
{f'<section class="notes"><span class="eyebrow">Scout notes</span><p>{_e(scout_notes)}</p></section>' if scout_notes else ''}
<section class="section">
  <h2>Grade board</h2>
  <p class="muted small">Each team gets two grades per horizon. <b>Abs</b> compares the team's best lineup to a
  league-average starting lineup ({b['week']:.1f} pts this week, {b['ros']:.0f} rest of season).
  <b>Curve</b> ranks the team against the rest of this league.</p>
  {_board(res)}
</section>
<section class="section">
  <h2>Teams</h2>
  <p class="muted small">Open a team to see its best lineup, bench, and each player's profile. Week = projected
  points after matchup, injury and research adjustments; games already played count their actual points.</p>
  {teams}
</section>
{_defenses(res)}
<section class="section">
  <h2>How the grades work</h2>
  <div class="ladder">{ladder}</div>
  <div class="method small">
    <div><b>Player projection</b><p>Half Sleeper's projection, half an independent baseline (last 4 games 40%,
    this season 35%, last season 25%) scaled by a matchup multiplier. Both are scored with this league's own settings.</p></div>
    <div><b>Matchup multiplier</b><p>50% how much the opponent allows to the position versus league average, 30% the
    player's history against defenses of a similar style, 20% the player's history against this opponent. Small samples are
    shrunk toward neutral and the result is capped at ×0.80–×1.20.</p></div>
    <div><b>Injuries and research</b><p>Sleeper injury status discounts availability (Questionable ×0.90 this week,
    Out/IR ×0). Injured starters from each team's depth chart flag the players whose role they change (a QB out for
    his receivers, an RB1 out for his backup, a CB1 out for the receivers facing that defense). Cited research can override
    the discount, set a return week, or adjust for those role changes.</p></div>
    <div><b>Letter scale</b><p>C+ is league average. Abs moves one step per {ABS_STEP:.1%} above or below the
    benchmark lineup; Curve moves one step per {CURVE_STEP:.1f} standard deviations within this league.</p></div>
  </div>
</section>
<footer>
  <p>Sources: {sources}</p>
  {warn}
  <p>Projections are estimates, not guarantees. Generated by Sleeper Scout, a Claude Code plugin.</p>
</footer>
</div>
"""
