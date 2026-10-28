# aarch.science

**Verified, signed, native arm64 (aarch64) containers for the scientific-computing
stack** — geospatial / earth-observation first. For Apple Silicon and AWS Graviton.

Sister project to [aarch.bio](https://aarch.bio/), which rebuilds **bioconda tool
packages**. aarch.science covers the **conda-forge** scientific stack.

> **Status:** live. **29 verified, signed, public env images** on
> [`quay.io/aarchsci`](https://quay.io/organization/aarchsci), a daily reconciler,
> and a site at **[aarch.science](https://aarch.science/)**.

## Looking for a specific tool? Which project has it

Two questions decide it, and the first one usually settles it:

**1. Do you want one tool, or a working stack?**

| | aarch.bio | aarch.science |
|---|---|---|
| **unit** | one tool per image (~503) | a curated multi-package env (29) |
| **example** | `samtools`, `bwa`, `bcftools` | `geospatial`, `dft`, `astro`, `single-cell` |
| **source** | bioconda / BioContainers | conda-forge |
| **you want it when** | "I need *this tool* in a pipeline step" | "I need a *working stack* I can import against" |

**2. The same software can be in both — in different shapes.** The split is by artifact,
not by subject or channel, so "it's biology, go to aarch.bio" and "it's conda-forge, come
here" are *both* wrong. `scanpy` is a single-tool image in aarch.bio **and** a component
of `single-cell` here, and both are correct:

| you want | go to |
|---|---|
| a drop-in for `quay.io/biocontainers/scanpy:…` | **aarch.bio** |
| a stack where scanpy, Seurat, leidenalg and anndata already work together | **aarch.science** ([`single-cell`](envs/single-cell.yaml)) |
| `samtools` for one pipeline step | **aarch.bio** |
| a verified geospatial / DFT / astronomy stack | **aarch.science** |

Neither project is the "bioinformatics one". aarch.bio ships biology *and* general tools
as single images; aarch.science ships biology *and* everything else as environments
([`single-cell`](envs/single-cell.yaml), [`neuroimaging`](envs/neuroimaging.yaml)). Full
reasoning in [DESIGN.md](DESIGN.md) under **D5**.

Still unsure? [Open an issue](https://github.com/playgroundlogic/aarchsci/issues/new) and
we'll tell you which project to use, or build it if neither does.

## Why

The geospatial stack (GDAL, PROJ, GEOS, rasterio, shapely, scikit-image) is an
ideal Graviton workload — measured ~2.5× cheaper per physical core than Intel.
But it often **won't assemble on arm64 via pip** (`No matching distribution found
for rasterio` — PyPI's arm64 wheel coverage for native-lib science is fragile).

The packages *do* exist on **conda-forge** for arm64 — the same stack solves
cleanly there (123 packages, verified). The gap is the same one
[aarch.bio](https://aarch.bio/) fills for bioconda tools:
**the capability is present, the delivery is broken.** aarch.science builds a
verified, signed arm64 container from the conda-forge packages so the work can run
native on Graviton.

## Use it

```bash
# pull a verified stack — native arm64, no account, no login
docker pull quay.io/aarchsci/geospatial:latest

# re-run the verification yourself — it ships inside every image
docker run --rm quay.io/aarchsci/geospatial:latest python /opt/aarchsci/smoke.py

# confirm it was built by this repo's CI, from source, logged in Sigstore/Rekor
cosign verify quay.io/aarchsci/geospatial:latest \
  --certificate-identity-regexp 'github.com/playgroundlogic/aarchsci' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Available environments

Each is a curated, version-pinned conda-forge env, built native arm64, verified
(assemble + import + functional smoke test), signed, and public. Tags: an immutable
`<date>.<HHMMSS>` build tag (`2026.09.07.041233`), a date (`2026.06.26`), a
content-addressed `s<lock-hash>`, and `latest`.

**If you pin, pin `@sha256:` or the `<date>.<HHMMSS>` tag — those are the only two
that never move.** `latest` moves every republish; `<date>` and `s<lock-hash>` move
if the same env is republished on the same UTC day with an unchanged package set.
`s<lock-hash>` in particular names a resolved package *set*, not an image, so two
rebuilds of the same set share the tag but are different digests. We learned this the
expensive way — before the immutable tag existed, four manifests lost all their tags
that way and were garbage-collected ([issue #13](https://github.com/playgroundlogic/aarchsci/issues/13)
has the full retention policy and the four dead digests).

**Two different digests are both "the" digest, so be explicit about which you mean.** Every
image here is published as an OCI image index, so there is an index digest *and* a
per-architecture digest, and they are different objects — for `pic:latest`, measured:

```
index (manifest list)   sha256:3299b36b70841b0a2fe1be96d92fe5a6ffa21b67b450bbac296f068afe9b719c
linux/arm64             sha256:e02db0b494ce602c6948104fe4a9085b6fc004611f501c48c27eed1874100b95
```

**Cosign signs the index digest**, which is what our build logs and release notes quote, so
that is the one to verify against. The **per-arch digest** is the one to pin if you want a
pull that resolves to exactly one platform with no index indirection. Neither is wrong and
neither moves; comparing one against the other just looks like a mismatch when it isn't.
(`docker buildx imagetools inspect <ref>` shows both.)

| Env | Pkgs | What's in it |
|-----|-----:|--------------|
| [`geospatial`](envs/geospatial.yaml) | 110 | gdal, proj, geos, rasterio, fiona, shapely, pyproj, scikit-image |
| [`earth-observation`](envs/earth-observation.yaml) | 246 | + xarray, dask, rioxarray, stackstac, pystac-client, odc-stac, netcdf4, zarr |
| [`geo-ml`](envs/geo-ml.yaml) | 367 | + scikit-learn, xgboost, lightgbm, geopandas, pysal, statsmodels, datashader, scipy-tests |
| [`climate`](envs/climate.yaml) | 268 | xarray/dask + flox, cartopy, cfgrib, eccodes, metpy, xesmf, esmpy, cdo, nco, gsw |
| [`pointcloud`](envs/pointcloud.yaml) | 234 | + pdal, python-pdal, laspy, richdem (LiDAR / DEM / terrain) |
| [`comp-chem`](envs/comp-chem.yaml) | 212 | rdkit, openbabel, openmm, mdanalysis, mdtraj, ase, pyscf, vina, cantera — **xtb removed 2026-10-18**, see below |
| [`dft`](envs/dft.yaml) | 236 | gpaw, siesta, **cp2k**, nwchem, quantum-espresso + sssp, ase, libxc, libvdwxc, ELPA, ScaLAPACK, OpenMPI, spglib, phonopy, pymatgen |
| [`md`](envs/md.yaml) | 246 | gromacs, lammps, ambertools, OpenMPI, mdanalysis, mdtraj, parmed |
| [`viz`](envs/viz.yaml) | 246 | paraview (`pvbatch`), vtk, mesa/llvmpipe, Xvfb, pillow — headless CPU rendering |
| [`r`](envs/r.yaml) | 328 | R 4.5 + tidyverse, data.table, arrow, sf, terra, glmnet, randomForest, caret, knitr/rmarkdown + pandoc, Rcpp |
| [`astro`](envs/astro.yaml) | 361 | astropy, photutils, sunpy, healpy, yt, regions, reproject, specutils, astroquery, rebound |
| [`fem-cfd`](envs/fem-cfd.yaml) | 131 | fenics-dolfinx, basix, ufl, PETSc/SLEPc (+py bindings), CalculiX, OpenMPI, mpi4py, HDF5/ADIOS2 |
| [`cfd-fv`](envs/cfd-fv.yaml) | 56 | SU2 (finite-volume CFD), OpenMPI, mpi4py — compressible/aerodynamic flow |
| [`optimization`](envs/optimization.yaml) | 64 | HiGHS, SCIP, CBC + highspy/PySCIPOpt — LP & mixed-integer programming |
| [`neuroimaging`](envs/neuroimaging.yaml) | 296 | AFNI, DIPY, nibabel, nilearn, nipype — volume-based fMRI & diffusion MRI |
| [`single-cell`](envs/single-cell.yaml) | 529 | Scanpy + Seurat (both ecosystems), anndata, leidenalg, igraph, umap, scikit-misc — single-cell transcriptomics |
| [`bayes`](envs/bayes.yaml) | 126 | CmdStan + cmdstanpy + ArviZ — Bayesian inference / MCMC (compiles models at runtime) |
| [`geoscience`](envs/geoscience.yaml) | 113 | ObsPy (seismology), MODFLOW 6 + FloPy (groundwater flow) |
| [`cp2k`](envs/cp2k.yaml) | 111 | **deprecated** — CP2K now ships inside [`dft`](envs/dft.yaml); kept as a transition alias |
| [`pathology`](envs/pathology.yaml) | 209 | OpenSlide + openslide-python, tifffile/imagecodecs, scikit-image, zarr, dask — whole-slide imaging |
| [`quantum`](envs/quantum.yaml) | 165 | Qiskit + Aer (CPU), qiskit-algorithms/optimization/machine-learning, openfermion, QuTiP — quantum circuit simulation |
| [`hep`](envs/hep.yaml) | 355 | ROOT, Pythia8, Geant4 (headless) + data, HepMC3, FastJet, LHAPDF, YODA, uproot, awkward — high-energy physics |
| [`pic`](envs/pic.yaml) | 167 | WarpX (electromagnetic particle-in-cell) + AMReX/pyAMReX, openPMD, yt — plasma physics, OpenMP-threaded |
| [`structbio`](envs/structbio.yaml) | 122 | cctbx + gemmi (two independent symmetry implementations), biopython — crystallography |
| [`popgen`](envs/popgen.yaml) | 247 | msprime + tskit, tszip, demes, scikit-allel — coalescent simulation / population genetics |
| [`tomography`](envs/tomography.yaml) | 88 | ASTRA (CPU) + TomoPy, scikit-image, tifffile — CT reconstruction |
| [`psi4`](envs/psi4.yaml) | 82 | Psi4 — correlated-wavefunction molecular quantum chemistry (HF, DFT, MP2, CC) |
| [`nlp`](envs/nlp.yaml) | 91 | spaCy + the packaged `en_core_web_sm` pipeline — NLP, no model download |
| [`gw`](envs/gw.yaml) | 216 | Bilby, LALSuite, PyCBC — gravitational-wave parameter estimation (python 3.13) |

`dft`, `md`, `fem-cfd` and `cfd-fv` are the MPI-parallel envs, so their verification goes
further than the others': the smoke tests run the same calculation serially and again under
`mpiexec -n 2` and fail unless the answers agree (`dft` on bulk-silicon DFT *and* on an
NWChem H2O SCF, `md` on a LAMMPS Lennard-Jones melt, `fem-cfd` on a Poisson solve whose
L2 error must match to 1e-10 across the domain decomposition, `cfd-fv` on an SU2 Euler
solve that must preserve free-stream to ~1e-14 on a distorted mesh under both). Run them
in parallel the same way:

```bash
docker run --rm quay.io/aarchsci/dft:latest mpiexec -n 4 python your_script.py
```

Twelve caveats worth knowing before you use them, all measured rather than assumed:

- **`comp-chem` images published before 2026-10-18 contain an xtb that returns WRONG
  FORCES.** Read this one if you have ever pinned `comp-chem`. conda-forge's linux-aarch64
  xtb computes wrong analytic gradients while its energies are exactly right — on xtb's
  own `test_gfn2` water case the gradient norm is **0.134752** against upstream's pinned
  **0.006457**, 21× too large, with one component of the wrong sign. All seven aarch64
  builds do this, so no pin avoids it. Energy-only work was unaffected; **geometry
  optimisation, frequencies and MD run with xtb from those images are not trustworthy and
  should be re-run.** xtb has been removed from `comp-chem:latest`, and digest or
  `<date>.<HHMMSS>` pins to older images keep the defective build, because those never
  move by design. Found by a user running upstream's unit-test geometry
  ([#46](https://github.com/playgroundlogic/aarchsci/issues/46)); our own gate checked only
  that the H₂ energy was negative, which the bad build passed. Tracked upstream as
  [conda-forge/xtb-feedstock#50](https://github.com/conda-forge/xtb-feedstock/issues/50).
- **Legacy time-zone names (`US/Pacific`, `US/Eastern`, …) were broken from 2026-10-08 to
  2026-10-18 in every env with pyarrow** — astro, climate, geo-ml, earth-observation,
  pathology, neuroimaging, popgen, single-cell. A regression this project introduced:
  moving the base to Debian 13 (for lammps's GLIBC_2.38) dropped those links into a
  separate `tzdata-legacy` package, and pyarrow reads `/usr/share/zoneinfo` rather than
  conda's tzdata, so `pa.timestamp("s", tz="US/Pacific")` raised `ArrowInvalid`. Fixed in
  the base image, and the build now fails if the links are ever missing again. Images in
  that window, pinned by digest, still have the bug. Found by running pandas' own test suite
  ([#45](https://github.com/playgroundlogic/aarchsci/issues/45)).

- **`gw` is on python 3.13, and `pycbc` is floored at 2.10 — both deliberate.** PyCBC has
  no py314 build on *any* platform (2.10.0 is py311–py313 on linux-64 too), so this is a
  python cap rather than an arm64 gap, and containing it in its own env is why `astro`
  stays on 3.14. The floor is the sharper point: **pycbc 2.8.2 cannot import its own
  waveform module** against current astropy — `pycbc.cosmology` does
  `from astropy.cosmology.core import CosmologyError`, and that module is gone in astropy
  8.0.1. A bare `import pycbc` succeeds while `pycbc.waveform`, `.filter` and
  `.conversions` are all dead, which is why the smoke test imports the submodules
  individually. Taking 2.10 costs one lalsuite minor (7.25 rather than 7.26) in exchange
  for a working PyCBC.
- **`nlp` ships the model, so it needs no network.** spaCy's documented install path is
  `python -m spacy download en_core_web_sm`, a runtime fetch; conda-forge packages the
  pipeline instead. Worth knowing what the small model lacks: it ships **no word vectors**
  (`nlp.vocab.vectors.shape` is `(0, 0)`), and `token.has_vector` returns True anyway
  because it falls back to context-sensitive tok2vec output. Similarity scores from this
  image are therefore not static-embedding similarities. A `md`/`lg` model would be a
  separate, larger addition.

- **`tomography`: ASTRA and TomoPy are verified to different depths, deliberately.** ASTRA
  is the verified engine — projections conserve the object's mass to 9.5e-07 and FBP error
  falls monotonically with the angle count. TomoPy is verified at **import plus its phantom
  generator** only, for two measured reasons: its reconstruction conventions don't match
  ASTRA's sinograms without scaling work that belongs in a recipe (best correlation 0.62,
  magnitudes ~1e4 off), and its multiprocessing path died with `ConnectionResetError` from
  a dead child — twice, including at `ncore=1` — while the machine was under load from a
  concurrent build, then passed at 64/128/256 px when idle. Intermittent-under-load is the
  worst property a build gate can have, so the gate doesn't rest on it. Use TomoPy's
  pipeline by all means; just know ASTRA is the part this image proves.
- **`popgen` ships without `stdpopsim`, and that is not an oversight.** Every release from
  0.2.0 on requires `slim`, which has **no conda-forge package on any platform**, so an
  unpinned spec silently resolves 2020-era **0.1.2** instead of failing. Shipping that
  under a name implying the current model catalog would be advertising a capability rather
  than providing one. `msprime`, `tskit`, `tszip`, `demes` and `scikit-allel` are all
  current. Note also that `msprime` on bioconda is stuck at 0.4.0 with zero aarch64 files —
  conda-forge is the right source, which a naive search gets wrong.

- **`pic` has no MPI, and that is the package's choice rather than ours.** conda-forge's
  WarpX is `nompi` on *every* architecture and has been since 21.03, so this is not an
  arm64 gap. We deliberately did **not** add openmpi/mpi4py to the spec: dressing a
  single-node code in an MPI stack is exactly what the rank-count checks in the other
  parallel envs exist to prevent. But **nompi is not serial** — the binaries are
  `warpx.{1d,2d,3d,rz,rcylinder,rsphere}.NOMPI.OMP.*` and WarpX reports
  `OMP initialized with N OMP threads`, so it scales across a Graviton's cores with
  `OMP_NUM_THREADS`, just not across nodes. The smoke test asserts the thread count, so
  the claim is earned rather than assumed. Also: `pywarpx.__version__` is `None` and the
  banner prints `WarpX (Unknown)` — get the version from conda metadata, not the binary.

- **`hep`: Geant4 is here as a library and its data, not as something you can drive from
  Python.** conda-forge ships **no working Python bindings for Geant4 in any variant** —
  the `py*` builds install *zero* files into `site-packages` (both `Geant4` and
  `geant4_pybind` raise `ModuleNotFoundError`) while still pinning `python_abi`, so this
  image takes the newer, headless `noqt_*` build, which at least declares no interpreter
  coupling for an API that does not exist. You get `geant4-config`, the shared libraries,
  the headers and all twelve physics datasets wired through their `G4*DATA` variables —
  everything a C++ application needs. You do **not** get a simulation you can run with
  `python`, and this project does not compile from source, so don't expect one. ROOT,
  Pythia8, uproot and the jet/PDF layer are fully usable from Python.
  **Second `hep` note, on precision:** `TMath`'s special functions are not uniformly
  double precision, and the split is inside one namespace. Measured against
  `scipy.special` over x ∈ [0.1, 10] at 991 points — `Erf`, `Erfc` and `LnGamma` are
  **bit-identical** at every point, while the Bessel functions are single-precision-grade
  polynomial approximations: `BesselI0` 3.3e-08 and `BesselI1` 2.6e-08 relative,
  `BesselJ0`/`J1` ~4.9e-09 absolute (their *relative* error reaches 6e-05 near J0's zero
  at x≈2.405, where relative error is the wrong metric). Fine for histogram-scale work,
  not for anything that needs the last few digits — use `scipy.special` there. Thanks to
  [#34](https://github.com/playgroundlogic/aarchsci/issues/34) for spotting this.
  `hep` is also by far the largest image in the catalog: 8.95 GB unpacked against
  1.77 GB for `quantum` and 1.93 GB for `pic`, so pull time will dominate short jobs.

- **`dft`, on NWChem's ARMCI network:** conda-forge ships two arm64 runtime variants and
  this image pins the two-sided one (`mpi_ts`). Measured on a container's default 64 MB
  `/dev/shm`: `mpi_ts` runs clean on 1, 2 and 4 ranks with identical energies, whereas
  `mpi_pr` (progress ranks) *aborts* on 1 rank, reports only `nproc = 1` on 2 (one rank
  becomes a data server), and dies on 3 with `check_devshm: /dev/shm out of space`.
  Upstream recommends `mpi_pr` for large multi-node runs, so that would be a deliberate
  second image, not something to hope the resolver picks.
- **`md`:** conda-forge's gromacs for linux-aarch64 is built `ARM_NEON_ASIMD`, so it
  uses NEON and leaves SVE/SVE2 idle on Graviton 3 and later. The binary is `gmx_mpi`
  (not `gmx`), and `bin/gmx_mpi` is a wrapper that picks the SIMD build at run time.
  `pmemd` is not included — it ships only under the paid Amber licence.
- **`viz`:** ParaView needs a display even though it needs no GPU. conda-forge has no
  OSMesa at all and EGL has no device to bind to, so rendering goes through GLX against
  a virtual X server. `Xvfb` is in the image; start it and set `DISPLAY` (see
  [`envs/viz.smoke.py`](envs/viz.smoke.py) for the exact recipe the tests use).
- **`fem-cfd`, two things.** It **compiles C at runtime**: FFCx generates and builds a
  C extension for every variational form on first use, which is why `gcc` is a runtime
  dependency of `fenics-dolfinx` rather than a build-time one. Don't strip the toolchain,
  and expect the first solve in a session to pay a JIT cost the rest don't. Separately,
  its BLAS is **NVPL** (NVIDIA Performance Libraries), not OpenBLAS — the only env in the
  catalog where that's true, and nothing in the spec asks for it; the solver picks it. It
  is CPU-only Arm math, needs no GPU, and its `arm-variant * sbsa` dependency is a
  platform-class marker that every Graviton satisfies. The smoke test checks dgemm and a
  symmetric eigensolve against exact answers so the choice is verified, not assumed.
- **`astro` is deliberately offline.** astropy will fetch IERS earth-orientation tables
  over the network given the chance; the smoke test sets
  `astropy.utils.iers.conf.auto_download = False` and proves the bundled tables are
  correct instead (TAI−UTC = exactly 37 s in 2020). The image inherits nothing from that
  setting, so if you need sub-millisecond UT1 for recent dates, re-enable auto-download
  yourself. `astroquery` is included but is the one package verified by import alone —
  its job is querying remote archives, and a smoke test that needs the internet isn't one.

Want another? [Request an env](https://github.com/playgroundlogic/aarchsci/issues/new?template=request-env.yml).
Known arm64 gaps and why: [GAPS.md](GAPS.md).

### On HPC, without a Docker daemon

Clusters have no Docker daemon, so the runtime there is Apptainer. That path is now
verified on real Graviton hardware — a c8g (Graviton4) host, using the pinned runtime
from the [`apptainer`](envs/apptainer.yaml) env (42 pkgs), rootless, no suid:

```bash
apptainer build geospatial.sif docker://quay.io/aarchsci/geospatial:latest
apptainer run geospatial.sif python /opt/aarchsci/smoke.py
```

Use `run`, not `exec`. `run` goes through the image's entrypoint, which sources conda's
`etc/conda/activate.d/*.sh`; `exec` skips it. When this was first measured (2026-09-04)
that difference cost seven variables — `CONDA_PREFIX`, `CONDA_DEFAULT_ENV`, `CONDA_SHLVL`,
`CONDA_PROMPT_MODIFIER`, `GSETTINGS_SCHEMA_DIR` (+ its backup) and `XML_CATALOG_FILES` —
plus one `PATH` entry (`/opt/conda/condabin`), and none of it mattered: `geospatial`, `dft`
and `md` all passed under `exec`, `run` **and** `run --cleanenv`, including `dft`/`md`'s
2-rank MPI legs (`dft` reproduced its serial bulk-Si energy to 6e-08 eV under Apptainer).
The other twelve are untested under Apptainer.

**That "none of it mattered" no longer holds for `dft`, and the reason is worth stating
plainly:** adding `nwchem` made `run` load-bearing rather than merely advisable.
conda-forge's nwchem binary has the *feedstock build directory* compiled in as its default
basis-set path and relies on `etc/conda/activate.d/nwchem_env.sh` to set
`NWCHEM_BASIS_LIBRARY`/`NWCHEM_NWPW_LIBRARY` from `$CONDA_PREFIX`. Skip activation and
nwchem exits 255 hunting for `sto-3g` under
`/home/conda/feedstock_root/build_artifacts/...` (measured, not inferred). So `dft` under
`apptainer exec` now genuinely breaks — the smoke test asserts that variable precisely so
this cannot regress unnoticed. Use `run`.

**As of 2026-10-05 `dft` has a second reason to need `run`, and this one costs you the
parallelism rather than an error you can't miss.** gpaw 26.7 made its C MPI backend
opt-in (upstream's own interface is a `gpaw python` wrapper), so the image exports
`GPAW_MPI_BACKEND=cgpaw` from `activate.d`. With it, `mpiexec -n N python script.py` works
exactly as documented above. Without activation, gpaw falls back to a serial communicator
and `mpiexec -n 2 python` exits 1 — measured. The smoke test asserts the variable directly
([issue #19](https://github.com/playgroundlogic/aarchsci/issues/19)). Use `run`.

**`hep` is the fourth env that needs `run`** (after `dft`, `bayes` and `cp2k`), and it is
worth knowing *which* of its failures without activation is the dangerous one. Measured on
the published image with activation skipped:

- **ROOT still works.** Histograms, fits and the uproot round-trip all pass without
  `ROOTSYS` set.
- **Geant4 loses all twelve `G4*DATA` variables** — unset, so an application finds no
  physics data. Loud, and the smoke test names it.
- **Pythia8 aborts with a misleading error**, which is the one that will cost you an hour:
  `PYTHIA Abort from Pythia::checkVersion: unmatched version numbers : in code 8.312 but
  in XML 0.000`. Nothing in that says the real cause, which is simply that `PYTHIA8DATA`
  (the XML data path, set from `activate.d`) is unset. The smoke test now asserts that
  variable *before* calling `init()`, so the failure reads as what it is.

Use `run`.

Also measured on that host, so you don't have to find out the hard way:

- **The writable-overlay path works** (`--writable-tmpfs`, via `fuse-overlayfs`).
- **`fusermount3` is not required to run, but Apptainer does try to call it on cleanup.**
  Upstream's admin guide says Apptainer "does not use `fusermount` in any mode"; that is
  true of mounting and false of unmounting. With no `fusermount3` on the host you get a
  loud `Cleanup error: ... Failed to call 'fusermount3'` after a **successful** run. It is
  cosmetic — every exit code above was 0 — but it looks like a failure. `--unsquash`
  avoids it entirely by unpacking to a sandbox instead of mounting squashfs.
- **Provenance survives the conversion.** `apptainer inspect` still shows
  `org.opencontainers.image.revision` and `.source`, so a SIF can be traced back to the
  spec and builder commit it came from.

The `apptainer` env itself is verified but **not published** — it is a runtime, not a
science stack, and you install it with conda rather than pulling it. Full measurements:
[issue #6](https://github.com/playgroundlogic/aarchsci/issues/6).

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
