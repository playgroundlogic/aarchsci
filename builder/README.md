# builder

The core of aarch.science: a **generic, parameterized builder** that turns a
curated conda-forge **environment** into a verified, signed, native arm64
container. One `Dockerfile` + one `build-env.sh` serve every env — the env name is
the argument, so the same recipe scales from 1 env to many.

This is the aarchbio builder adapted across the four design divergences:

| | aarchbio | aarch.science |
|---|---|---|
| **unit** | one `pkg=version` from bioconda | a curated **env** `envs/<name>.yaml` (D2) |
| **channel** | bioconda | conda-forge (D1) |
| **"what to build"** | *discovered* from BioContainers tags | **curated** by us (no registry to mirror) (D2) |
| **verification** | solve-time check ("arm64 build exists") | **assemble + import + functional smoke test**, in-image (D3) |
| **tag** | `<version>--<build>` (mirrors upstream) | `<date>` + `s<lock-hash>` + `latest` (our own) (OQ2) |

## Files

- `Dockerfile` — `micromamba install -f <env>.yaml` on a multi-arch base; **bakes
  the smoke test into the image** (`/opt/aarchsci/smoke.py`) so verification travels
  with the artifact; stamps provenance labels.
- `build-env.sh` — the builder: build `--platform linux/arm64` (native, no QEMU on
  an arm64 host) → read the **actually-resolved** package set from the finished
  image → run the in-image **D3 smoke test** (refuse to tag if it fails) → write the
  committed lock → tag + optionally push.

## Usage

```bash
# Build + verify locally (does NOT push). Runs the full D3 smoke test.
./builder/build-env.sh geospatial

# Build, verify, and push to quay.io/aarchsci (requires `docker login quay.io`):
PUSH=1 ./builder/build-env.sh geospatial
```

Requires a `docker-container` buildx builder (lessons #1):
`docker buildx create --name b --driver docker-container --bootstrap && docker buildx use b`.

## D3 — "verified" is earned, not asserted (the crucial divergence)

aarchbio could trust a solve. aarch.science cannot: fieldwork's stack *solved* on
pip and would still have failed `import rasterio` on arm64. So `build-env.sh` runs
`envs/<name>.smoke.py` **inside the built arm64 image** and exits non-zero — no tag,
no push — unless the env:

1. **imports** every headline package (native libs load, ABI matches),
2. each does **real work** touching its native backend (PROJ reproject, GEOS ops,
   GDAL driver registry, scikit-image transform),
3. the pieces **interoperate** (write a GTiff, read it back via rasterio *and*
   straight GDAL, reproject its corner; round-trip a GeoJSON through fiona).

The smoke test is also baked into the image, so any consumer can re-run it:
`docker run --rm <image> python /opt/aarchsci/smoke.py`.

## Versioning + the lock (OQ2 / OQ4)

The resolved package set is read from the **finished image**, never predicted from a
host solve (aarchbio lesson #16 — the in-container resolver is the source of truth).
That canonical `name version` list is:

- written to `envs/<name>.lock.txt` (committed — the spec's resolved truth), and
- hashed (sha256, short) into the `s<lock-hash>` tag.

So three tags publish per build: `<date>` (human/reconciler-friendly),
`s<lock-hash>` (content-addressed, idempotent), and `latest`. **The lock-hash is the
reconciler's "changed?" signal (OQ4):** re-solve the spec, rebuild; if the new
lock-hash differs from the committed one, the pinned set drifted → publish a new
dated tag. If it matches, it's a no-op.

## v1 scope: arm64-only

The geospatial head is arch-specific *as a whole* (gdal/proj/geos are native), so it
always builds natively per-arch — no emulation (D1). v1 publishes the **arm64** leg:
that's the gap fieldwork hit (`No matching distribution found for rasterio`); amd64
already assembles upstream via conda/pip. A multi-arch merge (mirroring aarchbio's
`build-arch.sh` + `merge.sh` for the noarch case) is a later addition if a future
env turns out arch-neutral.

## Status

`geospatial` builds + verifies end-to-end on native arm64 (Apple M-series, orion):
**123 packages**, all D3 checks green, resolved set identical to the independently
validated solve (`envs/_validated-geo-solve.txt`). Nothing pushed public yet —
awaiting go-live confirmation for `quay.io/aarchsci` + `aarch.science`.
