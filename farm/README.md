# farm

State for keeping the catalog current — mirrors aarchbio's `farm/`, adapted to the
curated-env model.

## skip-list.tsv

Env/package pairs the daily reconciler should stop retrying. Two tiers:

- **wontfix** — genuine dead-end; never retry (only a human edit removes it).
- **stuck** — a real chance of an upstream fix; skip the daily run, re-check on the
  1st of the month (or a manual dispatch with `include_stuck=true`).

Columns (tab-separated): `package  tier  revisit_days  reason`. The `package`
column is a conda-forge package name, or `env:<name>` to skip a whole env spec.

The reconciler (`.github/workflows/reconcile.yml`) reads this each run. Currently
**empty** — no known hard arm64 gaps in the science head (see [../GAPS.md](../GAPS.md)).

## Why a gap here isn't an aarchbio gap

aarchbio's gap = "no arm64 build of this tool exists" (a solve check finds it).
aarch.science has a subtler second mode: a package can **solve** yet fail to
**assemble/import** on arm64 (the D3 trap — a missing native lib or ABI mismatch).
The D3 smoke test inside the built image is what catches the second kind; a failed
smoke test blocks publishing and the package goes on the skip-list. See GAPS.md for
the two-kinds-of-gap model.

## Local build farm (orion arm64 + janus amd64)

The build machinery (`builder/build-env.sh`) runs on any native arm64 host. orion's
docker lives at `/opt/homebrew/bin/docker` (colima) — reach it over ssh with an
interactive login shell (`ssh orion.local 'zsh -lic "docker …"'`) or by prefixing
`PATH=/opt/homebrew/bin:$PATH`, since the non-interactive ssh PATH omits Homebrew.
The farm builds heavy + local (private/unsigned); CI keyless-signs light (no OIDC
on the farm — same split as aarchbio).
