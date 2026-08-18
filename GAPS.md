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
| **solve-gap** | 16 | No `linux-aarch64` build at all. Engines/potentials: `cp2k`, `sisl`, `asap3`, `kimpy`, `openkim-models`, `quippy`, `chgnet`, `m3gnet`. Phonons/transport: `phono3py`, `alamode`, `dynaphopy`, `boltztrap2`, `kwant`. DMFT: `triqs`, `triqs_dft_tools`, `edrixs`. All surfaced while scoping `dft` and probing its neighborhood; **none blocks a shipping env** — they are candidates we probed and declined. `cp2k` is the highest-value target, `phono3py` the cheapest. |
| **assemble-gap** | 1 | `whitebox` — solves, imports, but fetches an amd64 binary at runtime (wontfix); see below |
| **MPI-flavor conflict** | 1 | `abinit` — has an arm64 build, solves standalone, but MPICH-only (stuck); see below |

**The curated science head is near-complete on arm64.** This is still the real
finding, and it's the inverse of the pip experience: the exact stack that fails
`No matching distribution found for rasterio` on PyPI solves *and* assembles cleanly
on conda-forge. Seven envs ship verified (geospatial, earth-observation, geo-ml,
climate, pointcloud, comp-chem, dft), and every headline package in all seven
assembles and does real work natively.

The gap count moved off zero in 2026-08, and it's worth being precise about what
changed: every solve-gap listed above is a **candidate we probed and declined**, not a
hole in a shipping image. They cluster in one region — atomistic simulation beyond the
DFT engines themselves: classical/ML potentials (`asap3`, `kimpy`, `openkim-models`,
`quippy`, `chgnet`, `m3gnet`), anharmonic phonons and transport (`phono3py`, `alamode`,
`dynaphopy`, `boltztrap2`, `kwant`), and DMFT (`triqs` and friends). That is simply the
least arm64-travelled corner of the science stack we've reached so far, and the pattern
is informative: the *engines* mostly have arm64 builds (`gpaw`, `qe`, `siesta`, `nwchem`,
`psi4`, `dftbplus`, `lammps`, `elk`, `yambo`, `wannier90`), while the tooling layered
around them often doesn't.

`cp2k` remains the one most worth upstream effort — a major plane-wave/Gaussian DFT code,
entirely absent on `linux-aarch64` while all of those engines have builds. `phono3py` is
the cheapest, since `phonopy` already builds on arm64. See the neighborhood probe below.

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
verification reason, not an arm64 one. `gpaw` is in precisely because `gpaw-data` ships
its PAW setups in-package (559 files, resolved from disk with no runtime download —
cf. `whitebox`), so D3 can do real work rather than merely `import`.

**Does each engine ship the data its calculations need?** This was first written up as a
blanket "none of them does," which was wrong. Checked properly by extracting the arm64
packages and looking (2026-08-18):

| Package | Ships its own data? | Evidence |
|---|---|---|
| `nwchem` | **yes** | 606 basis files in `share/nwchem/libraries`, 1339 more in `libraries.bse`, 8 pseudopotentials in `libraryps` |
| `siesta` | no | ships only the `gen-basis` tool; no basis/pseudopotential data |
| `dftbplus` | **unverified** | the `.skf` search hit its output limit on `.mod` compile artifacts before absence was established. Upstream distributes Slater-Koster sets separately from the code, so "no" is likely — but likely is not verified, so this row is pending a recheck |
| `lammps` | no | no `potentials/` directory in the conda package |
| `elk` | no | no species files anywhere in the prefix |
| `psi4` | **yes** | `share/psi4/` ships `basis`, `databases`, `grids`, `quadratures` and `samples`, plus libint's 90 basis files in `share/libint/<ver>/basis` |

(The `nwchem`, `psi4`, `siesta`, `lammps` and `elk` rows rest on searches that completed
without truncation; `dftbplus` does not, and says so. The first version of this table got
`psi4` wrong precisely by reading a truncated listing as evidence of absence.)

So the verification objection holds for `siesta`, `lammps` and `elk`, but
**not** for `nwchem` or `psi4` — both have their basis sets on disk and so clear exactly
the bar `gpaw` cleared (`nwchem` on arm64 + OpenMPI, `psi4` on arm64 serial/threaded).
Both stay out of `dft` on domain grounds instead: they are molecular quantum chemistry,
not plane-wave/PAW DFT, so they belong in a future molecular-QC env rather than this one.
Note that the two *are* the ones that ship data, which is not a coincidence — molecular QC
basis sets are small text files that fit in a package, whereas pseudopotential and
Slater-Koster libraries are large, licence-encumbered, or curated out-of-band (which is
why `sssp` being a 62.7 MB `noarch` package is such a useful find; see below).

The lesson generalizes: "engines don't ship data" is too coarse a heuristic, and so is any
scoping claim asserted rather than checked. It has to be verified per package — the D3
doctrine applied one level earlier, at scoping time rather than build time.

### Coverage probe — the wider gpaw neighborhood (2026-08-18)

A follow-up sweep of ~90 packages across the roles that surround a DFT env: engines,
Wannier/tight-binding/transport, phonons, workflow managers, ML interatomic potentials,
analysis/defects, and DMFT. MPI flavor was checked too, since that is what excluded
`abinit`.

**arm64 build AND OpenMPI-flavored** (so they could co-exist with the shipped `dft`
stack): `qe` 7.5 (Quantum ESPRESSO) · `yambo` 5.3.0 (GW/BSE) · `wannier90` 4.0.1 ·
`nwchem` 7.3.1 · `siesta` 5.4.2 · `dftbplus` 25.1 · `deepmd-kit` 3.1.3 · `plumed` 2.10.1.
Serial-only on arm64: `elk` 10.2.4, `psi4` 1.12a3. Also arm64: `dftd4` 4.2.0 and
`simple-dftd3` 1.5.0 (dispersion corrections — natural gpaw/ase companions), and
`elpa` 2026.02.002, newer than the 2025.06.001 our solve currently picks.

`abinit` remains the **only** MPICH-only case found, which is worth knowing: the third
gap kind is real but rare, not endemic.

**`noarch`, so arm64 is a non-issue** — the whole workflow and analysis layer is pure
Python and carries no porting risk at all: `aiida-core` · `atomate2` · `jobflow` ·
`quacc` · `fireworks` · `custodian` · `myqueue` · `mp-api` · `matminer` · `abipy` ·
`pyprocar` · `sumo` · `doped` · `pydefect` · `shakenbreak` ·
`pymatgen-analysis-defects` · `cclib` · `crystal-toolkit` · `nglview` · `py3dmol` ·
`seekpath` · `hiphive` · `amset` · `wannierberri` · `pythtb` · `matgl` · `nequip` ·
`sevenn`. (Most of them *wrap* an engine, so they are only as useful as the engine
shipped beside them — which is a granularity question, OQ1, not an arm64 one.)

**No `linux-aarch64` build (solve-gap) — 11 more:** `phono3py` · `kwant` ·
`boltztrap2` · `alamode` · `dynaphopy` · `chgnet` · `m3gnet` · `quippy` · `triqs` ·
`triqs_dft_tools` · `edrixs`.

Two of these deserve attention:

- **`phono3py` is the best upstream target in the list.** We ship `phonopy` 4.4.0 on
  arm64; `phono3py` is the same author's anharmonic / thermal-conductivity companion at
  the same version, and has no arm64 build. Because phonopy already builds, this is
  plausibly the lowest effort-per-value fix available
  ([`conda-forge/phono3py-feedstock`](https://github.com/conda-forge/phono3py-feedstock)).
- **`libxsmm` 2.1.0 *does* have an arm64 build**, so it does not explain `cp2k`'s
  absence. The obvious excuse for the highest-value gap is gone, and the real fix may be
  more tractable than assumed.

**A data package that changes a scoping call:** `sssp` — the Standard Solid State
Pseudopotentials library — is on conda-forge as a **`noarch`, 62.7 MB, zero-dependency**
package. Size and empty dependency list mean it ships the UPF files rather than fetching
them at runtime (the `whitebox` failure mode is a few KB of Python shim). `pslibrary` has
no arm64 build, but `sssp` covers the need without it, and `basis_set_exchange` is
likewise `noarch`. That means **`qe` + `sssp` looks functionally D3-verifiable on arm64
with no runtime download** — so the pseudopotential-data objection that kept plane-wave
QE out of v1 may not survive. The natural v2 is therefore a QE-centered env
(`qe` + `sssp` + `wannier90` + `yambo`, all OpenMPI on arm64) rather than more packages
bolted onto `dft`: a second engine with its own data story earns its own image.

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
