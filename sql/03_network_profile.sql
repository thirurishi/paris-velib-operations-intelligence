-- Network rhythm: share of stations empty / full by day type and hour.
CREATE OR REPLACE TABLE mart_network_profile AS
SELECT
    CASE WHEN is_weekend THEN 'weekend' ELSE 'weekday' END     AS day_type,
    local_hour,
    count(DISTINCT local_date)                                 AS days,
    round(avg(is_empty::INT), 4)                               AS share_stations_empty,
    round(avg(is_full::INT), 4)                                AS share_stations_full,
    round(avg(fill_ratio), 3)                                  AS avg_fill_ratio,
    round(avg(activity) FILTER (WHERE minutes_since_prev <= 20), 3) AS avg_activity_per_station_slot
FROM stg_snapshots
GROUP BY ALL
ORDER BY day_type, local_hour;
