# aarch.science

**Verified, signed, native arm64 (aarch64) containers for the scientific-computing
stack** — geospatial / earth-observation first. For Apple Silicon and AWS Graviton.

Sister project to [aarchbio](https://github.com/playgroundlogic/aarchbio) (which
does this for bioinformatics / BioContainers). aarch.science covers the layer
aarchbio scopes out: the **conda-forge** scientific stack.

> **Status:** live. **10 verified, signed, public env images** on
> [`quay.io/aarchsci`](https://quay.io/organization/aarchsci), a daily reconciler,
> and a site at **[aarch.science](https://aarch.science/)**.

## Why

The geospatial stack (GDAL, PROJ, GEOS, rasterio, shapely, scikit-image) is an
ideal Graviton workload — measured ~2.5× cheaper per physical core than Intel.
But it often **won't assemble on arm64 via pip** (`No matching distribution found
for rasterio` — PyPI's arm64 wheel coverage for native-lib science is fragile).

The packages *do* exist on **conda-forge** for arm64 — the same stack solves
cleanly there (123 packages, verified). The gap is the same one
[aarchbio](https://github.com/playgroundlogic/aarchbio) fills for bioinformatics:
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
(assemble + import + functional smoke test), signed, and public. Tags: `latest`,
a date (`2026.06.26`), and a content-addressed `s<lock-hash>`.

| Env | Pkgs | What's in it |
|-----|-----:|--------------|
| [`geospatial`](envs/geospatial.yaml) | 125 | gdal, proj, geos, rasterio, fiona, shapely, pyproj, scikit-image |
| [`earth-observation`](envs/earth-observation.yaml) | 261 | + xarray, dask, rioxarray, stackstac, pystac-client, odc-stac, netcdf4, zarr |
| [`geo-ml`](envs/geo-ml.yaml) | 381 | + scikit-learn, xgboost, lightgbm, geopandas, pysal, statsmodels, datashader |
| [`climate`](envs/climate.yaml) | 249 | xarray/dask + cartopy, cfgrib, eccodes, metpy, xesmf, esmpy |
| [`pointcloud`](envs/pointcloud.yaml) | 245 | + pdal, python-pdal, laspy, richdem (LiDAR / DEM / terrain) |
| [`comp-chem`](envs/comp-chem.yaml) | 210 | rdkit, openbabel, openmm, mdanalysis, mdtraj, ase, pyscf, xtb, vina |
| [`dft`](envs/dft.yaml) | 237 | gpaw, siesta, psi4, nwchem, ase, libxc, libvdwxc, ELPA, ScaLAPACK, OpenMPI, spglib, phonopy, pymatgen |
| [`md`](envs/md.yaml) | 227 | gromacs, lammps, ambertools, OpenMPI, mdanalysis, mdtraj, parmed |
| [`viz`](envs/viz.yaml) | 245 | paraview (`pvbatch`), vtk, mesa/llvmpipe, Xvfb, pillow — headless CPU rendering |
| [`r`](envs/r.yaml) | 328 | R 4.5 + tidyverse, data.table, arrow, sf, terra, glmnet, randomForest, caret, knitr/rmarkdown + pandoc, Rcpp |

`dft` and `md` are the MPI-parallel envs, so their verification goes further than the
others': the smoke tests run the same calculation serially and again under
`mpiexec -n 2` and fail unless the answers agree (`dft` on bulk-silicon DFT *and* on an
NWChem H2O SCF, `md` on a LAMMPS Lennard-Jones melt). Run them in parallel the same way:

```bash
docker run --rm quay.io/aarchsci/dft:latest mpiexec -n 4 python your_script.py
```

Three caveats worth knowing before you use them, all measured rather than assumed:

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
The other six are untested under Apptainer.

**That "none of it mattered" no longer holds for `dft`, and the reason is worth stating
plainly:** adding `nwchem` made `run` load-bearing rather than merely advisable.
conda-forge's nwchem binary has the *feedstock build directory* compiled in as its default
basis-set path and relies on `etc/conda/activate.d/nwchem_env.sh` to set
`NWCHEM_BASIS_LIBRARY`/`NWCHEM_NWPW_LIBRARY` from `$CONDA_PREFIX`. Skip activation and
nwchem exits 255 hunting for `sto-3g` under
`/home/conda/feedstock_root/build_artifacts/...` (measured, not inferred). So `dft` under
`apptainer exec` now genuinely breaks — the smoke test asserts that variable precisely so
this cannot regress unnoticed. Use `run`.

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
