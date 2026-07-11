#!/usr/bin/env bash
# aws_bench.sh — run the geo-prep benchmark on a real c7g (Graviton) and c7i (Intel)
# in AWS, natively, same vCPU count / image / workload. The honest apples-to-apples
# the farm couldn't give (different physical boxes).
#
# Reporting channel: SSM Run Command reads the bench stdout directly (needs only
# AmazonSSMManagedInstanceCore — no extra IAM). user-data just installs docker +
# arms a `shutdown +25` safety net; we drive the bench via send-command and
# terminate manually as soon as results are in. Double safety: shutdown-behavior
# =terminate, and the +25 auto-off.
#
# Requires: AWS_PROFILE=aws. Region us-west-2.
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-aws}"
R=us-west-2
SUBNET=subnet-01b371d6fee7b70a5
PROFILE_NAME=AmazonSSMRoleForInstancesQuickSetup
declare -A AMI=( [c7g.large]=ami-0f67ee33b59cb8565 [c7i.large]=ami-0b787142aa56d54db )

# user-data: install docker + agent stays online; arm a 25-min auto-terminate net.
userdata() {
  cat <<'UD' | base64
#!/bin/bash
exec > /var/log/aarchsci-bench.log 2>&1
set -x
shutdown -h +25 "aarchsci-bench safety auto-terminate"   # net: gone in 25 min no matter what
dnf install -y docker && systemctl enable --now docker
UD
}

launch() {
  local it="$1"
  aws ec2 run-instances --region "$R" \
    --image-id "${AMI[$it]}" --instance-type "$it" \
    --subnet-id "$SUBNET" \
    --iam-instance-profile "Name=$PROFILE_NAME" \
    --instance-initiated-shutdown-behavior terminate \
    --user-data "$(userdata)" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=aarchsci-bench-$it},{Key=project,Value=aarchsci-benchmark}]" \
    --query 'Instances[0].InstanceId' --output text
}

case "${1:-}" in
  launch) launch "$2" ;;
  *) echo "usage: aws_bench.sh launch <c7g.large|c7i.large>" >&2; exit 1 ;;
esac
