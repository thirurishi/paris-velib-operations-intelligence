"""Tests for phases 2-4: features, analytics, training, prediction, optimiser.

They run on a small synthetic network (60 stations, 10 days) so they are fast.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from velib import analytics, features, geo, optimise, predict, simulate, storage, train


@pytest.fixture(scope="module")
def synthetic_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("syn")
    old = storage.DATA_DIR
    try:
        stations = simulate._fake_stations(np.random.default_rng(1), n=60)
        simulate.simulate(days=10, out=out, seed=3, end=pd.Timestamp("2026-11-10", tz="UTC"), stations=stations)
    finally:
        storage.DATA_DIR = old  # simulate() points storage at `out`; undo that
    return out


@pytest.fixture
def use_synthetic(synthetic_dir, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", synthetic_dir)
    return synthetic_dir


# --- geo -----------------------------------------------------------------------
def test_area_mapping_paris_and_suburb():
    st = pd.DataFrame(
        {
            "station_id": [1, 2],
            "station_code": ["16107", "21010"],
            "lat": [48.865983, 48.8355],
            "lon": [2.275725, 2.2410],
        }
    )
    out = geo.add_area(st)
    assert out.loc[0, "area"] == "Paris 16e"
    assert out.loc[1, "commune"] == "Boulogne-Billancourt" and not out.loc[1, "in_paris"]


def test_haversine_paris_to_versailles():
    assert geo.haversine_km(48.8566, 2.3522, 48.8049, 2.1204) == pytest.approx(17.9, abs=0.5)


# --- features: lags must never look into the future ---------------------------------
def _toy_history():
    slots = pd.date_range("2026-11-02", periods=8 * 96, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {
            "slot_ts": np.repeat(slots, 1),
            "station_id": 1,
            "bikes_total": np.arange(len(slots)) % 20,
            "docks_available": 20 - np.arange(len(slots)) % 20,
            "bikes_ebike": 0,
            "weather_temp_c": 10.0,
            "weather_precip_mm": 0.0,
            "weather_wind_kmh": 5.0,
            "is_installed": True,
            "is_renting": True,
            "is_returning": True,
        }
    )
    stations = pd.DataFrame({"station_id": [1], "capacity": [20], "lat": [48.86], "lon": [2.35]})
    return df, stations


def test_lag_and_target_alignment():
    df, stations = _toy_history()
    f = features.build_features(features.prepare(df), stations, np.array([[48.86, 2.35]]))
    row = f[f["slot_ts"] == pd.Timestamp("2026-11-09 12:00", tz="UTC")].iloc[0]
    i = int((row["slot_ts"] - df["slot_ts"].iloc[0]) / pd.Timedelta(minutes=15))
    assert row["bikes_now"] == i % 20
    assert row["d_bikes_15m"] == i % 20 - (i - 1) % 20
    assert row["bikes_lag_7d"] == (i - 7 * 96) % 20
    assert row["seas_7d_2h"] == (i + 8 - 7 * 96) % 20  # target time, last week
    assert row["bikes_2h"] == (i + 8) % 20  # the target itself
    # Seasonal features at the target time must come from the PAST, never the future.
    assert row["slot_ts"] + pd.Timedelta(hours=2) - pd.Timedelta(days=7) < row["slot_ts"]


def test_empty_2h_target():
    df, stations = _toy_history()
    f = features.build_features(features.prepare(df), stations, np.array([[48.86, 2.35]]))
    # bikes cycle 0..19: within the next 8 slots a 0 appears iff current value >= 12
    sample = f.dropna(subset=["empty_2h"]).iloc[100:140]
    expected = (sample["bikes_now"] >= 12).astype(float)
    assert (sample["empty_2h"].to_numpy() == expected.to_numpy()).all()


def test_only_latest_returns_one_row_per_station():
    df, stations = _toy_history()
    f = features.build_features(
        features.prepare(df), stations, np.array([[48.86, 2.35]]), with_targets=False, only_latest=True
    )
    assert len(f) == 1 and f["slot_ts"].iloc[0] == df["slot_ts"].max()
    assert not any(t in f.columns for t in features.TARGETS)


def test_school_holiday_flags():
    d = pd.Series([pd.Timestamp("2026-10-20").date(), pd.Timestamp("2026-11-03").date()])
    assert features.school_holiday_flags(d).tolist() == [True, False]


# --- analytics -------------------------------------------------------------------
def test_analytics_builds_marts_and_insights(use_synthetic, monkeypatch, tmp_path):
    monkeypatch.setattr(analytics, "REPORTS_DIR", tmp_path)
    ins = analytics.run()
    assert ins["days_collected"] >= 9 and ins["stations"] == 60
    assert 0 < ins["top20_share_of_empty_hours"] <= 1
    rel = pd.read_parquet(use_synthetic / "marts" / "mart_station_reliability.parquet")
    assert rel["share_time_empty"].between(0, 1).all()
    assert (tmp_path / "insights.md").read_text(encoding="utf-8").startswith("# Key insights")


# --- training + prediction --------------------------------------------------------
def test_train_and_predict(use_synthetic, monkeypatch, tmp_path):
    for mod, attr in ((train, "MODELS_DIR"), (train, "REPORTS_DIR"), (train, "IMG_DIR")):
        monkeypatch.setattr(mod, attr, tmp_path / attr)
    monkeypatch.setitem(train.PARAMS, "n_estimators", 40)
    meta = train.run(make_charts=False)
    assert meta is not None
    methods = {r["method"] for r in meta["results"]["regression"]}
    assert {"Persistence (same as now)", "LightGBM"} <= methods
    models = predict.load_models(tmp_path / "MODELS_DIR")
    history = storage.read_all_snapshots()
    fc = predict.predict_latest(history, analytics.latest_stations(), models)
    assert len(fc) == 60 and fc["method"].eq("lightgbm").all()
    assert fc["p_empty_2h"].between(0, 1).all()
    assert (fc["pred_bikes_2h"] <= fc["usable_capacity"]).all()


def test_predict_without_model_uses_rule(use_synthetic):
    fc = predict.predict_latest(storage.read_all_snapshots(), analytics.latest_stations(), None)
    assert fc["method"].eq("rule").all()
    assert set(fc["risk_level"]) <= {"green", "orange", "red"}
    empty_now = fc[fc["bikes_now"] == 0]
    assert (empty_now["risk_level"] == "red").all()


# --- optimiser ---------------------------------------------------------------------
def _toy_forecast(n=30, seed=0):
    rng = np.random.default_rng(seed)
    cap = rng.integers(15, 40, n)
    bikes = rng.integers(0, cap)
    p_empty = np.where(bikes <= 2, 0.9, rng.uniform(0, 0.2, n))
    p_full = np.where(cap - bikes <= 2, 0.9, rng.uniform(0, 0.1, n))
    return pd.DataFrame(
        {
            "station_id": np.arange(n),
            "station_code": [str(1000 + i) for i in range(n)],
            "name": [f"S{i}" for i in range(n)],
            "area": "Paris 1er",
            "lat": rng.uniform(48.84, 48.88, n),
            "lon": rng.uniform(2.30, 2.40, n),
            "bikes_now": bikes.astype(float),
            "docks_now": (cap - bikes).astype(float),
            "usable_capacity": cap.astype(float),
            "pred_bikes_2h": bikes.astype(float),
            "p_empty_2h": p_empty,
            "p_full_2h": p_full,
            "risk": np.maximum(p_empty, p_full),
        }
    )


@pytest.mark.parametrize("solver", ["ortools", "greedy"])
def test_routes_respect_capacity_and_shift(solver):
    cfg = optimise.Config(n_trucks=2, truck_capacity=15, start_load=5, shift_minutes=60, solver_seconds=2)
    tasks = optimise.build_tasks(_toy_forecast(), cfg)
    routes = optimise.solve_ortools(tasks, cfg) if solver == "ortools" else optimise.solve_greedy(tasks, cfg)
    plan = optimise.routes_to_plan(routes, tasks, cfg)
    assert not plan.empty
    assert plan["load_after"].between(0, cfg.truck_capacity).all()
    assert plan["arrive_min"].max() <= cfg.shift_minutes
    served = [k for r in routes for k in r]
    assert len(served) == len(set(served))  # never visit a station twice


def test_drop_never_exceeds_free_docks():
    tasks = optimise.build_tasks(_toy_forecast())
    drops = tasks[tasks["qty"] < 0]
    assert (drops["qty"].abs() <= drops["docks_now"]).all()
    picks = tasks[tasks["qty"] > 0]
    assert (picks["qty"] <= picks["bikes_now"]).all()


def test_ortools_beats_or_matches_random():
    cfg = optimise.Config(n_trucks=2, solver_seconds=2)
    cmp = optimise.compare_strategies(optimise.build_tasks(_toy_forecast(60, seed=4), cfg), cfg).set_index("strategy")
    prevented = cmp["problem_hours_prevented"]
    assert prevented["No action"] == 0
    assert prevented["OR-Tools routing"] >= prevented["Random order (avg of 20)"]


# --- collector dedupe ------------------------------------------------------------------
def test_slot_dedupe(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    ts = datetime(2026, 11, 6, 8, 2, tzinfo=UTC)
    assert not storage.slot_already_collected(ts)
    df = pd.DataFrame({"station_id": [1], "bikes_total": [3], "docks_available": [5]})
    storage.write_snapshot(df, ts)
    assert storage.slot_already_collected(datetime(2026, 11, 6, 8, 14, tzinfo=UTC))
    assert not storage.slot_already_collected(datetime(2026, 11, 6, 8, 16, tzinfo=UTC))
