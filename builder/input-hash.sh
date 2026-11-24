#!/usr/bin/env bash
# input-hash.sh — the build-input hash for one env (issue #56).
#
# Usage:  ./builder/input-hash.sh <env-name>      -> prints a 12-hex-digit hash
#
# WHY. The reconciler rebuilds an env when its conda solve drifts or when quay lacks an image
# for the committed lock. Neither notices a change to the BUILDER: a base-image bump, a new
# OS package (tzdata-legacy, #45), an activate.d fix, or a corrected smoke test moves no
# lock-hash, so every env reads "up to date" and keeps its old build indefinitely. This hash
# covers exactly those inputs. build-env.sh stamps it on each image as the label
# `science.aarch.build-input-hash`, and reconcile.yml rebuilds any env whose published label
# differs from the current value.
#
# WHAT IS HASHED, and why each is in or out:
#   in   builder/Dockerfile           shared by every env (base tag, OS packages, activation)
#   in   envs/<env>.smoke.{py,R}      baked into the image; a fixed check should reach it
#   out  envs/<env>.yaml              already covered by the lock-hash (resolved set)
#   out  builder/build-env.sh         orchestration only; it doesn't change image contents
#
# COMMENT-ONLY EDITS DON'T COUNT. Whole-line comments (`#` as first non-blank character)
# and blank lines are stripped before hashing. The Dockerfile is heavily commented by design,
# and without this a typo fix in a comment would rebuild all 29 envs. Trailing comments and
# docstrings still count; that is the price of keeping this a few lines of awk.
set -euo pipefail

ENV_NAME="${1:?usage: input-hash.sh <env-name>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

smoke=""
for ext in py R; do
  [ -f "$ROOT/envs/${ENV_NAME}.smoke.$ext" ] && smoke="$ROOT/envs/${ENV_NAME}.smoke.$ext" && break
done
[ -n "$smoke" ] || { echo "input-hash: no smoke test for $ENV_NAME" >&2; exit 2; }

strip() { awk '!/^[[:space:]]*#/ && NF'; }

{
  echo "== Dockerfile"; strip < "$HERE/Dockerfile"
  echo "== smoke";      strip < "$smoke"
} | { shasum -a 256 2>/dev/null || sha256sum; } | awk '{print substr($1,1,12)}'
