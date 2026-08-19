# benchmark

Measured, not guessed — but honest about what "measured" means here.

Two benchmarks live here:

- **`geo_prep_bench.py`** — geospatial CPU-prep throughput, arm64 vs x86_64
  (c7g vs c7i). *Is Graviton worth it for this stack?*
- **`sve_dispatch_bench.py`** — ARM SIMD/microarch runtime dispatch across five
  Graviton generations (Graviton2 → Graviton5, plus Graviton3E). *Do the shipped
  images actually use SVE, and what is it worth?*

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

## Measured on AWS: ARM SIMD dispatch across five Graviton generations (2026-08-18/19)

`sve_dispatch_bench.py` answers a question the catalog previously only asserted:
**does the shipped image actually use SVE on Graviton, and what is it worth?**

aarch.science compiles nothing from source (DESIGN non-goals), so it never *chooses*
an ISA target. What it inherits from conda-forge is **runtime dispatch**: OpenBLAS is
built `DYNAMIC_ARCH` with 27 core-specific kernel families (including `armv8sve`,
`neoversev1`, `neoversev2`, `a64fx`, `armv9sme`), and NumPy compiles an SVE dispatch
path on top of its mandatory NEON/ASIMD baseline. Which kernel runs is decided by
HWCAP at load time, on the silicon — so it can only be measured, not inspected.

Method: the **unmodified published `quay.io/aarchsci/dft:latest`**, the same image on
every generation, run three times per host with only `OPENBLAS_CORETYPE` differing —
`auto` (what a user gets), `neoversen1` (the same binary with SVE off the table), and
`armv8` (generic baseline). **24 configurations across 8 instance types and 5 Graviton
generations**, at two sizes (2 vCPU and 16 vCPU) so no conclusion rests on a single
problem scale. Driven by `aws_bench.sh sve`; instances terminated after collection.

Raw records: `results/aws-sve-dispatch-2026.08.18.jsonl` (c7g/c8g.large, two independent
runs agreeing within ~1%; the tables below quote `run2`, the one with a valid gpaw leg)
and `results/aws-generation-sweep-2026.08.19.jsonl` (the other six legs).

### What the silicon reports

| Instance | CPU | CPU part | SVE | SVE2 | SVE vector length | `auto` selects |
|----------|-----|----------|-----|------|------------------:|----------------|
| c6g | Graviton2 (Neoverse N1) | `0xd0c` | **no** | no | — | `neoversen1` |
| c7g | Graviton3 (Neoverse V1) | `0xd40` | yes | **no** | **256 bit** | `neoversev1` |
| hpc7g | Graviton3E (Neoverse V1) | `0xd40` | yes | no | 256 bit | `neoversev1` |
| c8g | Graviton4 (Neoverse V2) | `0xd4f` | yes | **yes** | **128 bit** | `neoversev2` |
| c9g | **Graviton5** | `0xd84` | yes | **yes** | 128 bit | `neoversev2` ⚠ |

NEON/ASIMD is present on every one of them and on Apple silicon — it is architecturally
mandatory in AArch64, which is why NumPy carries it as an unconditional *baseline*
rather than a dispatch target. SVE is the genuinely optional part, and Graviton2 is the
proof: it is the one generation here without it. The vector lengths are measured via
`prctl(PR_SVE_GET_VL)`, and Graviton3 → Graviton4 is the textbook V1-vs-V2 tradeoff:
2×256-bit becomes 4×128-bit, and Graviton5 keeps the 128-bit width. `i8mm` and `bf16`
appear from Graviton3 onward. (The table leaves Graviton5's Neoverse core name blank
deliberately — `0xd84` is not a part we can map to a published core name, and guessing
one would be exactly the kind of unverified label the c7g/Graviton4 correction was about.)

So **the published images already run SVE kernels on Graviton, with no rebuild and no
configuration.** On an Apple-silicon host the same image reports `SVE: false` and falls
back to `neoversen1` — the dispatch is doing its job in both directions.

⚠ **Graviton5 is new enough that OpenBLAS has not caught up.** Part `0xd84` reports SVE2,
`i8mm` and `bf16`, but OpenBLAS 0.3.34's dispatch table has no entry for it and falls back
to the Graviton4 `neoversev2` kernel. Everything below shows Graviton5 winning anyway — on
microarchitecture alone, with its vector dispatch running someone else's kernel. That is
unclaimed headroom sitting in a future OpenBLAS, not something this project can fix.

### What the dispatch is worth (same image, same host, kernel pinned)

Ratio of `auto` to pinned `neoversen1` — identical binary, identical host, one env var apart:

| Instance | CPU | SVE2 | DGEMM | SGEMM | gpaw SCF |
|----------|-----|------|------:|------:|---------:|
| **c6g.large** (control) | Graviton2 | no SVE at all | *0.980×* | *0.995×* | *0.995×* |
| **c7g.large** | Graviton3 | no | **1.223×** | **1.234×** | **1.046×** |
| **c7g.4xlarge** | Graviton3 | no | **1.224×** | **1.223×** | 1.017× |
| **hpc7g.4xlarge** | Graviton3E | no | **1.176×** | **1.249×** | 1.005× |
| c8g.large | Graviton4 | yes | 0.983× | 0.989× | 1.009× |
| c8g.4xlarge | Graviton4 | yes | 0.982× | 0.996× | 1.000× |
| c9g.large | Graviton5 | yes | 0.980× | 0.989× | 0.994× |
| c9g.4xlarge | Graviton5 | yes | 0.981× | 1.001× | 1.001× |

**Read the control row first.** On c6g there is no SVE, so `auto` and pinned `neoversen1`
select the *literally identical kernel* — and still differ by 0.980×. That 2% is this
harness's ordering/noise floor (the `auto` leg always runs first, on a colder cache), not
a property of any silicon. It is the number that makes the rest of the table interpretable:

- **On Graviton3 the SVE kernels are worth ~22–24% on BLAS3** — ten times the noise floor,
  reproduced at 2 vCPU and again at 16 vCPU with 4× the problem size, on three separate
  instances. That is free performance, already shipping in the published image. End-to-end
  on a real DFT SCF cycle it is worth ~2–5%, because BLAS is only part of that time.
- **On Graviton4 and Graviton5, SVE2 is worth nothing measurable.** Every SVE2 row sits at
  0.980–0.982× on DGEMM — *exactly* the control value, to three digits. Confirmed at both
  sizes on both SVE2 generations.

  (Until 2026-08-19 this README said the SVE2 path was "marginally behind" the NEON-only one.
  The control shows that was over-reading a measurement artifact: the apparent 2% deficit
  is the same 2% a no-SVE chip shows between two runs of one kernel. The corrected claim is
  a bounded null — SVE2 delivers no benefit *and* no penalty here, within ±2% — which is a
  different and more defensible statement than the one it replaces. Running a control was
  the cheapest leg of the sweep and it was the only one that changed a conclusion.)

**Correctness first, per D3:** across all **24** kernel configurations and all five
generations there is exactly **one energy per cell size** — −5.940776 eV/atom for the
16-atom cell and −5.942215 for the 54-atom cell, with zero variation between kernels or
between chips, and the former still matching the local Apple-silicon run to the last
printed digit. A faster kernel that changes the answer is worthless, so the benchmark
asserts the physics before it reports the speed.

### The generational story, as a user actually gets it

`auto`, on-demand us-west-2 pricing, 16-atom Si SCF at 2 vCPU:

| Instance | CPU | DGEMM | gpaw SCF | $/hr | cost per SCF | vs Graviton2 |
|----------|-----|------:|---------:|-----:|-------------:|-------------:|
| c6g.large | Graviton2 | 36.5 GF/s | 30.4 s | $0.0680 | $5.75 × 10⁻⁴ | 1.00× |
| c7g.large | Graviton3 | **48.2** GF/s | 19.2 s | $0.0725 | $3.86 × 10⁻⁴ | 1.59× |
| c8g.large | Graviton4 | 40.8 GF/s | 16.4 s | $0.0798 | $3.64 × 10⁻⁴ | 1.85× |
| **c9g.large** | **Graviton5** | **64.7** GF/s | **12.2** s | $0.0869 | **$2.95 × 10⁻⁴** | **2.49×** |

Graviton2 → Graviton5 is **2.49× faster and 1.95× cheaper per calculation** on real DFT.
Note that Graviton3 beats Graviton4 on DGEMM (48.2 vs 40.8) while losing the actual
calculation — the microbenchmark and the workload disagree, and the workload wins the
argument. Quote the end-to-end number; the GEMM number is diagnostic, not a recommendation.

At 16 vCPU with a 54-atom cell the ordering holds, and the interesting row is hpc7g:

| Instance | CPU | vCPU | mem | DGEMM | gpaw SCF | $/hr | cost per SCF |
|----------|-----|-----:|----:|------:|---------:|-----:|-------------:|
| c7g.4xlarge | Graviton3 | 16 | 32 GiB | 384.5 GF/s | 151.4 s | $0.580 | $2.44 × 10⁻² |
| hpc7g.4xlarge | Graviton3E | 16 | 128 GiB | **587.0** GF/s | 147.7 s | $1.683 | $6.91 × 10⁻² ⚠ |
| c8g.4xlarge | Graviton4 | 16 | 32 GiB | 329.0 GF/s | 119.2 s | $0.638 | $2.11 × 10⁻² |
| **c9g.4xlarge** | **Graviton5** | 16 | 32 GiB | 511.6 GF/s | **93.5** s | $0.696 | **$1.81 × 10⁻²** |

⚠ hpc7g's cost column is real but misleading as a family judgment — every hpc7g size costs
the same $1.6832/hr, so the 4xlarge pays for 64 cores and uses 16. See the hpc7g section below.

### Graviton3E: the vector claim holds, and it buys you almost nothing here

hpc7g is Graviton3E, and it is the cleanest controlled comparison in the set — **the same
CPU part `0xd40`, the same 16 physical cores, the same SVE at 256 bit** as c7g.4xlarge.
AWS markets it as ~35% higher vector performance. Measured, on BLAS3, it beats plain
Graviton3 by **1.53× on DGEMM and 1.54× on SGEMM** — the claim holds and is exceeded.

**But the advantage is not vector-specific, so "wider vector unit" is not the explanation.**
Pinning the kernel decomposes it, and every path gains about the same amount:

| Kernel pinned | c7g.4xlarge | hpc7g.4xlarge | hpc7g gains |
|---|---:|---:|---:|
| `auto` (SVE `neoversev1`) DGEMM | 384.5 | 587.0 | 1.527× |
| `neoversen1` (NEON only) DGEMM | 314.1 | 499.2 | **1.589×** |
| `armv8` (generic NEON) DGEMM | 311.9 | 488.6 | 1.566× |
| `auto` (SVE `neoversev1`) SGEMM | 784.5 | 1208.2 | 1.540× |
| `neoversen1` (NEON only) SGEMM | 641.7 | 967.4 | 1.508× |
| `armv8` (generic NEON) SGEMM | 645.2 | 958.9 | 1.486× |

A wider or beefier *vector* datapath would lift the SVE rows more than the NEON rows. It
does not — SVE/NEON works out to 0.96 on DGEMM and 1.02 on SGEMM. The gain is broad, and
it is not in the ISA. Consistent with that, the vector *width* is measurably identical:
same CPU part `0xd40`, same 256-bit VL via `prctl(PR_SVE_GET_VL)`, and AWS's own HPC blog
states Graviton3E "implement[s] Scalable Vector Extension (SVE) of the Neoverse V1
architecture" — the same core as Graviton3. The 35% claim is about vector-instruction
*performance*, not vector width.

**What does explain it: every hpc7g size is the same machine at the same price.** Per AWS,
"each size in the instance family will have the same engineering specs and price, and will
differ only by the number of cores offered" — and the pricing API agrees exactly:

| | vCPU | memory | network | $/hr |
|---|---:|---:|---:|---:|
| hpc7g.4xlarge | 16 | 128 GiB | 200 Gbps | $1.6832 |
| hpc7g.8xlarge | 32 | 128 GiB | 200 Gbps | $1.6832 |
| hpc7g.16xlarge | 64 | 128 GiB | 200 Gbps | $1.6832 |

So hpc7g.4xlarge is a 64-core node with 48 cores switched off: the full 128 GiB of DDR5
feeding 16 cores, roughly **4× the memory bandwidth per core** of c7g.4xlarge's 32 GiB/16
cores. That accounts for a broad ~1.5× with no vector-specific component on an n=6144 GEMM,
whose ~900 MB working set streams far past any cache — and it accounts for the SCF *not*
benefiting, since a 54-atom plane-wave calculation has a small enough working set to be
latency- and FFT-bound rather than bandwidth-bound. Both observations, one mechanism. Still
labelled a hypothesis: we did not measure bandwidth directly (no STREAM run), so this is the
explanation best supported by the data rather than a demonstrated one.

### ⚠ The hpc7g cost-per-calculation figure above is not a verdict on hpc7g

On the DFT calculation hpc7g.4xlarge is 1.03× faster than c7g.4xlarge for 2.9× the price —
$6.91 × 10⁻² per SCF against $2.44 × 10⁻², i.e. 2.83× worse. **That is a fair measurement of
the instance we rented and an unfair one of the family**, for a reason we only found after
the fact: because all sizes cost the same, benchmarking the 4xlarge means paying for 64 cores
and using 16. At the same $1.6832/hr, hpc7g.16xlarge offers 4× the cores for free. Anyone
sizing an hpc7g should therefore **always take the largest size** — a genuinely useful,
non-obvious operational fact, and one this sweep only surfaced by getting it wrong first.

Two further limits on generalizing from that row:

- We have **not** measured whether a 54-atom gpaw SCF would actually use 64 cores. This
  bench runs gpaw serially with threaded BLAS, and thread-scaling a plane-wave SCF to 64
  threads is a different experiment, not a multiplication.
- **This benchmark structurally cannot see what hpc7g is sold for** — no MPI, no EFA, no
  multi-node scaling. hpc7g exists for tightly-coupled jobs spanning many nodes over a
  200 Gbps fabric. Nothing here speaks to that case.

## Reproduce the AWS runs

```bash
AWS_PROFILE=aws ./benchmark/aws_bench.sh sve c7g.large       # 2 vCPU tier
GEMM_N=6144 SI_REPEAT=3 ./benchmark/aws_bench.sh sve c9g.4xlarge   # 16 vCPU tier
./benchmark/aws_bench.sh discover hpc7g.4xlarge              # facts + pricing, no launch
./benchmark/aws_bench.sh audit                               # leak check
```

Launches a native instance, installs docker, runs the bench inside the published image
via **SSM Run Command** (no SSH, no inbound ports), prints `RESULT` JSON lines, and
terminates. The bench script travels in user-data as gzip+base64, so a run always
measures your working copy rather than whatever is pushed to GitHub.

Discovery is delegated to [`truffle`](https://spore.host) and instance lifecycle to
`spawn`: `truffle` derives the region, offered AZs and spot/on-demand price per type
(which is how the harness knows, rather than remembers, that hpc7g is us-east-1a-only
with no spot market), and `spawn` owns the VPC/subnet and the auto-terminate timer. Spot
is the default where a spot market exists — ~55–60% off the same silicon, and an
interrupted benchmark just gets re-run. **Published price/performance is always computed
at on-demand prices**, since spot prices float and quoting them would make the numbers
unreproducible.

Cost guardrails, in order of how much they can be defeated by this script dying:
`spawn --ttl` (the timer lives on the instance), `--terminate-on-error`, and an exit
trap. `LAUNCHER=awscli` forces a pure aws-cli path — no spore.host tools needed — so
these numbers stay reproducible by anyone; that path keeps the older
`--instance-initiated-shutdown-behavior terminate` plus a user-data `shutdown -h +30`.

The 2026-07 geo-prep run used an earlier revision of the same launcher; see git history.

## Reproduce

```bash
docker run --rm quay.io/aarchsci/geospatial:latest \
  python - < benchmark/geo_prep_bench.py     # (or -v mount the file)
```
