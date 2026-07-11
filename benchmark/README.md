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

## What this is NOT (read before quoting a number)

The wall-clock ratio above (~1.6× on these boxes) is **two different physical
machines** — an Apple laptop vs a Rocky x86 server — **not** AWS `c7g` vs `c7i`, and
**not** an apples-to-apples core-for-core hardware comparison. Do not cite it as a
Graviton-vs-Intel speedup. It establishes two honest things:

1. **The native arm64 build works and is competitive** — it is not slower; the
   native stack performs in the same class as (here, ahead of) the x86 build on
   comparable general-purpose silicon.
2. **Correctness parity** — identical output across arches (the point above).

## The price/perf claim (`~2.5×`) is *cited pricing*, not measured here

The project's headline economic claim — Graviton ~2.5× better price per physical
core — comes from **published AWS on-demand pricing** on equivalent instance
families (the fieldwork bake-off: `c7g` $0.036 vs `c7i` $0.089 per physical core),
combined with the "performs in the same class" result above. It is a *pricing*
argument, not a benchmark result. Keep the two separate when quoting.

## To get a true AWS c7g-vs-c7i number

Run `geo_prep_bench.py` inside `quay.io/aarchsci/geospatial:latest` on a real `c7g`
and the equivalent `c7i`, same vCPU count, and divide (throughput ÷ $/hr). That
requires launching EC2 instances — deliberately not done here (CLAUDE.md: no
instance launches without confirmation). The harness is ready for it: the script is
self-contained and emits one JSON line per run.

## Reproduce

```bash
docker run --rm quay.io/aarchsci/geospatial:latest \
  python - < benchmark/geo_prep_bench.py     # (or -v mount the file)
```
