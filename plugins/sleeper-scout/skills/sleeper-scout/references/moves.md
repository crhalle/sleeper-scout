# Roster moves (`scout.py moves`)

Owner-only recommendations, written to `~/sleeper-scout/<league_id>/moves.json`. Nothing here feeds `report.html`, the published Artifact, `adjustments.json` or `league_notes`.

## How values are computed

Every move is scored by re-running the engine's lineup scoring (`grading.team_value`) on the changed roster. The value is **rest-of-season points**, counting the current week and later weeks through the last fantasy week, from the researched projections. Gains are the difference from the current roster.

- **Projection outliers**: before anything is scored, each player's later weeks (not the current week) are capped at 2× his typical per-game value (`OUTLIER_MULT`). Typical means the median over his future weeks that have a value, or `own_ppg` when there are fewer than 3. NBA weeks are compared per game. This stops one bad Sleeper projection, such as a kicker projected 75 for a single week, from driving a move. Capped weeks are listed in `outliers`, and a note is added when a capped player is part of a recommendation. Only moves use the capped values: grades and the report don't change.

- **Drops**: `ros_loss` / `week_loss` is how much the roster loses without that player. Players whose NFL game has started this week are never listed, because Sleeper won't drop them.
  - `must_drop`: the roster breaks a size or position limit, and this is the cheapest fix.
  - `ir_instead`: the player's status is IR-eligible in this league and an IR slot is free. Move him to IR rather than dropping him.
- **Waivers**: each free agent is paired with the best of your 5 lowest-value players to drop (or an open spot). Position limits are respected. At most 3 adds per position are shown.
  - `faab_bid` = [lo, hi] dollars from the ROS gain as a share of your ROS score: ≥6% → 25–40% of the remaining budget, ≥3% → 10–20%, ≥1.5% → 4–10%, else $0–2%.
  - In rolling or reverse-standings leagues there is no bid. Say whether the add is worth spending waiver priority on (worth it at about 3% ROS gain or more).
- **Trades**: 1-for-1, 2-for-1 and 1-for-2 against every team.
  - A team that ends up over the roster size drops its lowest-value player (`my_drop` / `their_drop`). A freed spot stays empty, so no trade depends on winning a waiver claim.
  - A trade is kept when your ROS gain is at least 0.5% of your ROS score (and ≥1 pt), and the partner loses at most 0.5% of theirs.
  - It must also pass a market check: the partner can't give up much more "points over replacement" (ROS points above the best free agent at the position, raised to the 1.5 power) than they get. That keeps out "your bench star for my kicker" deals.
  - Trades are skipped after the trade deadline and in the playoffs.

## moves.json

```json
{
  "league": {"league_id": "...", "name": "...", "week": 10, "waiver": "FAAB", "waiver_budget": 100, "trade_deadline": 12},
  "team": {"roster_id": 1, "team_name": "...", "week_score": 0, "ros_score": 0, "open_spots": 0,
           "faab_remaining": 80, "waiver_position": 4, "compliance": []},
  "drops":   [{"pid": "...", "name": "...", "pos": "RB", "status": "Out", "week_loss": 0, "ros_loss": 0, "tags": []}],
  "waivers": [{"add": {...}, "drop": {...} | null, "week_gain": 0, "ros_gain": 0, "ros_gain_pct": 0.03, "faab_bid": [10, 20] | null}],
  "trades":  [{"partner": {"roster_id": 2, "team_name": "...", "owner": "..."}, "give": [{...}], "get": [{...}],
               "my_gain_ros": 0, "my_gain_week": 0, "their_gain_ros": 0, "their_gain_week": 0,
               "my_drop": {...} | null, "their_drop": {...} | null}],
  "outliers": [{"pid": "...", "name": "...", "pos": "K", "week": 15, "raw": 41.5, "capped": 14.4}],
  "notes": ["..."]
}
```

## Writing it up

- Lead with the single best move. Keep the list short: the owner acts on 2–4 moves a week, not 20.
- Waiver adds were not part of the research pass, so news-check them before you recommend one (`SKILL.md` step 8.4).
- A trade pitch names what the partner gains in terms of their lineup (for example "fills your empty FLEX with a WR1"), not ours.
- Values are projections. Say "projects to add ~X points ROS", never "will win you X".
