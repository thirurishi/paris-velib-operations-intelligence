"""Data-quality report for the collected snapshots.

Run it any time (locally, after `git pull`):
    python -m velib.check_data

It answers the Phase 1 "done" question: did ~96 snapshots arrive per day,
with no big gaps, and does the data look sane?
"""

from __future__ import annotations

import sys

import pandas as pd

from velib import storage

EXPECTED_PER_DAY = 96  # 24 h x 4 runs per hour
GAP_WARNING_MINUTES = 30  # GitHub may run a few minutes late; 30+ is a real gap


def build_report(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (one row per snapshot, one row per UTC day)."""
    per_snapshot = (
        df.groupby("snapshot_ts")
        .agg(
            stations=("station_id", "nunique"),
            invalid=("is_valid", lambda s: int((~s).sum())),
            empty=("bikes_total", lambda s: int((s == 0).sum())),
            full=("docks_available", lambda s: int((s == 0).sum())),
            bikes=("bikes_total", "sum"),
            has_weather=("weather_temp_c", lambda s: bool(s.notna().any())),
        )
        .reset_index()
        .sort_values("snapshot_ts")
    )
    per_snapshot["gap_min"] = per_snapshot["snapshot_ts"].diff().dt.total_seconds() / 60
    per_snapshot["day"] = per_snapshot["snapshot_ts"].dt.strftime("%Y-%m-%d")

    per_day = (
        per_snapshot.groupby("day")
        .agg(
            snapshots=("snapshot_ts", "count"),
            max_gap_min=("gap_min", "max"),
            gaps_over_30min=("gap_min", lambda s: int((s > GAP_WARNING_MINUTES).sum())),
            stations_min=("stations", "min"),
            stations_max=("stations", "max"),
            invalid_rows=("invalid", "sum"),
            avg_empty_stations=("empty", "mean"),
            avg_full_stations=("full", "mean"),
            weather_missing=("has_weather", lambda s: int((~s).sum())),
        )
        .round(1)
    )
    return per_snapshot, per_day


def main() -> int:
    df = storage.read_all_snapshots()
    if df.empty:
        print(f"No data found under {storage.DATA_DIR.resolve()}. Did you `git pull`?")
        return 1

    per_snapshot, per_day = build_report(df)
    rows = len(df)
    first, last = per_snapshot["snapshot_ts"].iloc[[0, -1]]
    hours = (last - first).total_seconds() / 3600

    pd.set_option("display.width", 160)
    print(f"Rows: {rows:,} | snapshots: {len(per_snapshot)} | from {first} to {last} ({hours:.1f} h)")
    print(f"Expected snapshots in that window: ~{int(hours * 4) + 1}\n")
    print("Per UTC day (a full day should have ~96 snapshots):")
    print(per_day.to_string(), "\n")

    big_gaps = per_snapshot[per_snapshot["gap_min"] > GAP_WARNING_MINUTES]
    if not big_gaps.empty:
        print(f"Gaps longer than {GAP_WARNING_MINUTES} min (snapshot AFTER the gap):")
        print(big_gaps[["snapshot_ts", "gap_min"]].to_string(index=False), "\n")

    issues = df.loc[~df["is_valid"], "validation_issue"].value_counts()
    print("Validation issues (rows):" if not issues.empty else "Validation issues: none")
    if not issues.empty:
        print(issues.to_string())

    dupes = df.duplicated(["snapshot_ts", "station_id"]).sum()
    print(f"\nDuplicate (snapshot, station) rows: {dupes}")
    print("Latest snapshot:")
    print(per_snapshot.tail(1).drop(columns=["day"]).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
