-- Collection health per UTC day: coverage, gaps and validation issues.
CREATE OR REPLACE TABLE mart_data_quality AS
WITH snaps AS (
    SELECT
        snapshot_ts,
        CAST(snapshot_ts AS DATE)                              AS utc_date,
        count(*)                                               AS stations,
        sum((NOT is_valid)::INT)                               AS invalid_rows,
        bool_or(weather_temp_c IS NOT NULL)                    AS has_weather
    FROM raw_snapshots
    GROUP BY snapshot_ts
),
gaps AS (
    SELECT *, datediff('minute', lag(snapshot_ts) OVER (ORDER BY snapshot_ts), snapshot_ts) AS gap_min
    FROM snaps
)
SELECT
    utc_date,
    count(*)                                                   AS snapshots,
    round(count(*) / 96.0, 3)                                  AS coverage,
    max(gap_min)                                               AS max_gap_min,
    sum((gap_min > 30)::INT)                                   AS gaps_over_30min,
    min(stations)                                              AS stations_min,
    max(stations)                                              AS stations_max,
    sum(invalid_rows)                                          AS invalid_rows,
    sum((NOT has_weather)::INT)                                AS snapshots_without_weather
FROM gaps
GROUP BY utc_date
ORDER BY utc_date;
