"""IoT Core side: one thing per device, one shared least-privilege policy, optional certificates.

The policy uses IoT policy variables so a single document is scoped per device: a thing may
only connect as itself and may only publish to its own telemetry topic. The certificate is
a placeholder: creating keys is an interactive, one-time, secret-producing step that must
not run in CI (see scripts/provision_device.sh); pass the resulting ARNs through the
`certificate_arns` context map and the stack attaches them.
"""

from __future__ import annotations

from aws_cdk import Stack
from aws_cdk import aws_iot as iot
from constructs import Construct

from pipeline.config import PipelineConfig

THING_NAME_VAR = "${iot:Connection.Thing.ThingName}"


def device_policy_document(stack: Stack, topic_prefix: str) -> dict:
    def arn(resource: str) -> str:
        return stack.format_arn(service="iot", resource=resource)

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "iot:Connect",
                "Resource": arn(f"client/{THING_NAME_VAR}"),
                "Condition": {"Bool": {"iot:Connection.Thing.IsAttached": "true"}},
            },
            {
                "Effect": "Allow",
                "Action": "iot:Publish",
                "Resource": arn(f"topic/{topic_prefix}/{THING_NAME_VAR}/telemetry"),
            },
            {
                "Effect": "Allow",
                "Action": "iot:Subscribe",
                "Resource": arn(f"topicfilter/{topic_prefix}/{THING_NAME_VAR}/commands"),
            },
            {
                "Effect": "Allow",
                "Action": "iot:Receive",
                "Resource": arn(f"topic/{topic_prefix}/{THING_NAME_VAR}/commands"),
            },
        ],
    }


class Devices(Construct):
    def __init__(self, scope: Construct, id: str, cfg: PipelineConfig) -> None:
        super().__init__(scope, id)
        stack = Stack.of(self)

        self.policy = iot.CfnPolicy(
            self,
            "DevicePolicy",
            policy_name=f"{cfg.project}-device-policy",
            policy_document=device_policy_document(stack, cfg.topic_prefix),
        )

        self.things: dict[str, iot.CfnThing] = {}
        for device_id in cfg.device_ids:
            thing = iot.CfnThing(
                self,
                f"Thing-{device_id}",
                thing_name=device_id,
                attribute_payload=iot.CfnThing.AttributePayloadProperty(
                    attributes={"project": cfg.project, "kind": "incubator"}
                ),
            )
            self.things[device_id] = thing
            cert_arn = cfg.certificate_arns.get(device_id)
            if cert_arn:
                iot.CfnThingPrincipalAttachment(
                    self, f"ThingCert-{device_id}", principal=cert_arn, thing_name=thing.ref
                )
                iot.CfnPolicyPrincipalAttachment(
                    self, f"PolicyCert-{device_id}", policy_name=self.policy.ref, principal=cert_arn
                )
