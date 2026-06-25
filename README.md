# aarch.science

**Verified, signed, native arm64 (aarch64) containers for the scientific-computing
stack** — geospatial / earth-observation first. For Apple Silicon and AWS Graviton.

Sister project to [aarchbio](https://github.com/playgroundlogic/aarchbio) (which
does this for bioinformatics / BioContainers). aarch.science covers the layer
aarchbio scopes out: the **conda-forge** scientific stack.

> **Status:** design stage. The architecture is settled — see [DESIGN.md](DESIGN.md).
> The builder and first images are not built yet.

## Why

The geospatial stack (GDAL, PROJ, GEOS, rasterio, shapely, scikit-image) is an
ideal Graviton workload — measured ~2.5× cheaper per physical core than Intel.
But it often **won't assemble on arm64 via pip** (`No matching distribution found
for rasterio` — PyPI's arm64 wheel coverage for native-lib science is fragile).

The packages *do* exist on **conda-forge** for arm64 — the same stack solves
cleanly there (123 packages, verified). The gap is the same one
[aarchbio](https://github.com/playgroundlogic/aarchbio) fills for bioinformatics:
**the capability is present, the delivery is broken.** aarch.science builds a
verified, signed arm64 container from the conda-forge packages so the work can run
native on Graviton.

## How it will differ from aarchbio

- **Channel:** conda-forge (not bioconda).
- **Curated environments, not a registry mirror:** conda-forge has no
  BioContainers-equivalent to copy, so aarch.science defines its own versioned
  domain images ([`envs/`](envs/)) — `geospatial` first.
- **Deeper verification:** every image must *import* and *functionally smoke-test*
  its stack on arm64 — because the failure mode here is "solves but doesn't
  assemble," not "no build exists."
- **CPU only:** Graviton has no NVIDIA GPU, so GPU/CUDA stacks are out of scope.

See [DESIGN.md](DESIGN.md) for the full rationale.

## License

[Apache 2.0](LICENSE) · Copyright 2026 Playground Logic LLC. Unofficial community
project — not affiliated with conda-forge, NumFOCUS, OSGeo, or AWS.
