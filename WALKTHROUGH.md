# Walkthrough (for Mariam)

## Why it is designed this way

The incubator already sends a record every 15 s to Firebase. This repo keeps that record
untouched and builds the AWS side around it: "how would you take a working prototype to a
fleet", not "did you build a demo". Three rules drove every choice: the device holds no
secret that unlocks the whole account (certificate per thing, policy pinned to the thing
name); bad data is visible, never silently stored or dropped (validation before storage, a
reject queue, a metric per outcome); reads stay cheap as data grows (a status table for
"now", a TTL table for "this month", Parquet in S3 for "ever").

## File map

- `app.py`, `cdk.json` – CDK entry point and the context knobs (device ids, TTL, e-mail).
- `src/pipeline/config.py` – the knobs as a validated dataclass.
- `src/pipeline/devices.py` – IoT things, the shared scoped policy, certificate attachment.
- `src/pipeline/storage.py` – two DynamoDB tables, the S3 bucket, Glue table, Firehose with
  JSON→Parquet conversion, Athena workgroup with a 1 GB per-query fuse.
- `src/pipeline/ingest.py` – topic rule (with error action to logs) → ingest Lambda → queue.
- `src/pipeline/api.py`, `dashboard.py`, `monitoring.py` – HTTP API, CloudFront site, alarms.
- `lambdas/common/telemetry.py` – the schema: fields, ranges, clock bounds. Read this first.
- `lambdas/ingest/handler.py`, `lambdas/status_api/handler.py` – the two Lambdas, tested
  against `moto` in `tests/`.
- `simulator/` – thermal model + MQTT publisher; `sample_history.json` is one real hour.
- `scripts/cost.py` – the cost table with a source URL per price.
- `tests/test_stack.py` – synthesises the stack and asserts the security- and cost-relevant
  properties (TTL on, policy scoped, buckets private, API throttled).

## Three questions you will be asked

**1. Why DynamoDB *and* S3? Isn't that two databases?**
They answer different questions. "What is hive 7 doing now" and "draw the last hour" need
reads by key; DynamoDB does that cheaply and the TTL deletes for free after 30 days. "What
was the daily duty cycle over the season" needs a scan over everything; in DynamoDB that is
slow and expensive, in Parquet on S3 it is a SQL query at $5 per TB scanned. Firehose does
the conversion so the Lambda stays simple. Firebase RTDB gave one node for both, so the app
had to download the whole history.

**2. How do you stop a compromised device from poisoning other hives' data?**
Three layers. The IoT policy only lets a certificate connect as the thing it is attached to
and publish to that thing's topic. The rule takes the device id from the topic, not from the
JSON, so the stored `device_id` is whatever the policy allowed. Validation rejects
out-of-range values, so nobody can push 500 °C into the history. What this does not stop is
a device lying within range about itself; that needs the model-based checks from
`incubator-telemetry-analysis`.

**3. The heartbeat alarms cost more than the data at 1 000 devices. Did you see that?**
Yes; `scripts/cost.py` makes it the largest line. One metric and alarm per device is simple
and right for ten hives, wrong for a thousand. The fix is in TODO.md: a Lambda on a
5-minute schedule scans the status table for stale `received_at` values and publishes one
"stale devices" metric; a single alarm then costs ten cents a month. The repo keeps the
simple design and makes its cost visible.

## One thing you could extend

Add the return path. The policy already allows each thing to subscribe to
`incubator/<id>/commands`; add `POST /devices/{id}/target` that publishes
`{"targetTemp": 34.5}` through `iot-data`, and make the simulator honour it. That turns
telemetry into control and raises the question interviewers enjoy: what must the device
refuse no matter what the cloud says (the firmware's safety layer).
