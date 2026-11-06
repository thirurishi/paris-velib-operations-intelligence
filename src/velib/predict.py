"""Phase 3: live +1h / +2h forecasts and shortage risk for every station.

Run (from the repo root):
    python -m velib.predict          # uses the latest collected snapshot

Each station gets:
    pred_bikes_1h, pred_bikes_2h   forecast bikes available
    p_empty_2h, p_full_2h          probability of hitting 0 bikes / 0 docks within 2h
    risk, risk_level               max of the two probabilities -> green / orange / red

If no trained model exists yet (first days of collection), a simple rule is used
instead and `method` says "rule", so the app still works from day one.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from velib import analytics, features, storage

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "models"
RED, ORANGE = 0.5, 0.2  # risk thresholds

log = logging.getLogger("velib.predict")


def load_models(models_dir: Path = MODELS_DIR) -> tuple[dict, dict] | None:
    meta_path = models_dir / "meta.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    boosters = {
        name: lgb.Booster(model_file=str(models_dir / f"lgbm_{name}.txt"))
        for name in ("bikes_1h", "bikes_2h", "empty_2h", "full_2h")
    }
    return boosters, meta


def risk_level(risk: pd.Series) -> pd.Series:
    return pd.Series(np.select([risk >= RED, risk >= ORANGE], ["red", "orange"], "green"), index=risk.index)


def _rule_based(f: pd.DataFrame) -> pd.DataFrame:
    """Fallback before a model exists: persistence forecast + low-stock rule."""
    f["pred_bikes_1h"] = f["bikes_now"]
    f["pred_bikes_2h"] = f["bikes_now"]
    f["p_empty_2h"] = np.clip((3 - f["bikes_now"]) / 3, 0, 1) * 0.8
    f["p_full_2h"] = np.clip((3 - f["docks_now"]) / 3, 0, 1) * 0.8
    f["method"] = "rule"
    return f


def predict_latest(
    history: pd.DataFrame,
    stations: pd.DataFrame,
    models: tuple[dict, dict] | None = None,
) -> pd.DataFrame:
    """Forecast for the latest slot of every station present in `history`."""
    prepared = features.prepare(history)
    if prepared.empty:
        raise ValueError("No usable snapshots to predict from.")
    centroids = np.array(models[1]["centroids"]) if models else features.fit_clusters(stations)
    f = features.build_features(prepared, stations, centroids, with_targets=False, only_latest=True)
    # Ignore stations whose last report is stale (closed or missing in the latest collections).
    f = f[f["slot_ts"] >= f["slot_ts"].max() - pd.Timedelta(minutes=30)].copy()

    if models:
        boosters, _ = models
        X = f[features.FEATURES]
        cap = f["usable_capacity"].to_numpy()
        f["pred_bikes_1h"] = np.clip(boosters["bikes_1h"].predict(X), 0, cap)
        f["pred_bikes_2h"] = np.clip(boosters["bikes_2h"].predict(X), 0, cap)
        f["p_empty_2h"] = boosters["empty_2h"].predict(X)
        f["p_full_2h"] = boosters["full_2h"].predict(X)
        f["method"] = "lightgbm"
    else:
        f = _rule_based(f)

    f["risk"] = f[["p_empty_2h", "p_full_2h"]].max(axis=1)
    f["risk_type"] = np.where(f["p_empty_2h"] >= f["p_full_2h"], "empty", "full")
    f["risk_level"] = risk_level(f["risk"])
    info = stations[["station_id", "station_code", "name"] + [c for c in ("area",) if c in stations]]
    keep = [
        "station_id", "slot_ts", "bikes_now", "docks_now", "ebikes_now", "usable_capacity", "capacity",
        "lat", "lon", "pred_bikes_1h", "pred_bikes_2h", "p_empty_2h", "p_full_2h",
        "risk", "risk_type", "risk_level", "method",
    ]  # fmt: skip
    return f[keep].merge(info, on="station_id", how="left").sort_values("risk", ascending=False).reset_index(drop=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    history = storage.read_recent_snapshots(days=8)
    if history.empty:
        log.error("No snapshots found.")
        return 1
    stations = analytics.latest_stations()
    forecast = predict_latest(history, stations, load_models())
    out = storage.DATA_DIR / "forecasts"
    out.mkdir(parents=True, exist_ok=True)
    forecast.to_parquet(out / "latest.parquet", index=False)
    pd.set_option("display.width", 160)
    print(f"Forecast for {forecast['slot_ts'].max()} ({forecast['method'].iloc[0]}):")
    print(forecast["risk_level"].value_counts().to_string(), "\n")
    cols = ["station_code", "name", "area", "bikes_now", "docks_now", "pred_bikes_2h", "risk_type", "risk"]
    print(forecast[cols].head(15).round(2).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
