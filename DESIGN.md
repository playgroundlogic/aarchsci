# aarch.science — Design

Verified, signed, native **arm64 (aarch64)** containers for the **scientific-computing
stack** — geospatial / earth-observation first. Sister project to
[aarchbio](https://github.com/playgroundlogic/aarchbio), which does the same for
bioinformatics (BioContainers/bioconda). aarch.science covers the layer aarchbio
deliberately excluded: the **conda-forge** scientific stack.

> This document is the authoritative design. It is written before the builder so
> the architecture — especially where it *diverges* from aarchbio — is deliberate.

## Why this exists (the measured demand)

The founding use case, like taxprofiler was for aarchbio: the **fieldwork / BuckAI**
H2-prospecting pipeline (Ohio State, Earth Sciences) has a CPU-heavy geospatial
prep stage — `rasterio` + GDAL/PROJ/GEOS + `scikit-image` + `shapely`. It is an
ideal Graviton workload: **c7g measured cheapest per physical core** ($0.036 vs
c7a $0.051 vs c7i $0.089 — ~2.5× better price/core).

But the Graviton run **failed outright**: `No matching distribution found for
rasterio`. PyPI's arm64 wheel coverage for the native-library geo stack is
incomplete and Python-version-fragile — the "advertised arm64 isn't really there"
problem. The pipeline fell back to Intel c7i, leaving the price/perf win on the
table.

**The gap is real and one layer over from aarchbio's:** the *packages exist on
arm64* (conda-forge ships `linux-aarch64`), they just don't assemble via the
channel the consumer reached for (pip/PyPI). Same shape as aarchbio — capability
present, delivery broken — different channel.

### Thesis test (passed)

`micromamba create --platform linux/arm64 -c conda-forge gdal proj geos rasterio
scikit-image shapely pyproj fiona` solves cleanly: **123 packages, all native
arm64** (gdal 3.12.3, rasterio 1.5.0, proj 9.7.1, geos 3.14.1, …). The exact stack
pip couldn't assemble resolves fine on conda-forge. The fix is to build a
verified, signed arm64 *container* from the conda-forge packages.

## What transfers from aarchbio (reuse, don't reinvent)

The core machinery is proven and directly applicable:
- **Native build, no emulation** — each arch on its own runner; multi-arch
  manifests where the stack is arch-neutral.
- **Keyless signing** (cosign / Sigstore / Rekor) — verify the build, not the publisher.
- **Daily reconciler** — keep current with upstream; skip-list for dead-ends.
- **The farm** (janus amd64 + orion arm64), the registry/trust model, the
  SEO/site/agent-metadata playbook, gap tracking + provenance.
- **Power-law scoping** — build & deeply verify the *head* (~100–200 packages ≈
  95% of real usage); document the long tail, don't chase it.

## Where it DIVERGES from aarchbio (the design work)

### D1 — Channel: conda-forge, not bioconda

aarchbio builds from **bioconda**; aarch.science builds from **conda-forge** (the
general scientific channel: gdal, numpy, scipy, dask, xarray, scikit-*, etc.).
This is exactly the layer aarchbio's scope statement (its D16) excluded — so the
two projects are complementary, not overlapping. Many images will pull both
channels (bioconda often depends on conda-forge), but the *curated head* here is
conda-forge science.

### D2 — Curated environments, NOT a 1:1 registry mirror

**This is the biggest divergence.** aarchbio mirrors an existing registry
(BioContainers) — every image maps to a known upstream tag, so "what to build" is
*discovered* from that tag list. **conda-forge has no such container registry.** It
ships *packages*, not a curated set of tool *containers*.

So aarch.science must **define its own curated environments** — named, versioned
multi-package images grouped by domain workflow, e.g.:
- `geospatial` — gdal, proj, geos, rasterio, fiona, shapely, pyproj, scikit-image
- `earth-observation` — + xarray, dask, rioxarray, stackstac, pystac-client
- (later) `geo-ml`, `climate`, etc.

Env specs live in `envs/<name>.yaml` (conda environment files, version-pinned).
"What to build" is **curation**, not discovery. The fieldwork stack is the v1
`geospatial` spec — a real first consumer.

**Consequence for tags:** no upstream `<version>--<build>` scheme to mirror.
aarch.science versions its *own* images (e.g. `geospatial:2026.06` or a content
hash of the pinned spec). The spec file is the source of truth and is committed.

### D3 — Verification must prove ASSEMBLE + IMPORT, not just "solves"

aarchbio's gap was *"no arm64 build exists"* — a solve-time check sufficed.
aarch.science's gap is subtler and is **exactly what bit fieldwork**: the env may
*solve* yet fail to *assemble/import* on arm64 (a missing native lib, an ABI
mismatch, a broken wheel). pip "solved" too — then `import rasterio` would have
failed.

Therefore every build MUST, inside the arm64 image:
1. install the pinned env,
2. **import every headline package** (`import rasterio, osgeo.gdal, shapely,
   skimage, pyproj, fiona`),
3. run a **minimal functional smoke test** (e.g. open a tiny raster with rasterio,
   reproject a point with pyproj) — proving the native libs actually work, not
   just that files installed.

"Solves" is necessary but not sufficient. The smoke test is the trust the name
"verified" earns.

### D4 — Scope: CPU scientific stack only; GPU is out (hardware, not packaging)

The fieldwork GPU stage (SAM / PyTorch on `g5`) is **out of scope** — and not
because of packaging. **Graviton has no NVIDIA GPU**; arm64+CUDA on AWS means the
niche `g5g` (Graviton + T4) only. That's a hardware reality aarch.science can't
package around. aarch.science targets the **CPU scientific stack** where Graviton
wins on price/perf. (The pip-arm64-wheel gap is a CPU-stack problem anyway.)

## Open questions

- **OQ1 — image granularity:** few broad domain images (easy to use, larger) vs
  many fine-grained ones (composable, more to maintain)? Lean broad for v1.
- **OQ2 — versioning scheme:** date tag (`geospatial:2026.06`) vs spec content
  hash vs both? Needs to be reproducible and reconciler-friendly.
- **OQ3 — registry/org: SETTLED.** Naming rule: brand is **aarchsci** everywhere
  it's a free choice; `.science` only in the domain (no `.sci` TLD exists).
  → domain **aarch.science** (secured), registry **quay.io/aarchsci/<env>**,
  repo **playgroundlogic/aarchsci**, robot `aarchsci+robot`.
- **OQ4 — reconciler trigger:** conda-forge has no "new tag" event per env; the
  reconciler re-solves each spec and rebuilds if the pinned set changed. Define
  "changed."

## Non-goals (v1)

- GPU / CUDA images (D4 — hardware).
- Mirroring all of conda-forge (D2 — curated heads only).
- Compiling packages from source (same as aarchbio: build from blessed conda-forge
  packages; gaps go upstream).
- Bioinformatics (that's aarchbio).
