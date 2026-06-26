#!/usr/bin/env python3
# earth-observation.smoke.py — the D3 verification for the `earth-observation` env.
#
# Same contract as geospatial.smoke.py (assemble + import + do real work, inside the
# built arm64 image), extended with the EO layer: labeled n-d arrays (xarray), lazy
# parallel compute (dask), CRS-aware raster<->array (rioxarray), STAC clients
# (pystac-client/stackstac/odc-stac), and the netCDF/Zarr storage backends. A green
# solve with a red import is exactly the trap this guards — pip "solved" too.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound on this arch.
import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []


def check(name):
    """Run a named check, record failure instead of aborting, so one run reports
    every problem rather than stopping at the first."""
    def wrap(fn):
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as exc:  # noqa: BLE001 - we want every failure surfaced
            FAILURES.append((name, exc))
            print(f"  FAIL {name}: {exc!r}")
            traceback.print_exc()
        return fn
    return wrap


# --- 1. Imports: do the native extension modules even load on this arch? --------
HEADLINE = [
    "numpy",
    "pyproj",
    "shapely",
    "fiona",
    "rasterio",
    "skimage",          # scikit-image
    "osgeo.gdal",       # gdal python bindings
    "xarray",
    "dask",
    "rioxarray",
    "stackstac",
    "pystac_client",
    "odc.stac",         # odc-stac
    "netCDF4",
    "zarr",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. geospatial-base functional (the same native backends EO sits on) --------
print("[smoke] 2. geospatial base")


@check("pyproj reproject WGS84->WebMercator")
def _pyproj():
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = t.transform(-83.0, 40.0)
    assert -9.3e6 < x < -9.2e6, f"unexpected easting {x}"
    assert 4.8e6 < y < 4.9e6, f"unexpected northing {y}"


@check("shapely GEOS geometry ops")
def _shapely():
    from shapely.geometry import Point
    poly = Point(0, 0).buffer(1.0)
    assert 3.0 < poly.area < 3.2, f"buffer area {poly.area}"


@check("osgeo.gdal driver registry")
def _gdal():
    from osgeo import gdal
    gdal.UseExceptions()
    assert gdal.GetDriverByName("GTiff") is not None, "GTiff driver missing"


# --- 3. EO layer: arrays, lazy compute, CRS-aware raster, storage ---------------
print("[smoke] 3. earth-observation layer")


@check("xarray + dask lazy compute")
def _xr_dask():
    import numpy as np
    import xarray as xr
    # A chunked DataArray is dask-backed and only computes on demand.
    da = xr.DataArray(
        np.arange(100, dtype="float64").reshape(10, 10),
        dims=("y", "x"),
    ).chunk({"x": 5, "y": 5})
    assert da.chunks is not None, "expected a dask-backed (chunked) array"
    total = float(da.sum().compute())   # exercises the dask scheduler
    assert total == float(np.arange(100).sum()), f"lazy sum wrong: {total}"


@check("rioxarray CRS-aware raster round-trip")
def _rioxarray():
    import numpy as np
    import rioxarray  # noqa: F401 - registers the .rio accessor
    import xarray as xr
    from affine import Affine

    data = np.arange(16, dtype="float32").reshape(1, 4, 4)
    da = xr.DataArray(data, dims=("band", "y", "x"),
                      coords={"band": [1], "y": [40.03, 40.02, 40.01, 40.0],
                              "x": [-83.0, -82.99, -82.98, -82.97]})
    da.rio.write_crs("EPSG:4326", inplace=True)
    da.rio.write_transform(Affine(0.01, 0, -83.0, 0, -0.01, 40.03), inplace=True)
    assert da.rio.crs.to_epsg() == 4326, f"crs lost: {da.rio.crs}"

    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "eo.tif")
        da.rio.to_raster(path)                       # rioxarray -> GDAL GTiff
        import rioxarray as rxr
        back = rxr.open_rasterio(path)               # read it back through GDAL
        assert back.rio.crs.to_epsg() == 4326, "crs not preserved on round-trip"
        assert int(back.sum()) == int(data.sum()), "raster data round-trip mismatch"
        # Reproject the array to WebMercator — the real EO prep operation.
        reproj = back.rio.reproject("EPSG:3857")
        assert reproj.rio.crs.to_epsg() == 3857, "reproject did not set target CRS"


@check("netCDF4 + zarr storage round-trip (via xarray)")
def _storage():
    import numpy as np
    import xarray as xr
    ds = xr.Dataset(
        {"t2m": (("time", "y", "x"), np.random.RandomState(0).rand(2, 3, 3))},
        coords={"time": [0, 1]},
    )
    with tempfile.TemporaryDirectory() as d:
        nc = str(Path(d) / "eo.nc")
        ds.to_netcdf(nc)                              # netCDF4 backend
        rt = xr.open_dataset(nc)
        assert rt["t2m"].shape == (2, 3, 3), "netcdf round-trip shape mismatch"
        rt.close()

        zp = str(Path(d) / "eo.zarr")
        ds.to_zarr(zp)                                # zarr backend
        rz = xr.open_zarr(zp)
        assert rz["t2m"].shape == (2, 3, 3), "zarr round-trip shape mismatch"


@check("STAC clients construct (pystac-client / stackstac / odc-stac)")
def _stac():
    # No network in the build — just prove the clients/objects instantiate and the
    # native deps (rasterio/gdal under stackstac/odc-stac) are wired up.
    from pystac_client import Client  # noqa: F401
    import stackstac                  # noqa: F401
    import odc.stac                   # noqa: F401
    import pystac
    # Build a minimal in-memory STAC Item (no I/O) to confirm the model works.
    from datetime import datetime, timezone
    item = pystac.Item(
        id="probe", geometry={"type": "Point", "coordinates": [-83.0, 40.0]},
        bbox=[-83.0, 40.0, -83.0, 40.0],
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc), properties={},
    )
    assert item.id == "probe"


# --- verdict --------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): "
          + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: earth-observation env assembles, imports, and works on "
      + sys.platform + "/" + (sys.implementation.name) + " — verified.")
