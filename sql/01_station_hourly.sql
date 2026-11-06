-- One row per station per local date and hour. Main fact table for Power BI.
CREATE OR REPLACE TABLE mart_station_hourly AS
SELECT
    station_id,
    local_date,
    local_hour,
    any_value(iso_dow)                               AS iso_dow,
    any_value(is_weekend)                            AS is_weekend,
    count(*)                                         AS n_snapshots,
    round(avg(bikes_total), 2)                       AS avg_bikes,
    min(bikes_total)                                 AS min_bikes,
    max(bikes_total)                                 AS max_bikes,
    round(avg(docks_available), 2)                   AS avg_docks,
    round(avg(fill_ratio), 3)                        AS avg_fill_ratio,
    round(avg(is_empty::INT), 3)                     AS share_empty,
    round(avg(is_full::INT), 3)                      AS share_full,
    -- Each snapshot stands for 15 minutes of the hour.
    sum(is_empty::INT) * 0.25                        AS empty_hours,
    sum(is_full::INT) * 0.25                         AS full_hours,
    coalesce(sum(activity) FILTER (WHERE minutes_since_prev <= 20), 0) AS activity,
    round(avg(weather_temp_c), 1)                    AS temp_c,
    round(sum(weather_precip_mm) / 4, 2)             AS precip_mm
FROM stg_snapshots
GROUP BY station_id, local_date, local_hour;
