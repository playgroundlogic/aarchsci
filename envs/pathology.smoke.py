#!/usr/bin/env python3
# pathology.smoke.py — the D3 verification for the `pathology` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image). The risky
# native here is the OpenSlide C library and its Python binding, plus imagecodecs' JPEG
# path — all of which can import and then mis-read a slide.
#
# NOTHING IS STAGED AND NOTHING IS DOWNLOADED, and that is a deliberate departure from what
# issue #32 proposed. The request's checks were built on CAMELYON16 from the AWS Open Data
# Registry — excellent provenance (the depositors publish md5s for all 963 files) but a
# 546 MB fetch, which is the runtime-download pattern that made `whitebox` a wontfix. So
# this test GENERATES its own whole-slide image instead. That is legitimate for the same
# reason a generated mesh is (cfd-fv, fem-cfd): a pyramidal TIFF with a planted region is
# input geometry, not fitted biological data.
#
# Generating it also makes the checks STRONGER than the published-data versions, because the
# truth is planted rather than looked up:
#
#   * the pyramid must have exactly the dimensions W/2^k and downsamples exactly 2^k
#   * with lossless (deflate) tiles, read_region must return the planted pixel values
#     BIT-EXACTLY — measured, the tumour region contains exactly one unique colour
#   * the issue's own cross-level area identity holds EXACTLY at levels 0-2
#     (measured relative error 0.00e+00), not merely within a band
#   * the issue's slide-vs-mask dimension identity needs two files, so two are written
#
# The CAMELYON16 work in #32 remains the right thing for a cookbook recipe. It is simply
# not what a build gate should depend on.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import platform
import sys
import tempfile
import traceback
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

FAILURES = []

# Slide geometry. 2048x1536 at 256px tiles over 4 levels is small enough to be fast and
# large enough to be a genuine multi-tile pyramid.
W, H, TILE, LEVELS = 2048, 1536, 256, 4
BG = (230, 230, 230)          # pale "background"
TUMOUR = (120, 60, 140)       # planted "tumour" block
Y0, Y1, X0, X1 = 400, 900, 600, 1400
PLANTED_AREA = (Y1 - Y0) * (X1 - X0)      # 400_000 px at level 0, exactly

STATE = {}


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


def _write_slide(path, compression="deflate"):
    """Write a pyramidal, tiled TIFF with a planted rectangle. Returns the level arrays."""
    import numpy as np
    import tifffile
    base = np.full((H, W, 3), BG, np.uint8)
    base[Y0:Y1, X0:X1] = TUMOUR
    levels = [base]
    for _ in range(LEVELS - 1):
        # Plain 2x decimation keeps the planted edges on exact pixel boundaries for the
        # first few levels, which is what makes the area identity below exact.
        levels.append(levels[-1][::2, ::2])
    with tifffile.TiffWriter(str(path), bigtiff=True) as tw:
        for i, lvl in enumerate(levels):
            # subfiletype=1 marks a reduced-resolution page, which is what makes OpenSlide
            # read these as one pyramid rather than four unrelated images.
            tw.write(lvl, tile=(TILE, TILE), photometric="rgb",
                     compression=compression, subfiletype=0 if i == 0 else 1)
    return levels


# --- 1. imports -------------------------------------------------------------------
HEADLINE = [
    "numpy", "scipy", "pandas", "PIL", "matplotlib",
    "tifffile", "imagecodecs", "skimage",
    "zarr", "dask", "dask.array",
    "openslide",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


@check("openslide binding reports the C library version it is bound to")
def _versions():
    import openslide
    # Two distinct versions matter: the binding and the native library under it. A
    # binding that imports while the C library is missing or mismatched is exactly the
    # assemble-gap this project exists to catch.
    lib = openslide.__library_version__
    assert lib, "openslide reports no C library version"
    assert int(lib.split(".")[0]) >= 4, f"OpenSlide C library is {lib}, expected >=4"
    print(f"       (openslide-python {openslide.__version__}, libopenslide {lib})")


# --- 2. the generated slide, and the pyramid geometry -----------------------------
print("[smoke] 2. whole-slide pyramid geometry (generated, lossless tiles)")


@check("OpenSlide reads the generated pyramid with exact dimensions and downsamples")
def _pyramid():
    import openslide
    d = Path(tempfile.mkdtemp())
    slide = d / "slide.tif"
    _write_slide(slide, "deflate")
    fmt = openslide.OpenSlide.detect_format(str(slide))
    assert fmt == "generic-tiff", f"OpenSlide detected {fmt!r}, expected generic-tiff"
    s = openslide.OpenSlide(str(slide))
    assert s.dimensions == (W, H), f"level-0 dimensions {s.dimensions}, expected {(W, H)}"
    assert s.level_count == LEVELS, f"level_count {s.level_count}, expected {LEVELS}"
    # Exact, not approximate: each level is half the previous one in each axis.
    for k in range(LEVELS):
        want = (W >> k, H >> k)
        assert s.level_dimensions[k] == want, \
            f"level {k} dimensions {s.level_dimensions[k]}, expected {want}"
        ds = s.level_downsamples[k]
        assert abs(ds - 2 ** k) < 1e-9, f"level {k} downsample {ds}, expected {2 ** k}"
    STATE["slide"] = str(slide)
    STATE["dir"] = d
    print(f"       ({fmt}, {s.dimensions}, {s.level_count} levels, "
          f"downsamples {tuple(int(x) for x in s.level_downsamples)})")
    s.close()


@check("read_region returns the planted pixels BIT-EXACTLY through lossless tiles")
def _bit_exact():
    import numpy as np
    import openslide
    path = STATE.get("slide")
    assert path, "the pyramid check did not run"
    s = openslide.OpenSlide(path)
    # deflate is lossless, so this is an equality assertion rather than a tolerance: any
    # tile-stitching, stride or colour-order bug shows up as a changed pixel value.
    tum = np.asarray(s.read_region((X0, Y0), 0, (128, 128)).convert("RGB"))
    assert (tum == np.array(TUMOUR, np.uint8)).all(), (
        f"tumour region is not bit-exact; unique colours found: "
        f"{np.unique(tum.reshape(-1, 3), axis=0)[:4].tolist()}")
    bg = np.asarray(s.read_region((0, 0), 0, (128, 128)).convert("RGB"))
    assert (bg == np.array(BG, np.uint8)).all(), "background region is not bit-exact"
    n_unique = len(np.unique(tum.reshape(-1, 3), axis=0))
    assert n_unique == 1, f"tumour region has {n_unique} distinct colours, expected 1"
    s.close()
    print(f"       (tumour and background both bit-exact; {n_unique} unique colour in region)")


# --- 3. the issue's own identities -------------------------------------------------
print("[smoke] 3. cross-level area and slide/mask identities (issue #32's checks)")


@check("tumour area agrees across pyramid levels after scaling by the downsample")
def _area_identity():
    import numpy as np
    import openslide
    path = STATE.get("slide")
    assert path, "the pyramid check did not run"
    s = openslide.OpenSlide(path)
    results = []
    for k in range(LEVELS):
        full = np.asarray(s.read_region((0, 0), k, s.level_dimensions[k]).convert("RGB"))
        mask = np.abs(full.astype(int) - np.array(TUMOUR)).sum(axis=2) < 30
        scaled = mask.sum() * (s.level_downsamples[k] ** 2)
        results.append((k, int(mask.sum()), float(scaled)))
        rel = abs(scaled - PLANTED_AREA) / PLANTED_AREA
        # Levels 0-2 are EXACT: the planted edges fall on integer pixel boundaries under
        # 2x decimation, so the scaled area equals the planted area with zero error
        # (measured 0.00e+00). The coarsest level is not exact and should not be asserted
        # as such — at 1/8 scale a 500x800 block becomes 62.5x100, so it cannot land on
        # whole pixels and aliasing costs ~1%. That is the geometry, not a defect.
        limit = 1e-9 if k <= 2 else 0.05
        assert rel <= limit, (
            f"level {k}: scaled area {scaled:.0f} vs planted {PLANTED_AREA} "
            f"(rel {rel:.2e} > {limit:g})")
    s.close()
    print("       (" + "; ".join(f"L{k}: {px}px -> {sc:.0f}" for k, px, sc in results)
          + f"; planted {PLANTED_AREA})")


@check("a slide and its separately-written mask report identical level-0 dimensions")
def _slide_mask():
    import openslide
    d = STATE.get("dir")
    assert d, "the pyramid check did not run"
    mask_path = d / "mask.tif"
    _write_slide(mask_path, "deflate")
    a = openslide.OpenSlide(STATE["slide"])
    b = openslide.OpenSlide(str(mask_path))
    # Two independent files; a reader that silently rounded or transposed one of them
    # would break this. This is the identity issue #32 proposed for CAMELYON16's
    # slide/mask pairs, available here without the download.
    assert a.dimensions == b.dimensions, \
        f"slide {a.dimensions} and mask {b.dimensions} disagree at level 0"
    assert a.level_dimensions == b.level_dimensions, \
        "slide and mask pyramids differ"
    print(f"       (slide {a.dimensions} == mask {b.dimensions}, pyramids identical)")
    a.close()
    b.close()


# --- 4. JPEG tiles, which is what real slides use ---------------------------------
print("[smoke] 4. JPEG-tiled slides (imagecodecs is load-bearing)")


@check("imagecodecs enables JPEG-tiled slides, the format real WSIs ship in")
def _jpeg_tiles():
    import imagecodecs
    import openslide
    d = STATE.get("dir")
    assert d, "the pyramid check did not run"
    jp = d / "slide_jpeg.tif"
    # Without imagecodecs this raises
    #   KeyError: "<COMPRESSION.JPEG: 7> requires the 'imagecodecs' package"
    # which is why imagecodecs is in the spec rather than being left to chance. Real
    # whole-slide images are JPEG-tiled, so an env that cannot handle them is a
    # half-capability.
    _write_slide(jp, "jpeg")
    s = openslide.OpenSlide(str(jp))
    assert s.dimensions == (W, H), f"JPEG-tiled slide dimensions {s.dimensions}"
    assert s.level_count == LEVELS, f"JPEG-tiled level_count {s.level_count}"
    # Lossy, so no bit-exact assertion here — but the planted block must still be clearly
    # the planted colour rather than background.
    import numpy as np
    tum = np.asarray(s.read_region((X0 + 64, Y0 + 64), 0, (64, 64)).convert("RGB"))
    mean = tum.reshape(-1, 3).mean(axis=0)
    assert np.abs(mean - np.array(TUMOUR)).max() < 20, \
        f"JPEG-tiled tumour region mean {mean.round(1).tolist()}, expected ~{TUMOUR}"
    s.close()
    print(f"       (imagecodecs {imagecodecs.__version__}, JPEG-tiled pyramid read; "
          f"region mean {mean.round(1).tolist()})")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: pathology env assembles and reads whole-slide pyramids exactly on "
      + platform.machine() + " (python " + platform.python_version() + ") — verified.")
