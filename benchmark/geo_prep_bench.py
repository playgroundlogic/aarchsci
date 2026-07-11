#!/usr/bin/env python3
# geo_prep_bench.py — a representative geospatial CPU-prep workload, timed.
#
# Runs INSIDE the quay.io/aarchsci/geospatial image on whatever arch the host is
# (native — no emulation, per the project ethos). It mirrors the shape of the
# fieldwork/BuckAI prep stage that motivated this project:
#   raster read -> reproject (PROJ/GDAL) -> scikit-image transform -> vectorize
#   (GEOS/shapely) -> repeat over many synthetic tiles.
#
# It is CPU-bound and deterministic (fixed seed), so wall-clock is a fair
# cross-arch comparison of the SAME native stack. Output is one JSON line so a
# caller can aggregate arm64 vs amd64.
#
# Self-contained: generates its own rasters (no network, no input files), so the
# same script runs identically on orion (arm64) and janus (amd64).
import json
import platform
import sys
import time

import numpy as np
import rasterio
from rasterio.transform import from_origin
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform as shp_transform
from skimage.filters import sobel
from skimage.transform import resize
import rasterio.features


TILE = 512          # px per synthetic tile edge
N_TILES = 40        # tiles per run — enough to dominate startup noise
SEED = 20260626


def make_tile(rng):
    """A synthetic single-band raster with georeferencing, in EPSG:4326."""
    data = (rng.random((TILE, TILE), dtype="float64") * 255).astype("uint8")
    transform = from_origin(-83.0, 40.0, 0.0001, 0.0001)
    return data, transform


def prep_one(data, transform, reproj):
    """One tile's worth of the real prep operations, touching each native lib."""
    # 1. scikit-image: edge filter + downsample (the "feature prep" step).
    edges = sobel(data.astype("float64"))
    small = resize(edges, (TILE // 2, TILE // 2), anti_aliasing=True)

    # 2. rasterio/GDAL: threshold + polygonize (raster -> vector features, GEOS).
    mask = (small > small.mean()).astype("uint8")
    feats = list(rasterio.features.shapes(mask, transform=transform))

    # 3. shapely + pyproj: reproject each feature polygon to WebMercator (PROJ).
    n_pts = 0
    for geom, val in feats:
        if val == 0:
            continue
        poly = shape(geom)
        proj = shp_transform(lambda x, y, z=None: reproj.transform(x, y), poly)
        n_pts += len(proj.exterior.coords) if proj.geom_type == "Polygon" else 0
    return len(feats), n_pts


def main():
    rng = np.random.default_rng(SEED)
    reproj = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

    # Pre-generate tiles so RNG cost isn't in the timed region.
    tiles = [make_tile(rng) for _ in range(N_TILES)]

    t0 = time.perf_counter()
    total_feats = total_pts = 0
    for data, transform in tiles:
        f, p = prep_one(data, transform, reproj)
        total_feats += f
        total_pts += p
    elapsed = time.perf_counter() - t0

    result = {
        "arch": platform.machine(),           # aarch64 | x86_64
        "platform": platform.platform(),
        "python": platform.python_version(),
        "gdal": rasterio.__gdal_version__,
        "rasterio": rasterio.__version__,
        "numpy": np.__version__,
        "tiles": N_TILES,
        "tile_px": TILE,
        "features": total_feats,
        "vertices": total_pts,
        "seconds": round(elapsed, 4),
        "tiles_per_sec": round(N_TILES / elapsed, 3),
    }
    print(json.dumps(result))


if __name__ == "__main__":
    sys.exit(main())
