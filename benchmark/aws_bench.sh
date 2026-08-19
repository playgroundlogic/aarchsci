#!/usr/bin/env bash
# aws_bench.sh — run an aarch.science benchmark on real EC2, natively, no emulation.
#
# Two benchmarks live here:
#   geo   — the geo-prep throughput comparison (c7g vs c7i), 2026.07.
#   sve   — ARM SIMD/microarch runtime dispatch across Graviton generations, 2026.08.
#           Answers "which kernels does the shipped image actually select on Graviton2
#           through Graviton5, and what is that dispatch worth?" See sve_dispatch_bench.py.
#
# ---------------------------------------------------------------------------------
# Discovery is delegated to `truffle`, lifecycle to `spawn` (spore.host).
#
# This script previously hand-rolled both, and the hand-rolled versions produced three
# of the four bugs recorded in CHANGELOG.md under "Fixed (benchmark harness)":
#   - a pinned subnet id + a region read from $AWS_REGION -> InvalidSubnetID.NotFound.
#     `spawn` auto-creates and tags its own VPC/subnet, so that failure cannot occur.
#   - a per-family region/subnet table maintained by hand (hpc7g is us-east-1a only).
#     `truffle find` reports the offered AZs, so the table is derived, not remembered.
#   - a cleanup trap holding an instance id, which under `set -e` lost the variable to
#     frame unwinding and leaked the instance. `spawn --ttl` + `--on-complete terminate`
#     put the timer on the instance itself, where a dead launcher cannot defeat it.
#
# SSM stays the results channel even under spawn (spawn also offers SSH): a third party
# reproducing these numbers then needs no key material and no inbound port, and the run
# is identical whether it was launched by spawn or by the aws-cli fallback below.
#
# LAUNCHER=spawn|awscli|auto (default auto) — `auto` uses spawn when it is on PATH and
# falls back to raw aws-cli otherwise, so the published numbers stay reproducible by
# someone who does not have the spore.host tools installed.
# ---------------------------------------------------------------------------------
#
# The bench script is shipped in user-data as gzip+base64 rather than curled from
# GitHub, so a run always measures the WORKING COPY — no push required, and no
# chance of benchmarking a different revision than the one in your editor.
#
# Requires: AWS_PROFILE=aws (or set it).
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-aws}"
# Deliberately NOT AWS_REGION: that variable is commonly exported in a shell profile
# (mine was us-east-1), and inheriting it silently sent RunInstances at another region.
R="${AARCHSCI_BENCH_REGION:-us-west-2}"
PROFILE_NAME=AmazonSSMRoleForInstancesQuickSetup
SSM_POLICY=arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
IMAGE="${IMAGE:-quay.io/aarchsci/dft:latest}"
SPOT="${SPOT:-1}"
TTL="${TTL:-45m}"
LAUNCHER="${LAUNCHER:-auto}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '[aws_bench %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

have() { command -v "$1" >/dev/null 2>&1; }

launcher() {
  case "$LAUNCHER" in
    spawn|awscli) printf '%s' "$LAUNCHER" ;;
    auto) have spawn && printf 'spawn' || printf 'awscli' ;;
  esac
}

# ------------------------------------------------------------------------ discovery
#
# `truffle find`  -> vCPUs, physical cores, memory, offered AZs (so the region is
#                    derived rather than hardcoded per family).
# `truffle spot`  -> spot AND on-demand price in one call. Families with no spot market
#                    (all HPC families, incl. hpc7g) print a plain-text notice and no
#                    JSON; that absence is the signal to launch on-demand.
#
# Prices for PUBLISHED price/performance come from truffle's `on_demand_price`, which
# matches the AWS pricing API. Note `spawn launch --estimate-only` reports a rounder
# figure (it said $0.1000/hr for a c8g.large that truffle and the pricing API both put
# at $0.07976) — fine for a pre-flight cost warning, wrong for a published ratio.
discover() {
  local it="$1"
  have truffle || { echo "{}"; return 0; }
  IT="$it" REGION="$R" python3 - <<'PY'
import json, os, subprocess

it, region = os.environ["IT"], os.environ["REGION"]

def truffle(*args):
    p = subprocess.run(["truffle", *args, "-o", "json"],
                       capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except Exception:
        return None            # no JSON == no data for this query (e.g. no spot market)

out = {"instance_type": it}

# Search every region we might use, then pick one that actually offers the type.
found = truffle("find", it, "--regions", f"{region},us-east-1") or []
match = [e for e in found if e.get("instance_type") == it]
pref = [e for e in match if e.get("region") == region] or match
if pref:
    e = pref[0]
    out.update(region=e["region"], vcpus=e["vcpus"], physical_cores=e["physical_cores"],
               threads_per_core=e["threads_per_core"], memory_gib=e["memory_mib"] // 1024,
               azs=e["availability_zones"], spawn_supported=e.get("spawn_supported"))

sp = truffle("spot", it, "--regions", out.get("region", region), "--show-savings") or []
sp = [e for e in sp if e.get("instance_type") == it]
if sp:
    e = min(sp, key=lambda x: x["spot_price"])
    out.update(spot_price=e["spot_price"], on_demand_price=e["on_demand_price"],
               spot_az=e["availability_zone"], spot_market=True)
else:
    out["spot_market"] = False

print(json.dumps(out))
PY
}

# ---------------------------------------------------------------------------- launch
# Resolve the current Amazon Linux 2023 AMI for the arch instead of pinning a stale id.
ami_for() {
  local arch=arm64
  case "$1" in *c7i*|*c6i*|*m7i*) arch=x86_64 ;; esac
  aws ssm get-parameter --region "$R" \
    --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-${arch}" \
    --query 'Parameter.Value' --output text
}

# user-data: docker + the bench script. Under the aws-cli path this also arms a
# `shutdown +30`; under spawn, --ttl does that job on the instance itself.
userdata() {
  local script="$1" armshutdown="${2:-1}"
  {
    echo '#!/bin/bash'
    echo 'exec > /var/log/aarchsci-bench.log 2>&1'
    echo 'set -x'
    [ "$armshutdown" = "1" ] && echo 'shutdown -h +30 "aarchsci-bench safety auto-terminate"'
    cat <<'UD'
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
  }
}

launch_spawn() {
  local it="$1" script="$2" name="aarchsci-bench-${it//./-}"
  local ud idf; ud="$(mktemp)"; idf="$(mktemp)"
  userdata "$script" 0 > "$ud"
  local -a extra=()
  [ "$SPOT" = "1" ] && [ "$3" = "true" ] && extra+=(--spot)
  # --ttl and --terminate-on-error are the cost guardrails: the timer lives on the
  # instance, so it survives this script being killed. --iam-managed-policies is what
  # keeps the SSM results channel working under spawn.
  spawn launch "$name" \
    --instance-type "$it" --region "$R" \
    --ttl "$TTL" --terminate-on-error --yes --quiet \
    --iam-managed-policies "$SSM_POLICY" \
    --user-data-file "$ud" \
    --tag project=aarchsci-benchmark --tag bench=sve \
    --output-id "$idf" \
    "${extra[@]}" >&2
  rm -f "$ud"
  # spawn writes the instance/sweep id here; fall back to a tag lookup if it did not.
  if [ -s "$idf" ]; then tr -d '[:space:]' < "$idf"; else
    aws ec2 describe-instances --region "$R" \
      --filters "Name=tag:Name,Values=$name" "Name=instance-state-name,Values=pending,running" \
      --query 'Reservations[-1].Instances[-1].InstanceId' --output text
  fi
  rm -f "$idf"
}

launch_awscli() {
  local it="$1" script="$2" spot_ok="$3"
  local -a extra=()
  [ "$SPOT" = "1" ] && [ "$spot_ok" = "true" ] &&
    extra+=(--instance-market-options 'MarketType=spot,SpotOptions={SpotInstanceType=one-time}')
  local sn; sn="$(default_subnet)"
  aws ec2 run-instances --region "$R" \
    --image-id "$(ami_for "$it")" --instance-type "$it" \
    --subnet-id "$sn" \
    --iam-instance-profile "Name=$PROFILE_NAME" \
    --instance-initiated-shutdown-behavior terminate \
    --user-data "$(userdata "$script" 1 | base64)" \
    "${extra[@]}" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=aarchsci-bench-$it},{Key=project,Value=aarchsci-benchmark}]" \
    --query 'Instances[0].InstanceId' --output text
}

# Fallback path only: pick any subnet with auto-assign public IP in the region, rather
# than carrying a hardcoded id that goes stale or belongs to another region.
default_subnet() {
  aws ec2 describe-subnets --region "$R" \
    --filters "Name=map-public-ip-on-launch,Values=true" \
    --query 'Subnets[0].SubnetId' --output text
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
  if [ "$(launcher)" = spawn ] && have spawn; then
    spawn terminate "$1" --region "$R" --yes >&2 2>/dev/null && return 0
  fi
  aws ec2 terminate-instances --region "$R" --instance-ids "$1" \
    --query 'TerminatingInstances[0].CurrentState.Name' --output text >&2 || true
}

# sve <instance-type> — full cycle, emits RESULT lines on stdout.
# BENCH_ID is a global, not a local: under `set -e` bash unwinds the function frame
# before running the EXIT trap, so a local would be out of scope exactly when the
# cleanup needs it — the trap then dies on `unbound variable` and leaks the instance.
BENCH_ID=""
sve() {
  local it="$1" facts spot_ok region
  facts="$(discover "$it")"
  region=$(printf '%s' "$facts" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("region",""))' 2>/dev/null || true)
  [ -n "$region" ] && R="$region"
  spot_ok=$(printf '%s' "$facts" | python3 -c 'import json,sys; print(str(json.load(sys.stdin).get("spot_market",False)).lower())' 2>/dev/null || echo false)
  log "$it facts: $facts"
  log "$it -> region $R launcher=$(launcher) spot=$([ "$SPOT" = 1 ] && [ "$spot_ok" = true ] && echo yes || echo no) ttl=$TTL"

  trap 'terminate "$BENCH_ID"' EXIT INT TERM
  if [ "$(launcher)" = spawn ]; then
    BENCH_ID=$(launch_spawn "$it" "$HERE/sve_dispatch_bench.py" "$spot_ok")
  else
    BENCH_ID=$(launch_awscli "$it" "$HERE/sve_dispatch_bench.py" "$spot_ok")
  fi
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
  -e AARCHSCI_GEMM_N=${GEMM_N:-3072} -e AARCHSCI_SI_REPEAT=${SI_REPEAT:-2} \
  $IMAGE /opt/conda/bin/python /tmp/bench.py
" 2700
  terminate "$id"; BENCH_ID=""
  trap - EXIT INT TERM
}

# Leak check. `spawn orphans` knows about spawn-managed resources (including the VPC and
# security groups spawn creates); the tag scan catches anything either path launched.
audit() {
  have spawn && { log "spawn orphans (all regions):"; spawn orphans --all-regions >&2 || true; }
  for r in us-west-2 us-east-1; do
    log "--- $r tagged aarchsci-benchmark ---"
    aws ec2 describe-instances --region "$r" \
      --filters "Name=tag:project,Values=aarchsci-benchmark" \
      --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name,LaunchTime]' \
      --output text >&2
  done
}

case "${1:-}" in
  sve)      sve "$2" ;;
  discover) discover "$2" ;;
  audit)    audit ;;
  *) echo "usage: aws_bench.sh sve <instance-type>       # c6g/c7g/c8g/c9g/hpc7g
       aws_bench.sh discover <instance-type>   # truffle facts + spot/on-demand price
       aws_bench.sh audit                      # leak check (spawn orphans + tag scan)

  env: SPOT=0          on-demand instead of spot (spot is default; HPC has no spot)
       TTL=45m         instance-side auto-terminate (spawn path)
       LAUNCHER=awscli force the aws-cli path instead of spawn
       GEMM_N=6144     GEMM size        SI_REPEAT=3   Si supercell repeat (54 atoms)
       IMAGE=...       image under test (default quay.io/aarchsci/dft:latest)" >&2; exit 1 ;;
esac
