"""Phase 3: train and evaluate the forecasting models.

Run (from the repo root):
    python -m velib.train

What it does
------------
1. Builds features + targets from every collected snapshot.
2. Splits by TIME: the most recent 7 days are the test set (or the last 30% of
   the period if there are fewer than 14 days). Never a random split, because
   neighbouring 15-min slots are almost identical and would leak.
3. Trains 4 LightGBM models (one global model across all stations):
      bikes_1h, bikes_2h     regression: change in bikes over 1h / 2h (added to now)
      empty_2h, full_2h      classification: will the station hit 0 bikes / 0 docks
                             at any point in the next 2 hours?
4. Compares them with baselines that the model must beat:
      persistence  "same as now"
      seasonal     "same time last week" (falls back to yesterday, then to now)
      rule alert   "alert if <= 2 bikes (or docks) right now"
5. Saves models/, reports/model_results.{md,json} and SHAP charts in docs/img/.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, mean_absolute_error, precision_score, recall_score

from velib import analytics, features, storage

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"
IMG_DIR = ROOT / "docs" / "img"

MIN_DAYS = 3
MAX_TRAIN_ROWS = 1_000_000
ALERT_THRESHOLD = 0.5
RULE_LEVEL = 2  # baseline alert: <= 2 bikes (or docks) now

PARAMS = dict(
    n_estimators=600,
    learning_rate=0.05,
    num_leaves=31,
    min_child_samples=200,
    subsample=0.8,
    subsample_freq=1,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    n_jobs=-1,
    verbose=-1,
)

log = logging.getLogger("velib.train")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def load_dataset() -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
    raw = storage.read_all_snapshots()
    if raw.empty:
        raise FileNotFoundError("No snapshots found.")
    stations = analytics.latest_stations()
    centroids = features.fit_clusters(stations)
    data = features.build_features(features.prepare(raw), stations, centroids, with_targets=True)
    return data, stations, centroids


def time_split(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    first, last = data["slot_ts"].min(), data["slot_ts"].max()
    days = (last - first).total_seconds() / 86400
    cutoff = last - pd.Timedelta(days=7) if days >= 14 else first + (last - first) * 0.7
    cutoff = cutoff.floor("D") if days >= 14 else cutoff.floor("15min")
    # Leave a 2h gap so training targets never overlap the test period.
    train = data[data["slot_ts"] < cutoff - pd.Timedelta(hours=2)]
    test = data[data["slot_ts"] >= cutoff]
    return train, test, cutoff


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
def _fit(model, X: pd.DataFrame, y: pd.Series):
    """Fit with early stopping on the most recent 15% of the training period."""
    order = np.argsort(X.index.to_numpy())  # data is time-sorted; keep order
    X, y = X.iloc[order], y.iloc[order]
    split = int(len(X) * 0.85)
    model.fit(
        X.iloc[:split],
        y.iloc[:split],
        eval_X=(X.iloc[split:],),
        eval_y=(y.iloc[split:],),
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    return model


def train_models(train: pd.DataFrame) -> dict:
    if len(train) > MAX_TRAIN_ROWS:  # keep the most recent rows: closest to today's behaviour
        train = train.sort_values("slot_ts").tail(MAX_TRAIN_ROWS)
    X = train[features.FEATURES]
    models = {}
    for target in ("bikes_1h", "bikes_2h"):
        ok = train[target].notna()
        log.info("Training %s on %d rows", target, int(ok.sum()))
        # Learn the CHANGE in bikes (target - now): persistence is then the "zero" the
        # model starts from, which works better than predicting the level directly.
        delta = train.loc[ok, target] - train.loc[ok, "bikes_now"]
        models[target] = _fit(lgb.LGBMRegressor(objective="l1", **PARAMS), X[ok], delta)
    for target in ("empty_2h", "full_2h"):
        ok = train[target].notna()
        y = train.loc[ok, target].astype(int)
        log.info("Training %s on %d rows (positive rate %.1f%%)", target, int(ok.sum()), 100 * y.mean())
        models[target] = _fit(lgb.LGBMClassifier(objective="binary", **PARAMS), X[ok], y)
    return models


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def seasonal_baseline(df: pd.DataFrame, horizon: str) -> pd.Series:
    return df[f"seas_7d_{horizon}"].fillna(df[f"seas_1d_{horizon}"]).fillna(df["bikes_now"])


def evaluate(models: dict, test: pd.DataFrame) -> dict:
    X = test[features.FEATURES]
    res: dict = {"regression": [], "alerts": []}
    for h in ("1h", "2h"):
        t = f"bikes_{h}"
        ok = test[t].notna()
        y = test.loc[ok, t]
        rows = {
            "Persistence (same as now)": test.loc[ok, "bikes_now"],
            "Seasonal (last week, else yesterday)": seasonal_baseline(test[ok], h),
            "LightGBM": pd.Series(
                np.clip(
                    test.loc[ok, "bikes_now"].to_numpy() + models[t].predict(X[ok]),
                    0,
                    test.loc[ok, "usable_capacity"].to_numpy(),
                ),
                index=y.index,
            ),
        }
        base_mae = mean_absolute_error(y, rows["Persistence (same as now)"])
        for name, pred in rows.items():
            mae = mean_absolute_error(y, pred)
            res["regression"].append(
                {
                    "horizon": h,
                    "method": name,
                    "mae_bikes": round(mae, 3),
                    "vs_persistence": round(mae / base_mae - 1, 3),
                }
            )

    for t, now_col in (("empty_2h", "bikes_now"), ("full_2h", "docks_now")):
        ok = test[t].notna()
        y = test.loc[ok, t].astype(int)
        proba = models[t].predict_proba(X[ok])[:, 1]
        rule = (test.loc[ok, now_col] <= RULE_LEVEL).astype(int)
        model_alert = (proba >= ALERT_THRESHOLD).astype(int)
        for name, alert, score in (
            (f"Rule: <= {RULE_LEVEL} now", rule, rule),
            ("LightGBM", model_alert, proba),
        ):
            res["alerts"].append(
                {
                    "event": t,
                    "method": name,
                    "base_rate": round(float(y.mean()), 4),
                    "precision": round(float(precision_score(y, alert, zero_division=0)), 3),
                    "recall": round(float(recall_score(y, alert, zero_division=0)), 3),
                    "avg_precision": round(float(average_precision_score(y, score)), 3) if y.any() else None,
                    "alerts_per_slot": round(float(alert.sum() / test.loc[ok, "slot_ts"].nunique()), 1),
                }
            )
    return res


def shap_charts(models: dict, test: pd.DataFrame) -> list[str]:
    """Mean |SHAP| bar charts: what drives the 2h forecast and the empty-risk model."""
    import warnings

    import matplotlib

    matplotlib.use("Agg")
    warnings.filterwarnings("ignore", category=UserWarning, module="shap")
    import matplotlib.pyplot as plt
    import shap

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    sample = test[features.FEATURES].sample(min(4000, len(test)), random_state=0)
    paths = []
    for target, title in (
        ("bikes_2h", "Bikes in 2 hours"),
        ("empty_2h", "Risk of running empty within 2 hours"),
    ):
        values = shap.TreeExplainer(models[target]).shap_values(sample)
        if isinstance(values, list):  # older shap: one array per class
            values = values[1]
        imp = pd.Series(np.abs(values).mean(axis=0), index=sample.columns).sort_values().tail(15)
        fig, ax = plt.subplots(figsize=(7, 5))
        ax.barh(imp.index, imp.values, color="#2a6f97")
        ax.set_title(f"What drives the model: {title}", loc="left", fontsize=12)
        ax.set_xlabel("Mean |SHAP value| (impact on prediction)")
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        path = IMG_DIR / f"shap_{target}.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        paths.append(str(path.relative_to(ROOT)))
    return paths


def render_results(meta: dict) -> str:
    r = meta["results"]
    lines = [
        "# Forecasting results",
        "",
        f"_Trained {meta['trained_at']} UTC on data from {meta['train_start']} to {meta['train_end']}; "
        f"tested on {meta['test_start']} → {meta['test_end']} ({meta['test_rows']:,} station-slots). "
        "Time-based split, never random._",
        "",
    ]
    if meta["days"] < 14:
        lines += [
            "> ⚠️ Fewer than 14 days of data: test set is the last 30% of the period. Results are preliminary.",
            "",
        ]
    lines += [
        "## Bikes available in 1h / 2h (MAE, lower is better)",
        "",
        "| Horizon | Method | MAE (bikes) | vs persistence |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| +{x['horizon']} | {x['method']} | {x['mae_bikes']:.2f} | {x['vs_persistence']:+.1%} |"
        for x in r["regression"]
    ]
    lines += [
        "",
        "## Shortage alerts within 2h",
        "",
        "| Event | Method | Base rate | Precision | Recall | Avg precision | Alerts / slot |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {x['event']} | {x['method']} | {x['base_rate']:.1%} | {x['precision']:.2f} | {x['recall']:.2f} | "
        f"{x['avg_precision'] if x['avg_precision'] is not None else 'n/a'} | {x['alerts_per_slot']} |"
        for x in r["alerts"]
    ]
    lines += ["", "## What drives the predictions", ""] + [f"![SHAP]({'../' + p})" for p in meta.get("shap_charts", [])]
    return "\n".join(lines) + "\n"


def save(models: dict, meta: dict, centroids: np.ndarray) -> None:
    MODELS_DIR.mkdir(exist_ok=True)
    for name, model in models.items():
        model.booster_.save_model(str(MODELS_DIR / f"lgbm_{name}.txt"))
    meta = {**meta, "features": features.FEATURES, "centroids": centroids.tolist()}
    (MODELS_DIR / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "model_results.json").write_text(json.dumps(meta["results"], indent=2), encoding="utf-8")
    (REPORTS_DIR / "model_results.md").write_text(render_results(meta), encoding="utf-8")


def run(make_charts: bool = True) -> dict | None:
    data, _, centroids = load_dataset()
    days = (data["slot_ts"].max() - data["slot_ts"].min()).total_seconds() / 86400
    if days < MIN_DAYS:
        log.warning("Only %.1f days of data; need at least %d to train. Skipping.", days, MIN_DAYS)
        return None
    train, test, cutoff = time_split(data)
    models = train_models(train)
    results = evaluate(models, test)
    meta = {
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "days": round(days, 1),
        "train_start": str(train["slot_ts"].min()),
        "train_end": str(train["slot_ts"].max()),
        "test_start": str(test["slot_ts"].min()),
        "test_end": str(test["slot_ts"].max()),
        "test_rows": int(len(test)),
        "alert_threshold": ALERT_THRESHOLD,
        "regression_target": "delta",  # models predict bikes(t+h) - bikes(t)
        "best_iterations": {k: int(m.best_iteration_ or m.n_estimators) for k, m in models.items()},
        "results": results,
    }
    if make_charts:
        meta["shap_charts"] = shap_charts(models, test)
    save(models, meta, centroids)
    log.info("Saved models to %s and results to %s", MODELS_DIR, REPORTS_DIR / "model_results.md")
    return meta


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run()
    except Exception:  # noqa: BLE001
        log.exception("Training failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
