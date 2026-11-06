# Power BI dashboard: build guide

The analytics tables are Parquet files that Power BI reads directly. This guide
builds the 3-page dashboard from the project plan in about 1–2 hours.

## 0. Refresh the data (each time before you open Power BI)

In VS Code, in the project folder:

```powershell
git pull --rebase
.venv\Scripts\activate
$env:PYTHONPATH="src"
python -m velib.analytics
```

This writes the tables to `data\marts\` (they are not on GitHub because they are rebuilt from the raw data).

## 1. Load the tables

1. Power BI Desktop → **Get data → More… → Parquet**.
2. Paste the full path of a file, e.g. `C:\Users\rishi\paris-velib-operations-intelligence\data\marts\dim_station.parquet`.
3. Repeat for: `mart_station_hourly`, `mart_station_reliability`, `mart_network_profile`,
   `mart_area_summary`, `mart_weather_effect`, `mart_data_quality`.
4. Rename each query to its file name (without `.parquet`).

Tip: create a text **parameter** `MartsFolder` = `C:\Users\rishi\paris-velib-operations-intelligence\data\marts\`
and in each query's Advanced Editor use
`Parquet.Document(File.Contents(MartsFolder & "mart_station_hourly.parquet"))`, so moving the folder is a one-line change.

| Table | One row per | Use |
|---|---|---|
| `dim_station` | station | name, area (arrondissement / commune), lat / lon, capacity |
| `mart_station_hourly` | station × date × hour | main fact table: empty / full hours, fill ratio, activity, weather |
| `mart_station_reliability` | station | league table, `problem_rank` |
| `mart_network_profile` | weekday/weekend × hour | rush-hour curves |
| `mart_area_summary` | area | arrondissement / commune comparison, morning fill change |
| `mart_weather_effect` | day type × rain × temperature | weather page |
| `mart_data_quality` | UTC day | collection coverage (credibility!) |

## 2. Model view

- `dim_station[station_id]` 1 → * `mart_station_hourly[station_id]`
- `dim_station[station_id]` 1 → 1 `mart_station_reliability[station_id]` (single direction)
- Date table: **Modeling → New table** `Date = CALENDAR ( MIN ( mart_station_hourly[local_date] ), MAX ( mart_station_hourly[local_date] ) )`,
  mark it as a date table, relate `Date[Date]` 1 → * `mart_station_hourly[local_date]`.
- Add the measures from [`measures.dax`](measures.dax).
- **View → Themes → Browse for themes** → [`theme.json`](theme.json).

## 3. Pages

**Page 1: Network overview**
- Cards: `Stations`, `Empty Hours per Day`, `Full Hours per Day`, `Top 20 Share of Empty Hours`, `Collection Coverage`.
- Line chart: `mart_network_profile` — x `local_hour`, y `share_stations_empty` and `share_stations_full`, legend/filter `day_type`.
- Map (Azure map or Map visual): `dim_station` lat / lon, bubble size `Empty Station Hours`, colour by `% Time Empty`.
- Bar chart: top 15 areas by `% Time Empty` (from `mart_area_summary`).

**Page 2: Station deep-dive (drill-through)**
- Drill-through field: `dim_station[name]`.
- Cards: `% Time Empty`, `% Time Full`, capacity, area.
- Matrix heatmap: rows `local_hour`, columns `Date[Day of week]` (add a column `FORMAT([Date],"ddd")`), values `Avg Fill Ratio`, conditional formatting red → green.
- Table on page 1 (`mart_station_reliability` sorted by `problem_rank`) → right-click a station → Drill through.

**Page 3: Weather & calendar**
- Clustered bar: `mart_weather_effect` — `rain_bucket` vs `avg_activity_per_station_slot`, split by `day_type`.
- Card: `Rain Activity vs Dry`.
- Line: `Activity per Station-Hour` by `local_hour`, small multiples by `is_weekend`.
- Footnote text box: "Activity = net change in bikes between 15-min snapshots (a lower bound on trips)".

## 4. Save

Save as `powerbi\velib_operations.pbix`, export each page as PNG to `docs\img\powerbi_page1.png` …,
then commit (`git add powerbi docs/img` → commit → push). The README links to those screenshots.
