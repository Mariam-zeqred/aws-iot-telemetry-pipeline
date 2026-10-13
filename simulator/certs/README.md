# Device certificates

Nothing in this directory is committed except this file (see `.gitignore`).
Create a certificate for one device on your own machine, never in CI:

```sh
scripts/provision_device.sh hive-SIM001 eu-central-1
curl -o simulator/certs/AmazonRootCA1.pem https://www.amazontrust.com/repository/AmazonRootCA1.pem
```

The script writes `hive-SIM001.pem.crt`, `hive-SIM001.private.key` and prints the
certificate ARN; put that ARN into `cdk.json` -> `certificate_arns` and redeploy so the
stack attaches it to the thing and to the device policy.
