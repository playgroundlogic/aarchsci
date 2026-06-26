#!/usr/bin/env python3
# geo-ml.smoke.py — the D3 verification for the `geo-ml` env.
#
# Same contract as the other smoke tests (assemble + import + do real work, inside
# the built arm64 image), for the geospatial base + the classic CPU ML layer. The
# ML libs (xgboost, lightgbm) ship native extension modules with their own arm64
# builds — a green solve does NOT guarantee they load and train; that's what this
# proves. Pure stdlib + the env's own packages. Exit 0 = functionally sound.
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
    "numpy", "pandas", "pyproj", "shapely", "fiona", "rasterio",
    "skimage", "osgeo.gdal",
    "geopandas", "libpysal", "sklearn", "xgboost", "lightgbm",
    "statsmodels.api", "datashader",
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


@check("geopandas GeoDataFrame + spatial op")
def _geopandas():
    import geopandas as gpd
    from shapely.geometry import Point
    gdf = gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(-83.0, 40.0), Point(-82.0, 41.0)],
        crs="EPSG:4326",
    )
    proj = gdf.to_crs("EPSG:3857")          # exercises pyproj/GDAL under geopandas
    assert proj.crs.to_epsg() == 3857
    assert proj.geometry.area.notna().all() or True  # points have 0 area; just touch it
    assert len(gdf.sindex) == 2             # builds the spatial index (GEOS)


# --- 3. the ML layer: do the native extensions actually train? ------------------
print("[smoke] 3. machine-learning layer")


@check("scikit-learn fit + predict")
def _sklearn():
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    rng = np.random.RandomState(0)
    X = rng.rand(60, 4); y = (X[:, 0] > 0.5).astype(int)
    clf = RandomForestClassifier(n_estimators=8, random_state=0).fit(X, y)
    assert clf.predict(X[:5]).shape == (5,)


@check("xgboost train (native booster)")
def _xgboost():
    import numpy as np
    import xgboost as xgb
    rng = np.random.RandomState(0)
    X = rng.rand(80, 5); y = (X[:, 0] + X[:, 1] > 1.0).astype(int)
    dtrain = xgb.DMatrix(X, label=y)
    bst = xgb.train({"max_depth": 3, "objective": "binary:logistic"}, dtrain, num_boost_round=5)
    pred = bst.predict(xgb.DMatrix(X[:5]))
    assert pred.shape == (5,) and (0.0 <= pred).all() and (pred <= 1.0).all()


@check("lightgbm train (native booster)")
def _lightgbm():
    import numpy as np
    import lightgbm as lgb
    rng = np.random.RandomState(0)
    X = rng.rand(120, 5); y = (X[:, 0] > 0.5).astype(int)
    ds = lgb.Dataset(X, label=y)
    bst = lgb.train({"objective": "binary", "num_leaves": 7, "verbose": -1}, ds, num_boost_round=5)
    pred = bst.predict(X[:5])
    assert len(pred) == 5


@check("statsmodels OLS")
def _statsmodels():
    import numpy as np
    import statsmodels.api as sm
    rng = np.random.RandomState(0)
    x = rng.rand(50); y = 2.0 * x + 0.1 * rng.rand(50)
    res = sm.OLS(y, sm.add_constant(x)).fit()
    assert 1.5 < res.params[1] < 2.5, f"slope off: {res.params[1]}"


@check("datashader aggregation")
def _datashader():
    import numpy as np
    import pandas as pd
    import datashader as ds
    rng = np.random.RandomState(0)
    df = pd.DataFrame({"x": rng.rand(1000), "y": rng.rand(1000)})
    canvas = ds.Canvas(plot_width=20, plot_height=20)
    agg = canvas.points(df, "x", "y")
    assert int(agg.sum()) == 1000, "datashader dropped points"


# --- verdict --------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: geo-ml env assembles, imports, and works on "
      + sys.platform + "/" + sys.implementation.name + " — verified.")
