#!/usr/bin/env bash
# base-diff.sh — what a base-image bump removes, BEFORE it ships (issue #53).
#
# Usage:
#   ./builder/base-diff.sh <old-tag> <new-tag>
#   ./builder/base-diff.sh 1.5.8 2.9.0-debian13
#
# Tags are mambaorg/micromamba tags, i.e. values of MAMBA_VERSION in builder/Dockerfile.
#
# WHY THIS EXISTS. The 2026-10-08 bump from Debian 12 to 13 was checked thoroughly for its
# purpose (glibc 2.38, for lammps) and not for anything else. Debian 13 had moved the legacy
# time-zone links (/usr/share/zoneinfo/US/*) out of `tzdata` into `tzdata-legacy`, so pyarrow
# failed on `US/Pacific` in eight envs for ten days (issue #45). A package-level diff would
# NOT have shown it: `tzdata` is in both bases, at different versions. Only a FILE-level diff
# does, and that is the core of this script.
#
# What it reports, most useful first:
#   1. paths present in the old base and missing from the new one, grouped by directory
#   2. OS packages removed, added and changed
#   3. glibc and distro versions
#
# Read section 1 for anything the envs might rely on. The conda prefix (/opt/conda) is
# excluded: conda packages come from the env spec, not the base.
#
# AFTER A BUMP, EVERY ENV REBUILDS. A base change moves no env's lock-hash, but since #56 it
# changes every env's build-input hash (builder/input-hash.sh), so the next reconcile flags
# them all BUILDER-STALE and republishes them. Before landing a bump, still rebuild locally
# and run every D3: the reconciler republishes, but a failing env just fails its publish.
set -euo pipefail

OLD="${1:?usage: base-diff.sh <old-tag> <new-tag>   (e.g. 1.5.8 2.9.0-debian13)}"
NEW="${2:?usage: base-diff.sh <old-tag> <new-tag>}"
IMG="mambaorg/micromamba"
PLATFORM="${PLATFORM:-linux/arm64}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

snapshot() {  # tag outdir
  docker run --rm --platform "$PLATFORM" --entrypoint /bin/sh -u root "$IMG:$1" -c '
    . /etc/os-release; echo "distro $PRETTY_NAME"
    echo "glibc $(ldd --version 2>/dev/null | head -1 | grep -oE "[0-9]+\.[0-9]+$")"
  ' > "$2.meta"
  docker run --rm --platform "$PLATFORM" --entrypoint /bin/sh -u root "$IMG:$1" -c \
    "dpkg-query -W -f '\${Package} \${Version}\n'" | sort > "$2.pkgs"
  # Files AND symlinks: the tz links that broke pyarrow were symlinks.
  docker run --rm --platform "$PLATFORM" --entrypoint /bin/sh -u root "$IMG:$1" -c \
    'find /usr /etc /lib /bin /sbin \( -path /usr/local -o -path /opt \) -prune -o \( -type f -o -type l \) -print 2>/dev/null' \
    | sort > "$2.files"
}

echo "[base-diff] snapshotting $IMG:$OLD and $IMG:$NEW ($PLATFORM) ..." >&2
snapshot "$OLD" "$WORK/old"
snapshot "$NEW" "$WORK/new"

echo "== $IMG:$OLD -> $IMG:$NEW =="
paste -d' ' <(sed 's/^/old: /' "$WORK/old.meta") <(sed 's/^/| new: /' "$WORK/new.meta")
echo

comm -23 "$WORK/old.files" "$WORK/new.files" > "$WORK/removed.files"
n_removed="$(wc -l < "$WORK/removed.files" | tr -d ' ')"
echo "== 1. paths in the OLD base missing from the NEW one: $n_removed =="
if [ "$n_removed" -gt 0 ]; then
  # Group by the first four path components so a removed directory reads as one line.
  awk -F/ '{d="/"$2"/"$3"/"$4"/"$5; sub(/\/+$/,"",d); c[d]++} END{for(k in c) printf "%6d  %s\n", c[k], k}' \
    "$WORK/removed.files" | sort -rn | head -40
  [ "$(awk -F/ '{print "/"$2"/"$3"/"$4"/"$5}' "$WORK/removed.files" | sort -u | wc -l)" -gt 40 ] \
    && echo "   ... (more; full list: comm -23 on the snapshots)"
fi
echo

echo "== 2. OS packages =="
join -a1 -a2 -e '-' -o 0,1.2,2.2 "$WORK/old.pkgs" "$WORK/new.pkgs" > "$WORK/joined"
echo "-- removed:"; awk '$3=="-" {printf "   %s %s\n", $1, $2}' "$WORK/joined"
echo "-- added:";   awk '$2=="-" {printf "   %s %s\n", $1, $3}' "$WORK/joined"
echo "-- changed: $(awk '$2!="-" && $3!="-" && $2!=$3' "$WORK/joined" | wc -l | tr -d ' ') package(s)"
awk '$2!="-" && $3!="-" && $2!=$3 {printf "   %s %s -> %s\n", $1, $2, $3}' "$WORK/joined" | head -30
echo
echo "Next: read section 1 for anything an env relies on, add a build-time assertion to"
echo "builder/Dockerfile for each OS path you decide to keep, then rebuild EVERY env."
