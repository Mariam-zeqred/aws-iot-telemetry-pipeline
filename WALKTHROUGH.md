# Walkthrough (for Mariam)

## Why it is designed this way

The incubator already sends a record every 15 s to Firebase. This repo keeps that record
untouched and builds the AWS side around it, so the question a professor hears is "how
would you take a working prototype to a fleet", not "did you build a demo". Every choice
is about three things: the device must hold no secret that unlocks the whole account
(certificate per thing, policy pinned to the thing name), bad data must be visible, not
silently stored or dropped (validation before storage, a reject queue, a metric per
outcome), and reads must stay cheap as data grows (a tiny status table for "now", a TTL
table for "this month", Parquet in S3 for "ever"). The dashboard and API are deliberately
small: GET only, throttled, one HTML file.

## File map

- `app.py`, `cdk.json` – CDK entry point and the context knobs (device ids, TTL, e-mail).
- `src/pipeline/config.py` – the knobs as a validated dataclass.
- `src/pipeline/devices.py` – IoT things, the shared scoped policy, certificate attachment.
- `src/pipeline/storage.py` – two DynamoDB tables, the S3 bucket, Glue table, Firehose with
  JSON→Parquet conversion, Athena workgroup with a 1 GB per-query fuse.
- `src/pipeline/ingest.py` – topic rule (with error action to logs) → ingest Lambda → queue.
- `src/pipeline/api.py`, `dashboard.py`, `monitoring.py` – HTTP API, CloudFront site, alarms.
- `lambdas/common/telemetry.py` – the schema: required/optional fields, ranges, clock bounds.
  Read this first; everything else trusts it.
- `lambdas/ingest/handler.py`, `lambdas/status_api/handler.py` – the two Lambdas, both
  ~100 lines, tested against `moto` in `tests/`.
- `simulator/` – thermal model + MQTT publisher; `sample_history.json` is one real hour.
- `scripts/cost.py` – the cost table with a source URL per price.
- `tests/test_stack.py` – synthesises the stack and asserts the security- and cost-relevant
  properties (TTL on, policy scoped, buckets private, API throttled, missing data breaching).

## Three questions you will be asked

**1. Why DynamoDB *and* S3? Isn't that two databases?**
They answer different questions. "What is hive 7 doing now" and "draw the last hour" need
millisecond reads by key; DynamoDB does that for about a dollar per million writes, and the
TTL throws the data away for free after 30 days. "What was the average duty cycle per day
over the season" needs a scan over everything; in DynamoDB that is slow and expensive, in
Parquet on S3 it is a SQL query that costs $5 per TB scanned. Firehose does the format
conversion so the Lambda stays simple. Firebase RTDB gave you one node for both, which is
why the app had to download the whole history.

**2. How do you stop a compromised device from poisoning other hives' data?**
Three layers. The IoT policy only lets a certificate connect as the thing it is attached to
and publish to that thing's topic. The rule takes the device id from the topic, not from the
JSON, so the stored `device_id` is whatever the policy allowed. And validation rejects
out-of-range values, so a device cannot push 500 °C into the history. What this does not
stop is a device lying within range about itself; detecting that needs the model-based
checks from `incubator-telemetry-analysis`.

**3. The heartbeat alarms cost more than the data at 1 000 devices. Did you see that?**
Yes; `scripts/cost.py` makes it the largest line. One CloudWatch metric and alarm per device
is simple and right for ten hives, and wrong for a thousand. The fix is in TODO.md: a Lambda
on a 5-minute schedule that scans the small status table for stale `received_at` values and
publishes one "stale devices" metric; a single alarm then costs ten cents a month. The
design is deliberately the simple one with the cost made visible, not the clever one.

## One thing you could extend

Add the return path. The policy already allows each thing to subscribe to
`incubator/<id>/commands`; add `POST /devices/{id}/target` to the API that publishes a
`{"targetTemp": 34.5}` message through `iot-data`, and make the simulator honour it. That
turns the pipeline from telemetry-only into control, and raises the question an interviewer
will enjoy: what must the device still refuse to do no matter what the cloud says (the
safety layer in the firmware, chapter 5 of the handbook).
