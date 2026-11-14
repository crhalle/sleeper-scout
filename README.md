# Sleeper Scout

A Claude Code plugin that researches and grades every roster in a [Sleeper](https://sleeper.com) fantasy league, for both NFL and NBA leagues, and publishes a shareable report.

`/sleeper-scout:scout <your Sleeper username or league ID>` runs these steps:

1. **Finds the league.** Give a username and it lists your leagues to pick from, or give the league ID directly.
2. **Reads the rules.** Detects the scoring framework (PPR / half / standard, TE premium, superflex, 2QB, IDP, bonuses, NBA points weights) and lineup slots, then scores everything with the league's own settings.
3. **Pulls every roster** in the league.
4. **Builds a profile for each player.** Each profile includes Sleeper injury status, points per game (season, last 4 games, last season), floor and ceiling, an independent baseline, and a matchup multiplier. The multiplier combines:
   - what the opponent allows to the player's position,
   - the player's history against defenses with a **similar defensive style** (for NFL, nflverse data on blitz rate, 8+ man boxes, EPA allowed, and man coverage),
   - the player's **head-to-head** history against this opponent.
5. **Does sport-specific research.** Dedicated research agents search the web for injury news, timelines, role changes, and opposing scheme context, then record cited adjustments (see below).
6. **Grades every team A+ to F** for **this week** and **rest of season**. Each grade appears two ways: **absolute** (against a league-average starting lineup) and **curved** (against the other teams in the league).
7. **Publishes the report** as a private claude.ai page you can share with league-mates, or saves an HTML file.

## Install

Requirements: [Claude Code](https://claude.com/claude-code) and [uv](https://docs.astral.sh/uv/) (`brew install uv`). On first run, uv fetches Python 3.10+ and `polars` automatically.

Add this repo as a marketplace, then install the plugin:

```
/plugin marketplace add crhalle/sleeper-scout
/plugin install sleeper-scout@football
```

To try it from a local checkout:

```
/plugin marketplace add /path/to/this/repo
/plugin install sleeper-scout@football
```

## Use

```
/sleeper-scout:scout                 # Claude asks for your username or league ID
/sleeper-scout:scout myusername
/sleeper-scout:scout 1386037431169470464
```

The skill also triggers on plain requests such as "grade my Sleeper league".

The first run of a season downloads about 30–60 MB of public data and takes 1–2 minutes. Later runs use the cache in `~/.cache/sleeper-scout` and finish in seconds. Output goes to `~/sleeper-scout/<league_id>/` (`analysis.json`, `adjustments.json`, `report.html`).

## Research agents

The plugin ships two research subagents. Both run on Sonnet to keep search-heavy work inexpensive.

- **`nfl-researcher`**: practice reports and game designations, injury return timelines, role changes, opposing coverage and missing defenders, weather.
- **`nba-researcher`**: games missed this week, rest days, minutes limits, rotation changes, opposing defensive scheme.

The engine splits the research queue into batches, grouped by real game (NFL) or team (NBA), so each batch covers both sides of a matchup. Small queues (under 12 players) are researched in the main session. Larger ones fan out to 2–5 agents running in parallel. Each agent writes its own result file, and `merge-research` combines them into `adjustments.json`. The merge drops any adjustment that doesn't cite a source.

## How grades work

- **Player week projection** = 50% Sleeper projection + 50% independent baseline × matchup multiplier, then × injury availability × research adjustment.
- **Matchup multiplier** = 50% opponent vs position + 30% similar-defense history + 20% head-to-head. Small samples are shrunk toward neutral, and the result is capped at ×0.80–×1.20.
- **Team score** = the best legal lineup from the roster. For rest of season, that lineup is summed week by week, so byes and injuries pull on bench depth.
- **Letter ladder**: F, D-, D, D+, C-, C, **C+ (average)**, B-, B, B+, A-, A, A+. Absolute moves one step per 3.5% above or below the benchmark lineup. Curved moves one step per 0.3 standard deviations within the league.

## Data and privacy

The plugin only reads public endpoints and never logs in to or writes to Sleeper:
- [Sleeper API](https://docs.sleeper.com) for leagues, rosters, users, injuries, game logs, and projections.
- [nflverse](https://github.com/nflverse/nflverse-data) for NFL play-by-play, FTN charting, and participation data.

Reports include league members' Sleeper display names and team names. Published pages are private until you share the link.

## Develop

```bash
uv run --with pytest --with polars pytest plugins/sleeper-scout/tests -q
uv run plugins/sleeper-scout/skills/sleeper-scout/scripts/scout.py analyze <league_id>
claude plugin validate .
```
