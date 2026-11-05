# 🚲 Paris Vélib' Operations Intelligence

[![Collect Vélib data](https://github.com/thirurishi/paris-velib-operations-intelligence/actions/workflows/collect.yml/badge.svg)](https://github.com/thirurishi/paris-velib-operations-intelligence/actions/workflows/collect.yml)
[![CI](https://github.com/thirurishi/paris-velib-operations-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/thirurishi/paris-velib-operations-intelligence/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

> Predict which Vélib' stations will run out of bikes or docks in the next 2 hours, and recommend where rebalancing trucks should go first.

**Status:** 🟢 Phase 1 (data pipeline) live: collecting every 15 minutes since November 2026.
Analytics, forecasting and the rebalancing app are next.

---

## The problem

Vélib' Métropole runs about 1,400 stations across Paris and 55 nearby towns. Every rush hour,
residential stations empty out and business-district stations fill up, so riders cannot take or
return a bike. The operator moves bikes with a limited number of trucks, which raises one question:

**Which stations will have a problem soon, and in what order should the trucks visit them?**

## What this project delivers

| # | Deliverable | Status |
|---|---|---|
| 1 | Automated pipeline: live station data every 15 min | ✅ Running |
| 2 | Power BI analytics: patterns, worst stations, rush hours, weather | ⏳ Phase 2 |
| 3 | LightGBM forecast of bikes per station at +1h / +2h | ⏳ Phase 3 |
| 4 | Empty / full risk alerts | ⏳ Phase 3 |
| 5 | OR-Tools truck rebalancing plan | ⏳ Phase 4 |
| 6 | Live Streamlit map app | ⏳ Phase 5 |

## Pipeline (Phase 1)

```
Vélib' GBFS feeds ─┐
                   ├─► GitHub Actions (every 15 min) ─► Python collector ─► Parquet in data/
Open-Meteo weather ┘        retries · validation · logging      (compacted to 1 file per day)
```

Each run:

1. Downloads `station_status` and `station_information` from the official Vélib' GBFS feed, plus current Paris weather.
2. Cleans and joins them into one row per station (~1,400 rows).
3. Validates every row (no negative counts, bikes + docks ≤ capacity, mechanical + e-bike = total) and flags problems instead of hiding them.
4. Saves a small Parquet file; the first run after midnight UTC merges the previous day into `data/daily/`.

### Snapshot table

| Column | Meaning |
|---|---|
| `snapshot_ts` / `slot_ts` | Collection time (UTC) / its 15-minute slot |
| `station_id`, `station_code` | Station identifiers (`station_code` is the public code) |
| `capacity` | Total docks |
| `bikes_mechanical`, `bikes_ebike`, `bikes_total` | Bikes available |
| `docks_available` | Free docks |
| `is_installed`, `is_renting`, `is_returning` | Station status flags |
| `last_reported` | Operator's last update for this station (UTC) |
| `weather_*` | Temperature, precipitation, rain, wind, cloud cover, WMO weather code |
| `is_valid`, `validation_issue` | Data-quality flag and reason |

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export PYTHONPATH=src              # Windows PowerShell: $env:PYTHONPATH="src"
python -m velib.collect            # collect one snapshot now
python -m velib.check_data         # data-quality report
pytest                             # tests (no internet needed)
```

## Repository structure

```
.github/workflows/   collect.yml (every 15 min) · ci.yml (lint + tests)
src/velib/           collect.py · storage.py · check_data.py · (features, train, predict, optimise: coming)
data/                raw/ · daily/ · stations/   (written by the collector)
tests/               pytest suite with sample feed files
sql/ notebooks/ powerbi/ app/ docs/   later phases
```

## Data sources & licences

- **Vélib' Métropole real-time availability** (GBFS):
  [opendata.paris.fr](https://opendata.paris.fr/explore/dataset/velib-disponibilite-en-temps-reel/).
  © Smovengo / Syndicat Autolib' Vélib' Métropole, [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
- **Weather:** [Open-Meteo.com](https://open-meteo.com/), CC BY 4.0.
- **Code:** MIT licence.

## About

Built by **Thirumurugan Kumar**, MSc Data Science & Analytics (EPITA, Paris).
Looking for a Data / Business Operations internship from January 2027.
GitHub: [@thirurishi](https://github.com/thirurishi)
