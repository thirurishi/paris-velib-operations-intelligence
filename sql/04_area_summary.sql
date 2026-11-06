-- Arrondissement / commune view.
CREATE OR REPLACE TABLE mart_area_summary AS
SELECT
    st.area,
    any_value(st.commune)                                      AS commune,
    any_value(st.in_paris)                                     AS in_paris,
    count(DISTINCT s.station_id)                               AS stations,
    round(avg(s.is_empty::INT), 4)                             AS share_time_empty,
    round(avg(s.is_full::INT), 4)                              AS share_time_full,
    round(avg(s.is_empty::INT) FILTER (WHERE NOT s.is_weekend AND s.local_hour BETWEEN 7 AND 9), 4)  AS share_empty_am_peak,
    round(avg(s.is_full::INT)  FILTER (WHERE NOT s.is_weekend AND s.local_hour BETWEEN 7 AND 9), 4)  AS share_full_am_peak,
    round(avg(s.fill_ratio) FILTER (WHERE NOT s.is_weekend AND s.local_hour = 9), 3)               AS fill_ratio_9am,
    round(avg(s.fill_ratio) FILTER (WHERE NOT s.is_weekend AND s.local_hour = 5), 3)               AS fill_ratio_5am
FROM stg_snapshots AS s
JOIN stations AS st USING (station_id)
GROUP BY st.area
ORDER BY share_time_empty + share_time_full DESC;
