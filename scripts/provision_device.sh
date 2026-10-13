#!/usr/bin/env sh
# Create an X.509 certificate for one device and print the ARN to put into cdk.json.
# Runs on your machine with your AWS credentials; never in CI. Usage:
#   scripts/provision_device.sh <device-id> [region]
set -eu
DEVICE="${1:?device id, e.g. hive-SIM001}"
REGION="${2:-${AWS_REGION:-eu-central-1}}"
DIR="$(dirname "$0")/../simulator/certs"
mkdir -p "$DIR"
OUT="$(aws iot create-keys-and-certificate --region "$REGION" --set-as-active \
  --certificate-pem-outfile "$DIR/$DEVICE.pem.crt" \
  --public-key-outfile "$DIR/$DEVICE.public.key" \
  --private-key-outfile "$DIR/$DEVICE.private.key" \
  --query certificateArn --output text)"
chmod 600 "$DIR/$DEVICE.private.key"
echo "certificate ARN for $DEVICE: $OUT"
echo "add to cdk.json: \"certificate_arns\": {\"$DEVICE\": \"$OUT\"}  then: cdk deploy"
echo "endpoint: $(aws iot describe-endpoint --region "$REGION" --endpoint-type iot:Data-ATS --query endpointAddress --output text)"
