#!/usr/bin/env bash
# aws_bench.sh — run an aarch.science benchmark on real EC2, natively, no emulation.
#
# Two benchmarks live here:
#   geo   — the geo-prep throughput comparison (c7g vs c7i), 2026.07.
#   sve   — ARM SIMD/microarch runtime dispatch (c7g vs c8g), 2026.08. Answers
#           "which kernels does the shipped image actually select on Graviton3
#           vs Graviton4, and what is that dispatch worth?" See sve_dispatch_bench.py.
#
# Reporting channel: SSM Run Command reads the bench stdout directly (needs only
# AmazonSSMManagedInstanceCore — no extra IAM, no SSH, no inbound ports). user-data
# installs docker, drops the bench script, and arms a `shutdown +30` safety net; we
# drive the bench via send-command and terminate as soon as results are in.
# Triple safety: shutdown-behavior=terminate, the +30 auto-off, and a trap on exit.
#
# The bench script is shipped in user-data as gzip+base64 rather than curled from
# GitHub, so a run always measures the WORKING COPY — no push required, and no
# chance of benchmarking a different revision than the one in your editor.
#
# Requires: AWS_PROFILE=aws (or set it), region us-west-2.
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-aws}"
# Deliberately NOT AWS_REGION: that variable is commonly exported in a shell profile
# (mine was us-east-1), and inheriting it silently sent RunInstances at another region
# where the pinned subnet legitimately does not exist. The subnet and region here are
# a matched pair, so they get their own namespaced override.
R="${AARCHSCI_BENCH_REGION:-us-west-2}"
SUBNET="${AARCHSCI_BENCH_SUBNET:-subnet-01b371d6fee7b70a5}"   # us-west-2c, auto public IP
PROFILE_NAME=AmazonSSMRoleForInstancesQuickSetup
IMAGE="${IMAGE:-quay.io/aarchsci/dft:latest}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '[aws_bench %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

# Resolve the current Amazon Linux 2023 AMI for the arch instead of pinning a stale
# id (the previously hardcoded ones are months old and drift out of support).
ami_for() {
  local arch=arm64
  case "$1" in *c7i*|*c6i*|*m7i*) arch=x86_64 ;; esac
  aws ssm get-parameter --region "$R" \
    --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${arch}" \
    --query 'Parameter.Value' --output text
}

# user-data: docker + the bench script + the auto-terminate net.
userdata() {
  local script="$1"
  {
    cat <<'UD'
#!/bin/bash
exec > /var/log/aarchsci-bench.log 2>&1
set -x
shutdown -h +30 "aarchsci-bench safety auto-terminate"
dnf install -y docker && systemctl enable --now docker
mkdir -p /opt/aarchsci
base64 -d <<'B64GZ' | gunzip > /opt/aarchsci/bench.py
UD
    gzip -9c "$script" | base64
    cat <<'UD'
B64GZ
chmod 0644 /opt/aarchsci/bench.py
touch /opt/aarchsci/ready
UD
  } | base64
}

launch() {
  local it="$1" script="$2"
  aws ec2 run-instances --region "$R" \
    --image-id "$(ami_for "$it")" --instance-type "$it" \
    --subnet-id "$SUBNET" \
    --iam-instance-profile "Name=$PROFILE_NAME" \
    --instance-initiated-shutdown-behavior terminate \
    --user-data "$(userdata "$script")" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=aarchsci-bench-$it},{Key=project,Value=aarchsci-benchmark}]" \
    --query 'Instances[0].InstanceId' --output text
}

wait_ssm() {
  local id="$1" deadline=$((SECONDS + 420))
  while (( SECONDS < deadline )); do
    if [ "$(aws ssm describe-instance-information --region "$R" \
             --filters "Key=InstanceIds,Values=$id" \
             --query 'length(InstanceInformationList)' --output text)" = "1" ]; then
      log "$id online in SSM"; return 0
    fi
    sleep 10
  done
  log "TIMEOUT waiting for $id in SSM"; return 1
}

# Run one shell snippet on the instance and echo its stdout. Polls to completion.
#
# Built via --cli-input-json, NOT --parameters: the CLI's shorthand parser does not
# unescape a JSON string, so a multi-line command arrives on the instance with
# literal backslash-n and dies with a bash syntax error at line 1.
remote() {
  local id="$1" cmd="$2" timeout="${3:-1800}"
  local cid json
  json="$(mktemp)"
  CMD="$cmd" IID="$id" TMO="$timeout" python3 -c '
import json, os
print(json.dumps({
    "InstanceIds": [os.environ["IID"]],
    "DocumentName": "AWS-RunShellScript",
    "TimeoutSeconds": int(os.environ["TMO"]),
    "Parameters": {"commands": [os.environ["CMD"]],
                   "executionTimeout": [os.environ["TMO"]]},
}))' > "$json"
  cid=$(aws ssm send-command --region "$R" --cli-input-json "file://$json" \
          --query 'Command.CommandId' --output text)
  rm -f "$json"
  local deadline=$((SECONDS + timeout + 120)) status
  while (( SECONDS < deadline )); do
    status=$(aws ssm get-command-invocation --region "$R" --command-id "$cid" \
               --instance-id "$id" --query 'Status' --output text 2>/dev/null || echo Pending)
    case "$status" in
      Success|Failed|Cancelled|TimedOut) break ;;
    esac
    sleep 15
  done
  log "command $cid -> $status"
  aws ssm get-command-invocation --region "$R" --command-id "$cid" --instance-id "$id" \
    --query 'StandardOutputContent' --output text
  aws ssm get-command-invocation --region "$R" --command-id "$cid" --instance-id "$id" \
    --query 'StandardErrorContent' --output text >&2
  [ "$status" = "Success" ]
}

terminate() {
  [ -n "${1:-}" ] || return 0
  log "terminating $1"
  aws ec2 terminate-instances --region "$R" --instance-ids "$1" \
    --query 'TerminatingInstances[0].CurrentState.Name' --output text >&2 || true
}

# sve <instance-type> — full cycle, emits RESULT lines on stdout.
# BENCH_ID is a global, not a local: under `set -e` bash unwinds the function frame
# before running the EXIT trap, so a local would be out of scope exactly when the
# cleanup needs it — the trap then dies on `unbound variable` and leaks the instance.
BENCH_ID=""
sve() {
  local it="$1"
  trap 'terminate "$BENCH_ID"' EXIT INT TERM
  BENCH_ID=$(launch "$it" "$HERE/sve_dispatch_bench.py")
  local id="$BENCH_ID"
  log "launched $it as $id"
  wait_ssm "$id"
  remote "$id" "
set -x
for i in \$(seq 1 60); do [ -f /opt/aarchsci/ready ] && docker info >/dev/null 2>&1 && break; sleep 10; done
docker info >/dev/null || { echo 'docker never came up'; exit 1; }
nproc; grep -m1 ^Features /proc/cpuinfo
docker pull -q $IMAGE
docker run --rm -v /opt/aarchsci/bench.py:/tmp/bench.py:ro \
  -e AARCHSCI_INSTANCE_TYPE=$it -e AARCHSCI_IMAGE=$IMAGE \
  $IMAGE /opt/conda/bin/python /tmp/bench.py
" 1800
  terminate "$id"; BENCH_ID=""
  trap - EXIT INT TERM
}

case "${1:-}" in
  launch) launch "$2" "${3:-$HERE/sve_dispatch_bench.py}" ;;
  sve)    sve "$2" ;;
  *) echo "usage: aws_bench.sh sve <c7g.large|c8g.large>
       aws_bench.sh launch <instance-type> [bench.py]" >&2; exit 1 ;;
esac
