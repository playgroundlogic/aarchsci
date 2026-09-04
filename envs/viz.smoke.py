#!/usr/bin/env python3
# viz.smoke.py — the D3 verification for the `viz` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image) for
# headless ParaView. This env earns D3 harder than any other in the catalog, because
# "it imported" is spectacularly insufficient here: a bare conda-forge paraview
# install cannot even load its own shared libraries (see envs/viz.yaml), and once it
# can, it still refuses to render unless it has a display to render into.
#
# So the test does the whole pipeline: start a virtual X server, build a dataset,
# apply a filter, render with the software rasteriser, write a PNG — then read that
# PNG back with pillow, which had no part in producing it, and prove the image
# contains actual geometry rather than a blank frame. Pure stdlib + the env's own
# packages. Exit 0 = functionally sound.
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

FAILURES = []

DISPLAY_NUM = 99
PREFIX = Path(sys.prefix)


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


def start_xvfb():
    """Start Xvfb and return (proc, display). Waits for the socket, not a fixed sleep."""
    xvfb = PREFIX / "bin" / "Xvfb"
    if not xvfb.is_file():
        raise AssertionError(
            f"{xvfb} missing — the xorg-xvfb-server package provides `Xvfb` "
            "(there is no `xvfb-run` wrapper); without it nothing can render")
    proc = subprocess.Popen(
        [str(xvfb), f":{DISPLAY_NUM}", "-screen", "0", "1024x768x24"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sock = Path(f"/tmp/.X11-unix/X{DISPLAY_NUM}")
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if sock.exists():
            return proc, f":{DISPLAY_NUM}"
        if proc.poll() is not None:
            raise AssertionError(f"Xvfb exited immediately with {proc.returncode}")
        time.sleep(0.05)
    proc.terminate()
    raise AssertionError(f"Xvfb never created {sock} within 20s")


# --- 1. Imports -----------------------------------------------------------------
# These two are the whole point. `import paraview.simple` is what fails outright when
# the hdf5 soname is wrong, so it is a real check here and not a formality.
HEADLINE = ["numpy", "PIL", "PIL.Image", "vtk", "paraview", "paraview.simple"]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. the native library graph is actually satisfied --------------------------
print("[smoke] 2. native libraries resolve")


@check("pvbatch and pvpython have no unresolved shared libraries")
def _ldd_clean():
    missing = {}
    for name in ("pvbatch", "pvpython"):
        exe = PREFIX / "bin" / name
        if not exe.is_file():
            raise AssertionError(f"{exe} not found")
        out = subprocess.run(["ldd", str(exe)], capture_output=True, text=True).stdout
        bad = [ln.strip() for ln in out.splitlines() if "not found" in ln]
        if bad:
            missing[name] = bad
    assert not missing, (
        "unresolved libraries — this is the paraview underlinking bug, check the "
        f"hdf5 pin in envs/viz.yaml: {missing}")


@check("hdf5 soname matches what paraview was linked against")
def _hdf5_soname():
    # Guard the pin explicitly, so a future channel migration that silently lifts
    # hdf5 to a new soname fails here with a clear message rather than as a
    # confusing render error later.
    sonames = sorted(p.name for p in (PREFIX / "lib").glob("libhdf5.so.*")
                     if p.name.count(".") == 2)
    assert sonames, "no libhdf5.so.<N> in the prefix at all"
    assert "libhdf5.so.310" in sonames, (
        f"expected libhdf5.so.310 (hdf5 1.14.x), found {sonames} — paraview's "
        "binaries link 310 and will not start against another soname")


# --- 3. headless software rendering, end to end --------------------------------
print("[smoke] 3. headless render (Xvfb + llvmpipe GLX)")

RENDERED = {}

# The render runs in a child `pvbatch`, not in this interpreter, for two reasons.
# First, `pvbatch` is the entry point consumers of this env actually use, so testing
# it tests the promise. Second, correctness: a process holding a GL context must exit
# BEFORE its X server does. Tearing ParaView down in-process instead (Delete /
# ResetSession) makes X11 raise "XIO: fatal IO error 22", which calls exit(1)
# immediately — killing this script mid-run and discarding the buffered verdict.
RENDER_SCRIPT = '''
from paraview.simple import (
    Wavelet, Contour, GetActiveViewOrCreate, Show, ColorBy,
    GetDisplayProperties, Render, SaveScreenshot,
)
import sys
# Wavelet is ParaView's built-in synthetic volume, so nothing is staged or
# downloaded. Contour puts a real filter (flying edges) in the pipeline, and ColorBy
# forces a scalar lookup table so the output must have structure rather than be a
# flat silhouette.
wavelet = Wavelet()
contour = Contour(Input=wavelet, ContourBy=["POINTS", "RTData"], Isosurfaces=[150.0])
view = GetActiveViewOrCreate("RenderView")
view.ViewSize = [400, 300]
Show(contour, view)
ColorBy(GetDisplayProperties(contour, view), ("POINTS", "RTData"))
Render()
SaveScreenshot(sys.argv[-1], view)
'''


@check("pvbatch renders a filtered dataset to a PNG with no GPU and no display")
def _render():
    xvfb, display = start_xvfb()
    try:
        work = Path(tempfile.mkdtemp())
        out = work / "render.png"
        (work / "render.py").write_text(RENDER_SCRIPT)
        pvbatch = Path(sys.prefix) / "bin" / "pvbatch"
        assert pvbatch.is_file(), f"{pvbatch} not found"

        env = dict(os.environ)
        env["DISPLAY"] = display
        # Force the software rasteriser rather than hoping for it: on a host that does
        # expose a GL driver we still want the CPU path under test, because that is
        # what a Graviton node has.
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"
        env["GALLIUM_DRIVER"] = "llvmpipe"
        # The image runs as an unprivileged user whose HOME may not be writable;
        # without this llvmpipe prints "Failed to create //.cache for shader cache".
        env.setdefault("XDG_CACHE_HOME", str(work))
        # Clusters routinely run containers as an arbitrary uid with no home
        # directory, and ParaView then warns that it cannot write its settings file.
        # Point HOME somewhere writable so this test behaves the same either way.
        if not os.access(env.get("HOME", "/"), os.W_OK):
            env["HOME"] = str(work)

        proc = subprocess.run(
            [str(pvbatch), str(work / "render.py"), str(out)],
            cwd=str(work), env=env, capture_output=True, text=True, timeout=900)
        assert proc.returncode == 0, (
            f"pvbatch exited {proc.returncode}:\n"
            + "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:]))
        assert out.is_file(), (
            "pvbatch wrote no file — rendering failed silently. Look for "
            "'libOSMesa not found' or 'Could not initialize a device': this env "
            "renders via GLX against Xvfb, not via OSMesa or EGL.\n"
            + (proc.stdout + proc.stderr)[-800:])
        assert out.stat().st_size > 1000, f"PNG suspiciously small: {out.stat().st_size} B"
        RENDERED["path"] = out
        print(f"       (pvbatch wrote {out.stat().st_size} B PNG)")
    finally:
        # Safe now: the only X client exited with the subprocess.
        xvfb.terminate()
        xvfb.wait(timeout=10)


# --- 4. the image is verified by something that did not draw it -----------------
print("[smoke] 4. independent verification of the rendered image")


@check("pillow reads the PNG back and it contains real geometry")
def _verify_png():
    path = RENDERED.get("path")
    assert path is not None, "no PNG was rendered — see check 3"
    from PIL import Image
    im = Image.open(path).convert("RGB")
    assert im.size == (400, 300), f"unexpected image size {im.size}"

    colors = im.getcolors(maxcolors=1_000_000)
    assert colors is not None, "image has an implausible number of distinct colours"
    # A blank frame is one flat background colour. A real render of a coloured
    # contour surface has hundreds. This is the check that distinguishes "ParaView
    # wrote a file" from "ParaView rendered something".
    assert len(colors) > 50, \
        f"only {len(colors)} distinct colours — this looks like a blank frame"

    grey = im.convert("L")
    lo, hi = grey.getextrema()
    assert hi - lo > 40, f"luminance range {lo}..{hi} too flat to be a render"
    # The largest single colour is the background; it must not be the whole image.
    dominant = max(count for count, _ in colors)
    fraction = dominant / float(im.size[0] * im.size[1])
    assert fraction < 0.98, \
        f"{fraction:.1%} of pixels are one colour — geometry did not draw"
    print(f"       ({len(colors)} distinct colours, luminance {lo}..{hi}, "
          f"background {fraction:.1%} of frame)")


# --- verdict --------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: viz env assembles, imports, and works on "
      + sys.platform + "/" + sys.implementation.name + " — verified.")
