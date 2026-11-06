-- Clean, analysis-ready snapshots (one row per station per collection).
-- Inputs : raw_snapshots (all Parquet files), stations (latest list + area)
-- Rules  : keep stations that are installed AND renting AND returning, so an
--          "empty" station is a real shortage, not a closed station.
--          Rows with missing/negative counts are dropped; the small
--          "bikes + docks > capacity" mismatch is kept (counts are still real).
CREATE OR REPLACE VIEW stg_snapshots AS
WITH base AS (
    SELECT
        s.snapshot_ts,
        s.slot_ts,
        timezone('Europe/Paris', s.slot_ts)                        AS local_ts,
        s.station_id,
        s.bikes_total,
        s.bikes_mechanical,
        s.bikes_ebike,
        s.docks_available,
        s.bikes_total + s.docks_available                          AS usable_capacity,
        s.weather_temp_c,
        s.weather_precip_mm,
        s.weather_wind_kmh
    FROM raw_snapshots AS s
    WHERE s.is_installed AND s.is_renting AND s.is_returning
      AND coalesce(s.validation_issue, 'ok') IN ('ok', 'bikes_plus_docks_over_capacity')
)
SELECT
    b.*,
    CAST(b.local_ts AS DATE)                                       AS local_date,
    hour(b.local_ts)                                               AS local_hour,
    isodow(b.local_ts)                                             AS iso_dow,          -- 1 = Monday
    isodow(b.local_ts) >= 6                                        AS is_weekend,
    b.bikes_total = 0                                              AS is_empty,
    b.docks_available = 0                                          AS is_full,
    b.bikes_total / nullif(b.usable_capacity, 0)                   AS fill_ratio,
    CASE WHEN b.weather_precip_mm IS NULL THEN 'unknown'
         WHEN b.weather_precip_mm = 0      THEN 'dry'
         WHEN b.weather_precip_mm < 1      THEN 'light rain'
         ELSE 'rain' END                                           AS rain_bucket,
    -- Activity proxy: bikes taken or returned since the previous snapshot
    -- (net change only, so it undercounts, but it moves with real usage).
    abs(b.bikes_total - lag(b.bikes_total) OVER w)                 AS activity,
    datediff('minute', lag(b.slot_ts) OVER w, b.slot_ts)          AS minutes_since_prev
FROM base AS b
WINDOW w AS (PARTITION BY b.station_id ORDER BY b.slot_ts);
