# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Claude Code plugin marketplace (`.claude-plugin/marketplace.json`, name `football`) containing one plugin, `plugins/sleeper-scout`. It grades Sleeper fantasy leagues (NFL + NBA). The README is the user-facing doc.

## Commands

```bash
# unit tests (offline)
uv run --with pytest --with polars pytest plugins/sleeper-scout/tests -q
uv run --with pytest --with polars pytest plugins/sleeper-scout/tests -q -k ladder   # single test

# run the engine directly (uv reads the PEP 723 header in scout.py for deps)
S=plugins/sleeper-scout/skills/sleeper-scout/scripts/scout.py
uv run $S leagues <sleeper_username>
uv run $S analyze <league_id> [--out DIR] [--seasons 3]
uv run $S plan-research  <league_id> [--batches N]   # research/batch-<n>.json (+ inline|parallel mode)
uv run $S merge-research <league_id>                 # research/result-*.json -> adjustments.json
uv run $S report  <league_id> --adjustments FILE      # writes report.html

# run one research agent headless (prompt must come before the variadic --allowedTools)
claude -p "Launch one Agent, subagent_type 'sleeper-scout:nfl-researcher', prompt 'Batch file: <path>'" \
  --plugin-dir plugins/sleeper-scout --add-dir <out dir> --allowedTools "Agent,WebSearch,WebFetch,Read,Write"

# manifest checks
claude plugin validate .
claude plugin validate plugins/sleeper-scout

# load the plugin in a session without installing
claude --plugin-dir plugins/sleeper-scout
```

Sample in-season NFL league for live testing: `1386037431169470464` (public Sleeper league, 12-team PPR).

## Architecture

The work is split between **deterministic Python** and **Claude-driven steps**:

- `commands/scout.md` is a thin wrapper that loads the skill.
- `skills/sleeper-scout/SKILL.md` defines the workflow. The main session finds the league and runs `analyze`, then `plan-research`. It either fans out one research agent per batch or researches inline. It then runs `merge-research`, adds `league_notes`, runs `report`, and publishes `report.html` as an Artifact.
- `agents/{nfl,nba}-researcher.md` are the research subagents (`model: sonnet`, tools WebSearch/WebFetch/Read/Write). They are the **single source of truth for each sport's research playbook**. The inline fallback also reads them, so edit the playbook there. Each agent reads one `research/batch-<n>.json` and writes `research/result-<n>.json`, and must reply in one line.
- Subagents can't spawn subagents, and they can't see the skill's base directory, so every path they need is passed through the batch file (`result_path`).
- The scripts live inside the skill directory, not at the plugin root, so the skill can find them through its "Base directory" path. Don't move them without updating SKILL.md.

Python package `scripts/sleeper_scout/`:
- `api.py`: every HTTP call goes through it, with an on-disk cache in `~/.cache/sleeper-scout` (`SLEEPER_SCOUT_CACHE` overrides). Uses stdlib `urllib` only. Past seasons are cached forever, the current week for 1h, and projections for 3h.
- `league.py`: slot eligibility (`SLOT_ELIGIBILITY`), relevant positions, and format detection (`scoring_summary`).
- `history.py`: per-game logs scored with league settings, defense-vs-position, style vectors (z-scored within each season), and `similar_defenses` (nearest 25% by distance).
- `schemes_nfl.py`: nflverse scheme features (blitz, 8+ box, EPA allowed, man rate). Optional; a failure only adds a warning.
- `engine.py`: `run(league_id, adjustments)` ties everything together. It is deterministic for a given cache state, so `analyze` and `report` both call it, and `report` simply passes adjustments.
- `research.py`: `plan_batches` (grouped by game, `MIN_PARALLEL`=12, 2–5 batches) and `merge` (drops unknown pids, clamps to the engine's `ADJ_MIN`/`ADJ_MAX`, discards unsourced adjustments). Covered by `tests/test_research.py`.
- `grading.py`: greedy optimal lineup, benchmark lineup, and the 13-step letter ladder (`ABS_STEP`, `CURVE_STEP`). The unit tests cover these pure functions.
- `report.py`: renders one self-contained HTML body fragment, with no external JS and light/dark tokens, so it can be published as an Artifact unchanged.

## Sleeper API facts this code relies on

- The documented `api.sleeper.app/v1` covers state, users, leagues, rosters, matchups, and players. The undocumented `api.sleeper.com/{stats|projections}/{sport}/{season}/{week}?season_type=regular&position[]=…` returns per-player (NFL) or per-game (NBA) rows with `team` and `opponent`. NFL needs the `position[]` filter; NBA doesn't.
- Stat keys equal `scoring_settings` keys, including DEF `pts_allow_*` buckets, so `history.score` is a plain dot product. Sleeper's default `pass_int` is -1.
- NBA "played" detection must accept `sp` (seconds), because older seasons have no `min`.
- `api.sleeper.com/schedule/{sport}/regular/{season}` gives each game's `status` (`pre_game` / `complete`); NBA `home`/`away` are `{team, record}` dicts. `engine.final_games` uses it so that games already played this week score their actual points instead of projections. Research agents skip week-status research for `final: true` games.
- Defense descriptions must come from `schemes_nfl.context` labels (top/bottom quartile = high/low). Raw rates mislead: 27% blitz is league average.
- nflverse team `LA` maps to Sleeper `LAR` (`schemes_nfl.TEAM_MAP`). pbp participation (man/zone) for the current season isn't published until after the season.
