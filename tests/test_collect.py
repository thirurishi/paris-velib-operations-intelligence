"""Tests for the collector and storage, using small saved copies of the real feeds.

Run with:  pytest
No internet is needed: the HTTP calls are replaced by the fixture files.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
import requests

from velib import check_data, collect, storage

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 6, 8, 7, 12, tzinfo=UTC)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    """Every test writes into its own temporary data/ folder."""
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


@pytest.fixture
def fake_http(monkeypatch):
    """Replace real HTTP calls with the fixture files."""
    responses = {
        collect.STATUS_URL: load("station_status.json"),
        collect.INFO_URL: load("station_information.json"),
        collect.WEATHER_URL: load("weather.json"),
    }

    def fake_fetch(session, url, **params):
        result = responses[url]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(collect, "fetch_json", fake_fetch)
    monkeypatch.setattr(collect, "MIN_EXPECTED_STATIONS", 1)  # fixtures have 4 stations
    return responses


# --- parsing -----------------------------------------------------------------
def test_parse_station_status_splits_bike_types():
    df = collect.parse_station_status(load("station_status.json"))
    row = df.set_index("station_code").loc["16107"]
    assert (row.bikes_mechanical, row.bikes_ebike, row.bikes_total) == (2, 3, 5)
    assert row.docks_available == 30
    assert bool(row.is_renting) is True
    assert df["last_reported"].dt.tz is not None


def test_status_flags_accept_ints_and_bools():
    payload = {
        "data": {"stations": [{"station_id": 1, "is_installed": True, "is_renting": 0, "is_returning": 1}]}
    }
    row = collect.parse_station_status(payload).iloc[0]
    assert (bool(row.is_installed), bool(row.is_renting), bool(row.is_returning)) == (True, False, True)


def test_bike_types_handles_list_and_dict():
    assert collect._bike_types([{"mechanical": 1}, {"ebike": 2}]) == {"mechanical": 1, "ebike": 2}
    assert collect._bike_types({"mechanical": 4}) == {"mechanical": 4}
    assert collect._bike_types(None) == {}


def test_bad_payload_raises():
    with pytest.raises(ValueError):
        collect.parse_station_status({"data": {}})
    with pytest.raises(ValueError):
        collect.parse_station_information({"data": {"stations": []}})


def test_parse_weather():
    w = collect.parse_weather(load("weather.json"))
    assert w["weather_temp_c"] == 9.4 and w["weather_code"] == 61


# --- validation --------------------------------------------------------------
def test_validation_flags_impossible_rows():
    status = collect.parse_station_status(load("station_status.json"))
    info = collect.parse_station_information(load("station_information.json"))
    df = collect.validate(collect.build_snapshot(status, info, collect.EMPTY_WEATHER, NOW))
    issues = df.set_index("station_code")["validation_issue"]
    assert pd.isna(issues["16107"]) and pd.isna(issues["6015"])
    assert issues["12109"] == "bikes_plus_docks_over_capacity"  # 12 + 30 > 36
    assert issues["9020"] == "type_breakdown_mismatch"  # 30 + 9 != 40
    assert df["is_valid"].sum() == 2


def test_snapshot_slot_is_floored_to_15_minutes():
    status = collect.parse_station_status(load("station_status.json"))
    info = collect.parse_station_information(load("station_information.json"))
    df = collect.build_snapshot(status, info, collect.EMPTY_WEATHER, NOW)
    assert df["slot_ts"].iloc[0] == pd.Timestamp("2026-11-06 08:00", tz="UTC")


def test_feed_health_rejects_tiny_feed():
    df = pd.DataFrame({"station_id": [1, 2]})
    with pytest.raises(ValueError):
        collect.check_feed_health(df)


# --- end-to-end + storage ----------------------------------------------------
def test_collect_end_to_end(fake_http, tmp_data_dir):
    snapshot = collect.collect(now=NOW)
    files = list((tmp_data_dir / "raw" / "date=2026-11-06").glob("*.parquet"))
    assert len(files) == 1 and files[0].name == "velib_2026-11-06T080712Z.parquet"
    assert (tmp_data_dir / "stations" / "stations_2026-11-06.parquet").exists()

    saved = pd.read_parquet(files[0])
    assert list(saved.columns) == storage.SNAPSHOT_SCHEMA.names
    assert len(saved) == len(snapshot) == 4
    assert saved["weather_temp_c"].iloc[0] == pytest.approx(9.4)


def test_weather_failure_does_not_stop_collection(fake_http):
    fake_http[collect.WEATHER_URL] = requests.ConnectionError("open-meteo down")
    snapshot = collect.collect(now=NOW)
    assert snapshot["weather_temp_c"].isna().all()
    assert len(snapshot) == 4


def test_compaction_merges_finished_days(fake_http, tmp_data_dir):
    for hour in (22, 23):
        collect.collect(now=datetime(2026, 11, 5, hour, 7, tzinfo=UTC))
    assert len(list((tmp_data_dir / "raw" / "date=2026-11-05").glob("*.parquet"))) == 2

    collect.collect(now=NOW)  # first run of the next day triggers compaction

    assert not (tmp_data_dir / "raw" / "date=2026-11-05").exists()
    daily = pd.read_parquet(tmp_data_dir / "daily" / "velib_2026-11-05.parquet")
    assert len(daily) == 8 and daily["snapshot_ts"].nunique() == 2
    assert len(storage.read_all_snapshots()) == 12  # 8 compacted + 4 from today


def test_check_data_report(fake_http):
    for minute in (0, 15, 30, 90):  # 90 leaves a 60-minute gap
        collect.collect(now=datetime(2026, 11, 6, 8 + minute // 60, minute % 60, tzinfo=UTC))
    per_snapshot, per_day = check_data.build_report(storage.read_all_snapshots())
    assert per_day.loc["2026-11-06", "snapshots"] == 4
    assert per_day.loc["2026-11-06", "gaps_over_30min"] == 1
    assert check_data.main() == 0
