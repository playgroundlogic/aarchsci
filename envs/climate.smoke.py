#!/usr/bin/env python3
# climate.smoke.py — the D3 verification for the `climate` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image)
# for the array core + the atmospheric/climate stack. The risky natives here are
# eccodes (GRIB C library) and esmpy/xesmf (the ESMF regridding engine) — both
# notorious to pip-install and exactly the kind of native-lib stack that can solve
# yet fail to load. Pure stdlib + the env's own packages. Exit 0 = sound.
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


# --- verdict --------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: climate env assembles, imports, and works on "
      + sys.platform + "/" + sys.implementation.name + " — verified.")
