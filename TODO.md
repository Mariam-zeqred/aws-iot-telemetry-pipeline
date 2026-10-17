# TODO

Spec (portfolio-plan PLAN.md #8) items are all present: IoT Core thing/policy/certificate
placeholder, rule, Lambda, DynamoDB TTL, Firehose → S3 Parquet, Athena table, heartbeat
alarm, dashboard (S3 + CloudFront) over API Gateway + Lambda, simulator with X.509, moto
tests, README with diagram, cost, security notes and the Firebase mapping, `cdk synth`
passing. Session 3 (2026-10-17) closed the cloud-doable follow-ups: the scheduled heartbeat
Lambda with one `StaleDevices` alarm and the `per_device_metrics` switch, the Firehose
batching decision (accepted and documented, see README), cdk-nag in the test suite, and a
test for `scripts/mock_api.py`.

## Optional (cloud-doable, not required by the spec)
1. The `commands` return path (see WALKTHROUGH "One thing you could extend"): `POST
   /devices/{id}/target` → `iot-data` publish, and the simulator honouring it.
2. A Cognito or IAM authoriser on the HTTP API (the cdk-nag APIG4 suppression in
   `src/pipeline/nag.py` goes away with it).
3. Revisit the Firehose decision if the fleet passes ~5 000 devices or the interval drops
   below 15 s: a Kinesis Data Stream in front of Firehose removes the 5 KB rounding, but its
   price is not in `scripts/cost.py` yet (add it with a source URL first).
4. Lambda runtime is `python3.14` (what cdk-nag's L1 rule asks for); the handlers are only
   run under 3.11–3.13 here. Add 3.14 to the CI matrix when `actions/setup-python` and
   `aws-cdk-lib` both support it, or pin the runtime back to 3.13 and suppress L1.

## Needs Mariam (a laptop with an AWS account)
- `cdk bootstrap` + `cdk deploy`, provision one certificate, run the simulator for an hour,
  confirm: status table item, history rows, a Parquet object after ~5 min, Athena query 1
  returns rows, the dashboard renders, stopping the simulator trips the `StaleDevices` alarm
  after 10–15 min (and the per-device one after 10) and the e-mail arrives. Record what it
  actually cost in the README.
- Re-check every unit price in `scripts/cost.py` against the linked pricing pages (the
  sandbox could not reach aws.amazon.com) and replace the "unverified" note in README. In
  particular confirm that CloudWatch bills the `Rejected` per-device metric only in months a
  device actually rejects (the table assumes three metric names per device).
- Point the real incubator at IoT Core: the firmware needs the ATS endpoint, the certificate
  and `incubator/<thing-name>/telemetry` as topic; payload unchanged.
