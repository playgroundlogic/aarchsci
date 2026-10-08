# CLAUDE.md — aarch.science

Native arm64 (aarch64) containers for the **conda-forge scientific stack**
(geospatial first). Sister to **aarchbio** (`~/src/aarchbio`), which does this for
bioinformatics/bioconda and is **launched + self-running** (503 tools, live site
at aarch.bio, daily reconciler). Read [DESIGN.md](DESIGN.md) and
[docs/lessons-from-aarchbio.md](docs/lessons-from-aarchbio.md) first.

## The one-line thesis
The scientific stack (GDAL/PROJ/GEOS/rasterio/…) is ideal for Graviton (~2.5×
cheaper/core) but won't assemble on arm64 via **pip**; it *does* on **conda-forge**.
Build verified, signed arm64 containers from conda-forge so the work runs native.

## Validated facts (don't re-litigate)
- The geospatial stack solves clean native arm64 on conda-forge — 123 pkgs
  (gdal 3.12.3, rasterio 1.5.0, proj 9.7.1, geos 3.14.1, shapely, pyproj, fiona,
  scikit-image). Evidence: `envs/_validated-geo-solve.txt`.
- Founding consumer: fieldwork/BuckAI (`~/src/fieldwork`) CPU geo-prep, currently
  paying the Intel c7i tax because c7g failed (`No matching distribution found for
  rasterio`).

## What aarch.science is NOT (scope, from DESIGN D1–D5)
- Not bioconda (that's aarchbio). Channel = conda-forge.
- **Bioinformatics is NOT excluded by subject (amended 2026-10-08, DESIGN D5).** Each
  project tracks the software's CURRENT HOME: live development on bioconda -> aarchbio;
  moved to conda-forge -> aarchsci, whatever the subject. A stale copy left behind
  confers no claim. Moves a handful of migrated packages, not a domain — the ~503 tools
  bioconda actively carries stay with aarchbio. Qualifying so far: afni/dipy/nibabel/
  nilearn/nipype (absent from bioconda -> `neuroimaging`), scanpy (bioconda 1.7.2 fails
  to import vs conda-forge 1.12.4) and r-seurat (3.0.2 vs 5.5.1, no aarch64) -> the
  `single-cell` env. Check the channels before asserting a home.
- Not a registry mirror — curate our own domain env images (`envs/*.yaml`).
- Not GPU/CUDA — Graviton has no NVIDIA GPU (hardware, not packaging).
- Not from-source — build from blessed conda-forge packages; gaps go upstream.

## Conventions
- Python via `uv run python` locally; scripts must fall back to `python3` (CI has
  no uv). See lessons doc #12.
- Reuse aarchbio's builder/sign/reconcile machinery (`~/src/aarchbio/builder/`,
  `.github/workflows/`) — adapt, don't reinvent. Inherit its bug fixes.
- The build farm: **orion.local** (Apple M-series, arm64) + **janus.local**
  (Rocky Linux, x86_64), both with Docker + a `docker-container` buildx builder +
  quay login. Same farm aarchbio uses.
- Don't run destructive AWS/registry actions or publish public images without
  confirmation. Secrets live in `.env` (gitignored), never committed.

## The crucial divergence (DESIGN D3): verify ASSEMBLE, not just SOLVE
aarchbio's gap was "no arm64 build exists" (solve check suffices). Here the gap is
"solves but doesn't assemble/import on arm64" — exactly what bit fieldwork (pip
"solved" too). Every build MUST, inside the arm64 image: install the pinned env →
`import` every headline package → run a minimal functional smoke test (open a tiny
raster, reproject a point). "Verified" is earned by the smoke test, not the tag.

## Settled — naming
Rule: the brand is **aarchsci** everywhere it's a free choice; **`.science` only
in the domain** (forced — there's no `.sci` TLD).
- Domain: **aarch.science** (secured)
- Quay org / registry: **quay.io/aarchsci**
- GitHub repo: **playgroundlogic/aarchsci**
- Robot account (when created): `aarchsci+robot` (mirrors `aarchbio+robot`)

## Open questions (DESIGN OQ1, OQ2, OQ4)
Image granularity (broad vs fine), versioning scheme (date vs spec-hash),
reconciler "changed" definition. (OQ3 naming is now settled above.)
