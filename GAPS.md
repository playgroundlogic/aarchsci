# arm64 gaps — what aarch.science can't build (yet), and why

The honest record of conda-forge scientific packages that **can't** be assembled
into a verified native arm64 env today, and why. This is both a transparency
record and a **prioritized upstream-contribution roadmap** — gaps get fixed in the
relevant conda-forge feedstock, not by us.

aarch.science does **not** compile packages from source (that would make it a
second conda-forge and break the verify-the-build trust model — DESIGN non-goals).
Gaps are fixed *upstream* by enabling `linux-aarch64` in the feedstock, or by
fixing the native-lib/ABI bug that makes a package solve-but-not-assemble. Our job
is to surface, verify, and prioritize them. The daily reconciler re-checks and the
skip-list (`farm/skip-list.tsv`) stops us looping on known dead-ends.

## Two kinds of gap (and why the second is the one that matters here)

Unlike aarchbio — whose gap is simply *"no arm64 build exists"* (a solve check
suffices) — aarch.science has to distinguish two failure modes:

| Kind | Meaning | How we detect it |
|------|---------|------------------|
| **solve-gap** | the package has no `linux-aarch64` build on conda-forge, so the env won't even resolve | `builder/solve-hash.sh` fails to solve |
| **assemble-gap** | the env *solves* but a package fails to **import / run** on arm64 (missing native lib, ABI mismatch, broken build) — the trap that bit fieldwork on pip | the D3 smoke test fails inside the built arm64 image |

The **assemble-gap is the dangerous one** and the reason "verified" means a
functional smoke test, not a green solve. A solve-gap is loud; an assemble-gap is
silent until you `import`.

## Summary (as of 2026-06)

| Kind | Count | Notes |
|------|------:|-------|
| **solve-gap** | 0 | every package probed for the planned envs solves clean on conda-forge linux-aarch64 |
| **assemble-gap** | 0 | both shipped envs (geospatial, earth-observation) pass the full D3 smoke test |

**There are no known hard arm64 gaps in the curated science head.** This is the
real finding, and it's the inverse of the pip experience: the exact stack that
fails `No matching distribution found for rasterio` on PyPI solves *and* assembles
cleanly on conda-forge. The delivery channel was the whole problem.

### Coverage probe (conda-forge linux-aarch64 dry-run solve)

Candidate packages across the planned envs (geo-ml, climate, lidar, …) were
probed for an arm64 solution. All of the following resolve native arm64:

`geopandas` · `pysal` · `scikit-learn` · `xgboost` · `lightgbm` · `statsmodels` ·
`cartopy` · `cfgrib` · `eccodes` · `metpy` · `xesmf` · `esmpy` · `pdal` ·
`python-pdal` · `laspy` · `pytorch` (CPU) · `opencv` · `intake` · `s3fs` ·
`fsspec` · `zarr` · `datashader` · `leafmap` · `geemap` · `whitebox` · `richdem`

(A handful of names probed `EMPTY` simply because they aren't real conda-forge
packages — e.g. `pdal-python` is `python-pdal`, `cpuonly` is a PyTorch variant
selector, `rioxarray-spatial` doesn't exist. Those are naming, not gaps.)

The one true hardware boundary is **GPU/CUDA** — out of scope by DESIGN D4, because
Graviton has no NVIDIA GPU. `pytorch` solves and imports on arm64 *as a CPU build*;
that's what we'd ship.

## When a real gap appears

1. The reconciler's solve-hash fails (solve-gap) **or** the D3 smoke test fails
   (assemble-gap) — the env won't publish either way (the gate holds).
2. Add an entry to [`farm/skip-list.tsv`](farm/skip-list.tsv): `wontfix` (never
   retry) or `stuck` (revisit monthly), with the blocking package and reason.
3. Record it in this file under a new "Active gaps" section with the upstream
   feedstock to fix.
4. File it upstream at the package's conda-forge feedstock
   (`github.com/conda-forge/<pkg>-feedstock`).

## Active gaps

_None._
