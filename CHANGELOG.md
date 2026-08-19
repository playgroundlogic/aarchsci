# Changelog

All notable changes to aarch.science. Dates are UTC. The catalog itself is
versioned per-image (date + content-hash tags); this records project-level milestones.

## 2026-08-19

### Notable (five-generation Graviton sweep: Graviton2 → Graviton5)
- **Extended the SIMD-dispatch measurement to every Graviton generation AWS rents**, at
  two problem sizes — **24 configurations, 8 instance types, 5 generations** — all on the
  unmodified published `quay.io/aarchsci/dft:latest`. Raw records in
  `benchmark/results/aws-generation-sweep-2026.08.19.jsonl`. Findings:
  - **Graviton5 (c9g) exists and is the fastest thing in the catalog for DFT**: part
    `0xd84`, SVE2 + `i8mm` + `bf16`, 128-bit vectors. Graviton2 → Graviton5 is **2.49×
    faster and 1.95× cheaper per SCF calculation** on real gpaw.
  - **…and OpenBLAS has not caught up to it.** 0.3.34's dispatch table has no entry for
    `0xd84`, so it falls back to the Graviton4 `neoversev2` kernel. Graviton5 wins anyway,
    on microarchitecture alone, while running someone else's kernel — unclaimed headroom
    sitting in a future OpenBLAS, not something this project can fix.
  - **The Graviton3 SVE uplift replicates**: 1.22–1.23× on DGEMM at 2 vCPU and again at
    16 vCPU with 4× the problem size, across three separate instances.
  - **Graviton3E (hpc7g) is the cleanest controlled comparison in the set** — identical
    CPU part `0xd40`, identical 16 physical cores, identical 256-bit SVE as c7g.4xlarge.
    AWS's ~35% higher-vector-performance claim **holds and is exceeded** on BLAS3
    (**1.53× DGEMM, 1.54× SGEMM**) while buying only 1.03× on the DFT calculation. See the
    two corrections below: the mechanism is **not** a wider vector unit, and the
    cost-per-calculation figure is not a verdict on the family.
  - **D3 across the whole sweep:** all 24 configurations returned one energy per cell size
    (−5.940776 eV/atom at 16 atoms, −5.942215 at 54), the former still matching the local
    Apple-silicon run to the last printed digit.

### Corrected (2026-08-19)
- **"SVE2 is marginally *behind* the NEON path on Graviton4" was over-reading noise.** The
  claim published a day earlier rested on a 0.98× ratio with nothing to compare it against.
  Adding a **no-SVE control (c6g / Graviton2)** settled it: there, `auto` and pinned
  `neoversen1` select the *literally identical kernel* and still differ by **0.980×**, so
  ~2% is this harness's ordering/noise floor (the `auto` leg runs first, on a colder cache).
  Every SVE2 measurement — both generations, both sizes — sits at 0.980–0.982×, i.e. exactly
  the control. The corrected claim is a **bounded null**: on Graviton4 and Graviton5, SVE2
  delivers no benefit *and* no penalty, within ±2%. Graviton3's 1.22–1.23× is ten times that
  floor and unaffected. The control was the cheapest leg of the sweep and the only one that
  changed a conclusion — a reminder that D3's "verify, don't assert" applies to our own
  measurements, not just to upstream packages.

### Corrected (Graviton3E, 2026-08-19)
- **The hpc7g advantage is not a wider vector unit, and we can now say what it is.** Prompted
  by the reasonable question "doesn't the 3E have a wider vector unit?", pinning the kernel
  decomposed the 1.53× BLAS3 gain by ISA path — and it is **not vector-specific**: NEON-only
  kernels gain 1.51–1.59×, SVE kernels 1.53–1.54× (SVE/NEON = 0.96 DGEMM, 1.02 SGEMM). The
  vector *width* is measurably identical too — same CPU part `0xd40`, same 256-bit VL via
  `prctl` — and AWS's own HPC blog confirms Graviton3E "implement[s] Scalable Vector Extension
  (SVE) of the Neoverse V1 architecture", i.e. the same core as Graviton3. AWS's 35% claim is
  about vector-instruction *performance*, not width.
  The supported mechanism instead: **every hpc7g size is the same machine at the same price**
  (verified against the pricing API — 4xlarge/8xlarge/16xlarge are all $1.6832/hr with 128 GiB
  and 200 Gbps), so hpc7g.4xlarge is a 64-core node with 48 cores off, giving ~4× the memory
  bandwidth per core of c7g.4xlarge. That explains a broad ~1.5× on an n=6144 GEMM (~900 MB
  streaming working set) *and* explains the SCF not benefiting (54 atoms is latency/FFT-bound,
  not bandwidth-bound). This supersedes yesterday's "we did not establish the mechanism", whose
  stated objection — that bandwidth should have sped up DFT too — was itself the error: it
  assumed DFT is bandwidth-hungry generically without checking the two working-set sizes.
- **…and the hpc7g cost-per-calculation figure is not a verdict on hpc7g.** Because all sizes
  cost the same, benchmarking the 4xlarge means paying for 64 cores and using 16. The measured
  2.83×-worse cost per SCF is a fair number for the instance rented and an unfair one for the
  family; at the same $1.6832/hr, hpc7g.16xlarge offers 4× the cores. The operational lesson is
  worth more than the benchmark: **on hpc7g, always take the largest size.** Not corrected by
  re-measuring — whether a 54-atom gpaw SCF thread-scales to 64 cores is a separate experiment,
  and this bench runs serial gpaw with threaded BLAS.

### Changed (benchmark harness now uses the spore.host tools)
- **`aws_bench.sh` delegates discovery to `truffle` and lifecycle to `spawn`** instead of
  hand-rolling both. The hand-rolled versions produced three of the four bugs recorded
  under "Fixed (benchmark harness)" below, and each is now structurally impossible rather
  than merely patched:
  - a pinned subnet id plus a region read from `$AWS_REGION` → `InvalidSubnetID.NotFound`;
    `spawn` creates and tags its own VPC/subnet.
  - a hand-maintained per-family region/AZ table; `truffle find` reports offered AZs, so
    the harness now *derives* that hpc7g is us-east-1a-only, and `truffle spot` returning
    no JSON is what tells it the family has no spot market.
  - a cleanup trap whose instance id was lost to `set -e` frame unwinding; `spawn --ttl`
    puts the timer on the instance, where a dead launcher cannot defeat it.
- One call to `truffle spot --show-savings` also replaces the hand-rolled pricing-API loop,
  returning spot and on-demand price together. Noted in-code: `spawn launch --estimate-only`
  reports a rounder figure ($0.1000/hr for a c8g.large that truffle and the pricing API both
  put at $0.07976) — fine as a pre-flight cost warning, wrong for a published ratio.
- **SSM stays the results channel** even under `spawn` (which also offers SSH), and
  `LAUNCHER=awscli` forces a pure aws-cli path, so a third party can reproduce the numbers
  with no key material, no inbound ports and no spore.host tools installed.
- New subcommands: `discover <type>` (facts + pricing, launches nothing) and `audit`
  (leak check via `spawn orphans --all-regions` plus a tag scan across both regions).

## 2026-08-18

### Added
- **`dft` env (191 pkgs)** — the 7th verified image, and the first MPI-parallel one.
  Plane-wave / LCAO density-functional theory: `gpaw` 25.7.0
  (`py314_mpi_openmpi_omp`), `ase`, `libxc` 7.1.2, `libvdwxc`, `elpa` 2025.06.001,
  `scalapack`, `fftw`, OpenMPI 5.0.10, `mpi4py`, plus `spglib`/`phonopy`/`pymatgen`
  for structure, symmetry and phonons. The strongest Graviton case in the catalog:
  DFT is CPU-bound, MPI+OpenMP, bandwidth-hungry, and gpaw's GPU path is CUDA — so
  D4 (no GPU on Graviton) costs this workload nothing.
- **D3 now verifies parallelism, not just assembly.** `envs/dft.smoke.py` runs the
  same bulk-Si `PW(200)`/LDA calculation serially and under `mpiexec -n 2` and fails
  unless the energies agree (measured delta: 6e-08 eV). An MPI stack that links but
  computes wrong is invisible to a single-process check. It stays a single entrypoint
  (`python /opt/aarchsci/smoke.py`) by re-invoking itself under mpiexec, so the
  consumer-facing re-verification command is unchanged.

### Fixed
- **The reconciler was rebuilding all six envs every day, indefinitely.**
  `publish.yml`'s "Commit the refreshed lock" step only printed a notice — it never
  committed, and the job ran with `contents: read`. So the committed lock-hash stayed
  frozen at each env's first build while the registry moved on, every reconcile run
  saw drift, and every run dispatched a full rebuild + republish. Observed: 40
  consecutive daily reconcile→publish pairs, ~230 accumulated tags, and geospatial's
  committed lock still reading `c35344f5346d`/123 pkgs against a published
  `s615335b5a733`/124. The step now actually commits and pushes, with `contents:
  write` and a rebase-retry loop for the concurrent matrix legs. It gates on
  lock-hash drift rather than file content, so the `Built:`/`Builder:` header does
  not generate empty churn. This also makes OQ4 work as designed for the first time —
  "changed = lock-hash drift" needs a current baseline to diff against.
- **...and the lock commit no longer sits behind the registry visibility flip.** `dft`'s
  first publish found the fix above could still be defeated: the image built, passed D3,
  pushed and was signed, then Quay's `changevisibility` returned 403 (CI's
  `QUAY_OAUTH_TOKEN` lacks `repo:admin`), which skipped the lock-commit step sitting
  downstream of it — re-arming the loop on the very first env to publish after the fix.
  Registry visibility is orthogonal to what was built; the artifact has already shipped
  by that point. So the lock commit now runs immediately after build/push/sign, and
  set-public runs last, as the only step that changes nothing about the image. Confirmed
  in production: `envs/dft.lock.txt drifted: d7203fdaf84e -> c5674a815d28` committed and
  pushed on attempt 1, and a fresh `solve-hash.sh dft` now returns that same hash — so
  the next reconcile should report `dft` up to date rather than drifted.

### Notable
- **First real arm64 gaps, and a third kind of gap.** Scoping `dft` moved the
  solve-gap count off zero and turned up a failure mode the two-kind taxonomy
  couldn't express:
  - **MPI-flavor conflict** (new category, `GAPS.md`) — `abinit` 10.0.3 has a
    `linux-aarch64` build and solves standalone, but only against MPICH. conda-forge
    permits one MPI implementation per env, so it cannot join an OpenMPI env. Not a
    solve-gap (it solves alone), not an assemble-gap (it never gets that far).
    Recorded `stuck`, 90-day revisit; upstream fix is an `openmpi` variant in
    `conda-forge/abinit-feedstock`.
  - Rebuilding `dft` on MPICH to accommodate `abinit` was measured and rejected: it
    regresses gpaw 25.7.0→23.9.1, python 3.14→3.12, libxc 7.1.2→6.2.2 and elpa
    2025.06→2021.11. Hence the load-bearing `mpi=*=openmpi` pin in `envs/dft.yaml`.
  - **5 solve-gaps** — `cp2k`, `sisl`, `asap3`, `kimpy`, `openkim-models` have no
    `linux-aarch64` build at all. None blocks a shipping env; `cp2k` is the
    highest-value upstream target (a major DFT code, absent on arm64 while gpaw,
    siesta, nwchem, psi4, dftbplus and lammps all have builds).
- **Scope call on `dft` (OQ1 applied):** `siesta`, `dftbplus`, `lammps`, `nwchem` and
  `psi4` all co-solve with the shipped OpenMPI stack, but none bundles the
  pseudopotential / Slater-Koster / basis data its calculations need — so D3 could
  only import them, weakening "verified" from functional to import-only. Left out.
  `gpaw` qualifies because `gpaw-data` ships its 559 PAW setup files in-package,
  resolved from disk with no runtime download (cf. the `whitebox` wontfix).

### Corrected
- **"None of the other engines bundles its data" was wrong about `nwchem` and `psi4`.** The `dft`
  scope note above justified leaving `siesta`/`dftbplus`/`lammps`/`nwchem`/`psi4` out on
  the grounds that none ships the pseudopotential / Slater-Koster / basis data its
  calculations need, so D3 could only `import` them. Checked properly — by extracting the
  arm64 packages and looking rather than assuming — `nwchem` **does**: 606 basis files in
  `share/nwchem/libraries`, 1339 more in `libraries.bse`, and 8 pseudopotentials in
  `libraryps`. It is arm64, OpenMPI-flavored, and has its data on disk, so it clears
  exactly the bar `gpaw` cleared. So does `psi4`, whose `share/psi4/` ships `basis`,
  `databases`, `grids`, `quadratures` and `samples` (arm64, serial/threaded rather than
  MPI, and confirmed functionally: H2O SCF/cc-pVDZ = -76.026620 Eh computed offline on
  arm64, basis resolved from `share/psi4/basis`). The objection does hold for `siesta`
  (only `gen-basis`), `dftbplus` (zero `*.skf` in the prefix), `lammps` (no
  `potentials/`) and `elk` (no species files). Both data-shipping
  engines stay out of `dft` on domain grounds instead — molecular quantum chemistry, not
  plane-wave/PAW DFT — and become candidates for a future molecular-QC env. `GAPS.md` now
  records the per-package evidence. The general lesson: "engines don't ship data" is too
  coarse to scope on, and neither that claim nor its first correction should have been
  written from anything but an extracted package — the D3 doctrine applied one step
  earlier, at scoping time.

### Notable (ARM SIMD dispatch, measured on real Graviton3 + Graviton4)
- **The published images already run SVE kernels on Graviton, with no rebuild.** The
  question "can these containers use NEON/SVE/SVE2 or generation-specific tuning?" was
  answered by measurement rather than by reasoning from the from-source non-goal.
  New `benchmark/sve_dispatch_bench.py`, run on real c7g.large and c8g.large via
  `aws_bench.sh sve` (two independent runs each, agreeing within ~1%), using the
  **unmodified `quay.io/aarchsci/dft:latest`** on both:
  - aarch.science compiles nothing, so it never *selects* an ISA target — but it
    inherits conda-forge's **runtime dispatch**. OpenBLAS 0.3.34 is `DYNAMIC_ARCH`
    with 27 core kernel families (`armv8sve`, `neoversev1`, `neoversev2`, `a64fx`,
    `armv9sme`, …) and NumPy compiles an `SVE` dispatch path over its mandatory
    NEON/ASIMD baseline. `auto` selects **`neoversev1` on c7g** and **`neoversev2` on
    c8g**, NumPy reports `SVE: true` on both, and the same image falls back to
    `neoversen1`/`SVE: false` on Apple silicon.
  - **Worth ~23% on Graviton3** (DGEMM 48.45 vs 39.39 GF/s, SGEMM 99.25 vs 79.81,
    pinning `OPENBLAS_CORETYPE=neoversen1` to take SVE off the table), and ~5% on a
    real 16-atom Si SCF cycle. **Worth nothing measurable on Graviton4** — `neoversev2`
    is within noise of, and marginally behind, the NEON-only path. Recorded as measured
    rather than explained; we did not determine whether that is the 4×128-bit geometry
    (vs Graviton3's 2×256-bit, both measured via `prctl(PR_SVE_GET_VL)`) or an
    under-tuned kernel. **("marginally behind" is superseded — a no-SVE control run the
    next day showed that was the harness noise floor. See 2026-08-19 → Corrected.)**
  - **D3 applied to dispatch:** all six kernel configurations returned an identical
    −5.940776 eV/atom, matching the Apple-silicon run to the last digit. A faster
    kernel that changes the answer is worthless.
  - Cross-generation, the microbenchmark and the workload disagree and the workload
    wins the argument: Graviton3 takes DGEMM by 1.19×, while Graviton4 finishes the
    DFT calculation 1.17× faster and 1.06× cheaper per calculation.
  - Ceiling worth knowing: conda-forge ships `_x86_64-microarch-level` (up to v4) so the
    x86 solver can pick a tuned build, but there is **no aarch64 equivalent** and
    `__archspec` on arm64 reports a bare `aarch64`. On arm64 there is exactly one
    packaging tier — armv8-a baseline plus whatever each package dispatches internally.
    That is the real limit on this axis, and it is a packaging-ecosystem limit rather
    than a container or DESIGN one.

### Corrected (2026-08-18)
- **c7g is Graviton3, not Graviton4.** `benchmark/README.md` and the July results JSONL
  both labelled c7g.large "Graviton4". c7g is **Graviton3 / Neoverse V1**; c8g is
  Graviton4 / Neoverse V2. Confirmed two ways: AWS's pricing API reports
  `physicalProcessor=AWS Graviton3 Processor` for c7g.large, and the instance itself
  reports CPU part `0xd40` (Neoverse V1) against c8g's `0xd4f` (Neoverse V2). The
  measured throughput numbers were never affected — only the CPU label — but the
  mislabel mattered here, because this is exactly the generation distinction the SVE
  work turns on.

### Fixed (benchmark harness, 2026-08-18)
- `aws_bench.sh` no longer honours an inherited **`AWS_REGION`** (`R="${AWS_REGION:-…}"`
  silently sent `RunInstances` at us-east-1 on a shell exporting it, where the pinned
  us-west-2 subnet legitimately does not exist → `InvalidSubnetID.NotFound`). Region and
  subnet are a matched pair and now share a namespaced override.
- Its SSM command is built with `--cli-input-json` instead of the CLI's shorthand
  `--parameters`, which does not unescape a JSON string — multi-line commands arrived on
  the instance with literal `\n` and died at bash line 1.
- The cleanup trap uses a global, not a `local`: under `set -e` bash unwinds the function
  frame before running the `EXIT` trap, so the trap died on `unbound variable` exactly
  when it was supposed to terminate a leaked instance.
- AMIs are resolved from the current AL2023 SSM parameter per arch rather than pinned to
  ids that were months stale.

### Notable (neighborhood probe, 2026-08-18)
- **Swept ~90 packages around a DFT env** — engines, Wannier/transport, phonons, workflow
  managers, ML potentials, analysis, DMFT — checking arm64 availability *and* MPI flavor.
  Recorded in full in `GAPS.md`; the headlines:
  - `qe` (Quantum ESPRESSO) 7.5, `yambo`, `wannier90`, `nwchem`, `siesta`, `dftbplus`,
    `deepmd-kit` and `plumed` all have arm64 builds **flavored for OpenMPI**, so they
    could co-exist with the shipped `dft` stack. `abinit` is still the only MPICH-only
    case found — the third gap kind is real but rare, not endemic.
  - **11 more solve-gaps** (`phono3py`, `kwant`, `boltztrap2`, `alamode`, `dynaphopy`,
    `chgnet`, `m3gnet`, `quippy`, `triqs`, `triqs_dft_tools`, `edrixs`), taking the total
    to 16 — all probed-and-declined candidates, none blocking a shipping env. `phono3py`
    is the cheapest upstream fix in the catalog: same author and version as the `phonopy`
    we already ship on arm64, just no aarch64 build.
  - `libxsmm` 2.1.0 **does** build on arm64, so it does not explain `cp2k`'s absence — the
    obvious excuse for the highest-value gap is gone.
  - `sssp` (Standard Solid State Pseudopotentials) is a `noarch`, 62.7 MB,
    **zero-dependency** conda-forge package, i.e. real UPF data rather than a `whitebox`
    style runtime downloader. So `qe` + `sssp` looks functionally D3-verifiable on arm64
    with no network fetch, which undercuts the reason plane-wave QE was left out of v1.
    Points to a QE-centered v2 env rather than more packages bolted onto `dft`.

## 2026-06-26

### Added
- **Six verified, signed, public env images** on `quay.io/aarchsci`, each built
  native arm64 (no emulation), verified by an in-image D3 smoke test
  (assemble + import + functional), cosign keyless-signed in CI, and public:
  - `geospatial` (123 pkgs) — the founding fieldwork CPU-prep stack.
  - `earth-observation` (236 pkgs) — + xarray, dask, rioxarray, STAC clients.
  - `geo-ml` (378 pkgs) — + scikit-learn, xgboost, lightgbm, geopandas, pysal.
  - `climate` (247 pkgs) — cartopy, cfgrib/eccodes, metpy, xesmf/esmpy regridding.
  - `pointcloud` (287 pkgs) — pdal, python-pdal, laspy, richdem.
  - `comp-chem` (195 pkgs) — rdkit, openmm, pyscf, xtb, ase, mdanalysis, mdtraj.
- **The env-model builder** (`builder/`): `build-env.sh` + a generic `Dockerfile`
  that installs a curated `envs/<name>.yaml`, reads the resolved set from the
  finished image, runs the D3 smoke test (refuses to tag on failure), writes a
  committed lock, and tags `<date>` + `s<lock-hash>` + `latest`.
- **`solve-hash.sh`** — cheap dry-run solve → lock-hash; the reconciler's
  "changed?" probe.
- **Self-running machinery**: daily `reconcile.yml` (re-solve each spec, rebuild on
  lock-hash drift), `publish.yml`, `sign-existing.yml`.
- **GAPS.md + `farm/skip-list.tsv`** — the honest arm64-gap record + wontfix/stuck
  skip tiers, wired into the reconciler.
- **Site** at [aarch.science](https://aarch.science/) (GitHub Pages, HTTPS),
  `robots.txt` + `llms.txt` agent-discovery, issue templates.

### Notable
- **D3 verification earned its keep** — it caught two assemble-not-solve failures a
  solve-only check would have shipped broken:
  - `climate`/`xesmf`: env solved but `import xesmf` failed (resolver picked
    xesmf 0.6.0, pre the ESMF 8.4 `ESMF`→`esmpy` module rename). Fixed with an
    `xesmf >=0.8.4` floor in the spec.
  - `pointcloud`/`whitebox`: solves + imports, but downloads a precompiled amd64
    binary from the network at runtime — wrong arch + runtime fetch. Recorded as
    the first `wontfix` and excluded; richdem covers terrain natively.
- **Finding:** across all six envs, **zero solve-gaps** — conda-forge science is
  near-total on arm64. The delivery channel (pip's fragile arm64 wheels) was the
  whole problem, exactly the project thesis.

### Settled
- OQ2 (versioning: date + `s<lock-hash>` + latest), OQ3 (registry `quay.io/aarchsci`,
  domain aarch.science), OQ4 (reconciler "changed" = lock-hash drift).
