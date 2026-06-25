#!/usr/bin/env python3
# geospatial.smoke.py — the D3 verification for the `geospatial` env.
#
# THIS is what earns the word "verified". aarchbio could trust a solve-time check
# ("an arm64 build exists"). aarch.science cannot: the fieldwork failure was an env
# that *solved* on pip yet would not *assemble/import* on arm64. So this test runs
# INSIDE the built arm64 image and proves three escalating things:
#   1. every headline package IMPORTS (native libs load, ABI matches),
#   2. each one does REAL WORK touching its native backend (GDAL/PROJ/GEOS),
#   3. the pieces interoperate (write a raster, reproject its corner, read it back).
#
# A green solve with a red import is exactly the trap this guards. Exit 0 = the
# image is functionally sound on this arch; any failure exits non-zero and the
# builder refuses to tag/publish.
#
# Pure stdlib + the env's own packages. Runs under the image's python (no uv here);
# writes only to a temp dir it cleans up.
import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []


def check(name):
    """Decorator: run a named check, record failure instead of aborting, so one
    run reports every problem rather than stopping at the first."""
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
    "skimage",        # scikit-image
    "osgeo.gdal",     # gdal's python bindings
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. Per-package functional work touching the native backend -----------------
print("[smoke] 2. functional")


@check("pyproj reproject WGS84->WebMercator")
def _pyproj():
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = t.transform(-83.0, 40.0)  # near Columbus, OH (the fieldwork AOI)
    # WebMercator easting for -83 deg lon is ~ -9.24e6; sanity-bound it.
    assert -9.3e6 < x < -9.2e6, f"unexpected easting {x}"
    assert 4.8e6 < y < 4.9e6, f"unexpected northing {y}"


@check("shapely GEOS geometry ops")
def _shapely():
    from shapely.geometry import Point, Polygon
    poly = Point(0, 0).buffer(1.0)          # exercises GEOS
    assert poly.area > 3.0 and poly.area < 3.2, f"buffer area {poly.area}"
    tri = Polygon([(0, 0), (1, 0), (0, 1)])
    assert abs(tri.area - 0.5) < 1e-9
    assert poly.intersects(tri)


@check("scikit-image transform")
def _skimage():
    import numpy as np
    from skimage.transform import resize
    img = np.arange(64, dtype="float64").reshape(8, 8)
    out = resize(img, (4, 4), anti_aliasing=True)
    assert out.shape == (4, 4)


@check("osgeo.gdal driver registry")
def _gdal_drivers():
    from osgeo import gdal
    gdal.UseExceptions()
    assert gdal.GetDriverByName("GTiff") is not None, "GTiff driver missing"


# --- 3. Interop: the real fieldwork shape — write a raster, read it, reproject ---
print("[smoke] 3. interop (write raster -> read back -> gdal open -> reproject)")


@check("rasterio<->gdal raster round-trip + CRS")
def _raster_roundtrip():
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from osgeo import gdal

    data = (np.arange(16, dtype="uint8").reshape(4, 4))
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "tiny.tif")
        transform = from_origin(-83.0, 40.0, 0.01, 0.01)  # lon/lat origin, 0.01deg px
        with rasterio.open(
            path, "w", driver="GTiff", height=4, width=4, count=1,
            dtype="uint8", crs="EPSG:4326", transform=transform,
        ) as dst:
            dst.write(data, 1)

        # Read it back with rasterio and verify the pixels + georeferencing survived.
        with rasterio.open(path) as src:
            assert src.crs.to_epsg() == 4326, f"crs lost: {src.crs}"
            back = src.read(1)
            assert (back == data).all(), "raster data round-trip mismatch"
            bounds = src.bounds

        # Open the SAME file straight through GDAL — proves the native driver, not
        # just rasterio's wrapper, reads what we wrote.
        ds = gdal.Open(path)
        assert ds is not None, "gdal.Open returned None"
        assert ds.RasterXSize == 4 and ds.RasterYSize == 4
        assert ds.GetRasterBand(1).Checksum() >= 0

    # Reproject the raster's NW corner with pyproj — the prep step fieldwork needs.
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    ex, ny = t.transform(bounds.left, bounds.top)
    assert ex < 0 and ny > 0, f"reprojected corner looks wrong: {ex},{ny}"


@check("fiona vector round-trip (GeoJSON)")
def _fiona():
    import fiona
    from fiona.crs import CRS
    schema = {"geometry": "Point", "properties": {"id": "int"}}
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "pts.geojson")
        with fiona.open(
            path, "w", driver="GeoJSON", schema=schema, crs=CRS.from_epsg(4326),
        ) as dst:
            dst.write({"geometry": {"type": "Point", "coordinates": (-83.0, 40.0)},
                       "properties": {"id": 1}})
        with fiona.open(path) as src:
            feats = list(src)
            assert len(feats) == 1, f"expected 1 feature, got {len(feats)}"
            assert feats[0]["properties"]["id"] == 1


# --- verdict --------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): "
          + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: geospatial env assembles, imports, and works on "
      + sys.platform + "/" + (sys.implementation.name) + " — verified.")
