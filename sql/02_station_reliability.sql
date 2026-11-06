-- Station league table: how often is each station empty or full?
CREATE OR REPLACE TABLE mart_station_reliability AS
WITH per_station AS (
    SELECT
        station_id,
        count(*)                                     AS n_snapshots,
        count(DISTINCT local_date)                   AS days_observed,
        avg(usable_capacity)                         AS avg_capacity,
        avg(bikes_total)                             AS avg_bikes,
        avg(is_empty::INT)                           AS share_time_empty,
        avg(is_full::INT)                            AS share_time_full,
        sum(is_empty::INT) * 0.25                    AS empty_hours,
        sum(is_full::INT) * 0.25                     AS full_hours,
        avg(is_empty::INT) FILTER (WHERE NOT is_weekend AND local_hour BETWEEN 7 AND 9)  AS share_empty_am_peak,
        avg(is_full::INT)  FILTER (WHERE NOT is_weekend AND local_hour BETWEEN 7 AND 9)  AS share_full_am_peak,
        avg(is_empty::INT) FILTER (WHERE NOT is_weekend AND local_hour BETWEEN 17 AND 19) AS share_empty_pm_peak,
        avg(is_full::INT)  FILTER (WHERE NOT is_weekend AND local_hour BETWEEN 17 AND 19) AS share_full_pm_peak
    FROM stg_snapshots
    GROUP BY station_id
)
SELECT
    p.station_id,
    st.station_code,
    st.name,
    st.area,
    st.commune,
    st.lat,
    st.lon,
    st.capacity,
    p.n_snapshots,
    p.days_observed,
    round(p.avg_bikes, 1)                                        AS avg_bikes,
    round(p.share_time_empty, 4)                                 AS share_time_empty,
    round(p.share_time_full, 4)                                  AS share_time_full,
    round(p.share_time_empty + p.share_time_full, 4)             AS share_time_problem,
    round(p.empty_hours / p.days_observed, 2)                    AS empty_hours_per_day,
    round(p.full_hours / p.days_observed, 2)                     AS full_hours_per_day,
    round(p.share_empty_am_peak, 3)                              AS share_empty_am_peak,
    round(p.share_full_am_peak, 3)                               AS share_full_am_peak,
    round(p.share_empty_pm_peak, 3)                              AS share_empty_pm_peak,
    round(p.share_full_pm_peak, 3)                               AS share_full_pm_peak,
    rank() OVER (ORDER BY p.share_time_empty + p.share_time_full DESC) AS problem_rank
FROM per_station AS p
LEFT JOIN stations AS st USING (station_id);
