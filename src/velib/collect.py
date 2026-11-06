"""Collect one snapshot of Vélib' Métropole station data plus Paris weather.

What one run does
-----------------
1. Download the two Vélib' GBFS feeds:
      - station_status.json       (bikes / docks right now, changes every minute)
      - station_information.json  (name, lat/lon, capacity, changes rarely)
2. Download the current Paris weather from Open-Meteo (free, no API key).
3. Clean the columns, join them into one table (one row per station).
4. Validate the numbers and flag impossible rows.
5. Save the snapshot as a small Parquet file (see storage.py), save the
   station list once per day, and compact finished days into one daily file.

Run it locally with:
    python -m velib.collect          (from the repo root, with PYTHONPATH=src)

Exit codes: 0 = success, 1 = the Vélib' data could not be collected or is
broken. A weather failure does NOT fail the run: weather columns are left
empty and a warning is logged, because bike data is the priority.

Data licence: Vélib' data © Smovengo / Syndicat Autolib' Vélib' Métropole,
Open Database License (ODbL). Weather: Open-Meteo.com (CC BY 4.0).
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from velib import storage

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GBFS_BASE = "https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole"
STATUS_URL = f"{GBFS_BASE}/station_status.json"
INFO_URL = f"{GBFS_BASE}/station_information.json"

# Paris centre (Hôtel de Ville). One weather point is enough for the city.
PARIS_LAT, PARIS_LON = 48.8566, 2.3522
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_FIELDS = [
    "temperature_2m",
    "precipitation",
    "rain",
    "wind_speed_10m",
    "cloud_cover",
    "weather_code",
]

TIMEOUT_SECONDS = (5, 30)  # (connect timeout, read timeout)
MIN_EXPECTED_STATIONS = 1000  # Vélib' has ~1,400. Fewer means a broken feed.
STALE_FEED_MINUTES = 30  # Warn if the operator's data is older than this.

USER_AGENT = (
    "paris-velib-operations-intelligence/0.1 (+https://github.com/thirurishi/paris-velib-operations-intelligence)"
)

log = logging.getLogger("velib.collect")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def make_session() -> requests.Session:
    """Create an HTTP session that retries automatically on temporary errors.

    Retries 4 times with exponential back-off (1s, 2s, 4s, 8s) on connection
    errors and on HTTP 429 / 5xx responses.
    """
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = USER_AGENT
    return session


def fetch_json(session: requests.Session, url: str, **params: Any) -> dict:
    """GET a URL and return the parsed JSON, raising on any HTTP error."""
    response = session.get(url, params=params or None, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Parsing: raw JSON -> clean DataFrames
# ---------------------------------------------------------------------------
def _stations(payload: dict, feed_name: str) -> list[dict]:
    """Return the list of stations from a GBFS payload, or raise if missing."""
    try:
        stations = payload["data"]["stations"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{feed_name}: unexpected format, no data.stations") from exc
    if not isinstance(stations, list) or not stations:
        raise ValueError(f"{feed_name}: data.stations is empty")
    return stations


def _bike_types(raw: Any) -> dict[str, int]:
    """Turn Vélib's bike-type breakdown into {'mechanical': n, 'ebike': n}.

    Vélib' (GBFS 1.0) sends a list of one-key dicts:
        [{"mechanical": 3}, {"ebike": 2}]
    Some GBFS versions send a single dict instead. Both are handled.
    """
    merged: dict[str, int] = {}
    if isinstance(raw, dict):
        raw = [raw]
    for item in raw or []:
        if isinstance(item, dict):
            for key, value in item.items():
                if isinstance(value, (int, float)):
                    merged[key] = int(value)
    return merged


def _first(row: dict, *keys: str) -> Any:
    """Return the first non-null value among several possible field names."""
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def parse_station_status(payload: dict) -> pd.DataFrame:
    """Clean station_status.json into one row per station."""
    rows = []
    for st in _stations(payload, "station_status"):
        types = _bike_types(st.get("num_bikes_available_types"))
        rows.append(
            {
                "station_id": st.get("station_id"),
                "station_code": st.get("stationCode"),
                "bikes_mechanical": types.get("mechanical"),
                "bikes_ebike": types.get("ebike"),
                "bikes_total": _first(st, "num_bikes_available", "numBikesAvailable"),
                "docks_available": _first(st, "num_docks_available", "numDocksAvailable"),
                "is_installed": st.get("is_installed"),
                "is_renting": st.get("is_renting"),
                "is_returning": st.get("is_returning"),
                "last_reported": st.get("last_reported"),
            }
        )
    df = pd.DataFrame(rows)

    df["station_id"] = pd.to_numeric(df["station_id"], errors="coerce").astype("Int64")
    df["station_code"] = df["station_code"].astype("string")
    for col in ("bikes_mechanical", "bikes_ebike", "bikes_total", "docks_available"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int32")
    for col in ("is_installed", "is_renting", "is_returning"):
        # GBFS 1.0 uses 0/1, newer versions use true/false: both become bool.
        as_int = df[col].map(lambda v: int(v) if isinstance(v, (bool, int)) else None)
        df[col] = pd.to_numeric(as_int, errors="coerce").astype("Int8").astype("boolean")
    df["last_reported"] = pd.to_datetime(pd.to_numeric(df["last_reported"], errors="coerce"), unit="s", utc=True)
    return df


def parse_station_information(payload: dict) -> pd.DataFrame:
    """Clean station_information.json into one row per station."""
    df = pd.DataFrame(_stations(payload, "station_information"))
    df = df.rename(columns={"stationCode": "station_code"})
    for col in ("station_id", "station_code", "name", "lat", "lon", "capacity"):
        if col not in df.columns:
            df[col] = None
    df = df[["station_id", "station_code", "name", "lat", "lon", "capacity"]].copy()
    df["station_id"] = pd.to_numeric(df["station_id"], errors="coerce").astype("Int64")
    df["station_code"] = df["station_code"].astype("string")
    df["name"] = df["name"].astype("string")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce").astype("float64")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce").astype("float64")
    df["capacity"] = pd.to_numeric(df["capacity"], errors="coerce").astype("Int32")
    return df.drop_duplicates("station_id").reset_index(drop=True)


def parse_weather(payload: dict) -> dict[str, Any]:
    """Pick the useful fields from Open-Meteo's 'current' block."""
    current = payload.get("current") or {}
    return {
        "weather_temp_c": current.get("temperature_2m"),
        "weather_precip_mm": current.get("precipitation"),
        "weather_rain_mm": current.get("rain"),
        "weather_wind_kmh": current.get("wind_speed_10m"),
        "weather_cloud_pct": current.get("cloud_cover"),
        "weather_code": current.get("weather_code"),
    }


EMPTY_WEATHER = dict.fromkeys(parse_weather({"current": {}}), None)


def feed_age_minutes(payload: dict, now: datetime) -> float | None:
    """How old the operator's data is, in minutes (None if unknown)."""
    stamp = _first(payload, "lastUpdatedOther", "last_updated")
    if stamp is None:
        return None
    updated = datetime.fromtimestamp(int(stamp), tz=UTC)
    return round((now - updated).total_seconds() / 60, 1)


# ---------------------------------------------------------------------------
# Build + validate the snapshot
# ---------------------------------------------------------------------------
def build_snapshot(
    status: pd.DataFrame,
    info: pd.DataFrame,
    weather: dict[str, Any],
    snapshot_ts: datetime,
) -> pd.DataFrame:
    """Join status + capacity + weather into the final snapshot table."""
    df = status.merge(info[["station_id", "capacity"]], on="station_id", how="left")

    # Fill station_code from station_information if station_status lacks it.
    if df["station_code"].isna().any():
        codes = info.set_index("station_id")["station_code"]
        df["station_code"] = df["station_code"].fillna(df["station_id"].map(codes))

    ts = pd.Timestamp(snapshot_ts).tz_convert("UTC").floor("s")
    df.insert(0, "snapshot_ts", ts)
    # slot_ts = the 15-minute slot this snapshot belongs to (e.g. 08:07 -> 08:00).
    # Makes joins and gap checks easy even when GitHub runs a few minutes late.
    df.insert(1, "slot_ts", ts.floor("15min"))

    for key, value in weather.items():
        df[key] = value
    return df


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Flag impossible rows instead of silently dropping them.

    Adds two columns:
      is_valid          True if the row passed every rule
      validation_issue  short text describing the first problem found
    Rows are kept so we can measure data quality later (Phase 2).
    """
    df = df.drop_duplicates("station_id", keep="last").reset_index(drop=True)

    rules: list[tuple[str, pd.Series]] = [
        ("missing_station_id", df["station_id"].isna()),
        ("missing_counts", df["bikes_total"].isna() | df["docks_available"].isna()),
        ("negative_count", (df["bikes_total"] < 0) | (df["docks_available"] < 0)),
        (
            "bikes_plus_docks_over_capacity",
            (df["bikes_total"] + df["docks_available"]) > df["capacity"],
        ),
        (
            "type_breakdown_mismatch",
            (df["bikes_mechanical"] + df["bikes_ebike"]) != df["bikes_total"],
        ),
        ("unknown_station", df["capacity"].isna()),
    ]

    issue = pd.Series(pd.NA, index=df.index, dtype="string")
    for name, mask in rules:
        mask = mask.fillna(False).astype(bool)
        issue = issue.mask(mask & issue.isna(), name)
        if mask.any():
            log.warning("Validation: %d rows with %s", int(mask.sum()), name)

    df["validation_issue"] = issue
    df["is_valid"] = issue.isna()
    return df


def check_feed_health(df: pd.DataFrame) -> None:
    """Stop the run if the feed is clearly broken (better a red run than bad data)."""
    n = df["station_id"].nunique()
    if n < MIN_EXPECTED_STATIONS:
        raise ValueError(f"Only {n} stations in feed (expected >= {MIN_EXPECTED_STATIONS})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def fetch_snapshot(now: datetime | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Download and clean one snapshot WITHOUT saving it.

    Returns (snapshot, station_information, weather). Used by the collector and
    by the Streamlit app for live data.
    """
    now = now or datetime.now(UTC)
    session = make_session()

    log.info("Fetching Vélib' feeds")
    status_payload = fetch_json(session, STATUS_URL)
    info_payload = fetch_json(session, INFO_URL)

    age = feed_age_minutes(status_payload, now)
    if age is not None and age > STALE_FEED_MINUTES:
        log.warning("station_status feed looks stale: last updated %.0f min ago", age)

    status = parse_station_status(status_payload)
    info = parse_station_information(info_payload)

    log.info("Fetching Open-Meteo weather")
    try:
        weather = parse_weather(
            fetch_json(
                session,
                WEATHER_URL,
                latitude=PARIS_LAT,
                longitude=PARIS_LON,
                current=",".join(WEATHER_FIELDS),
                timezone="UTC",
            )
        )
    except (requests.RequestException, ValueError) as exc:
        log.warning("Weather unavailable, saving snapshot without it: %s", exc)
        weather = dict(EMPTY_WEATHER)

    snapshot = validate(build_snapshot(status, info, weather, now))
    check_feed_health(snapshot)
    return snapshot, info, weather


def collect(now: datetime | None = None) -> pd.DataFrame | None:
    """Run one full collection and return the saved snapshot.

    Returns None (and saves nothing) if this 15-minute slot was already collected.
    """
    now = now or datetime.now(UTC)
    if storage.slot_already_collected(now):
        log.info("Slot %s already collected, skipping", pd.Timestamp(now).floor("15min"))
        return None

    snapshot, info, weather = fetch_snapshot(now)
    path = storage.write_snapshot(snapshot, now)
    storage.save_stations(info, now)
    storage.compact_past_days(now)

    log.info(
        "Saved %d stations (%d invalid) to %s | bikes=%d docks=%d empty=%d full=%d temp=%s",
        len(snapshot),
        int((~snapshot["is_valid"]).sum()),
        path,
        int(snapshot["bikes_total"].sum()),
        int(snapshot["docks_available"].sum()),
        int((snapshot["bikes_total"] == 0).sum()),
        int((snapshot["docks_available"] == 0).sum()),
        weather["weather_temp_c"],
    )
    return snapshot


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    try:
        collect()
    except Exception:  # noqa: BLE001 - log everything, then fail the run clearly
        log.exception("Collection failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
