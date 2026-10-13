# TODO

Spec (portfolio-plan PLAN.md #8) items are all present: IoT Core thing/policy/certificate
placeholder, rule, Lambda, DynamoDB TTL, Firehose → S3 Parquet, Athena table, heartbeat
alarm, dashboard (S3 + CloudFront) over API Gateway + Lambda, simulator with X.509, moto
tests, README with diagram, cost, security notes and the Firebase mapping, `cdk synth`
passing. What follows is what the next session (or Mariam) should do.

## Next cloud session
1. **Scalable heartbeat check.** Add `lambdas/heartbeat/handler.py` on an EventBridge
   5-minute schedule: scan the status table, count devices with `received_at` older than
   2 periods, emit one EMF metric `StaleDevices` (and log their ids); one alarm on it.
   Keep the per-device alarms only for ids in `cdk.json` (small fleets). Drop the per-device
   dimension from `Accepted`/`Rejected` EMF when `cfg.per_device_metrics` is false. Update
   `scripts/cost.py` so the 1 000-device column reflects it (expect ~$650 less).
2. **Batch Firehose writes.** Firehose bills 5 KB per record; a 350-byte message costs 14×.
   Either `put_record_batch` from a buffered rule (IoT rule → Firehose action directly, with
   a Lambda only for validation) or accept it and document. Decide and update the cost table.
3. `cdk-nag` (`AwsSolutionsChecks`) in `tests/test_stack.py`, with justified suppressions.
4. Optional: the `commands` return path (see WALKTHROUGH "One thing you could extend").
5. A test for `scripts/mock_api.py` (it is exercised only by the screenshot script today).

## Needs Mariam (a laptop with an AWS account)
- `cdk bootstrap` + `cdk deploy`, provision one certificate, run the simulator for an hour,
  confirm: status table item, history rows, a Parquet object after ~5 min, Athena query 1
  returns rows, the dashboard renders, stopping the simulator trips the heartbeat alarm after
  10 min and the e-mail arrives. Record what it actually cost in the README.
- Re-check every unit price in `scripts/cost.py` against the linked pricing pages (the
  sandbox could not reach aws.amazon.com) and replace the "unverified" note in README.
- Point the real incubator at IoT Core: the firmware needs the ATS endpoint, the certificate
  and `incubator/<thing-name>/telemetry` as topic; payload unchanged.
