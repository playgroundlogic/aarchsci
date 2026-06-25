#!/usr/bin/env bash
# build-env.sh — build ONE native arm64 container for a curated conda-forge env,
# then PROVE it assembles (not just solves) before tagging.
#
# Usage:
#   ./build-env.sh <env-name>
#   ./build-env.sh geospatial
#   PUSH=1 ./build-env.sh geospatial      # also push to quay.io/aarchsci
#
# This is aarch.science's analogue of aarchbio's build.sh, with three divergences:
#   D2  it builds from envs/<env>.yaml (a curated multi-package env), not PKG=VER.
#   D3  it runs the baked-in smoke test inside the arm64 image and REFUSES to tag
#       unless the env imports + does real work. "Verified" is earned here.
#   OQ2 it tags from what was ACTUALLY installed: it reads the resolved package set
#       out of the finished image, writes the committed lock (envs/<env>.lock.txt),
#       and tags <date>, s<lock-hash>, and latest. The lock hash is the reconciler's
#       "changed?" signal (OQ4).
#
# An env that mixes noarch + arch-specific packages (geospatial does: gdal/proj/geos
# are native) is arch-specific AS A WHOLE, so it is always built natively per-arch —
# no emulation (D1). v1 builds the arm64 leg (the gap fieldwork hit; amd64 already
# assembles via conda/pip upstream). Multi-arch merge is a later addition.
set -euo pipefail

ENV_NAME="${1:?usage: build-env.sh <env-name>   (e.g. geospatial)}"

REGISTRY="${REGISTRY:-quay.io/aarchsci}"
PLATFORM="${PLATFORM:-linux/arm64}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
ENV_DIR="$ROOT/envs"
ENV_FILE="$ENV_DIR/${ENV_NAME}.yaml"
SMOKE_FILE="$ENV_DIR/${ENV_NAME}.smoke.py"
LOCK_FILE="$ENV_DIR/${ENV_NAME}.lock.txt"

[ -f "$ENV_FILE" ]   || { echo "[build] ERROR: no env spec at $ENV_FILE" >&2; exit 2; }
[ -f "$SMOKE_FILE" ] || { echo "[build] ERROR: no smoke test at $SMOKE_FILE (D3 requires one)" >&2; exit 2; }

# Provenance: link any tag back to the committed spec + builder commit.
SOURCE_SPEC="https://github.com/playgroundlogic/aarchsci/blob/main/envs/${ENV_NAME}.yaml"
GIT_SHA="$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# uv locally (project standard), python3 on CI (lessons #12). Array so it expands
# whether it's one word or two.
if command -v uv >/dev/null 2>&1; then PY=(uv run python); else PY=(python3); fi

# Date tag is fine to take from the clock in bash (this is not a resumable journal).
DATE_TAG="${DATE_TAG:-$(date -u +%Y.%m.%d)}"

# --- 1. Build the arm64 probe image (native, no emulation) -----------------
# Build to a temp tag and --load so we can (a) read the resolved package set, (b)
# run the smoke test — all BEFORE deciding how/whether to publish. The build context
# is envs/ so the Dockerfile can COPY the spec + smoke test in.
TMP_IMAGE="${REGISTRY}/${ENV_NAME}:_building"
echo "[build] building probe image for ${ENV_NAME} (${PLATFORM}, native = no emulation) ..."
docker buildx build \
  --platform "$PLATFORM" \
  --build-arg ENV_NAME="$ENV_NAME" \
  --build-arg SOURCE_SPEC="$SOURCE_SPEC" \
  --build-arg BUILDER_GIT_SHA="$GIT_SHA" \
  -f "$HERE/Dockerfile" \
  -t "$TMP_IMAGE" \
  --load \
  "$ENV_DIR"

# --- 2. Read the resolved package set from the FINISHED image (#16) ---------
# Never predict the lock from a host solve — the in-container resolver is the source
# of truth. Read it as JSON (the table view's header height varies across micromamba
# versions, so column-skipping by row number is fragile — bit us once). We keep
# "name version" sorted; that canonical text is the lock and feeds the lock-hash.
echo "[build] reading resolved package set from the probe image ..."
LIST_JSON="$(docker run --rm --platform "$PLATFORM" "$TMP_IMAGE" \
              micromamba list -n base --json 2>/dev/null)"
RESOLVED="$(printf '%s' "$LIST_JSON" | "${PY[@]}" -c '
import json, sys
try:
    pkgs = json.load(sys.stdin)
except Exception:
    sys.exit(1)
for p in sorted(pkgs, key=lambda x: x.get("name", "")):
    n, v = p.get("name", ""), p.get("version", "")
    if n:
        print(n, v)
')"
[ -n "$RESOLVED" ] || { echo "[build] ERROR: could not read installed package list" >&2; exit 2; }
PKG_COUNT="$(printf '%s\n' "$RESOLVED" | grep -c . || true)"
echo "[build] resolved ${PKG_COUNT} packages."

# Lock-hash: sha256 of the canonical resolved set, short form. Identical resolved
# sets -> identical hash -> idempotent tag (and the reconciler's "changed?" test).
LOCK_HASH="$(printf '%s\n' "$RESOLVED" \
  | { shasum -a 256 2>/dev/null || sha256sum; } \
  | awk '{print substr($1,1,12)}')"
HASH_TAG="s${LOCK_HASH}"
echo "[build] lock-hash: ${LOCK_HASH}  (tag ${HASH_TAG})"

# --- 3. D3 smoke test: prove ASSEMBLE + IMPORT + FUNCTION, not just solve ----
# The image already carries /opt/aarchsci/smoke.py. Run it on the target arch; a
# non-zero exit here is fatal — we will NOT tag an env that doesn't actually work.
echo "[build] running D3 smoke test (assemble + import + functional) on ${PLATFORM} ..."
if ! docker run --rm --platform "$PLATFORM" "$TMP_IMAGE" python /opt/aarchsci/smoke.py; then
  echo "[build] ERROR: smoke test FAILED — env solved but does not assemble/work on ${PLATFORM}." >&2
  echo "[build]        Refusing to tag. This is exactly the fieldwork failure (D3)." >&2
  docker rmi "$TMP_IMAGE" >/dev/null 2>&1 || true
  exit 1
fi
echo "[build] smoke test PASSED — ${ENV_NAME} is verified on ${PLATFORM}."

# --- 4. Commit the lock (the spec's resolved truth) -------------------------
# Written only after the smoke test passes, so a committed lock always corresponds
# to a build that actually worked.
{
  echo "# ${ENV_NAME} — resolved conda-forge package set (linux-aarch64)"
  echo "# Source spec: envs/${ENV_NAME}.yaml   Built: ${DATE_TAG}   Builder: ${GIT_SHA}"
  echo "# lock-hash: ${LOCK_HASH}   packages: ${PKG_COUNT}"
  echo "# Regenerate with: ./builder/build-env.sh ${ENV_NAME}"
  printf '%s\n' "$RESOLVED"
} > "$LOCK_FILE"
echo "[build] wrote lock: ${LOCK_FILE}"

# --- 5. Publish (optional) --------------------------------------------------
# Tags: <date> (human/reconciler), s<lock-hash> (content-addressed, idempotent),
# latest (convenience). arm64-only for v1 — the amd64 half already assembles
# upstream; this fills the arm64 gap. (buildx --load already produced the layers;
# --push rebuilds from cache and pushes the manifest.)
DIGEST=""
DATE_IMAGE="${REGISTRY}/${ENV_NAME}:${DATE_TAG}"
if [ "${PUSH:-0}" = "1" ]; then
  echo "[build] pushing ${REGISTRY}/${ENV_NAME} tags: ${DATE_TAG}, ${HASH_TAG}, latest (${PLATFORM}) ..."
  docker buildx build \
    --platform "$PLATFORM" \
    --build-arg ENV_NAME="$ENV_NAME" \
    --build-arg SOURCE_SPEC="$SOURCE_SPEC" \
    --build-arg BUILDER_GIT_SHA="$GIT_SHA" \
    -f "$HERE/Dockerfile" \
    -t "$DATE_IMAGE" \
    -t "${REGISTRY}/${ENV_NAME}:${HASH_TAG}" \
    -t "${REGISTRY}/${ENV_NAME}:latest" \
    --push \
    "$ENV_DIR"
  DIGEST="$(docker buildx imagetools inspect "$DATE_IMAGE" --format '{{.Manifest.Digest}}' 2>/dev/null)"
  echo "[build] pushed. digest=${DIGEST:-unknown}"
else
  echo "[build] not pushing (set PUSH=1). Probe image was built + verified locally."
fi
docker rmi "$TMP_IMAGE" >/dev/null 2>&1 || true

# --- 6. Machine-readable outputs (for a CI caller) --------------------------
PINNED=""
[ -n "$DIGEST" ] && PINNED="${REGISTRY}/${ENV_NAME}@${DIGEST}"
emit() { echo "$1=$2"; [ -n "${GITHUB_OUTPUT:-}" ] && echo "$1=$2" >> "$GITHUB_OUTPUT"; return 0; }
echo "[build] outputs:"
emit env        "$ENV_NAME"
emit date_tag   "$DATE_TAG"
emit hash_tag   "$HASH_TAG"
emit lock_hash  "$LOCK_HASH"
emit packages   "$PKG_COUNT"
emit image      "$DATE_IMAGE"
emit digest     "$DIGEST"
emit pinned     "$PINNED"
emit pushed     "${PUSH:-0}"
emit platform   "$PLATFORM"

echo "[build] done: ${ENV_NAME} (${PKG_COUNT} pkgs, ${PLATFORM}, ${HASH_TAG})"
