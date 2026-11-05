"""Save and load Vélib' snapshots as Parquet files.

Folder layout (all dates are UTC)
---------------------------------
data/
├── raw/date=2026-11-06/velib_2026-11-06T080712Z.parquet   one file per run (~30 KB)
├── daily/velib_2026-11-05.parquet                          one file per finished day
└── stations/stations_2026-11-06.parquet                    station list, once per day

Why not append to one daily file on every run?
    Parquet files cannot be appended to: "appending" means rewriting the whole
    file. If GitHub Actions committed a rewritten 1-2 MB file 96 times a day,
    git would keep every version and the repo would grow by ~100 MB per day.
    Instead, each run writes a small new file (never changed again), and the
    first run after midnight UTC merges yesterday's files into ONE daily file
    and deletes the small ones. You still get "one Parquet file per day".

Reading everything (Phase 2), e.g. with DuckDB:
    SELECT * FROM read_parquet(['data/daily/*.parquet', 'data/raw/*/*.parquet'])
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger("velib.storage")

DATA_DIR = Path(os.environ.get("VELIB_DATA_DIR", "data"))

# A fixed schema means every file has identical column types, so files can
# always be concatenated and read together by pandas, DuckDB or Power BI.
SNAPSHOT_SCHEMA = pa.schema(
    [
        ("snapshot_ts", pa.timestamp("us", tz="UTC")),
        ("slot_ts", pa.timestamp("us", tz="UTC")),
        ("station_id", pa.int64()),
        ("station_code", pa.string()),
        ("capacity", pa.int32()),
        ("bikes_mechanical", pa.int32()),
        ("bikes_ebike", pa.int32()),
        ("bikes_total", pa.int32()),
        ("docks_available", pa.int32()),
        ("is_installed", pa.bool_()),
        ("is_renting", pa.bool_()),
        ("is_returning", pa.bool_()),
        ("last_reported", pa.timestamp("us", tz="UTC")),
        ("weather_temp_c", pa.float32()),
        ("weather_precip_mm", pa.float32()),
        ("weather_rain_mm", pa.float32()),
        ("weather_wind_kmh", pa.float32()),
        ("weather_cloud_pct", pa.float32()),
        ("weather_code", pa.int16()),
        ("is_valid", pa.bool_()),
        ("validation_issue", pa.string()),
    ]
)

STATIONS_SCHEMA = pa.schema(
    [
        ("station_id", pa.int64()),
        ("station_code", pa.string()),
        ("name", pa.string()),
        ("lat", pa.float64()),
        ("lon", pa.float64()),
        ("capacity", pa.int32()),
    ]
)


def _utc(ts: datetime) -> datetime:
    return ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)


def _to_table(df: pd.DataFrame, schema: pa.Schema) -> pa.Table:
    """Convert a DataFrame to Arrow using the fixed schema (column order + types)."""
    df = df.copy()
    for field in schema:
        if field.name not in df.columns:
            df[field.name] = None
    # Weather may arrive as plain Python None/float/int: make the types explicit.
    for field in schema:
        if pa.types.is_floating(field.type):
            df[field.name] = pd.to_numeric(df[field.name], errors="coerce").astype("float64")
        elif pa.types.is_integer(field.type) and df[field.name].dtype == object:
            df[field.name] = pd.to_numeric(df[field.name], errors="coerce").astype("Int64")
    return pa.Table.from_pandas(df[schema.names], schema=schema, preserve_index=False)


def _atomic_write(table: pa.Table, path: Path) -> None:
    """Write to a temp file, then rename, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)


def raw_dir(day: str) -> Path:
    return DATA_DIR / "raw" / f"date={day}"


def daily_path(day: str) -> Path:
    return DATA_DIR / "daily" / f"velib_{day}.parquet"


def write_snapshot(df: pd.DataFrame, snapshot_ts: datetime) -> Path:
    """Save one snapshot as its own small Parquet file."""
    ts = _utc(snapshot_ts)
    path = raw_dir(ts.strftime("%Y-%m-%d")) / f"velib_{ts.strftime('%Y-%m-%dT%H%M%SZ')}.parquet"
    _atomic_write(_to_table(df, SNAPSHOT_SCHEMA), path)
    return path


def save_stations(info: pd.DataFrame, snapshot_ts: datetime) -> Path | None:
    """Save the station list once per UTC day (skip if today's file exists)."""
    day = _utc(snapshot_ts).strftime("%Y-%m-%d")
    path = DATA_DIR / "stations" / f"stations_{day}.parquet"
    if path.exists():
        return None
    _atomic_write(_to_table(info, STATIONS_SCHEMA), path)
    log.info("Saved station list (%d stations) to %s", len(info), path)
    return path


def compact_day(day: str) -> Path:
    """Merge all snapshot files of one day into data/daily/, then delete them."""
    files = sorted(raw_dir(day).glob("*.parquet"))
    tables = [pq.read_table(f, schema=SNAPSHOT_SCHEMA) for f in files]
    if daily_path(day).exists():  # a late file for an already-compacted day
        tables.insert(0, pq.read_table(daily_path(day), schema=SNAPSHOT_SCHEMA))
    table = pa.concat_tables(tables).sort_by([("snapshot_ts", "ascending"), ("station_id", "ascending")])
    _atomic_write(table, daily_path(day))
    shutil.rmtree(raw_dir(day))
    log.info("Compacted %d snapshot files for %s into %s", len(files), day, daily_path(day))
    return daily_path(day)


def compact_past_days(now: datetime) -> list[Path]:
    """Compact every finished day (any raw/date=... folder before today, UTC)."""
    today = _utc(now).strftime("%Y-%m-%d")
    done = []
    for folder in sorted((DATA_DIR / "raw").glob("date=*")):
        day = folder.name.removeprefix("date=")
        if day < today and any(folder.glob("*.parquet")):
            done.append(compact_day(day))
    return done


def read_all_snapshots() -> pd.DataFrame:
    """Load every snapshot (daily files + today's raw files) into one DataFrame."""
    files = sorted((DATA_DIR / "daily").glob("*.parquet")) + sorted(
        (DATA_DIR / "raw").glob("date=*/*.parquet")
    )
    if not files:
        return pd.DataFrame(columns=SNAPSHOT_SCHEMA.names)
    tables = [pq.read_table(f, schema=SNAPSHOT_SCHEMA) for f in files]
    return pa.concat_tables(tables).to_pandas()
