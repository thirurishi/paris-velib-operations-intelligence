"""Generate SYNTHETIC Vélib'-like history for testing and demos.

Not real data. It mimics the main real-world patterns so the pipeline, model and
optimiser can be developed before weeks of real data exist:
  - business areas (8e/9e, La Défense, 2e...) fill up in the morning and drain in
    the evening; residential areas do the opposite
  - weekends are flatter, rain reduces movement, hilltop stations run empty
  - random noise, a few missing slots

Usage (writes to a separate folder so it never mixes with real data):
    python -m velib.simulate --days 35 --out data_synthetic
    VELIB_DATA_DIR=data_synthetic python -m velib.analytics
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from velib import geo, storage

BUSINESS_HUBS = [  # (lat, lon, strength)
    (48.8722, 2.3106, 1.0),  # 8e - Saint-Lazare / Madeleine
    (48.8738, 2.3420, 0.8),  # 9e - Opéra
    (48.8920, 2.2380, 1.0),  # La Défense
    (48.8680, 2.3460, 0.6),  # 2e - Bourse
    (48.8400, 2.3730, 0.5),  # Gare de Lyon
]
HILLTOPS = [(48.8867, 2.3431), (48.8720, 2.3850)]  # Montmartre, Belleville


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def station_profiles(stations: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    st = stations.copy()
    biz = np.zeros(len(st))
    for lat, lon, strength in BUSINESS_HUBS:
        d = geo.haversine_km(st["lat"].to_numpy(), st["lon"].to_numpy(), lat, lon)
        biz = np.maximum(biz, strength * np.exp(-d / 1.2))
    st["business"] = np.clip(biz * 1.6 - 0.35 + rng.normal(0, 0.12, len(st)), -0.6, 1.0)
    hill = np.zeros(len(st))
    for lat, lon in HILLTOPS:
        d = geo.haversine_km(st["lat"].to_numpy(), st["lon"].to_numpy(), lat, lon)
        hill = np.maximum(hill, np.exp(-d / 0.6))
    st["base_fill"] = np.clip(0.45 - 0.30 * hill + rng.normal(0, 0.16, len(st)), 0.02, 0.92)
    st["capacity"] = st["capacity"].fillna(30).clip(lower=8).astype(int)
    return st


def weather_series(slots: pd.DatetimeIndex, rng: np.random.Generator) -> pd.DataFrame:
    local_hour = slots.tz_convert("Europe/Paris").hour + slots.minute / 60
    days = (slots - slots[0]).days
    temp = 8 + 4 * np.sin((local_hour - 9) / 24 * 2 * np.pi) - 0.08 * days + rng.normal(0, 0.7, len(slots))
    rain = np.zeros(len(slots))
    i = 0
    while i < len(slots):  # rain spells of 1-6 hours, ~20% of the time
        if rng.random() < 0.012:
            length = int(rng.integers(4, 24))
            rain[i : i + length] = rng.uniform(0.2, 2.5)
            i += length
        i += 1
    return pd.DataFrame(
        {
            "weather_temp_c": temp.round(1),
            "weather_precip_mm": rain.round(1),
            "weather_rain_mm": rain.round(1),
            "weather_wind_kmh": rng.uniform(5, 25, len(slots)).round(1),
            "weather_cloud_pct": np.where(rain > 0, 95, rng.uniform(10, 90, len(slots))).round(0),
            "weather_code": np.where(rain > 0, 61, 3),
        },
        index=slots,
    )


def simulate(
    days: int,
    out: Path,
    seed: int = 7,
    end: pd.Timestamp | None = None,
    stations: pd.DataFrame | None = None,
) -> Path:
    rng = np.random.default_rng(seed)
    storage.DATA_DIR = out
    if stations is None:
        station_files = sorted(Path("data/stations").glob("stations_*.parquet"))
        stations = pd.read_parquet(station_files[-1]) if station_files else _fake_stations(rng)
    st = station_profiles(stations, rng)
    n = len(st)

    end = (end or pd.Timestamp.now(tz="UTC")).floor("D")
    slots = pd.date_range(end - pd.Timedelta(days=days), end, freq="15min", inclusive="left")
    wx = weather_series(slots, rng)
    local = slots.tz_convert("Europe/Paris")
    hour = local.hour.to_numpy() + local.minute.to_numpy() / 60
    weekend = local.dayofweek.to_numpy() >= 5

    cap = st["capacity"].to_numpy()
    biz = st["business"].to_numpy()
    base = st["base_fill"].to_numpy()
    noise = rng.normal(0, 0.12, n)
    broken = rng.integers(0, 3, n)
    ebike_share = np.clip(rng.normal(0.4, 0.1, n), 0.1, 0.8)

    frames = []
    for k, ts in enumerate(slots):
        h = hour[k]
        amp = 0.12 if weekend[k] else 0.42
        amp *= 0.7 if wx["weather_precip_mm"].iloc[k] > 0 else 1.0
        commute = _sigmoid((h - 8.3) / 0.45) - _sigmoid((h - 18.2) / 0.55)
        night_drift = -0.05 * np.cos((h - 3) / 24 * 2 * np.pi)
        target = base + biz * amp * (commute - 0.25) + night_drift
        busy = 0.3 + 0.7 * np.exp(-(((h - 13.5) / 6) ** 2))  # more movement by day
        busy *= 0.6 if wx["weather_precip_mm"].iloc[k] > 0 else 1.0
        noise = 0.93 * noise + rng.normal(0, 0.05 * busy, n)
        fill = np.clip(target + noise, 0, 1)
        bikes = np.round(fill * (cap - broken)).astype(int)
        bikes = np.where(fill < 0.04, 0, bikes)
        ebikes = np.round(bikes * ebike_share).astype(int)
        if rng.random() < 0.02:  # a missed collection
            continue
        frames.append(
            pd.DataFrame(
                {
                    "snapshot_ts": ts + pd.Timedelta(minutes=2, seconds=int(rng.integers(0, 50))),
                    "slot_ts": ts,
                    "station_id": st["station_id"].to_numpy(),
                    "station_code": st["station_code"].to_numpy(),
                    "capacity": cap,
                    "bikes_mechanical": bikes - ebikes,
                    "bikes_ebike": ebikes,
                    "bikes_total": bikes,
                    "docks_available": cap - broken - bikes,
                    "is_installed": True,
                    "is_renting": True,
                    "is_returning": True,
                    "last_reported": ts,
                    **{c: wx[c].iloc[k] for c in wx.columns},
                    "is_valid": True,
                    "validation_issue": None,
                }
            )
        )
    df = pd.concat(frames, ignore_index=True)
    df["day"] = df["snapshot_ts"].dt.strftime("%Y-%m-%d")
    for day, part in df.groupby("day"):
        path = storage.daily_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(storage._to_table(part.drop(columns="day"), storage.SNAPSHOT_SCHEMA), path, compression="zstd")
    (out / "stations").mkdir(parents=True, exist_ok=True)
    stations.to_parquet(out / "stations" / f"stations_{df['day'].max()}.parquet", index=False)
    return out


def _fake_stations(rng: np.random.Generator, n: int = 300) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "station_id": np.arange(1, n + 1, dtype="int64"),
            "station_code": [str(1000 * int(rng.integers(1, 21)) + i) for i in range(n)],
            "name": [f"Synthetic station {i}" for i in range(n)],
            "lat": rng.uniform(48.82, 48.90, n),
            "lon": rng.uniform(2.25, 2.42, n),
            "capacity": rng.integers(15, 60, n),
        }
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--days", type=int, default=35)
    p.add_argument("--out", type=Path, default=Path("data_synthetic"))
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    print(f"Synthetic data written to {simulate(a.days, a.out, a.seed)}")


if __name__ == "__main__":
    main()
