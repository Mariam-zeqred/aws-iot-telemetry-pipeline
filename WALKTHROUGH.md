# Walkthrough (for Mariam)

## Why it is designed this way

The incubator already sends a record every 15 s to Firebase. This repo keeps that record
untouched and builds the AWS side around it: "how would you take a working prototype to a
fleet". Three rules: the device holds no
secret that unlocks the whole account (certificate per thing, policy pinned to the thing
name); bad data is visible, never silently stored or dropped (validation first, a reject
queue, a metric per outcome); reads stay cheap as data grows (a status table for "now", a
TTL table for "this month", Parquet in S3 for "ever").

## File map

- `app.py`, `cdk.json` – entry point and the context knobs.
- `src/pipeline/config.py` – the knobs, validated.
- `src/pipeline/devices.py` – IoT things, the shared scoped policy, certificate attachment.
- `src/pipeline/storage.py` – two DynamoDB tables, the S3 bucket, Glue table, Firehose with
  JSON→Parquet conversion, Athena workgroup with a 1 GB per-query fuse.
- `src/pipeline/ingest.py` – topic rule (with error action to logs) → ingest Lambda → queue.
- `src/pipeline/api.py`, `dashboard.py`, `monitoring.py` – HTTP API, CloudFront site,
  heartbeat Lambda, alarms; `nag.py` – cdk-nag suppressions, one reason each.
- `lambdas/common/telemetry.py` – the schema: fields, ranges, clock bounds. Read this first.
- `lambdas/ingest/`, `status_api/`, `heartbeat/` – the three Lambdas, tested against `moto`.
- `simulator/` – thermal model + MQTT publisher; `sample_history.json` is one real hour.
- `scripts/cost.py` – the cost table with a source URL per price.
- `tests/test_stack.py` – stack assertions (TTL on, policy scoped, buckets private, API
  throttled).

## Three questions you will be asked

**1. Why DynamoDB *and* S3? Isn't that two databases?**
They answer different questions. "What is hive 7 doing now" and "draw the last hour" need
reads by key; DynamoDB does that cheaply and the TTL deletes for free after 30 days. "What
was the daily duty cycle over the season" needs a scan over everything; in DynamoDB that is
slow and expensive, in Parquet on S3 it is SQL at $5 per TB scanned. Firebase RTDB had one
node for both, so the app downloaded the whole history.

**2. How do you stop a compromised device from poisoning other hives' data?**
Three layers. The IoT policy lets a certificate connect only as its own thing and publish
only to that thing's topic. The rule takes the device id from the topic, not the JSON, so the
stored `device_id` is what the policy allowed. Validation rejects out-of-range values, so
nobody can push 500 °C into the history. It does not stop a device lying within range about
itself; that needs the model-based checks from `incubator-telemetry-analysis`.

**3. The heartbeat alarms cost more than the data at 1 000 devices. Did you see that?**
Yes; `scripts/cost.py` makes it the largest line, and the stack has both designs. One metric
set and one alarm per device is right for ten hives: the alarm names the hive and notices a
device that never connected. For a fleet, `per_device_metrics = false` drops them; the
heartbeat Lambda scans the status table every five minutes, counts devices whose
`received_at` is older than two periods and publishes one `StaleDevices` metric. One
alarm costs ten cents a month and also fires if the Lambda stops running (missing data
counts as breaching). What it cannot see is a device that has no status item at all.

## One thing you could extend

Add the return path. The policy allows each thing to subscribe to `incubator/<id>/commands`;
add `POST /devices/{id}/target` publishing `{"targetTemp": 34.5}` through `iot-data`, and
make the simulator honour it. That turns
telemetry into control and raises the interview question: what must the device refuse
whatever the cloud says (the firmware's safety layer).
