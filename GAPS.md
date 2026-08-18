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

## Three kinds of gap (and why the second is the one that matters here)

Unlike aarchbio — whose gap is simply *"no arm64 build exists"* (a solve check
suffices) — aarch.science has to distinguish several failure modes:

| Kind | Meaning | How we detect it |
|------|---------|------------------|
| **solve-gap** | the package has no `linux-aarch64` build on conda-forge, so the env won't even resolve | `builder/solve-hash.sh` fails to solve |
| **assemble-gap** | the env *solves* but a package fails to **import / run** on arm64 (missing native lib, ABI mismatch, broken build) — the trap that bit fieldwork on pip | the D3 smoke test fails inside the built arm64 image |
| **MPI-flavor conflict** | the package has an arm64 build and solves *standalone*, but only against an MPI implementation that conflicts with the rest of the env | the env solve fails while the package alone solves fine |

The **assemble-gap is the dangerous one** and the reason "verified" means a
functional smoke test, not a green solve. A solve-gap is loud; an assemble-gap is
silent until you `import`.

The third kind was found in 2026-08 while scoping the `dft` env and is specific to
MPI-parallel science: conda-forge allows exactly **one MPI implementation per
environment** (arbitrated by the `mpi` metapackage), so MPI flavor is an env-wide
decision, not a per-package one. A package whose arm64 feedstock only builds against
MPICH therefore cannot enter an OpenMPI env even though nothing about it is broken on
arm64. It looks like a solve-gap from inside the env and like no gap at all from
outside it — which is why it needs its own name. `abinit` is the first instance; see
Active gaps.

## Summary (as of 2026-08)

| Kind | Count | Notes |
|------|------:|-------|
| **solve-gap** | 5 | `cp2k`, `sisl`, `asap3`, `kimpy`, `openkim-models` — no `linux-aarch64` build at all. All surfaced while scoping `dft`; none blocks a shipping env. `cp2k` is the highest-value upstream target. |
| **assemble-gap** | 1 | `whitebox` — solves, imports, but fetches an amd64 binary at runtime (wontfix); see below |
| **MPI-flavor conflict** | 1 | `abinit` — has an arm64 build, solves standalone, but MPICH-only (stuck); see below |

**The curated science head is near-complete on arm64.** This is still the real
finding, and it's the inverse of the pip experience: the exact stack that fails
`No matching distribution found for rasterio` on PyPI solves *and* assembles cleanly
on conda-forge. Seven envs ship verified (geospatial, earth-observation, geo-ml,
climate, pointcloud, comp-chem, dft), and every headline package in all seven
assembles and does real work natively.

The gap count moved off zero in 2026-08, and it's worth being precise about what
changed: the five solve-gaps are **candidates we probed and declined**, not holes in
a shipping image. They cluster in one place — classical-potential and DFT tooling
(`cp2k`, `asap3`, `kimpy`, `openkim-models`, `sisl`) — which is simply the least
arm64-travelled corner of the science stack we've reached so far. `cp2k` is the one
worth upstream effort: a major plane-wave/Gaussian DFT code, entirely absent on
`linux-aarch64` while `gpaw`, `siesta`, `nwchem`, `psi4`, `dftbplus` and `lammps` all
have builds.

Two near-misses caught by the D3 smoke test (and fixed in-spec, not skip-listed)
are worth recording, because they're precisely the assemble-not-solve trap D3
exists for — both would have shipped broken under a solve-only check:

- **`xesmf` (climate)** — the env *solved* but `import xesmf` failed
  (`No module named 'ESMF'`). The resolver picked xesmf 0.6.0, which predates the
  ESMF 8.4 module rename (`ESMF` → `esmpy`). Fixed by pinning `xesmf >=0.8.4` in
  the spec so the post-rename line is chosen. Not a gap — a version floor.
- **`whitebox` (pointcloud)** — see Active gaps below.

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

### Coverage probe — atomistic / DFT (2026-08, while scoping `dft`)

The first probe to return real gaps. Resolving native arm64 on conda-forge:

`gpaw` (incl. `mpi_openmpi` + OpenMP variants) · `gpaw-data` · `ase` · `libxc` ·
`libvdwxc` · `elpa` · `scalapack` · `fftw` · `openmpi` · `mpich` · `mpi4py` ·
`spglib` · `phonopy` · `pymatgen` · `siesta` · `dftbplus` · `lammps` · `nwchem` ·
`psi4` · `nglview`

**No `linux-aarch64` build (solve-gap):** `cp2k` · `sisl` · `asap3` · `kimpy` ·
`openkim-models`

**Arm64 build exists but MPICH-only (MPI-flavor conflict):** `abinit`

`siesta`, `dftbplus`, `lammps`, `nwchem` and `psi4` all co-solve with the shipped
OpenMPI `dft` stack, so they are *available* — they're left out of the v1 spec for a
verification reason, not an arm64 one: none of them bundles the pseudopotential /
Slater-Koster / basis-set data its calculations need, so D3 could only `import` them,
which would quietly downgrade "verified" from functional to import-only. `gpaw` is in
precisely because `gpaw-data` ships its PAW setups in-package (559 files, resolved
from disk with no runtime download — cf. `whitebox`).

## When a real gap appears

1. The reconciler's solve-hash fails (solve-gap **or** MPI-flavor conflict) **or** the
   D3 smoke test fails (assemble-gap) — the env won't publish either way (the gate
   holds). To tell a flavor conflict from a plain solve-gap, re-solve the blocking
   package **alone**: if it solves by itself but not in the env, read the solver's
   conflict tree for an `mpi 1.0.* <impl>` line.
2. Add an entry to [`farm/skip-list.tsv`](farm/skip-list.tsv): `wontfix` (never
   retry) or `stuck` (revisit monthly), with the blocking package and reason.
3. Record it in this file under a new "Active gaps" section with the upstream
   feedstock to fix.
4. File it upstream at the package's conda-forge feedstock
   (`github.com/conda-forge/<pkg>-feedstock`).

## Active gaps

### `abinit` — stuck (MPI-flavor conflict, revisit quarterly)

**Env affected:** `dft` (excluded; `gpaw` covers plane-wave/PAW DFT, and `siesta` /
`dftbplus` remain available if a future spec wants a second engine).

`abinit` 10.0.3 **does** have a `linux-aarch64` build and solves perfectly well on
its own (59 packages). Nothing about it is broken on arm64. The problem is which MPI
it was built against: on aarch64 conda-forge ships only the **MPICH** variant, and
conda-forge arbitrates MPI through the `mpi` metapackage so an environment can contain
exactly one implementation. `dft` is OpenMPI (that is what `gpaw`'s current arm64
builds, and `elpa`/`fftw`/`libvdwxc`, are flavored for), so the solve fails with
`abinit … requires mpich >=4.3,<5.0a0` against `openmpi … requires mpi 1.0 openmpi`.

Rebuilding `dft` around MPICH to accommodate it was measured and rejected — it drags
the entire stack backwards by roughly two years:

| | OpenMPI (shipped) | MPICH (to fit abinit) |
|---|---|---|
| gpaw | 25.7.0 | 23.9.1 |
| python | 3.14 | 3.12 |
| libxc | 7.1.2 | 6.2.2 |
| elpa | 2025.06.001 | 2021.11.002 |

**Upstream fix:** add an `openmpi` variant for `linux-aarch64` to
[`conda-forge/abinit-feedstock`](https://github.com/conda-forge/abinit-feedstock).
That is an ordinary feedstock matrix change, which is why this is `stuck` (retry) and
not `wontfix`. Recorded in [`farm/skip-list.tsv`](farm/skip-list.tsv) with a 90-day
revisit.

### `whitebox` — wontfix (assemble-gap, by design)

**Env affected:** `pointcloud` (excluded; richdem covers the terrain/hydrology need).

The conda-forge `whitebox` package solves and imports fine, but it is a thin Python
shim: on first use it **downloads a pre-compiled `WhiteboxTools_linux_amd64.zip`
binary from `whiteboxgeo.com` at runtime.** On arm64 that is the wrong architecture
(would require emulation) and a runtime network fetch — both directly against this
project's principles (native, no emulation, verifiable build, no runtime download).
The D3 smoke test caught it as a `PermissionError`/exec failure for the non-root
container user.

This is not upstream-fixable in a feedstock the way a missing build is — it's the
package's design. It is therefore **wontfix** (`farm/skip-list.tsv`) and excluded
from the spec. If WhiteboxGeo ships a native arm64 binary and the conda-forge
package selects it by arch, revisit. `richdem` (native, verified) covers terrain
analysis in the meantime.
