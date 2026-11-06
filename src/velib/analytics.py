"""Phase 2: turn raw snapshots into analysis tables (DuckDB) and written insights.

Run (from the repo root):
    python -m velib.analytics

Outputs
-------
data/marts/*.parquet       analysis tables, read by Power BI and the notebooks
reports/insights.md        key findings in plain English (regenerated every run)
reports/insights.json      the same numbers, used by the README and the app
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd

from velib import geo, storage

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "sql"
REPORTS_DIR = ROOT / "reports"
MARTS = [
    "mart_station_hourly",
    "mart_station_reliability",
    "mart_network_profile",
    "mart_area_summary",
    "mart_weather_effect",
    "mart_data_quality",
]

log = logging.getLogger("velib.analytics")


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def snapshot_files() -> list[str]:
    files = sorted((storage.DATA_DIR / "daily").glob("*.parquet"))
    files += sorted((storage.DATA_DIR / "raw").glob("date=*/*.parquet"))
    return [str(f) for f in files]


def latest_stations() -> pd.DataFrame:
    """Most recent station list, with area labels."""
    files = sorted((storage.DATA_DIR / "stations").glob("stations_*.parquet"))
    if not files:
        raise FileNotFoundError("No station list in data/stations/. Run the collector first.")
    return geo.add_area(pd.read_parquet(files[-1]))


def connect() -> duckdb.DuckDBPyConnection:
    """DuckDB connection with `raw_snapshots`, `stations` and `stg_snapshots` ready."""
    files = snapshot_files()
    if not files:
        raise FileNotFoundError("No snapshots found. Run the collector first.")
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    file_list = ", ".join(f"'{f}'" for f in files)
    con.execute(f"CREATE VIEW raw_snapshots AS SELECT * FROM read_parquet([{file_list}], union_by_name = true)")
    con.register("stations_df", latest_stations())
    con.execute("CREATE TABLE stations AS SELECT * FROM stations_df")
    con.execute((SQL_DIR / "00_stg_snapshots.sql").read_text(encoding="utf-8"))
    return con


def build_marts(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    """Run every sql/0X_*.sql model (except staging) and return the tables."""
    for path in sorted(SQL_DIR.glob("0[1-9]_*.sql")):
        log.info("Running %s", path.name)
        con.execute(path.read_text(encoding="utf-8"))
    return {name: con.table(name).df() for name in MARTS}


def export_marts(marts: dict[str, pd.DataFrame], stations: pd.DataFrame) -> Path:
    out = storage.DATA_DIR / "marts"
    out.mkdir(parents=True, exist_ok=True)
    for name, df in marts.items():
        df.to_parquet(out / f"{name}.parquet", index=False)
    stations.to_parquet(out / "dim_station.parquet", index=False)
    log.info("Exported %d tables to %s", len(marts) + 1, out)
    return out


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------
def _pct(x: float | None) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{100 * x:.1f}%"


def compute_insights(marts: dict[str, pd.DataFrame]) -> dict:
    """Headline numbers. Every value is computed from the data, nothing hard-coded."""
    rel = marts["mart_station_reliability"].copy()
    prof = marts["mart_network_profile"]
    area = marts["mart_area_summary"]
    wx = marts["mart_weather_effect"]
    dq = marts["mart_data_quality"]

    ins: dict = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "days_collected": int(dq["utc_date"].nunique()),
        "snapshots": int(dq["snapshots"].sum()),
        "first_day": str(dq["utc_date"].min()),
        "last_day": str(dq["utc_date"].max()),
        "stations": int(rel["station_id"].nunique()),
        "avg_coverage": float(dq["coverage"].clip(upper=1).mean()),
    }

    # 1. Concentration of empty-station hours
    total_empty = rel["empty_hours_per_day"].sum()
    top20 = rel.nlargest(20, "empty_hours_per_day")
    ins["empty_station_hours_per_day"] = round(float(total_empty), 1)
    ins["top20_share_of_empty_hours"] = float(top20["empty_hours_per_day"].sum() / total_empty) if total_empty else None
    ins["top20_empty_stations"] = top20[["station_code", "name", "area", "empty_hours_per_day"]].to_dict("records")
    ins["full_station_hours_per_day"] = round(float(rel["full_hours_per_day"].sum()), 1)
    ins["top20_full_stations"] = rel.nlargest(20, "full_hours_per_day")[
        ["station_code", "name", "area", "full_hours_per_day"]
    ].to_dict("records")

    # 2. Worst weekday hours
    wd = prof[prof["day_type"] == "weekday"]
    if not wd.empty:
        e, f = wd.loc[wd["share_stations_empty"].idxmax()], wd.loc[wd["share_stations_full"].idxmax()]
        ins["weekday_peak_empty_hour"] = int(e["local_hour"])
        ins["weekday_peak_empty_share"] = float(e["share_stations_empty"])
        ins["weekday_peak_full_hour"] = int(f["local_hour"])
        ins["weekday_peak_full_share"] = float(f["share_stations_full"])
        ins["weekday_min_empty_share"] = float(wd["share_stations_empty"].min())

    # 3. Morning commute flow: areas that drain vs fill between 5am and 9am
    a = area.dropna(subset=["fill_ratio_9am", "fill_ratio_5am"]).copy()
    a = a[a["stations"] >= 5]
    if not a.empty:
        a["am_change"] = a["fill_ratio_9am"] - a["fill_ratio_5am"]
        ins["areas_draining_am"] = a.nsmallest(3, "am_change")[["area", "am_change"]].to_dict("records")
        ins["areas_filling_am"] = a.nlargest(3, "am_change")[["area", "am_change"]].to_dict("records")

    # 4. Rain effect (weekday daytime, needs enough slots of each kind)
    w = wx[wx["day_type"] == "weekday"].groupby("rain_bucket")
    slots = w["slots"].sum()
    act = w.apply(lambda g: (g["avg_activity_per_station_slot"] * g["slots"]).sum() / g["slots"].sum())
    wet = slots.reindex(["light rain", "rain"]).fillna(0).sum()
    if slots.get("dry", 0) >= 8 and wet >= 8:
        wet_act = sum(act.get(k, 0) * slots.get(k, 0) for k in ("light rain", "rain")) / wet
        ins["rain_activity_change"] = float(wet_act / act["dry"] - 1)
    else:
        ins["rain_activity_change"] = None
    return ins


def render_insights(ins: dict) -> str:
    lines = [
        "# Key insights",
        "",
        f"_Auto-generated from {ins['snapshots']:,} snapshots over {ins['days_collected']} day(s) "
        f"({ins['first_day']} → {ins['last_day']}, average coverage {_pct(ins['avg_coverage'])}). "
        f"Updated {ins['generated_at']} UTC. Re-run `python -m velib.analytics` to refresh._",
        "",
    ]
    if ins["snapshots"] < 96:
        lines.append(
            f"> ⏳ Only {ins['snapshots']} snapshots so far: insights appear after one full day of collection."
        )
        return "\n".join(lines) + "\n"
    if ins["days_collected"] < 7:
        lines += [
            "> ⚠️ **Less than one week of data.** Treat these numbers as preliminary: "
            "weekly patterns and weather effects need several weeks to be reliable.",
            "",
        ]
    lines.append(
        f"1. **Shortages are concentrated.** Across {ins['stations']:,} stations the network logs "
        f"**{ins['empty_station_hours_per_day']:,} empty-station hours per day**; the worst 20 stations "
        f"cause **{_pct(ins['top20_share_of_empty_hours'])}** of them."
    )
    if "weekday_peak_empty_hour" in ins:
        lines.append(
            f"2. **Rush hours drive the problem.** On weekdays the share of empty stations peaks at "
            f"**{ins['weekday_peak_empty_hour']}:00** ({_pct(ins['weekday_peak_empty_share'])} of stations, vs "
            f"{_pct(ins['weekday_min_empty_share'])} at the quietest hour); full stations peak at "
            f"**{ins['weekday_peak_full_hour']}:00** ({_pct(ins['weekday_peak_full_share'])})."
        )
    if ins.get("areas_draining_am"):
        drain = ", ".join(f"{r['area']} ({r['am_change']:+.0%})" for r in ins["areas_draining_am"])
        fill = ", ".join(f"{r['area']} ({r['am_change']:+.0%})" for r in ins["areas_filling_am"])
        lines.append(
            f"3. **Morning commute moves bikes one way.** Between 5:00 and 9:00 on weekdays, stations drain "
            f"in {drain} and fill up in {fill} (change in average fill ratio)."
        )
    if ins.get("rain_activity_change") is not None:
        lines.append(
            f"4. **Rain cuts usage.** Weekday daytime activity is **{ins['rain_activity_change']:+.0%}** "
            "in rainy slots compared with dry ones."
        )
    else:
        lines.append("4. **Rain effect:** not enough rainy and dry slots yet to compare.")
    lines += [
        "",
        "## Worst 20 stations (empty hours per day)",
        "",
        "| Code | Station | Area | Empty h/day |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {r['station_code']} | {r['name']} | {r['area']} | {r['empty_hours_per_day']:.1f} |"
        for r in ins["top20_empty_stations"]
    ]
    return "\n".join(lines) + "\n"


def write_reports(ins: dict) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "insights.md").write_text(render_insights(ins), encoding="utf-8")
    (REPORTS_DIR / "insights.json").write_text(json.dumps(ins, indent=2, default=str), encoding="utf-8")


def run() -> dict:
    con = connect()
    stations = con.table("stations").df()
    marts = build_marts(con)
    export_marts(marts, stations)
    ins = compute_insights(marts)
    write_reports(ins)
    log.info("Insights written to %s", REPORTS_DIR / "insights.md")
    return ins


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run()
    except Exception:  # noqa: BLE001
        log.exception("Analytics failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
