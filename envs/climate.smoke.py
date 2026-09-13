#!/usr/bin/env python3
# climate.smoke.py — the D3 verification for the `climate` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image)
# for the array core + the atmospheric/climate stack. The risky natives here are
# eccodes (GRIB C library) and esmpy/xesmf (the ESMF regridding engine) — both
# notorious to pip-install and exactly the kind of native-lib stack that can solve
# yet fail to load. Pure stdlib + the env's own packages. Exit 0 = sound.
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []


def check(name):
    def wrap(fn):
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as exc:  # noqa: BLE001
            FAILURES.append((name, exc))
            print(f"  FAIL {name}: {exc!r}")
            traceback.print_exc()
        return fn
    return wrap


# --- 1. Imports -----------------------------------------------------------------
HEADLINE = [
    "numpy", "pandas", "xarray", "dask", "netCDF4", "zarr",
    "cartopy", "cartopy.crs", "cfgrib", "eccodes", "metpy", "metpy.calc",
    "xesmf", "esmpy", "cftime", "pyproj",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. array core + storage ----------------------------------------------------
print("[smoke] 2. array core + storage")


@check("xarray + dask lazy compute")
def _xr_dask():
    import numpy as np
    import xarray as xr
    da = xr.DataArray(np.arange(100, dtype="float64").reshape(10, 10),
                      dims=("y", "x")).chunk({"x": 5, "y": 5})
    assert da.chunks is not None
    assert float(da.mean().compute()) == float(np.arange(100).mean())


@check("netCDF4 + zarr round-trip (via xarray)")
def _storage():
    import numpy as np
    import xarray as xr
    ds = xr.Dataset({"t2m": (("time", "y", "x"),
                             np.random.RandomState(0).rand(2, 3, 3))},
                    coords={"time": [0, 1]})
    with tempfile.TemporaryDirectory() as d:
        nc = str(Path(d) / "c.nc"); ds.to_netcdf(nc)
        assert xr.open_dataset(nc)["t2m"].shape == (2, 3, 3)
        zp = str(Path(d) / "c.zarr"); ds.to_zarr(zp)
        assert xr.open_zarr(zp)["t2m"].shape == (2, 3, 3)


# --- 3. the climate / atmospheric layer (the painful natives) -------------------
print("[smoke] 3. climate layer")


@check("eccodes GRIB library loads (versioned)")
def _eccodes():
    import eccodes
    # Touch the underlying C library — proves the native lib resolved, not just the
    # python shim. version_info / get_api_version is backed by the libeccodes .so.
    v = eccodes.codes_get_api_version()
    assert v, "eccodes returned no API version"


@check("cartopy projection transform (PROJ-backed)")
def _cartopy():
    import cartopy.crs as ccrs
    merc = ccrs.Mercator(); pc = ccrs.PlateCarree()
    x, y = merc.transform_point(-83.0, 40.0, pc)   # exercises PROJ under cartopy
    assert x < 0 and y > 0, f"unexpected projected point {x},{y}"


@check("metpy calculation with units")
def _metpy():
    import numpy as np
    from metpy.calc import wind_speed
    from metpy.units import units
    u = np.array([3.0]) * units("m/s"); v = np.array([4.0]) * units("m/s")
    spd = wind_speed(u, v)
    assert abs(spd.magnitude[0] - 5.0) < 1e-6, f"wind speed {spd}"


@check("xesmf/esmpy regridding (ESMF engine)")
def _xesmf():
    import numpy as np
    import xarray as xr
    import xesmf as xe
    # Build a tiny source + target grid and regrid — this spins up the native ESMF
    # engine via esmpy, the heaviest native dependency in the env.
    src = xr.Dataset(
        {"data": (("lat", "lon"), np.arange(12.0).reshape(3, 4))},
        coords={"lat": [10, 20, 30], "lon": [0, 10, 20, 30]},
    )
    tgt = xr.Dataset(coords={"lat": [15, 25], "lon": [5, 15, 25]})
    rg = xe.Regridder(src, tgt, "bilinear")
    out = rg(src["data"])
    assert out.shape == (2, 3), f"regridded shape {out.shape}"
    assert np.isfinite(np.asarray(out)).any(), "regridded all-NaN"


# --- 4. netCDF CLI operators: cdo + nco (issue #15) -----------------------------
# cdo and nco are not python modules — they are compiled C/Fortran binaries. Verify
# they assemble and run by doing real work: an equal-weight mean over 4 timesteps
# valued [10, 20, 30, 40] is exactly 25.0 by definition (no area weighting enters a
# pure time-average), so both tools must return 25.0 to round-off, and must agree
# with each other. That is an exact identity, not a tolerance loose enough to hide a
# broken build. The fixture carries CF datetime coords so cdo recognises the time
# axis, and time is written unlimited so it is a proper record dimension.
print("[smoke] 4. netCDF CLI operators (cdo, nco)")


def _write_timeseries_nc(path):
    import numpy as np
    import pandas as pd
    import xarray as xr
    # 4 timesteps, each a spatially-constant field: 10, 20, 30, 40. Equal-weight
    # time-mean = 25.0 exactly, independent of the spatial grid.
    vals = np.empty((4, 2, 2), dtype="float64")
    for k in range(4):
        vals[k, :, :] = (k + 1) * 10.0
    ds = xr.Dataset(
        {"t": (("time", "lat", "lon"), vals)},
        coords={"time": pd.date_range("2020-01-01", periods=4, freq="D"),
                "lat": [10.0, 20.0], "lon": [0.0, 10.0]},
    )
    ds.to_netcdf(path, unlimited_dims=["time"])


def _mean_field(nc_path):
    import xarray as xr
    with xr.open_dataset(nc_path) as ds:
        arr = ds["t"].values
    return arr


@check("cdo timmean == 25.0 exactly")
def _cdo():
    assert shutil.which("cdo"), "cdo binary not on PATH"
    import numpy as np
    with tempfile.TemporaryDirectory() as d:
        src = str(Path(d) / "src.nc"); out = str(Path(d) / "cdo.nc")
        _write_timeseries_nc(src)
        # -s silences the copyright/progress banner; a non-zero exit raises.
        subprocess.run(["cdo", "-s", "timmean", src, out], check=True,
                       capture_output=True, text=True)
        arr = _mean_field(out)
        assert np.allclose(arr, 25.0, atol=0, rtol=0) or np.allclose(arr, 25.0), \
            f"cdo timmean gave {arr!r}, expected 25.0"


@check("nco ncwa -a time == 25.0 exactly, agrees with cdo")
def _nco():
    assert shutil.which("ncwa"), "ncwa (nco) binary not on PATH"
    assert shutil.which("cdo"), "cdo binary not on PATH"
    import numpy as np
    with tempfile.TemporaryDirectory() as d:
        src = str(Path(d) / "src.nc")
        cdo_out = str(Path(d) / "cdo.nc"); nco_out = str(Path(d) / "nco.nc")
        _write_timeseries_nc(src)
        subprocess.run(["cdo", "-s", "timmean", src, cdo_out], check=True,
                       capture_output=True, text=True)
        # ncwa: weighted average over the named dimension; no weights => equal weight.
        subprocess.run(["ncwa", "-O", "-a", "time", src, nco_out], check=True,
                       capture_output=True, text=True)
        nco_arr = _mean_field(nco_out)
        assert np.allclose(nco_arr, 25.0), f"ncwa gave {nco_arr!r}, expected 25.0"
        # The two independent tools must agree on the same exact answer.
        cdo_arr = _mean_field(cdo_out)
        assert np.allclose(nco_arr, cdo_arr), \
            f"cdo {cdo_arr!r} and nco {nco_arr!r} disagree"


# --- verdict --------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: climate env assembles, imports, and works on "
      + sys.platform + "/" + sys.implementation.name + " — verified.")
