# benchmark

Measured, not guessed — but honest about what "measured" means here.

Two benchmarks live here:

- **`geo_prep_bench.py`** — geospatial CPU-prep throughput, arm64 vs x86_64
  (c7g vs c7i). *Is Graviton worth it for this stack?*
- **`sve_dispatch_bench.py`** — ARM SIMD/microarch runtime dispatch across Graviton
  generations (c7g vs c8g). *Do the shipped images actually use SVE, and what for?*

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
| **c7g.large** | Graviton3 (Neoverse V1) | 2 | **5.52** tiles/s | $0.0725 | **76.1** |
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

(The c7g row said "Graviton4" until 2026-08-18. It is **Graviton3**; c8g is Graviton4.
Corrected here and in the results JSONL against AWS's own pricing API, which reports
`physicalProcessor=AWS Graviton3 Processor` for c7g.large — and against the instances
themselves, which report CPU part `0xd40` = Neoverse V1. The measured throughput
numbers were never affected; only the CPU label was wrong.)

## Measured on AWS: ARM SIMD dispatch, c7g vs c8g (2026-08-18)

`sve_dispatch_bench.py` answers a question the catalog previously only asserted:
**does the shipped image actually use SVE on Graviton, and what is it worth?**

aarch.science compiles nothing from source (DESIGN non-goals), so it never *chooses*
an ISA target. What it inherits from conda-forge is **runtime dispatch**: OpenBLAS is
built `DYNAMIC_ARCH` with 27 core-specific kernel families (including `armv8sve`,
`neoversev1`, `neoversev2`, `a64fx`, `armv9sme`), and NumPy compiles an SVE dispatch
path on top of its mandatory NEON/ASIMD baseline. Which kernel runs is decided by
HWCAP at load time, on the silicon — so it can only be measured, not inspected.

Method: the **unmodified published `quay.io/aarchsci/dft:latest`**, same image on both
generations, run three times per host with only `OPENBLAS_CORETYPE` differing —
`auto` (what a user gets), `neoversen1` (the same binary with SVE off the table), and
`armv8` (generic baseline). Two independent runs per instance type, agreeing within
~1%. Driven by `aws_bench.sh sve`; instances terminated after collection.

### What the silicon reports

| Instance | CPU | CPU part | SVE | SVE2 | SVE vector length |
|----------|-----|----------|-----|------|------------------:|
| c7g.large | Graviton3 (Neoverse V1) | `0xd40` | yes | **no** | **256 bit** |
| c8g.large | Graviton4 (Neoverse V2) | `0xd4f` | yes | **yes** | **128 bit** |

NEON/ASIMD is present on both and on Apple silicon — it is architecturally mandatory
in AArch64, which is why NumPy carries it as an unconditional *baseline* rather than a
dispatch target. SVE is the genuinely optional part. The vector lengths are the
textbook V1-vs-V2 tradeoff, measured via `prctl(PR_SVE_GET_VL)`: Graviton3 is
2×256-bit, Graviton4 is 4×128-bit.

### Which kernel the image selects, unprompted

| Instance | `auto` selects | NumPy `SVE` detected |
|----------|----------------|----------------------|
| c7g.large | `neoversev1` | true |
| c8g.large | `neoversev2` | true |

So **the published images already run SVE kernels on Graviton, with no rebuild and no
configuration.** On an Apple-silicon host the same image reports `SVE: false` and falls
back to `neoversen1` — the dispatch is doing its job in both directions.

### What the dispatch is worth (same image, same host, kernel pinned)

| Instance | Workload | `auto` | `neoversen1` (no SVE) | uplift |
|----------|----------|-------:|----------------------:|-------:|
| c7g.large | DGEMM n=3072 | **48.45** GF/s | 39.39 GF/s | **1.23×** |
| c7g.large | SGEMM n=3072 | **99.25** GF/s | 79.81 GF/s | **1.24×** |
| c7g.large | gpaw SCF (16-atom Si, PW 400 eV, k=2³) | **19.18** s | 20.06 s | **1.05×** |
| c8g.large | DGEMM n=3072 | 40.66 GF/s | 41.43 GF/s | 0.98× |
| c8g.large | SGEMM n=3072 | 84.78 GF/s | 85.52 GF/s | 0.99× |
| c8g.large | gpaw SCF (same) | 16.44 s | 16.58 s | 1.01× |

Two findings, one of them not what we expected:

- **On Graviton3 the SVE kernels are worth ~23% on BLAS3** and ~5% end-to-end on a real
  DFT SCF cycle (BLAS is only part of that time). Free, already shipping.
- **On Graviton4 they are worth nothing measurable** — `neoversev2` is within noise of,
  and marginally behind, the NEON-only path on both GEMM precisions. We are not going to
  dress that up: on this workload, at this size, OpenBLAS 0.3.34's `neoversev2` kernel
  does not beat its NEON path. Whether that is the 4×128-bit geometry or simply a
  less-tuned kernel, we did not determine, and the honest statement is that SVE2 on
  Graviton4 did **not** pay here.

**Correctness first, per D3:** all six kernel configurations across both generations
returned an identical total energy, −5.940776 eV/atom, matching the local Apple-silicon
run to the last printed digit. A faster kernel that changes the answer is worthless, so
the benchmark asserts the physics before it reports the speed.

### Cross-generation, as a user actually gets it

The microbenchmark and the real workload disagree, which is why both are here:

| | c7g.large (Graviton3) | c8g.large (Graviton4) |
|---|---:|---:|
| DGEMM n=3072 | **48.45** GF/s | 40.66 GF/s |
| gpaw SCF | 19.18 s | **16.44** s |
| $/hr (on-demand, us-west-2) | $0.0725 | $0.07976 |
| cost per SCF | $3.86 × 10⁻⁴ | **$3.64 × 10⁻⁴** |

Graviton3 wins DGEMM by 1.19× — plausibly its 256-bit vectors plus a better-tuned
kernel — yet Graviton4 finishes the actual DFT calculation **1.17× faster** and
**1.06× cheaper per calculation**. Take the end-to-end number; the GEMM number is
diagnostic, not a recommendation.

## Reproduce the AWS runs

```bash
AWS_PROFILE=aws ./benchmark/aws_bench.sh sve c7g.large    # + c8g.large
```

Launches a native instance, installs docker, runs the bench inside the published image
via SSM Run Command (no SSH, no inbound ports), prints `RESULT` JSON lines, and
terminates. Three safety nets against a leaked instance:
`--instance-initiated-shutdown-behavior terminate`, a `shutdown -h +30` armed in
user-data, and an exit trap. The bench script travels in user-data as gzip+base64, so a
run always measures your working copy rather than whatever is pushed to GitHub.

The 2026-07 geo-prep run used the same launcher; see git history for its exact commands.

## Reproduce

```bash
docker run --rm quay.io/aarchsci/geospatial:latest \
  python - < benchmark/geo_prep_bench.py     # (or -v mount the file)
```
