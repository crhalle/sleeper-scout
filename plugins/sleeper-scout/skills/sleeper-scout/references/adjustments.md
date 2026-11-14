# adjustments.json

Research results go to the engine through one JSON file. Every field is optional.

Normally `scout.py merge-research` builds it from the research agents' `research/result-<n>.json` files, which use the same `players`/`defenses` shape. The main session then adds `league_notes`. Merge validation:
- drops player IDs that aren't on a roster in this league
- clamps `week_mult`/`ros_mult` to 0–1.5 and drops non-numeric values
- discards `week_mult`, `ros_mult`, `return_week` and `out_for_season` when the entry has no sources, but keeps its profile and status note
- for a player that appears in two result files, keeps the entry with more sources
- for a defense that appears twice, joins the notes
- keeps an existing `league_notes` when re-merging

```json
{
  "league_notes": "string shown at the top of the report",
  "players": {
    "<sleeper player_id>": {
      "week_mult": 1.0,
      "ros_mult": 1.0,
      "return_week": 14,
      "out_for_season": false,
      "status_note": "one sentence on the latest status",
      "profile": "2-3 sentence individual profile",
      "sources": ["https://..."]
    }
  },
  "defenses": {
    "<TEAM ABBR>": { "note": "scheme / personnel context", "sources": ["https://..."] }
  }
}
```

Player IDs are the `pid` values in the research queue and the keys of `players` in `analysis.json`. NFL team defenses use the team abbreviation (for example `"KC"`).

## How the engine applies them

| Field | Effect |
|---|---|
| `week_mult` | Multiplies this week's projected points after the Sleeper injury discount. Clamped to 0–1.5. Set `0` for a player ruled out. |
| `ros_mult` | Multiplies every remaining week after this one. Clamped to 0–1.5. |
| `return_week` | Zero availability for weeks before this one, then ×0.95 after. Replaces the default injury discount for rest-of-season. |
| `out_for_season` | Same as a return week beyond the season. |
| `status_note`, `profile`, `sources` | Shown under the player in the report. |

The default injury discounts are in `scripts/sleeper_scout/engine.py` (`WEEK_AVAIL`, `ROS_AVAIL`). Adjustments stack on top of them for this week, so to undo a Questionable discount for a player confirmed active use about `1.1`, not `1.0`.

## Guardrails

- Every non-neutral multiplier, `return_week` and `out_for_season` needs at least one source URL.
- Keep multipliers in the 0.85–1.15 range unless the news is binary (ruled out, confirmed return, season-ending).
- Don't adjust for matchup strength alone; the engine already models it. Adjust for things it can't see: availability, role, personnel absences, weather.
- Don't invent statuses. If you can't find current news, write the profile from the data and leave multipliers alone.
