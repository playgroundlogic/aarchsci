# aarch.science

**Verified, signed, native arm64 (aarch64) containers for the scientific-computing
stack** — geospatial / earth-observation first. For Apple Silicon and AWS Graviton.

Sister project to [aarchbio](https://github.com/playgroundlogic/aarchbio) (which
does this for bioinformatics / BioContainers). aarch.science covers the layer
aarchbio scopes out: the **conda-forge** scientific stack.

> **Status:** live. **9 verified, signed, public env images** on
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
| [`geospatial`](envs/geospatial.yaml) | 123 | gdal, proj, geos, rasterio, fiona, shapely, pyproj, scikit-image |
| [`earth-observation`](envs/earth-observation.yaml) | 236 | + xarray, dask, rioxarray, stackstac, pystac-client, odc-stac, netcdf4, zarr |
| [`geo-ml`](envs/geo-ml.yaml) | 378 | + scikit-learn, xgboost, lightgbm, geopandas, pysal, statsmodels, datashader |
| [`climate`](envs/climate.yaml) | 247 | xarray/dask + cartopy, cfgrib, eccodes, metpy, xesmf, esmpy |
| [`pointcloud`](envs/pointcloud.yaml) | 287 | + pdal, python-pdal, laspy, richdem (LiDAR / DEM / terrain) |
| [`comp-chem`](envs/comp-chem.yaml) | 210 | rdkit, openbabel, openmm, mdanalysis, mdtraj, ase, pyscf, xtb, vina |
| [`dft`](envs/dft.yaml) | 225 | gpaw, siesta, psi4, ase, libxc, libvdwxc, ELPA, ScaLAPACK, OpenMPI, spglib, phonopy, pymatgen |
| [`md`](envs/md.yaml) | 227 | gromacs, lammps, ambertools, OpenMPI, mdanalysis, mdtraj, parmed |
| [`viz`](envs/viz.yaml) | 245 | paraview (`pvbatch`), vtk, mesa/llvmpipe, Xvfb, pillow — headless CPU rendering |

`dft` and `md` are the MPI-parallel envs, so their verification goes further than the
others': the smoke tests run the same calculation serially and again under
`mpiexec -n 2` and fail unless the answers agree (`dft` on bulk-silicon DFT, `md` on a
LAMMPS Lennard-Jones melt). Run them in parallel the same way:

```bash
docker run --rm quay.io/aarchsci/dft:latest mpiexec -n 4 python your_script.py
```

Two caveats worth knowing before you use them, both measured rather than assumed:

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

There is also an [`apptainer`](envs/apptainer.yaml) env (42 pkgs) — the Apptainer runtime
itself, pinned to an exact build and verified to build and read back a native arm64 SIF.
It exists because clusters have no Docker daemon, and because a future check that these
images run under `apptainer exec` needs a known runtime to run them with.

It is **not published, and there are no consumption instructions here yet, on purpose.**
Converting an image to SIF works and the `geospatial` smoke test passes under
`apptainer exec` on aarch64 — but that was measured on Apple silicon, not Graviton, and
Apptainer does **not** run conda's `activate.d` hooks, so some envs lose environment
variables their packages expect. Publishing a recipe before that is settled on real
hardware would be exactly the unearned "verified" this project refuses elsewhere. Progress
and measurements: [issue #6](https://github.com/playgroundlogic/aarchsci/issues/6).

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
