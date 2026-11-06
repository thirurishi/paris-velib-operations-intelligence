-- Does rain / cold reduce usage? Compare activity in the same hours across weather.
CREATE OR REPLACE TABLE mart_weather_effect AS
SELECT
    CASE WHEN is_weekend THEN 'weekend' ELSE 'weekday' END     AS day_type,
    rain_bucket,
    CASE WHEN weather_temp_c IS NULL THEN 'unknown'
         WHEN weather_temp_c < 5  THEN '< 5°C'
         WHEN weather_temp_c < 12 THEN '5-12°C'
         WHEN weather_temp_c < 20 THEN '12-20°C'
         ELSE '20°C+' END                                      AS temp_bucket,
    count(DISTINCT slot_ts)                                    AS slots,
    round(avg(activity), 4)                                    AS avg_activity_per_station_slot,
    round(avg(is_empty::INT), 4)                               AS share_stations_empty
FROM stg_snapshots
WHERE minutes_since_prev <= 20 AND local_hour BETWEEN 7 AND 21
GROUP BY ALL
ORDER BY day_type, rain_bucket, temp_bucket;
