---
name: sleeper-scout
description: Research and grade a Sleeper fantasy league (NFL or NBA). Finds the league from a Sleeper username or league ID, reads its scoring rules (PPR, superflex, TE premium, NBA points weights), pulls every roster, builds player profiles from injuries, matchups against similar defensive schemes and head-to-head history, then grades every team A+ to F for this week and rest of season and publishes a shareable report. Use when the user asks to analyze, scout, rank, grade or power-rank their Sleeper league or rosters, or (after that analysis) asks for drop, waiver-claim or trade recommendations for their own team.
argument-hint: "[sleeper username or league ID]"
---

# Sleeper Scout

Grades every roster in a Sleeper league on a sliding letter scale (A+ … F) for **this week** and **rest of season**, each shown two ways: **absolute** (vs a league-average starting lineup) and **curved** (vs the other teams in this league). The numbers come from a deterministic Python engine; your job is the league lookup, the sport-specific research the data can't see, and the write-up.

The engine lives in `scripts/` next to this file. Run it with `uv`, which installs Python dependencies automatically on first run:

```bash
uv run "<this skill's base directory>/scripts/scout.py" <command> ...
```

The first `analyze` of a season downloads about 30–60 MB (Sleeper game logs, nflverse play-by-play) and takes 1–2 minutes. After that it uses the cache in `~/.cache/sleeper-scout` and runs in seconds. If `uv` is missing, tell the user to install it (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`) and stop.

## Workflow

### 1. Identify the league

The argument (or the user's message) is a Sleeper **username** or a **league ID** (a long number, the last part of `sleeper.com/leagues/<id>`).

- Nothing given: ask for a Sleeper username or league ID.
- Username: run `scout.py leagues <username>`. It lists the user's NFL and NBA leagues for the current season. If there is more than one, ask which one with AskUserQuestion, using league names and formats as options. If there is exactly one, confirm it in a sentence and continue.
- League ID: use it directly.

### 2. Run the baseline analysis

```bash
uv run ".../scripts/scout.py" analyze <league_id>
```

Output goes to `~/sleeper-scout/<league_id>/analysis.json` (override with `--out DIR`). stdout is a compact summary:
- the league format and scoring framework (PPR / half / standard, TE premium, superflex, 2QB, IDP, bonuses, NBA category weights, playoff weeks)
- every team with week and ROS projections and the four grades, plus this week's fantasy matchup and win probability
- **roster compliance**: every roster that breaks a league rule: more active players than roster spots, more active players at a position than its limit (IR and taxi don't count, as in Sleeper), too many players on IR or taxi, or a player on IR whose injury status the league doesn't allow there (e.g. healthy or Questionable)
- a **research queue**: up to ~45 rostered players whose outlook depends on things the data can't see (their own injuries, injured starters at positions that change their role such as a QB out for his receivers, key starters, big matchup swings), with their `pid`
- opposing defenses this week with their style numbers and the defenses they most resemble

Tell the user the league format you detected in one or two lines before researching, because it affects everything that follows.

### 3. Research with the sport-specific research agents

The plugin ships two research agents, `sleeper-scout:nfl-researcher` and `sleeper-scout:nba-researcher`. Each agent file holds that sport's research playbook, input/output contract and result schema.

**3a. Plan the batches.**

```bash
uv run ".../scripts/scout.py" plan-research <league_id>
```

This splits the research queue into batch files under `~/sleeper-scout/<league_id>/research/`. Players are grouped by real game (NFL) or team (NBA), so each batch holds both sides of a matchup plus the opposing defense. It prints `mode: inline` or `mode: parallel`, the agent name, and one line per batch with its path. Results from an earlier run are cleared.

**3b. Research.**

- **`mode: parallel`**: in a single message, launch one Agent call per batch so they run concurrently:
  - `subagent_type`: the agent name printed (for example `sleeper-scout:nfl-researcher`)
  - `description`: `Research batch <n>`
  - `prompt`: `Batch file: <absolute batch path>`

  Wait for every agent to finish. Each replies with one line and writes `research/result-<n>.json`. Don't redo their research.
- **`mode: inline`**, or when the Agent tool or the plugin agents aren't available: do the research yourself. Read the playbook in `<plugin root>/agents/<sport>-researcher.md` (the plugin root is two directories above this skill's base directory). Then work through each batch file in order and write `research/result-<n>.json` for each in the schema that file describes.

**3c. Merge.**

```bash
uv run ".../scripts/scout.py" merge-research <league_id>
```

This writes `adjustments.json`. It validates every entry: unknown players are dropped, multipliers are clamped, and adjustments without a source are discarded (see `references/adjustments.md`). Read its warnings. If it lists high-priority queued players with no result (injured starters, teammate injuries, key starters), research those yourself and add them to `adjustments.json`.

### 4. Add league notes

Add a `league_notes` field to `adjustments.json`: a 3–5 sentence overview covering who is strongest now vs for the playoffs, the biggest injury swings (including depth-chart ripple effects), notable matchups, and any roster out of compliance with league rules. Base it on the merged findings and the grade table from step 2.

### 5. Build the report

```bash
uv run ".../scripts/scout.py" report <league_id> --adjustments ~/sleeper-scout/<league_id>/adjustments.json
```

This re-grades with your adjustments and writes `report.html` in the same folder. It is a single self-contained page that works in light and dark themes.

### 6. Share it

The user wants a shareable page. If your Artifact tool is available, publish `report.html` with it as is: it is already written as a page body with its own `<title>` and styles, so don't wrap or rewrite it. Use icon `trophy` and a one-sentence description such as "Week 10 grades and player profiles for <league name>". When you republish the same league later in the conversation, reuse the same file path so the link stays the same. If no Artifact tool is available, give the user the absolute path to `report.html` and say they can open it in a browser or send the file.

### 7. Summarize in chat

Keep it short. Give the format detected, the top and bottom teams with their grades, any roster flagged out of compliance and why, the biggest research-driven changes, and the link or path. Don't paste the full report.

End with one line offering private roster-move advice: "Want drop, waiver and trade recommendations for your team? They stay in this chat and aren't added to the report." Don't run step 8 until the owner asks.

### 8. Roster moves (only when the owner asks)

This advice is **private to the owner**. Never put it in `adjustments.json`, `league_notes`, `report.html`, or any Artifact, and don't republish the report because of it.

1. **The analysis must be finished first.** `moves` refuses to run unless `report` has been run this week with merged research. If it prints "Run the weekly analysis first", do steps 1–6 (asking only what you need), then come back.
2. **Find the owner's team.** If step 1 started from a username, pass `--owner <username>`. Otherwise ask with AskUserQuestion, using team names and owners from the grade table as options, and pass `--roster <roster_id>`.
3. **Run it:**
   ```bash
   uv run ".../scripts/scout.py" moves <league_id> --owner <username>   # or --roster <id>
   ```
   It writes `moves.json` next to `analysis.json` and prints drop candidates, waiver claims (each with the player to drop, week and ROS gain, and a FAAB bid range in FAAB leagues), and trade ideas (both teams' ROS gains; each one already passes a fairness check for the other owner). Read `references/moves.md` for the fields and for how to word bids and trade pitches.
4. **News check.** Web-search the top ~5 targets, which are the best waiver adds and the players you'd get in the best trades. Confirm health, role and any news since the analysis. Drop any target with disqualifying news (for example a new injury, or lost the starting job), and cite a source for each one you keep. Free agents had no research pass, so this check matters most for them.
5. **Reply in chat only**, short and ranked:
   - **Drops:** `must_drop` players first, then the lowest-value players. Suggest IR instead of dropping when a player is tagged `ir_instead`.
   - **Waiver claims:** add / drop (or "move X to IR" when `move_to_ir` is set), the ROS gain, the bid range (FAAB) or whether to use waiver priority, and one line on why. For `after_lock` adds, say the claim processes after this week's games.
   - **Trades:** up to 3–4 proposals with the partner team, give / get, why it helps you, and a one-sentence pitch for why the other owner gains.
   - Include the engine's `notes` (trade deadline passed, dynasty value not modelled, locked players, and any capped projection outlier for a player in a recommended move).

## Notes

- Re-running `report` with an edited adjustments file is instant. Use it when the user disputes a call or news breaks.
- `--seasons N` changes how many seasons of history feed the matchup splits (default 3).
- The engine never writes to Sleeper; the API is read-only and needs no login.
- Grades describe roster strength from projections. Don't present them as predictions of results.
