#!/usr/bin/env python3
# tomography.smoke.py — the D3 verification for the `tomography` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image). The risky
# native is ASTRA's C++ reconstruction core, plus TomoPy's compiled `libtomo`.
#
# NOTHING IS DOWNLOADED AND NOTHING IS STAGED: the phantom is generated. A Shepp-Logan
# phantom is synthetic by definition, so the ground truth is exact rather than looked up —
# the same reason generating a pyramidal slide beat fetching CAMELYON16 in `pathology`.
#
# TWO CHECKS, AND THEY ARE DIFFERENT IN KIND.
#
#   1. MASS CONSERVATION IS AN IDENTITY. A parallel-beam projection is a set of line
#      integrals, so the integral of every projection equals the total mass of the object,
#      at every angle, with no reference value needed. THE PROJECTOR CHOICE DECIDES WHETHER
#      THIS IS AN IDENTITY OR AN APPROXIMATION, which is worth knowing before anyone
#      "simplifies" it — measured at 128 px over 180 angles:
#         strip   9.5e-07   (area-integrating; this is float32 round-off, i.e. the floor)
#         linear  1.6e-03
#         line    2.1e-03   (ray-sampling; off by ~3 orders of magnitude)
#      So the test uses `strip` and asserts 1e-5. With `line` it would have to allow 0.5%
#      and would no longer be an identity.
#
#   2. FBP CONVERGENCE IS A TREND, and is asserted as one. Issue #41 asked for a
#      convergence behaviour in the number of projection angles rather than a band on one
#      reconstruction, and unlike the ladder in #35 this one holds. Measured relative RMS
#      against the phantom: 0.773 (16 angles) -> 0.425 (32) -> 0.250 (64) -> 0.217 (128)
#      -> 0.214 (256). Monotone, and it plateaus near 0.21, which is the FBP floor for this
#      phantom and grid rather than a defect. So this asserts MONOTONE DECREASE over the
#      range where the gains are real, and does not pretend the plateau is convergence.
#
# TOMOPY IS VERIFIED AT IMPORT PLUS ITS PHANTOM GENERATOR — the `siesta` precedent, and
# deliberately. Its reconstruction conventions do not match ASTRA's sinograms without
# scaling work that belongs in a recipe (best correlation reached 0.62, with magnitudes
# ~1e4 off), and its multiprocessing path crashed twice with `ConnectionResetError` from a
# dead child while the machine was loaded by a concurrent build, then passed at 64/128/256
# px when idle. A gate that fails under load is worse than a narrower gate that holds.
# `tomopy.shepp2d` is pure numpy and was reliable throughout, so that is what is used.
# Do not let the README imply tomopy's reconstruction is verified here.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import glob
import json
import platform
import sys
import tempfile
import traceback
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

FAILURES = []

N = 128                       # phantom edge, px
ANGLES = 180                  # for the identity checks
LADDER = (16, 32, 64, 128)    # the convergence range where gains are real
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


def _reconstruct(n_angles, projector="strip"):
    """Forward-project the phantom and FBP-reconstruct it. Returns (sinogram, recon)."""
    import astra
    import numpy as np
    ph = STATE["phantom"]
    vg = astra.create_vol_geom(N, N)
    ang = np.linspace(0.0, np.pi, n_angles, endpoint=False)
    pg = astra.create_proj_geom("parallel", 1.0, N, ang)
    pid = astra.create_projector(projector, pg, vg)
    sid, sino = astra.create_sino(ph, pid)
    rid = astra.data2d.create("-vol", vg, 0)
    cfg = astra.astra_dict("FBP")
    cfg["ProjectorId"] = pid
    cfg["ProjectionDataId"] = sid
    cfg["ReconstructionDataId"] = rid
    aid = astra.algorithm.create(cfg)
    astra.algorithm.run(aid)
    rec = astra.data2d.get(rid)
    astra.algorithm.delete(aid)
    astra.data2d.delete(rid)
    astra.data2d.delete(sid)
    astra.projector.delete(pid)
    return np.asarray(sino), np.asarray(rec)


def _rel_rms(rec):
    import numpy as np
    ph = STATE["phantom"]
    return float(np.sqrt(np.mean((rec - ph) ** 2)) / np.sqrt(np.mean(ph ** 2)))


# --- 1. imports -------------------------------------------------------------------
HEADLINE = [
    "numpy", "scipy",
    "astra",
    "tomopy",
    "skimage", "tifffile",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


@check("astra-toolbox is a CPU build, not a CUDA one")
def _cpu_build():
    # Read the build string from the package record rather than asking the library: this
    # is the distinction the whole env depends on, and the CUDA builds would import fine
    # on a GPU-less host and then fail at the first algorithm. Same rule as warpx and
    # geant4 — ask the package, not the program.
    recs = glob.glob("/opt/conda/conda-meta/astra-toolbox-*.json")
    assert recs, "no astra-toolbox conda-meta record"
    rec = json.load(open(recs[0]))
    build = rec["build"]
    assert "cuda" not in build.lower(), f"astra-toolbox is a CUDA build: {build}"
    assert build.startswith("py"), f"unexpected astra build string {build!r}"
    recs2 = glob.glob("/opt/conda/conda-meta/libtomo-*.json")
    tomo_build = json.load(open(recs2[0]))["build"] if recs2 else "(absent)"
    assert "cuda" not in tomo_build.lower(), f"libtomo is a CUDA build: {tomo_build}"
    STATE["builds"] = (rec["version"], build, tomo_build)
    print(f"       (astra-toolbox {rec['version']} {build}; libtomo {tomo_build})")


@check("tomopy generates a Shepp-Logan phantom (nothing is downloaded)")
def _phantom():
    import numpy as np
    import tomopy
    # tomopy's phantom generator is pure numpy — the one tomopy path this gate relies on,
    # for the reasons in the module docstring.
    ph = np.asarray(tomopy.shepp2d(size=N), dtype=np.float64)
    ph = ph[0] if ph.ndim == 3 else ph
    assert ph.shape == (N, N), f"phantom shape {ph.shape}, expected {(N, N)}"
    assert ph.max() > 0.0, "phantom is empty"
    STATE["phantom"] = ph
    STATE["mass"] = float(ph.sum())
    print(f"       (tomopy {tomopy.__version__}: {ph.shape} phantom, "
          f"total mass {STATE['mass']:.3f}, max {ph.max():.1f})")


# --- 2. the identity ---------------------------------------------------------------
print("[smoke] 2. every projection integral equals the object's mass (identity)")


@check("mass is conserved at every projection angle")
def _mass():
    import numpy as np
    sino, _ = _reconstruct(ANGLES, projector="strip")
    assert sino.shape == (ANGLES, N), f"sinogram shape {sino.shape}"
    sums = sino.sum(axis=1)
    worst = float(np.max(np.abs(sums - STATE["mass"])) / STATE["mass"])
    # 1e-5 against a measured 9.5e-07, which is float32 round-off accumulated over 128
    # values — i.e. the floor, not a tolerance chosen to pass. See the docstring for what
    # the `line` projector would have cost (2.1e-03).
    assert worst < 1e-5, (
        f"worst projection-mass deviation {worst:.3e} over {ANGLES} angles; with the "
        "`strip` projector this is an identity and should be ~1e-6")
    print(f"       ({ANGLES} angles, strip projector: worst relative deviation "
          f"{worst:.2e}, mass {STATE['mass']:.3f})")


# --- 3. the trend ------------------------------------------------------------------
print("[smoke] 3. FBP error falls monotonically with projection angles")


@check("reconstruction error decreases monotonically over 16 -> 128 angles")
def _convergence():
    errs = []
    for n in LADDER:
        _, rec = _reconstruct(n, projector="strip")
        errs.append((n, _rel_rms(rec)))
    for (n1, e1), (n2, e2) in zip(errs, errs[1:]):
        assert e2 < e1, (
            f"error rose from {e1:.5f} at {n1} angles to {e2:.5f} at {n2} — FBP is not "
            "converging in the number of projections")
    # The 16-angle reconstruction must be visibly bad and the 128-angle one usable; this
    # bounds the whole ladder rather than any single point.
    assert errs[0][1] > 0.5, f"16 angles gave {errs[0][1]:.3f}; too good to be real"
    assert errs[-1][1] < 0.30, f"128 angles gave {errs[-1][1]:.3f}; reconstruction failed"
    STATE["errs"] = errs
    print("       (" + " -> ".join(f"{n}:{e:.4f}" for n, e in errs)
          + "; monotone, floor ~0.21 is FBP's for this phantom)")


# --- 4. the image I/O layer --------------------------------------------------------
print("[smoke] 4. reconstruction survives a tifffile round trip")


@check("tifffile writes and reads the reconstruction bit-exactly")
def _tiff():
    import numpy as np
    import tifffile
    _, rec = _reconstruct(ANGLES, projector="strip")
    rec32 = np.asarray(rec, dtype=np.float32)
    d = Path(tempfile.mkdtemp())
    p = d / "recon.tif"
    tifffile.imwrite(str(p), rec32)
    back = tifffile.imread(str(p))
    # Uncompressed float32 TIFF is lossless, so equality is the right assertion — a
    # dtype or endianness slip would show up here and nowhere else.
    assert back.dtype == rec32.dtype, f"dtype changed: {rec32.dtype} -> {back.dtype}"
    assert np.array_equal(back, rec32), "tifffile round trip altered the reconstruction"
    print(f"       (tifffile {tifffile.__version__}: {p.stat().st_size} bytes, "
          f"{back.shape} {back.dtype}, bit-exact)")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: tomography env assembles; projections conserve mass exactly and "
      "FBP converges in the angle count on " + platform.machine()
      + f" (python {platform.python_version()}) — verified.")
