"""Lineup optimisation, league benchmark, and the sliding letter-grade scales.

Two grades are produced for every score:
  absolute  team score / benchmark lineup score, where the benchmark is the average
            starter at each slot if the best players were spread evenly across all teams.
            1.00 means "league-average starting lineup" and maps to C+.
  curved    z-score of the team's score within this league. The league mean maps to C+.

Both use the same 13-step ladder; one step = ABS_STEP (ratio) or CURVE_STEP (z).
"""
from __future__ import annotations

import math
import statistics

from .league import SLOT_ELIGIBILITY

LADDER = ["F", "D-", "D", "D+", "C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+"]
MID = LADDER.index("C+")
ABS_STEP = 0.035   # each 3.5% above/below the benchmark lineup moves one step
CURVE_STEP = 0.30  # each 0.3 standard deviations moves one step


def _ladder(steps: float) -> str:
    i = MID + int(round(steps))
    return LADDER[max(0, min(len(LADDER) - 1, i))]


def absolute_grade(score: float, benchmark: float) -> str:
    if benchmark <= 0:
        return "–"
    return _ladder((score / benchmark - 1) / ABS_STEP)


def curved_grades(scores: dict) -> dict:
    vals = list(scores.values())
    if len(vals) < 2:
        return {k: "C+" for k in scores}
    mu, sd = statistics.fmean(vals), statistics.pstdev(vals)
    if sd == 0:
        return {k: "C+" for k in scores}
    return {k: _ladder(((v - mu) / sd) / CURVE_STEP) for k, v in scores.items()}


def _slot_order(slots: list[str]) -> list[str]:
    # most restrictive slots first so flex spots get what is left
    return sorted(slots, key=lambda s: len(SLOT_ELIGIBILITY.get(s, {s})))


def optimal_lineup(points: dict, positions: dict, slots: list[str], fixed: dict | None = None,
                   exclude: set | None = None) -> tuple[float, list[tuple[str, str | None, float]]]:
    """Greedy fill (restrictive slots first). points: {pid: pts}; positions: {pid: set(pos)}.

    fixed: {slot index: pid} players that must stay in that slot (their game has started).
    exclude: players that can't be moved into the lineup (locked on the bench).
    Returns (total, [(slot, pid, pts)]) in slot-fill order."""
    fixed = fixed or {}
    used: set = set(fixed.values()) | set(exclude or ())
    lineup = []
    ranked = sorted(points.items(), key=lambda kv: -kv[1])
    order = sorted(range(len(slots)), key=lambda i: len(SLOT_ELIGIBILITY.get(slots[i], {slots[i]})))
    for i in order:
        slot = slots[i]
        if i in fixed:
            lineup.append((slot, fixed[i], points.get(fixed[i], 0.0)))
            continue
        elig = SLOT_ELIGIBILITY.get(slot, {slot})
        pick = next(((pid, p) for pid, p in ranked if pid not in used and positions.get(pid, set()) & elig), None)
        if pick:
            used.add(pick[0])
            lineup.append((slot, pick[0], pick[1]))
        else:
            lineup.append((slot, None, 0.0))
    return round(sum(p for _, _, p in lineup), 2), lineup


def lineup_with_locks(points: dict, locked_points: dict, locked: set, positions: dict, slots: list[str],
                      slot_starters: list, sport: str):
    """Best lineup that respects games already started. Hard rule: a game that has started
    keeps the player's Sleeper status as set.

    points: {pid: week pts}; locked_points: {pid: pts from games already started};
    locked: players with at least one game started this week;
    slot_starters: Sleeper's `starters` aligned with `slots` (None for an empty slot).
    NFL (one game a week): a started player is locked for the week. Locked starters keep their
        slot and locked bench players are never moved in.
    NBA (lineups change daily): only the started games are locked. Their points count for
        current starters only, and every player's remaining games are optimised freely.
    Returns (total, lineup, locked_base) where locked_base is the NBA banked points (0 for NFL).
    """
    starters = [p for p in slot_starters if p]
    if sport == "nba":
        base = round(sum(locked_points.get(p, 0.0) for p in starters if p in points), 2)
        rest = {p: max(0.0, v - locked_points.get(p, 0.0)) for p, v in points.items()}
        total, lineup = optimal_lineup(rest, positions, slots)
        return round(total + base, 2), lineup, base
    fixed = {i: p for i, p in enumerate(slot_starters[:len(slots)]) if p in locked}
    total, lineup = optimal_lineup(points, positions, slots, fixed=fixed, exclude=locked)
    return total, lineup, 0.0


def benchmark_lineup(points: dict, positions: dict, slots: list[str], n_teams: int) -> float:
    """Average starting lineup if the top players were distributed evenly across n_teams."""
    used: set = set()
    ranked = sorted(points.items(), key=lambda kv: -kv[1])
    total = 0.0
    for slot in _slot_order(slots):
        elig = SLOT_ELIGIBILITY.get(slot, {slot})
        picks = []
        for pid, p in ranked:
            if len(picks) == n_teams:
                break
            if pid not in used and positions.get(pid, set()) & elig:
                picks.append(p)
                used.add(pid)
        if picks:
            total += sum(picks) / n_teams
    return round(total, 2)


def win_probability(a: float, b: float, sport: str) -> float:
    """Rough P(team A outscores team B) from projections, normal approximation."""
    cv = 0.22 if sport == "nfl" else 0.12
    sd = ((a * cv) ** 2 + (b * cv) ** 2) ** 0.5
    if sd == 0:
        return 0.5
    z = (a - b) / sd
    return round(0.5 * (1 + math.erf(z / 2 ** 0.5)), 3)
