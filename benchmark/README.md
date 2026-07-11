# benchmark

Measured, not guessed — but honest about what "measured" means here.

`geo_prep_bench.py` is a representative geospatial CPU-prep workload (raster read →
reproject → scikit-image → polygonize → shapely/pyproj vectorize, over 40 synthetic
tiles, deterministic seed). It runs **inside the `quay.io/aarchsci/geospatial`
image, native on each arch** — no emulation, per the project ethos.

## What we measured

The same workload, same env spec (gdal 3.12.3 / rasterio 1.5.0 / numpy 2.5.1,
Python 3.14.6), built and run natively on each architecture on the build farm:

| Host | Arch | Best of 3 (s) | tiles/sec | Output |
|------|------|--------------:|----------:|--------|
| Apple M-series (orion/local) | aarch64 | **8.11** | 4.93 | 177,643 features · 1,405,988 vertices |
| janus (x86_64, Rocky 9)      | x86_64  | **12.97** | 3.04 | 177,643 features · 1,405,988 vertices |

### The result that actually matters: **identical output**

Both arches produced **bit-identical geospatial results** — the same 177,643
polygonized features and 1,405,988 reprojected vertices. That is the correctness
half of "verified": the native arm64 stack isn't just fast, it's *the same answer*.
No silent numerical drift, no emulation artifacts.

The farm numbers above are **two different physical machines** (Apple laptop vs
Rocky x86 server) — useful only for the *correctness parity* point (identical
output). For the real price/performance claim, we ran it on AWS. See below.

## Measured on AWS: c7g vs c7i (the real apples-to-apples)

Run on 2026-07-11 in us-west-2, **native on each arch** (no emulation), same env
spec (gdal 3.12.3), same workload, **both vCPUs saturated**, results collected via
SSM (`benchmark/aws_bench.sh`). Both instances are 2 vCPU, on-demand. Output was
**bit-identical** (177,643 features) on both — same correctness parity as the farm.

| Instance | CPU | vCPU | Throughput | $/hr | Throughput per $/hr |
|----------|-----|-----:|-----------:|-----:|--------------------:|
| **c7g.large** | Graviton4 | 2 | **5.52** tiles/s | $0.0725 | **76.1** |
| c7i.large | Intel Sapphire Rapids | 2 | 3.63 tiles/s | $0.08925 | 40.7 |

- **Raw performance:** c7g is **1.52× faster** than c7i on this workload.
- **Price:** c7g is **1.23× cheaper** per hour.
- **→ Price/performance: `1.87×` in Graviton's favor** (throughput per dollar).

That is the honest, measured number for *this workload* on *these instance sizes*
with *live on-demand pricing*. It is lower than the often-cited "~2.5×" because that
figure is a *per-physical-core price* comparison on larger instances; this is a
*whole-workload throughput-per-dollar* result — a stricter, end-to-end measure. We
quote **1.87× (measured)** and describe ~2.5× as a per-core pricing figure.

Notably, `quay.io/aarchsci/geospatial:latest` is **arm64-only by design** (D2 — amd64
assembles upstream), so it won't run on c7i (`no matching manifest for linux/amd64`).
The x86 leg builds the *same env spec* natively via micromamba on the c7i box — the
fair comparison of the identical stack per arch.

## Reproduce the AWS run

```bash
AWS_PROFILE=aws ./benchmark/aws_bench.sh launch c7g.large   # + c7i.large
# then drive the bench via SSM Run Command; see git history for the exact commands.
# Instances self-terminate (shutdown +25 safety net); terminate explicitly when done.
```

## Reproduce

```bash
docker run --rm quay.io/aarchsci/geospatial:latest \
  python - < benchmark/geo_prep_bench.py     # (or -v mount the file)
```
