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

## Four kinds of gap (and why the second is the one that matters here)

Unlike aarchbio — whose gap is simply *"no arm64 build exists"* (a solve check
suffices) — aarch.science has to distinguish several failure modes:

| Kind | Meaning | How we detect it |
|------|---------|------------------|
| **solve-gap** | the package has no `linux-aarch64` build on conda-forge, so the env won't even resolve | `builder/solve-hash.sh` fails to solve |
| **assemble-gap** | the env *solves* but a package fails to **import / run** on arm64 (missing native lib, ABI mismatch, broken build) — the trap that bit fieldwork on pip | the D3 smoke test fails inside the built arm64 image |
| **MPI-flavor conflict** | the package has an arm64 build and solves *standalone*, but only against an MPI implementation that conflicts with the rest of the env | the env solve fails while the package alone solves fine |
| **python-ABI collision** | the package has arm64 builds, but the set of Python versions it was built for doesn't intersect what another env member needs, so adding it silently drags the whole env to an older Python | the env still solves — but the resolved set regresses; only a lock diff shows it |

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

The fourth kind was found in 2026-09 while adding `psi4` to `comp-chem`, and it is the
**quietest of the four**: nothing fails. The solve succeeds, the smoke test passes, the
image ships — it is just an older image than before. conda-forge builds compiled Python
packages once per Python minor version, so a package effectively carries a *set* of
Python ABIs, and adding a package intersects that set with every other member's. If the
intersection excludes the Python the env was running, the solver does the only thing it
can and moves the whole env back. `psi4` 1.11 has `linux-aarch64` builds for
py310/311/312/**314 but not 313**, and `comp-chem`'s `xtb-python` has no py314 build, so
the only Python satisfying both is 3.11 — which would have downgraded 13 packages
(python 3.13→3.11, numpy 2.5→2.4, scipy 1.18→1.17, hdf5 2.2→1.14, plus rdkit, pyscf and
libboost). Neither the D3 smoke test nor the solve can catch this, because neither is
broken. **The only detector is reading the lock diff** — which is why a lock diff is now
part of reviewing any spec change, not just an artifact of it. The fix here needed no
upstream work at all: `dft` already runs py314, so psi4 went there instead and cost
nothing. See `envs/dft.yaml`.

## Summary (as of 2026-08)

| Kind | Count | Notes |
|------|------:|-------|
| **solve-gap** | 16 | No `linux-aarch64` build at all. Engines/potentials: `cp2k`, `sisl`, `asap3`, `kimpy`, `openkim-models`, `quippy`, `chgnet`, `m3gnet`. Phonons/transport: `phono3py`, `alamode`, `dynaphopy`, `boltztrap2`, `kwant`. DMFT: `triqs`, `triqs_dft_tools`, `edrixs`. All surfaced while scoping `dft` and probing its neighborhood; **none blocks a shipping env** — they are candidates we probed and declined. `cp2k` is the highest-value target, `phono3py` the cheapest. |
| **assemble-gap** | 1 | `whitebox` — solves, imports, but fetches an amd64 binary at runtime (wontfix); see below |
| **MPI-flavor conflict** | 1 | `abinit` — has an arm64 build, solves standalone, but MPICH-only (stuck); see below |
| **python-ABI collision** | 1 | `psi4` in `comp-chem` — no py313 arm64 build; relocated to `dft` (py314) instead of downgrading 13 packages. Not a gap in any shipping image; see below |
| **solve-gap (R/CRAN layer)** | 729 | Counted separately because it is a *coverage* measurement, not a list of probed candidates: 729 of the 3891 `r-*` packages installable on linux-64 have no arm64 build. 18.7%, and the `r` env ships anyway. See "The R layer" below |

**The curated science head is near-complete on arm64.** This is still the real
finding, and it's the inverse of the pip experience: the exact stack that fails
`No matching distribution found for rasterio` on PyPI solves *and* assembles cleanly
on conda-forge. Ten envs ship verified (geospatial, earth-observation, geo-ml,
climate, pointcloud, comp-chem, dft, md, viz, r), and every headline package in all ten
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

### The R layer — the first gap that is a percentage, not a list (2026-09-04, issue #8)

Every gap above is a named package we probed and declined. R is different: it is a whole
second package ecosystem, with 3891 `r-*` packages installable on linux-64, so the useful
question is coverage rather than a list. Measured against conda-forge repodata:

| | linux-64 | linux-aarch64 |
|---|---:|---:|
| `r-*` built for the platform | 2249 | **658** |
| `r-*` `noarch` (serve every platform) | 2542 | 2542 |
| **usable** | **3891** | **3162** |

So **729 packages (18.7%) are linux-64-only** — by far the largest absolute gap this
project has recorded, and yet the `r` env ships, because the missing 18.7% is a long
tail. Of 73 well-known R packages checked by hand, 58 have arm64 builds and 12 do not:
`r-duckdb`, `r-torch`, `r-prophet`, `r-fable`, `r-umap`, `r-rpostgres`,
`r-exactextractr`, `r-amelia`, `r-actuar`, `r-bart`, `r-bayesforecast`,
`r-adehabitathr`. The shape is the same power law the project's scoping rule assumes:
the head (tidyverse, `sf`/`terra`, `data.table`, `arrow`, `Matrix`/`MASS`/`survival`,
`glmnet`, `randomForest`, `caret`, `knitr`/`rmarkdown`, `Rcpp`) is all present.
`r-duckdb` and `r-exactextractr` are the two most worth upstream effort — both are
current, widely used, and have no arm64 blocker beyond the feedstock not enabling it.

Note the arithmetic that makes this survivable: **2542 of the 3162 usable packages are
`noarch`**, which is to say most of CRAN in conda-forge is pure R and therefore
arm64-native for free. The 729 gap is almost entirely packages with compiled code.

**What is emphatically NOT a gap, though it looks exactly like one.** `r-base` reaches
4.6.1 on linux-aarch64 while **zero of the 3162 `r-*` packages have an `r46` build** —
the whole CRAN layer is built against R 4.5 (`r45`) and older. It would be easy to file
that as an arm64 hole. It isn't: linux-64, osx-64, osx-arm64, win-64 and linux-ppc64le
show the identical `r34..r45` families with no `r46`, and the 2542 `noarch` r-* packages
are platform-independent by construction and likewise r45-max. It is a channel-wide
migration that has not run yet. So it belongs in `envs/r.yaml` as a version pin
(`r-base >=4.5,<4.6`) and not in this file — the same discipline applied to `paraview`'s
hdf5 soname bug, which also breaks identically on linux-64.

It is worth stating what over-pinning would have cost, because the request that started
this env proposed exactly that: `r-base=4.6.1 r-tidyverse` **does not solve at all**.
libmamba walks every r-* candidate wanting `r-base >=4.5,<4.6.0a0` (then >=4.4,<4.5, and
so on down to 3.6) and gives up. Pinning the newest interpreter would have produced an R
image with no CRAN ecosystem available to it.

Two near-misses caught by the D3 smoke test (and fixed in-spec, not skip-listed)
are worth recording, because they're precisely the assemble-not-solve trap D3
exists for — both would have shipped broken under a solve-only check:

- **`xesmf` (climate)** — the env *solved* but `import xesmf` failed
  (`No module named 'ESMF'`). The resolver picked xesmf 0.6.0, which predates the
  ESMF 8.4 module rename (`ESMF` → `esmpy`). Fixed by pinning `xesmf >=0.8.4` in
  the spec so the post-rename line is chosen. Not a gap — a version floor.
- **`whitebox` (pointcloud)** — see Active gaps below.

### Bugs D3 caught that are *not* arm64 gaps (2026-09, adding `md` and `viz`)

The most useful thing this section records is a negative result. Scoping the `md` and
`viz` envs turned up three packaging bugs severe enough to ship a broken image — and
**none of them is an arm64 problem**. Each reproduces identically on `linux-64`. They
are ordinary feedstock bugs that the arm64 route merely happened to walk into first,
and saying so plainly matters: this project's credibility rests on not inflating its
own findings, and "the arm64 build is broken" would have been the wrong claim in all
three cases. All three are fixed in-spec (or in the builder) and none is skip-listed.

- **`paraview` underlinks `libhdf5` (assemble-gap, arch-independent).** conda-forge
  `paraview` 6.1.1 links `libhdf5` directly but declares **no hdf5 dependency at all**
  — every `linux-aarch64` build of 6.1.1 lists only `vtk-base` and `vtk-io-ffmpeg`. Its
  binaries want `libhdf5.so.310` (hdf5 1.14.x), but `vtk-base` 9.6.2 exists in two
  build-7 variants, one migrated to hdf5 ≥2.2 (soname 320), and paraview's
  `vtk-base >=9.6.2,<9.6.3` pin accepts either. The solver picks the hdf5-2.2 one and
  then `pvbatch`, `pvpython` **and** `import paraview.simple` all die with
  `libhdf5.so.310: cannot open shared object file`. A bare `conda install paraview`
  cannot start. Fixed in-spec with `hdf5=1.14.*`; the real fix is a missing
  run-export/dependency in
  [`conda-forge/paraview-feedstock`](https://github.com/conda-forge/paraview-feedstock).
  Verified identical on linux-64, same missing soname.
- **`gromacs`'s activation script bricks the whole image (arch-independent).** The
  worst of the three, because it fails *before* any of our checks: it is not an import
  error, it is every command in the container exiting 1. `GMXRC.bash` does
  `for cfile in $GMXBIN/gmx-completion-*.bash ; do source $cfile ; done` with no
  existence test, and micromamba-docker's entrypoint runs `set -ef` — `-f` disables
  globbing, so `source` gets the literal pattern, fails, and `-e` aborts the entrypoint
  before `exec "$@"`. The completion files exist; the glob is simply never expanded.
  Repaired in [`builder/Dockerfile`](builder/Dockerfile) by guarding the loop, which is
  also the correct upstream fix (the loop is unsafe for any build that ships no
  completion files, on any platform). Worth recording as a *class*: a package's
  `activate.d` hook is executed by our entrypoint under flags the package never tested
  against, so it is attack surface for image usability that no `import` check can see.
- **`libxc-c` and `openmm` CUDA builds are installable on GPU-less hosts (D4 leak).**
  Not a break — a silent 800 MB of unreachable GPU code in a CPU image, which is worse
  in one respect: nothing fails, so neither the solve nor D3 can see it. conda-forge's
  mechanism for this is the `__cuda` virtual package, and `libxc-c` 7.1.2 shows the bug
  precisely: its **build-number-1** CUDA builds declare `__cuda` (correctly excluded on
  a GPU-less host) while its **build-number-0** CUDA builds declare only
  `cuda-version >=13.0,<14`, a freely-installable noarch metapackage. `pyscf` 2.14.0
  *requires* `libxc-c ... cuda_*`, so `comp-chem` drifted onto a 631 MB libxc-c where a
  21 MB one exists. `openmm` 8.6.0 has the same missing `__cuda` and, worse, no
  cpu/cuda marker in its build string to pin against. Fixed in-spec (see
  `envs/comp-chem.yaml`); upstream targets are
  [`conda-forge/libxc-feedstock`](https://github.com/conda-forge/libxc-feedstock) and
  [`conda-forge/openmm-feedstock`](https://github.com/conda-forge/openmm-feedstock).
  This is the same hazard already noted for `lammps` in `envs/md.yaml` — three
  feedstocks now, so it is a pattern rather than an oddity.

### A fourth, found 2026-09-04 adding `nwchem`, and this one was ours

Recorded separately because it is not a feedstock bug at all — conda-forge did nothing
wrong. It is the same class as the CUDA leak (a build-string flip the lock cannot see),
except that here the flip *was* breaking and one of our two gates was **asserting it
away**.

- **`gpaw` could flip to its nompi build on a build-number tie, and D3 would still
  pass.** `envs/dft.yaml` pinned `gpaw >=25.7` with no build string. gpaw 25.7.0's
  `py314_nompi_omp_3` and `py314_mpi_openmpi_omp_3` are **both build number 3**, so which
  one you get is a tie-break, and on 2026-09-04 it went to nompi. The published image had
  the OpenMPI build; a reconciler rebuild would have shipped a *serial* `dft` under a
  byte-identical lock and an identical lock-hash. gpaw's openmpi build is also what pins
  `elpa`/`fftw`/`libvdwxc` to `mpi_openmpi_*`, so the whole parallel chain follows this
  one choice.
- **The smoke test could not catch it, which is the part worth internalising.** A nompi
  gpaw run under `mpiexec -n 2` does not error: MPI starts two processes, each imports a
  serial gpaw, each sees `world.size == 1` and `world.rank == 0`, each does the entire
  calculation, and both print the same number. `dft.smoke.py` then compared "the parallel
  energy" against the serial one, found them equal, and reported the parallel path
  verified. A test whose success condition is *agreement* can be satisfied by doing the
  work twice. Fixed by asserting the precondition (`world.size > 1` in the child) instead
  of only the result, plus asserting build strings from `conda-meta` in the image.
- **`plumed` is the transitive version of the same trap.** It arrives as a required
  `nwchem` dependency — a package this repo never named — and its `mpi_nompi_*` build
  carries build number **103** against the openmpi build's **3**. An unpinned solve puts a
  serial plumed into an all-OpenMPI env, and nothing in the spec was there to stop it.
  Dependencies you did not ask for need flavour pins too.

**Two of the first three are invisible to both of the project's automated gates**, which is
the finding that should change how specs get reviewed. A solve cannot see them (nothing
conflicts) and D3 cannot see them (nothing is broken); the CUDA leak and the
python-ABI collision are only detectable by *reading the lock diff*. That is now part
of reviewing a spec change. See also the lock-format limitation noted in
[DESIGN.md](DESIGN.md) under OQ2: the lock records `name version` only, so a build-string
flip — `cpu_*`→`cuda_*`, or `mpi_openmpi_*`→`nompi_*` — changes neither the lock nor the
lock-hash, and is therefore invisible to the reconciler as well.

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
OpenMPI `dft` stack, so they are *available* — they were left out of the v1 spec for a
verification reason, not an arm64 one. `gpaw` is in precisely because `gpaw-data` ships
its PAW setups in-package (559 files, resolved from disk with no runtime download —
cf. `whitebox`), so D3 can do real work rather than merely `import`.

**Three of those five have since shipped** (2026-09), and the v1 reasoning above survives
in an amended form rather than intact:

- **`psi4` → `dft`.** Ships basis sets (see the table below), so it clears the bar
  outright: D3 runs H2 RHF/STO-3G = -1.116759 Hartree against an independent native
  integral/SCF stack. It went to `dft` rather than `comp-chem` because of the
  python-ABI collision described above, and it needs `psi4 >=1.11` — 1.10 installs
  cleanly against `libxc-c` 7.1.2 and then dies during `import psi4` with
  `Fatal Error: Could not find required LibXC functional`.
- **`lammps` → `md`.** Ships no potential files, but the canonical Lennard-Jones melt
  needs none, so D3 runs real MD (TotEng ≈ -2.30 reduced units) and checks 2-rank MPI
  agrees. The data objection turned out to be avoidable rather than fatal — for this
  engine, by choosing a physics problem whose potential is analytic.
- **`siesta` → `dft`, and this one is a genuine compromise.** The v1 objection holds
  exactly as written and could not be engineered around: siesta needs a pseudopotential
  per element and conda-forge's package ships none. So `siesta` gets **weaker
  verification than every other headline package in the catalog** — no SCF. D3 asserts
  the binary self-reports `Architecture: aarch64` and an MPI parallelisation, parses a
  real `.fdf`, runs its full initialisation, and distributes over 2 ranks; it stops at
  `pseudo_read`, which is where the missing data stops it. That is a real process doing
  real work and it is honestly weaker than `gpaw`'s. It is recorded in
  `envs/dft.smoke.py` at the point where someone would otherwise "strengthen" the checks
  and be puzzled.

`dftbplus` and `elk` remain out on the unchanged v1 grounds.

**Does each engine ship the data its calculations need?** This was first written up as a
blanket "none of them does," which was wrong. Checked properly by extracting the arm64
packages and looking (2026-08-18):

| Package | Ships its own data? | Evidence |
|---|---|---|
| `nwchem` | **yes** | 606 basis files in `share/nwchem/libraries`, 1339 more in `libraries.bse`, 8 pseudopotentials in `libraryps` |
| `siesta` | no | 359 files — the binaries, `libpsml` and the `psml2psf` converter. Zero `*.psf`, `*.vps` or `*.psml` data, and **no bundled example inputs either** (issue #1 assumed an example ships; measured, none does). The obvious source, conda-forge `pseudo_dojo`, is 0.2 MB of code with no tables and pins `numpy <1.25` / `pymatgen <=2023.9.10`, so it would wreck the env twice over |
| `dftbplus` | no | zero `*.skf` files in the whole prefix; `share/` holds only toolchain/doc dirs plus the bundled `dftd4` and `s-dftd3` dispersion data. Upstream distributes Slater-Koster sets separately from the code |
| `lammps` | no | 42 files total: `bin/lmp`, `bin/lmp_mpi` and the Python module. No `potentials/` directory, and **no `bench/in.lj`** — issue #3 assumed that input ships with the package; it does not, so `md`'s smoke test writes the melt input itself |
| `gromacs` | **yes** | the opposite of what was assumed: ships `share/gromacs/top` with the full force-field set (`amber99sb-ildn.ff`, `tip3p.itp`) and reference structures including `spc216.gro`, so `md`'s D3 runs real solvated MD with nothing staged or downloaded |
| `elk` | no | no species files anywhere in the prefix |
| `psi4` | **yes** | `share/psi4/` ships `basis`, `databases`, `grids`, `quadratures` and `samples`, plus libint's 90 basis files in `share/libint/<ver>/basis`. Proven functionally: psi4 1.11 ran H2O SCF/cc-pVDZ = -76.026620 Eh offline on arm64, resolving the basis from `share/psi4/basis` |

(Every row rests on a search that completed without truncation. That is worth stating
because the first version of this table got `psi4` exactly backwards by reading a
`head`-truncated listing as evidence of absence — an absence claim is only as good as the
completeness of the search behind it.)

So the verification objection holds for `siesta`, `dftbplus`, `lammps` and `elk`, but
**not** for `nwchem` or `psi4` — both have their basis sets on disk and so clear exactly
the bar `gpaw` cleared (`nwchem` on arm64 + OpenMPI, `psi4` on arm64 serial/threaded).
**Both are now in `dft`** — `psi4` via issue #2 and `nwchem` via issue #7 — and the
"domain grounds" rationale this section used to give for keeping them out (molecular QC
rather than plane-wave/PAW, so a future molecular-QC env) is retired. It did not survive
contact with the cost: a separate molecular-QC env would need its own duplicate OpenMPI /
ScaLAPACK / libxc / python stack, and both engines resolve against the one `dft` already
has. Shared MPI flavour beat taxonomic tidiness.
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
