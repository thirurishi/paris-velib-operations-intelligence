"""Phase 3: feature engineering for the +1h / +2h forecasts.

One row = one station at one 15-minute slot `t`. Everything a feature uses is
known at time t (no peeking into the future). Targets look 4 and 8 slots ahead.

Feature groups
--------------
now        bikes, docks, e-bikes, fill ratio, usable capacity
momentum   change in bikes over the last 15 / 30 / 60 minutes
seasonal   bikes at the same time yesterday / last week, and at the TARGET time
           yesterday / last week (the strongest signal for rush-hour flows)
calendar   hour, weekday, weekend, French public holiday, Paris school holiday
weather    temperature, precipitation, wind (at time t)
station    capacity, lat / lon, spatial cluster
"""

from __future__ import annotations

from datetime import date

import holidays
import numpy as np
import pandas as pd

SLOT = pd.Timedelta(minutes=15)
HORIZONS = {"1h": 4, "2h": 8}  # name -> number of 15-min slots ahead

# Paris (Zone C) school holidays, official calendar 2026-2027 (service-public.gouv.fr).
# Start = first day without school, end = day classes restart (exclusive).
SCHOOL_HOLIDAYS_ZONE_C = [
    (date(2026, 7, 4), date(2026, 9, 1)),  # summer 2026
    (date(2026, 10, 17), date(2026, 11, 2)),  # Toussaint
    (date(2026, 12, 19), date(2027, 1, 4)),  # Noël
    (date(2027, 2, 6), date(2027, 2, 22)),  # Hiver (zone C)
    (date(2027, 4, 3), date(2027, 4, 19)),  # Printemps (zone C)
    (date(2027, 7, 3), date(2027, 9, 1)),  # summer 2027
]

FEATURES = [
    "bikes_now",
    "docks_now",
    "ebikes_now",
    "usable_capacity",
    "fill_now",
    "d_bikes_15m",
    "d_bikes_30m",
    "d_bikes_60m",
    "bikes_lag_1d",
    "bikes_lag_7d",
    "seas_1d_1h",
    "seas_1d_2h",
    "seas_7d_1h",
    "seas_7d_2h",
    "hour",
    "dow",
    "is_weekend",
    "is_holiday",
    "is_school_holiday",
    "weather_temp_c",
    "weather_precip_mm",
    "weather_wind_kmh",
    "capacity",
    "lat",
    "lon",
    "cluster",
]
TARGETS = ["bikes_1h", "bikes_2h", "docks_1h", "docks_2h", "empty_2h", "full_2h"]


def prepare(snapshots: pd.DataFrame) -> pd.DataFrame:
    """Keep usable rows: open stations, sane counts, one row per station per slot."""
    df = snapshots[
        snapshots["is_installed"].fillna(False)
        & snapshots["is_renting"].fillna(False)
        & snapshots["is_returning"].fillna(False)
        & snapshots["bikes_total"].notna()
        & snapshots["docks_available"].notna()
        & (snapshots["bikes_total"] >= 0)
        & (snapshots["docks_available"] >= 0)
    ]
    cols = [
        "slot_ts",
        "station_id",
        "bikes_total",
        "docks_available",
        "bikes_ebike",
        "weather_temp_c",
        "weather_precip_mm",
        "weather_wind_kmh",
    ]
    df = df[cols].sort_values("slot_ts").drop_duplicates(["station_id", "slot_ts"], keep="last")
    return df.reset_index(drop=True)


def _lookup(series: pd.Series, station_id: pd.Series, ts: pd.Series) -> np.ndarray:
    """Value of `series` (indexed by station_id, slot_ts) at the given keys, NaN if missing."""
    idx = pd.MultiIndex.from_arrays([station_id.to_numpy(), ts.to_numpy()], names=series.index.names)
    return series.reindex(idx).to_numpy(dtype="float64")


def school_holiday_flags(local_dates: pd.Series) -> np.ndarray:
    d = pd.to_datetime(local_dates).dt.date
    flag = np.zeros(len(d), dtype=bool)
    for start, end in SCHOOL_HOLIDAYS_ZONE_C:
        flag |= ((d >= start) & (d < end)).to_numpy()
    return flag


def fit_clusters(stations: pd.DataFrame, k: int = 20, seed: int = 0) -> np.ndarray:
    """Spatial clusters of stations (K-means on lat/lon). Returns the centroids."""
    from sklearn.cluster import KMeans

    xy = stations[["lat", "lon"]].dropna().to_numpy()
    k = min(k, len(xy))
    return KMeans(n_clusters=k, n_init=10, random_state=seed).fit(xy).cluster_centers_


def assign_clusters(lat: pd.Series, lon: pd.Series, centroids: np.ndarray) -> np.ndarray:
    xy = np.column_stack([lat.to_numpy(), lon.to_numpy()])
    d = ((xy[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
    return d.argmin(axis=1)


def build_features(
    df: pd.DataFrame,
    stations: pd.DataFrame,
    centroids: np.ndarray,
    with_targets: bool = True,
    only_latest: bool = False,
) -> pd.DataFrame:
    """Add features (and optionally targets) to prepared snapshots.

    only_latest=True keeps just the most recent slot per station (for live prediction),
    but still uses the full history passed in for lags.
    """
    key = ["station_id", "slot_ts"]
    hist = df.set_index(key)
    bikes = hist["bikes_total"].astype("float64")
    docks = hist["docks_available"].astype("float64")

    out = df.copy()
    if only_latest:
        out = out.sort_values("slot_ts").groupby("station_id").tail(1).reset_index(drop=True)
    sid, ts = out["station_id"], out["slot_ts"]

    out["bikes_now"] = out["bikes_total"].astype("float64")
    out["docks_now"] = out["docks_available"].astype("float64")
    out["ebikes_now"] = out["bikes_ebike"].astype("float64")
    out["usable_capacity"] = out["bikes_now"] + out["docks_now"]
    out["fill_now"] = out["bikes_now"] / out["usable_capacity"].replace(0, np.nan)

    for minutes in (15, 30, 60):
        out[f"d_bikes_{minutes}m"] = out["bikes_now"] - _lookup(bikes, sid, ts - pd.Timedelta(minutes=minutes))
    day, week = pd.Timedelta(days=1), pd.Timedelta(days=7)
    out["bikes_lag_1d"] = _lookup(bikes, sid, ts - day)
    out["bikes_lag_7d"] = _lookup(bikes, sid, ts - week)
    for name, steps in HORIZONS.items():
        out[f"seas_1d_{name}"] = _lookup(bikes, sid, ts + steps * SLOT - day)
        out[f"seas_7d_{name}"] = _lookup(bikes, sid, ts + steps * SLOT - week)

    local = ts.dt.tz_convert("Europe/Paris")
    out["hour"] = local.dt.hour + local.dt.minute / 60
    out["dow"] = local.dt.dayofweek
    out["is_weekend"] = (out["dow"] >= 5).astype(int)
    years = range(local.dt.year.min(), local.dt.year.max() + 2)
    fr = holidays.France(years=years)
    out["is_holiday"] = local.dt.date.map(lambda d: d in fr).astype(int)
    out["is_school_holiday"] = school_holiday_flags(local.dt.date).astype(int)

    st = stations[["station_id", "capacity", "lat", "lon"]].drop_duplicates("station_id")
    out = out.merge(st, on="station_id", how="left")
    out["cluster"] = -1
    has_xy = out["lat"].notna() & out["lon"].notna()
    out.loc[has_xy, "cluster"] = assign_clusters(out.loc[has_xy, "lat"], out.loc[has_xy, "lon"], centroids)

    if with_targets:
        sid, ts = out["station_id"], out["slot_ts"]
        for name, steps in HORIZONS.items():
            out[f"bikes_{name}"] = _lookup(bikes, sid, ts + steps * SLOT)
            out[f"docks_{name}"] = _lookup(docks, sid, ts + steps * SLOT)
        future_b = np.column_stack([_lookup(bikes, sid, ts + k * SLOT) for k in range(1, 9)])
        future_d = np.column_stack([_lookup(docks, sid, ts + k * SLOT) for k in range(1, 9)])
        enough = np.isfinite(future_b).sum(axis=1) >= 6  # allow up to 2 missed slots
        with np.errstate(invalid="ignore"):
            out["empty_2h"] = np.where(enough, (np.nanmin(np.where(enough[:, None], future_b, 1), axis=1) == 0), np.nan)
            out["full_2h"] = np.where(enough, (np.nanmin(np.where(enough[:, None], future_d, 1), axis=1) == 0), np.nan)
    return out
