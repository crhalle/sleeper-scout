"""NFL defensive scheme features computed from nflverse open data.

Per (season, defense):
  blitz_rate      share of dropbacks with 1+ blitzer               (FTN charting, 2022+)
  heavy_box_rate  share of runs into an 8+ man box                 (FTN charting, 2022+)
  man_rate        share of charted coverages that were man          (pbp participation; past seasons only)
  pass_epa        EPA per dropback allowed                          (play-by-play)
  rush_epa        EPA per designed run allowed                      (play-by-play)

Any source that is unavailable is skipped; the caller just gets fewer features.
"""
from __future__ import annotations

import sys

from . import api

# nflverse -> Sleeper team abbreviations
TEAM_MAP = {"LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}


def _url(kind: str, name: str) -> str:
    return f"{api.NFLVERSE}/{kind}/{name}.parquet"


def scheme_features(seasons: list[int], current_season: int) -> tuple[dict, list[str]]:
    """Returns ({(season, team): {feature: value}}, warnings)."""
    warnings: list[str] = []
    try:
        import polars as pl
    except ImportError:
        return {}, ["polars is not installed; skipped nflverse scheme features"]

    out: dict = {}
    for s in seasons:
        ttl = api.FOREVER if s < current_season else 12 * api.HOUR
        try:
            pbp_path = api.get_file(_url("pbp", f"play_by_play_{s}"), ttl=ttl, suffix=".parquet")
        except Exception as e:  # network problems shouldn't kill the run
            warnings.append(f"nflverse play-by-play {s} unavailable ({e})")
            continue
        if pbp_path is None:
            warnings.append(f"nflverse play-by-play {s} not published yet")
            continue
        pbp = (
            pl.scan_parquet(pbp_path)
            .select(["game_id", "play_id", "defteam", "season_type", "pass", "rush", "qb_dropback", "epa"])
            .filter((pl.col("season_type") == "REG") & pl.col("defteam").is_not_null() & pl.col("epa").is_not_null())
            .filter((pl.col("pass") == 1) | (pl.col("rush") == 1))
            .collect()
        )
        feats = (
            pbp.group_by("defteam").agg(
                pl.col("epa").filter(pl.col("qb_dropback") == 1).mean().alias("pass_epa"),
                pl.col("epa").filter((pl.col("rush") == 1) & (pl.col("qb_dropback") != 1)).mean().alias("rush_epa"),
            )
        )
        rows = {r["defteam"]: dict(r) for r in feats.iter_rows(named=True)}

        try:
            ftn_path = api.get_file(_url("ftn_charting", f"ftn_charting_{s}"), ttl=ttl, suffix=".parquet")
        except Exception as e:
            ftn_path = None
            warnings.append(f"FTN charting {s} unavailable ({e})")
        if ftn_path is not None:
            ftn = pl.read_parquet(ftn_path, columns=["nflverse_game_id", "nflverse_play_id", "n_blitzers", "n_defense_box"])
            ftn = ftn.with_columns(pl.col("nflverse_play_id").cast(pl.Int64, strict=False))
            j = pbp.with_columns(pl.col("play_id").cast(pl.Int64, strict=False)).join(
                ftn, left_on=["game_id", "play_id"], right_on=["nflverse_game_id", "nflverse_play_id"], how="inner")
            f2 = j.group_by("defteam").agg(
                (pl.col("n_blitzers").filter(pl.col("qb_dropback") == 1) > 0).mean().alias("blitz_rate"),
                (pl.col("n_defense_box").filter((pl.col("rush") == 1) & (pl.col("qb_dropback") != 1)) >= 8).mean().alias("heavy_box_rate"),
            )
            for r in f2.iter_rows(named=True):
                rows.setdefault(r["defteam"], {}).update(r)

        try:
            part_path = api.get_file(_url("pbp_participation", f"pbp_participation_{s}"), ttl=api.DAY * 7, suffix=".parquet")
        except Exception:
            part_path = None
        if part_path is not None:
            part = pl.read_parquet(part_path, columns=["nflverse_game_id", "play_id", "defense_man_zone_type"])
            part = part.filter(pl.col("defense_man_zone_type").is_in(["MAN_COVERAGE", "ZONE_COVERAGE"]))
            j = pbp.select(["game_id", "play_id", "defteam"]).with_columns(pl.col("play_id").cast(pl.Int64, strict=False)).join(
                part.with_columns(pl.col("play_id").cast(pl.Int64, strict=False)),
                left_on=["game_id", "play_id"], right_on=["nflverse_game_id", "play_id"], how="inner")
            f3 = j.group_by("defteam").agg((pl.col("defense_man_zone_type") == "MAN_COVERAGE").mean().alias("man_rate"))
            for r in f3.iter_rows(named=True):
                rows.setdefault(r["defteam"], {}).update(r)
        elif s == current_season:
            warnings.append(f"Man/zone coverage data for {s} is not published until after the season; man_rate omitted for {s}")

        for team, r in rows.items():
            t = TEAM_MAP.get(team, team)
            out[(s, t)] = {k: v for k, v in r.items() if k != "defteam" and v is not None}
    return out, warnings


def context(scheme: dict, season: int, team: str) -> dict:
    """Each scheme feature for `team` with the league average, percentile and a label
    ("high" = top quartile, "low" = bottom quartile, else "average"), so nobody calls an
    average blitz rate "blitz-heavy". Falls back to the prior season early in the year.
    For pass_epa / rush_epa a *high* value means a *weak* defense."""
    s = season if (season, team) in scheme else season - 1
    feats = scheme.get((s, team)) or {}
    peers = [v for (ss, _), v in scheme.items() if ss == s]
    out = {}
    for k, v in feats.items():
        vals = [p[k] for p in peers if p.get(k) is not None]
        if v is None or len(vals) < 8:
            continue
        pct = sum(1 for x in vals if x < v) / (len(vals) - 1) if len(vals) > 1 else 0.5
        out[k] = {"value": round(v, 3), "league_avg": round(sum(vals) / len(vals), 3),
                  "percentile": round(pct * 100), "label": "high" if pct >= 0.75 else "low" if pct <= 0.25 else "average",
                  "season": s}
    return out


def describe(features: dict) -> str:
    """Short human-readable scheme label from raw features (used in the report)."""
    if not features:
        return ""
    bits = []
    b = features.get("blitz_rate")
    if b is not None:
        bits.append(f"blitz {b:.0%}")
    m = features.get("man_rate")
    if m is not None:
        bits.append(f"man {m:.0%}")
    h = features.get("heavy_box_rate")
    if h is not None:
        bits.append(f"8+ box {h:.0%}")
    pe, re_ = features.get("pass_epa"), features.get("rush_epa")
    if pe is not None and re_ is not None:
        bits.append(f"EPA/db {pe:+.2f}, EPA/rush {re_:+.2f}")
    return ", ".join(bits)


if __name__ == "__main__":  # quick manual check
    f, w = scheme_features([int(sys.argv[1])], int(sys.argv[1]))
    for k in sorted(f)[:5]:
        print(k, f[k])
    print(w)
