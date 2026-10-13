-- Run these in the Athena console with the "incubator-telemetry" workgroup selected.
-- Column names are lower-case: Firehose's JSON deserialiser lower-cases keys
-- (tempOut -> tempout). Partitions are projected, so no MSCK REPAIR is needed.

-- 1. Daily heater duty cycle and chamber temperature per device.
SELECT device_id, day,
       round(avg(temp), 2)                              AS mean_temp,
       round(100.0 * avg(CASE WHEN heater THEN 1 ELSE 0 END), 1) AS duty_pct,
       count(*)                                         AS readings
FROM incubator_telemetry.readings
WHERE year = '2026' AND month = '10'
GROUP BY device_id, day
ORDER BY device_id, day;

-- 2. Readings that overshoot the setpoint by more than 1 degC.
SELECT device_id, from_unixtime(ts) AS at, temp, targettemp, temp - targettemp AS overshoot
FROM incubator_telemetry.readings
WHERE year = '2026' AND temp - targettemp > 1.0
ORDER BY overshoot DESC
LIMIT 50;

-- 3. Device-to-cloud latency distribution (ts is the device clock, received_at the rule's).
SELECT device_id,
       approx_percentile(latency_s, 0.5) AS p50_s,
       approx_percentile(latency_s, 0.95) AS p95_s,
       max(latency_s) AS max_s
FROM incubator_telemetry.readings
WHERE year = '2026'
GROUP BY device_id;

-- 4. Gaps longer than 2 minutes between consecutive readings (missed heartbeats).
WITH ordered AS (
  SELECT device_id, ts, lag(ts) OVER (PARTITION BY device_id ORDER BY ts) AS prev_ts
  FROM incubator_telemetry.readings
  WHERE year = '2026'
)
SELECT device_id, from_unixtime(prev_ts) AS gap_start, ts - prev_ts AS gap_s
FROM ordered
WHERE ts - prev_ts > 120
ORDER BY gap_s DESC;
