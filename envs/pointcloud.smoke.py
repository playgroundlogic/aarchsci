#!/usr/bin/env python3
# pointcloud.smoke.py — the D3 verification for the `pointcloud` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image)
# for the geospatial base + the LiDAR/DEM/terrain layer. PDAL is the showcase: a
# deep native-library point-cloud engine driven through a python binding — precisely
# the "solves but does it actually run a pipeline?" risk D3 exists for. We run a real
# PDAL pipeline, not just an import. Pure stdlib + the env's own packages.
import sys
import json
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
    "numpy", "pyproj", "shapely", "fiona", "rasterio", "skimage", "osgeo.gdal",
    "pdal", "laspy", "richdem",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. geospatial base ---------------------------------------------------------
print("[smoke] 2. geospatial base")


@check("pyproj reproject WGS84->WebMercator")
def _pyproj():
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = t.transform(-83.0, 40.0)
    assert -9.3e6 < x < -9.2e6 and 4.8e6 < y < 4.9e6, f"{x},{y}"


# --- 3. the point-cloud / terrain layer (the heavy natives) ---------------------
print("[smoke] 3. point-cloud / terrain layer")


@check("PDAL runs a real pipeline (native engine)")
def _pdal():
    import pdal
    # A faux.reader generates synthetic points, then stats counts them — no input
    # file needed, but it drives the full native PDAL pipeline end to end.
    pipeline_json = json.dumps([
        {"type": "readers.faux", "mode": "ramp", "count": 100,
         "bounds": "([0,10],[0,10],[0,10])"},
        {"type": "filters.stats"},
    ])
    p = pdal.Pipeline(pipeline_json)
    n = p.execute()
    assert n == 100, f"PDAL pipeline produced {n} points, expected 100"
    assert p.arrays and len(p.arrays[0]) == 100, "PDAL arrays not populated"


@check("laspy LAS write + read round-trip")
def _laspy():
    import numpy as np
    import laspy
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "pts.las")
        hdr = laspy.LasHeader(point_format=3, version="1.2")
        las = laspy.LasData(hdr)
        las.x = np.array([0.0, 1.0, 2.0]); las.y = np.array([0.0, 1.0, 2.0])
        las.z = np.array([0.0, 0.5, 1.0])
        las.write(path)
        back = laspy.read(path)
        assert len(back.x) == 3, f"laspy round-trip lost points: {len(back.x)}"


@check("richdem fills a DEM depression")
def _richdem():
    import numpy as np
    import richdem as rd
    # A 5x5 DEM with a one-cell pit in the middle; fill it and confirm the pit rose.
    arr = np.ones((5, 5), dtype="float64") * 10.0
    arr[2, 2] = 1.0
    dem = rd.rdarray(arr, no_data=-9999)
    filled = rd.FillDepressions(dem)
    assert filled[2, 2] > 1.0, "richdem did not fill the depression"


# (whitebox intentionally excluded — see envs/pointcloud.yaml and GAPS.md.)


# --- verdict --------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: pointcloud env assembles, imports, and works on "
      + sys.platform + "/" + sys.implementation.name + " — verified.")
