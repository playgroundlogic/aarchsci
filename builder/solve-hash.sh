#!/usr/bin/env bash
# solve-hash.sh — cheaply compute an env's lock-hash WITHOUT building an image.
#
# This is the reconciler's "has the resolved set changed?" probe (DESIGN OQ4). It
# runs a micromamba dry-run SOLVE of envs/<name>.yaml inside an arm64 container — no
# install, no image — and hashes the resolved "name version" set the SAME way
# build-env.sh does (sha256 of the LC_ALL=C-sorted list, short). So:
#
#   solve-hash.sh <env>   ==   the s<lock-hash> a fresh build-env.sh would produce
#
# Reconciler logic: if this differs from the committed envs/<name>.lock.txt header,
# the channel moved -> rebuild + republish. If it matches, no-op.
#
# Build strings can differ between a dry-run solve and the final install, but we
# only ever hash name+version (not build), which the solver fixes identically — so
# the cheap solve and the real build agree. (The build remains authoritative: it
# rewrites the lock from the finished image, aarchbio lesson #16.)
#
# Usage:  ./solve-hash.sh <env-name>
# Emits:  lock_hash=<12hex>  (and to $GITHUB_OUTPUT if set); prints the hash alone on stdout.
set -uo pipefail

ENV_NAME="${1:?usage: solve-hash.sh <env-name>}"
PLATFORM="${PLATFORM:-linux/arm64}"
MAMBA_IMAGE="${MAMBA_IMAGE:-mambaorg/micromamba:1.5.8}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$(cd "$HERE/.." && pwd)/envs/${ENV_NAME}.yaml"
[ -f "$ENV_FILE" ] || { echo "[solve-hash] ERROR: no env spec at $ENV_FILE" >&2; exit 2; }

# uv locally, python3 on CI (lessons #12).
if command -v uv >/dev/null 2>&1; then PY=(uv run python); else PY=(python3); fi
emit() { echo "$1=$2"; [ -n "${GITHUB_OUTPUT:-}" ] && echo "$1=$2" >> "$GITHUB_OUTPUT"; return 0; }

# Dry-run solve the env from its spec, in an arm64 container. Mount the spec read-only.
json="$(docker run --rm --platform "$PLATFORM" -v "$ENV_FILE":/env.yaml:ro "$MAMBA_IMAGE" \
        micromamba create -n _probe --dry-run --json -f /env.yaml 2>/dev/null)"

# Parse via a temp file, not a pipe: the parser finishing early would SIGPIPE the
# large JSON under pipefail (aarchbio classify.sh paid for this on big solves).
JSON_TMP="$(mktemp)"; printf '%s' "$json" > "$JSON_TMP"
RESOLVED="$("${PY[@]}" -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
rows = []
for a in d.get("actions", {}).get("LINK", []):
    n, v = a.get("name", ""), a.get("version", "")
    if n:
        rows.append(n + " " + v)
for r in sorted(rows):       # match build-env.shs LC_ALL=C sort of "name version"
    print(r)
' "$JSON_TMP")"
rm -f "$JSON_TMP"

if [ -z "${RESOLVED:-}" ]; then
  echo "[solve-hash] ERROR: could not solve ${ENV_NAME} for ${PLATFORM} (no arm64 solution?)" >&2
  exit 2
fi

LOCK_HASH="$(printf '%s\n' "$RESOLVED" \
  | { shasum -a 256 2>/dev/null || sha256sum; } \
  | awk '{print substr($1,1,12)}')"

emit lock_hash "$LOCK_HASH"
