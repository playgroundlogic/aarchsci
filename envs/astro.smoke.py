#!/usr/bin/env python3
# astro.smoke.py — the D3 verification for the `astro` env.
#
# Same contract as the rest of the catalog (assemble + import + do real work, inside the
# built arm64 image). The risky natives here are wcslib (astropy.wcs._wcs), ERFA/SOFA
# (pyerfa's compiled ufuncs, which every time-scale and frame transform goes through),
# HEALPix C++ (healpy), photutils' compiled overlap geometry, and yt's Cython kernels.
# All of those can install and import while computing wrong answers, so this file drives
# real calculations.
#
# WHY THE ASSERTIONS ARE MOSTLY EXACT. Astronomy is unusually good to a smoke test:
# many of its facts are *definitions*, not measurements. The astronomical unit is exactly
# 149597870700 m by IAU 2012 resolution; c is exactly 299792458 m/s by SI definition;
# TAI-UTC in 2020 is exactly 37 leap seconds; a HEALPix map at nside=64 has exactly
# 12*64^2 pixels; the exact-overlap area of a circular aperture on a unit image is
# exactly pi*r^2. Where a number is defined, this file asserts it to machine precision
# rather than to a range — a wrong answer cannot hide inside a tolerance that tight.
# Ranges are used only where the value genuinely depends on a model or an epoch, and each
# one says so.
#
# EVERYTHING HERE IS OFFLINE, DELIBERATELY. astropy will fetch IERS earth-orientation
# tables over the network if allowed to, and astroquery's entire purpose is remote archive
# access. A smoke test that reaches the internet is not a smoke test: it fails on someone
# else's outage and passes for reasons it cannot see. So auto-download is switched off
# below and the leap-second check is a positive assertion that the bundled
# astropy-iers-data tables are present and correct.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import platform
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


# Switch off every network path before anything imports astropy's time/coords machinery.
# Doing it here rather than inside a check means no later check can accidentally trigger
# a download.
from astropy.utils.iers import conf as iers_conf  # noqa: E402

iers_conf.auto_download = False
# With auto_download off, astropy would otherwise raise on dates outside the bundled
# table rather than degrade. Nothing here needs UT1 precision, so accept the bundled
# tables' accuracy silently instead of erroring.
iers_conf.iers_degraded_accuracy = "ignore"


# --- 1. imports -------------------------------------------------------------------
HEADLINE = [
    "numpy", "scipy",
    "astropy", "astropy.units", "astropy.constants", "astropy.io.fits",
    "astropy.wcs", "astropy.time", "astropy.coordinates", "astropy.table",
    "erfa",
    "photutils", "photutils.aperture", "photutils.detection",
    "regions", "reproject",
    "sunpy", "sunpy.map", "sunpy.coordinates",
    "healpy",
    "yt",
    # astroquery is import-only on purpose — see the module docstring and astro.yaml.
    "astroquery",
    "specutils",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. native extension layer ----------------------------------------------------
# The point of this section is to prove the COMPILED code is present and is aarch64 —
# not to read build strings. A conda `py3NN` build string proves nothing about the
# target architecture or even about the Python ABI: measured 2026-09-05, fenics-dolfinx's
# py312-tagged build imports fine on Python 3.14 because it binds through nanobind's
# stable ABI. So the arch claim comes from the interpreter itself and the extension
# claim from the loaded module's own filename.
print("[smoke] 2. native extension layer")

# module path -> what it is, for the failure message to be useful
NATIVE_EXTS = {
    "astropy.wcs._wcs": "wcslib (all WCS transforms)",
    "erfa.ufunc": "ERFA/SOFA (all time scales + frame transforms)",
    "healpy._healpy_pixel_lib": "HEALPix C++ pixelization",
    # NOT `photutils.geometry` — that is a plain package __init__.py. The compiled code
    # is one level down, and it ships as `circular_overlap.abi3.so`: the stable ABI, so
    # its filename carries no interpreter or platform tag at all.
    "photutils.geometry.circular_overlap": "exact aperture-overlap geometry",
    "yt.utilities.lib.misc_utilities": "yt Cython kernels",
}


@check("interpreter reports aarch64")
def _arch():
    m = platform.machine()
    assert m == "aarch64", f"platform.machine() is {m!r}, expected aarch64"
    print(f"       ({m}, python {platform.python_version()})")


@check("compiled extensions are real .so files loaded into this interpreter")
def _natives():
    import importlib
    tagged = []
    for mod, what in sorted(NATIVE_EXTS.items()):
        m = importlib.import_module(mod)
        f = getattr(m, "__file__", None)
        assert f, f"{mod} ({what}) has no __file__"
        assert f.endswith(".so"), f"{mod} ({what}) is not a compiled extension: {f}"
        if "aarch64" in f:
            tagged.append(mod)
    # Two extension-naming conventions coexist here and the difference is instructive:
    # photutils and pyerfa build against the limited API and land as `*.abi3.so`, whose
    # filename encodes neither interpreter nor platform, while yt uses per-version tags
    # and lands as `*.cpython-3NN-aarch64-linux-gnu.so`. The latter is direct evidence
    # from a built artifact that this is a native arm64 wheel and not an emulated x86_64
    # one — stronger than platform.machine(), which only reports the interpreter.
    assert tagged, "no extension filename carries an aarch64 tag; cannot confirm arm64 natively"
    print(f"       ({len(NATIVE_EXTS)} native extensions; {len(tagged)} aarch64-tagged, "
          f"the rest abi3/untagged)")


# --- 3. astropy: defined quantities, asserted exactly -----------------------------
print("[smoke] 3. astropy core (exact identities)")


@check("units convert to the IAU/SI definitions exactly")
def _units():
    import astropy.units as u
    from astropy import constants as const
    # IAU 2012 Resolution B2 DEFINES the au as exactly 149597870700 m.
    au_m = (1 * u.au).to(u.m).value
    assert au_m == 149597870700.0, f"1 au = {au_m!r} m, expected exactly 149597870700"
    # The SI second/metre fix c exactly.
    assert const.c.value == 299792458.0, f"c = {const.c.value!r}, expected exactly 299792458"
    # A compound conversion, to prove the unit algebra and not just a lookup: parsecs to
    # au is 648000/pi by definition of the parsec.
    import math
    pc_au = (1 * u.pc).to(u.au).value
    assert abs(pc_au - 648000.0 / math.pi) < 1e-6, f"1 pc = {pc_au} au"
    print(f"       (1 au = {au_m:.0f} m, 1 pc = {pc_au:.6f} au)")


@check("leap seconds come from the bundled tables: TAI-UTC = 37 s in 2020 (offline)")
def _leap():
    from astropy.time import Time
    t = Time("2020-06-01T00:00:00", scale="utc")
    # TAI-UTC is an integer number of leap seconds, 37 since 2017-01-01. This runs
    # entirely through pyerfa against astropy-iers-data on disk — it is simultaneously
    # a correctness check and proof that no network was needed.
    dt = (t.tai.mjd - t.mjd) * 86400.0
    assert abs(dt - 37.0) < 1e-6, f"TAI-UTC = {dt} s in 2020, expected exactly 37"
    # A second, independent statement: the same instant round-trips through TT and back.
    assert abs((t.tt.utc.jd - t.jd) * 86400.0) < 1e-6, "UTC->TT->UTC did not round-trip"
    print(f"       (TAI-UTC = {dt:.6f} s, auto_download={iers_conf.auto_download})")


@check("WCS round-trips pixel -> world -> pixel through wcslib")
def _wcs():
    import numpy as np
    from astropy.wcs import WCS
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crpix = [50.5, 50.5]
    w.wcs.crval = [266.4, -29.0]
    w.wcs.cdelt = [-0.001, 0.001]
    # The reference pixel maps to the reference value by construction — an exact
    # statement about the projection, not an approximation.
    sky = w.wcs_pix2world([[49.5, 49.5]], 0)  # 0-based == crpix
    assert np.allclose(sky[0], [266.4, -29.0], atol=1e-10), f"crpix did not map to crval: {sky}"
    px = np.array([[10.0, 20.0], [37.5, 88.25], [99.0, 3.0]])
    back = w.wcs_world2pix(w.wcs_pix2world(px, 0), 0)
    err = float(np.max(np.abs(back - px)))
    assert err < 1e-8, f"pixel->world->pixel round-trip error {err}"
    print(f"       (TAN projection, round-trip max error {err:.2e} px)")


@check("FITS image + binary table survive a write/read round-trip bit-for-bit")
def _fits():
    import numpy as np
    from astropy.io import fits
    from astropy.table import Table
    img = (np.arange(64, dtype=">f8").reshape(8, 8) / 3.0)
    tbl = Table({"id": np.arange(5, dtype=np.int32),
                 "flux": np.array([1.5, 2.25, -0.5, 1e3, 0.0], dtype=np.float64),
                 "name": ["a", "bb", "ccc", "dddd", "e"]})
    d = Path(tempfile.mkdtemp())
    path = d / "round.fits"
    fits.HDUList([fits.PrimaryHDU(img), fits.BinTableHDU(tbl)]).writeto(path)
    with fits.open(path) as hdul:
        got_img = hdul[0].data
        got_tbl = Table(hdul[1].data)
    # Exact equality, not allclose: FITS is a binary format and float64 must survive
    # unchanged. A byte-order or scaling bug would show up right here.
    assert np.array_equal(got_img, img), "image data changed across the FITS round-trip"
    assert np.array_equal(got_tbl["flux"], tbl["flux"]), "table floats changed"
    assert list(got_tbl["name"]) == list(tbl["name"]), "table strings changed"
    print(f"       ({path.stat().st_size} bytes, {img.shape} image + {len(tbl)}-row table)")


@check("coordinate frames: Sgr A* transforms ICRS -> Galactic to the known values")
def _coords():
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    # Sgr A* is NEAR but not AT the Galactic coordinate origin, and the distinction is
    # the whole value of this check. The Galactic frame's pole and origin are fixed by
    # the IAU 1958 convention, defined from radio observations that predate the precise
    # position of Sgr A*; the black hole therefore sits at l = 359.9442, b = -0.0462 —
    # about 0.056 and 0.046 degrees off zero. Asserting "approximately zero" would be
    # both wrong and weaker: these two nonzero offsets are a fingerprint of the correct
    # IAU frame definition, so a transform built on the wrong convention fails here even
    # though it would pass a loose "near the centre" test.
    c = SkyCoord(266.41681662 * u.deg, -29.00782497 * u.deg, frame="icrs")
    g = c.galactic
    lon = g.l.wrap_at(180 * u.deg).deg
    lat = g.b.deg
    assert abs(lon - (-0.05566)) < 0.002, f"Sgr A* galactic longitude {lon}, expected -0.0557"
    assert abs(lat - (-0.04616)) < 0.002, f"Sgr A* galactic latitude {lat}, expected -0.0462"
    # Separation is metric and must be symmetric + zero to self.
    assert c.separation(c).deg == 0.0, "self-separation is not exactly zero"
    print(f"       (l={g.l.deg:.5f} deg [={lon:+.5f}], b={lat:+.5f} deg)")


# --- 4. photometry + region geometry ----------------------------------------------
print("[smoke] 4. photometry + region geometry")


@check("exact circular aperture on a unit image measures pi*r^2")
def _aperture():
    import math
    import numpy as np
    from photutils.aperture import CircularAperture, aperture_photometry
    # On an image of all ones, the exact-overlap sum over a circle IS its area. This is
    # an analytic identity, so it pins photutils' compiled geometry to ~1e-10 — far
    # tighter than any "looks about right" check could.
    data = np.ones((101, 101))
    r = 12.5
    ap = CircularAperture([(50.0, 50.0)], r=r)
    got = float(aperture_photometry(data, ap, method="exact")["aperture_sum"][0])
    want = math.pi * r * r
    assert abs(got - want) < 1e-8, f"aperture sum {got}, expected pi*r^2 = {want}"
    print(f"       (r={r}: sum {got:.10f} vs pi*r^2 {want:.10f})")


@check("source detection recovers an injected Gaussian's position")
def _detect():
    import numpy as np
    from astropy.modeling.models import Gaussian2D
    from photutils.detection import DAOStarFinder
    y, x = np.mgrid[0:64, 0:64]
    tx, ty = 41.3, 22.7
    data = Gaussian2D(amplitude=100.0, x_mean=tx, y_mean=ty, x_stddev=2.0, y_stddev=2.0)(x, y)
    found = DAOStarFinder(threshold=5.0, fwhm=4.7)(data)
    assert found is not None and len(found) >= 1, "no source detected in a noiseless image"
    i = int(np.argmax(found["peak"]))
    # photutils 3.0 renamed xcentroid -> x_centroid and keeps the old name as a
    # deprecated alias until 4.0. Prefer the new one so this does not start warning (and
    # later failing) on a routine channel update.
    cx, cy = ("x_centroid", "y_centroid") if "x_centroid" in found.colnames else \
             ("xcentroid", "ycentroid")
    dx = abs(float(found[cx][i]) - tx)
    dy = abs(float(found[cy][i]) - ty)
    # Sub-tenth-pixel: a centroid this close cannot come from a broken kernel.
    assert dx < 0.1 and dy < 0.1, f"centroid off by ({dx:.3f}, {dy:.3f}) px"
    print(f"       (injected ({tx}, {ty}), recovered offset ({dx:.4f}, {dy:.4f}) px)")


@check("sky regions convert to pixels, test containment, and round-trip through DS9")
def _regions():
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astropy.wcs import WCS
    from regions import CircleSkyRegion, PixCoord, Regions
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crpix = [50.5, 50.5]
    w.wcs.crval = [10.0, 20.0]
    w.wcs.cdelt = [-0.001, 0.001]
    reg = CircleSkyRegion(SkyCoord(10.0 * u.deg, 20.0 * u.deg), radius=0.005 * u.deg)
    pix = reg.to_pixel(w)
    # 0.005 deg at 0.001 deg/px is 5 px, so the centre is inside and a point 20 px away
    # is outside. Geometry, asserted both ways.
    assert pix.contains(PixCoord(49.5, 49.5)), "region does not contain its own centre"
    assert not pix.contains(PixCoord(49.5, 69.5)), "region wrongly contains a distant point"
    assert abs(pix.radius - 5.0) < 0.05, f"pixel radius {pix.radius}, expected ~5"
    back = Regions.parse(Regions([reg]).serialize(format="ds9"), format="ds9")
    assert len(back) == 1, "DS9 round-trip lost the region"
    print(f"       (pixel radius {pix.radius:.4f} px, DS9 round-trip ok)")


@check("reproject resamples between two WCSes and conserves flux")
def _reproject():
    import numpy as np
    from astropy.wcs import WCS
    from reproject import reproject_exact

    def wcs_at(crval1):
        w = WCS(naxis=2)
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.crpix = [16.5, 16.5]
        w.wcs.crval = [crval1, 0.0]
        w.wcs.cdelt = [-0.01, 0.01]
        return w

    src = np.zeros((32, 32))
    src[12:20, 12:20] = 1.0          # a compact top-hat, total flux 64
    out, footprint = reproject_exact((src, wcs_at(30.0)), wcs_at(30.0), shape_out=(32, 32))
    # Reprojecting onto the IDENTICAL WCS must be the identity map. reproject_exact is
    # flux-conserving, so this is a strong statement: the compiled spherical-polygon
    # overlap code has to return exactly the input.
    assert np.nanmax(np.abs(out - src)) < 1e-8, "identity reprojection changed the image"
    # Now a genuine resampling onto a shifted grid; total flux must be preserved.
    out2, _ = reproject_exact((src, wcs_at(30.0)), wcs_at(30.02), shape_out=(32, 32))
    got = float(np.nansum(out2))
    assert abs(got - 64.0) / 64.0 < 0.02, f"flux {got}, expected ~64 (2% tolerance)"
    print(f"       (identity exact; shifted-grid flux {got:.4f} vs 64)")


# --- 5. HEALPix ---------------------------------------------------------------------
print("[smoke] 5. healpy (HEALPix C++)")


@check("HEALPix pixel counts and round-trip are exact")
def _healpix():
    import numpy as np
    import healpy as hp
    nside = 64
    npix = hp.nside2npix(nside)
    # npix = 12 * nside^2 is the definition of the HEALPix grid.
    assert npix == 12 * nside * nside == 49152, f"npix {npix}, expected 49152"
    ipix = np.array([0, 1, 12345, npix - 1])
    theta, phi = hp.pix2ang(nside, ipix)
    back = hp.ang2pix(nside, theta, phi)
    assert np.array_equal(back, ipix), f"ang2pix(pix2ang) != identity: {back} vs {ipix}"
    print(f"       (nside={nside}, npix={npix}, pix<->ang exact)")


@check("spherical harmonic transform of a constant map has no power above the monopole")
def _anafast():
    import numpy as np
    import healpy as hp
    nside = 32
    m = np.full(hp.nside2npix(nside), 2.5)
    # A constant field is pure monopole: C_l must vanish for l >= 1. This drives the
    # HEALPix C++ SHT, which is the most substantial native code in the package, and the
    # expected answer is exactly zero rather than "small".
    cl = hp.anafast(m, lmax=16)
    assert cl.shape == (17,), f"unexpected cl shape {cl.shape}"
    worst = float(np.max(np.abs(cl[1:])))
    assert worst < 1e-10, f"constant map has power at l>=1: max |C_l| = {worst}"
    # And the mean must come back out.
    assert abs(float(np.mean(m)) - 2.5) < 1e-12
    print(f"       (lmax=16, max |C_l| for l>=1 = {worst:.2e})")


# --- 6. yt --------------------------------------------------------------------------
print("[smoke] 6. yt (volumetric analysis)")


@check("yt integrates mass over a uniform grid to the analytic value")
def _yt():
    import numpy as np
    import yt
    yt.set_log_level("error")     # yt is chatty; keep the smoke output readable
    n = 8
    rho = np.full((n, n, n), 2.0)
    ds = yt.load_uniform_grid(
        {"density": (rho, "g/cm**3")}, rho.shape,
        length_unit="cm", bbox=np.array([[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]]),
    )
    total = float(ds.all_data().quantities.total_quantity(("gas", "mass")).to("g"))
    # mass = integral of rho dV = 2.0 g/cm^3 * 1 cm^3 = 2.0 g, exactly. yt has to get
    # the cell volumes, the unit conversion and the reduction all right to land here.
    assert abs(total - 2.0) < 1e-9, f"total mass {total} g, expected exactly 2.0"
    print(f"       ({n}^3 grid, total mass {total:.12f} g)")


# --- 7. sunpy ------------------------------------------------------------------------
print("[smoke] 7. sunpy (solar coordinates)")


@check("sunpy builds a Map with a solar WCS and transforms its centre to Stonyhurst")
def _sunpy():
    import numpy as np
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    import sunpy.map
    from sunpy.coordinates import frames
    data = np.arange(100, dtype=float).reshape(10, 10)
    ref = SkyCoord(0 * u.arcsec, 0 * u.arcsec, obstime="2020-01-01T00:00:00",
                   observer="earth", frame=frames.Helioprojective)
    header = sunpy.map.make_fitswcs_header(data, ref, scale=[2, 2] * u.arcsec / u.pix)
    m = sunpy.map.Map(data, header)
    assert m.data.shape == (10, 10), f"map shape {m.data.shape}"
    # Disk centre as seen from Earth maps to Stonyhurst longitude 0 and latitude B0, the
    # solar-axis tilt for that date. B0 is a real ephemeris quantity that swings +/-7.25
    # deg over a year, so this is a RANGE by nature, not a definition: on 2020-01-01 it
    # is about -3 deg. The longitude, by contrast, is 0 by construction.
    hgs = m.center.transform_to(frames.HeliographicStonyhurst)
    lon, lat = float(hgs.lon.deg), float(hgs.lat.deg)
    assert abs(lon) < 0.5, f"disk-centre Stonyhurst longitude {lon}, expected ~0"
    assert -8.0 < lat < 8.0, f"B0 = {lat} deg is outside its physical +/-7.25 deg range"
    # The observer distance must be about 1 au in early January (perihelion), which
    # confirms the ephemeris ran rather than defaulting to something inert.
    dist_au = float(m.observer_coordinate.radius.to(u.au).value)
    assert 0.98 < dist_au < 1.02, f"observer distance {dist_au} au"
    print(f"       (2020-01-01: lon {lon:+.4f} deg, B0 {lat:+.3f} deg, {dist_au:.5f} au)")


# --- 8. specutils + astroquery -------------------------------------------------------
print("[smoke] 8. specutils (and astroquery, import-only)")


@check("specutils recovers the centroid of a synthetic emission line")
def _specutils():
    import numpy as np
    import astropy.units as u
    import specutils
    from specutils.analysis import centroid
    # specutils 2.x renamed Spectrum1D to Spectrum. Support both rather than pinning the
    # version: the identity being tested is the same either way.
    Spec = getattr(specutils, "Spectrum", None) or specutils.Spectrum1D
    wav = np.linspace(6500.0, 6600.0, 1001) * u.AA
    line_at = 6562.8            # H-alpha
    flux = np.exp(-0.5 * ((wav.value - line_at) / 1.5) ** 2) * u.Jy
    spec = Spec(spectral_axis=wav, flux=flux)
    got = float(centroid(spec, region=None).to(u.AA).value)
    # A symmetric Gaussian's flux-weighted centroid is its centre. Tight, because the
    # profile is noiseless and the grid is fine.
    assert abs(got - line_at) < 0.05, f"centroid {got} A, expected {line_at} A"
    print(f"       ({Spec.__name__}: centroid {got:.4f} A vs {line_at} A)")


@check("astroquery imports and exposes a service class (NO network, by design)")
def _astroquery():
    # This env's one import-only check. astroquery's real work is an HTTP request to a
    # remote archive; making the build depend on that would mean the image cannot be
    # verified offline and fails on a third party's outage. So we assert only that the
    # package assembled and its service objects are constructed — which is the part that
    # could plausibly break on a new platform.
    from astroquery.simbad import Simbad
    assert hasattr(Simbad, "query_object"), "Simbad has no query_object"
    import astroquery
    print(f"       (astroquery {astroquery.__version__}, no network touched)")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: astro env assembles, imports, and works on "
      + platform.machine() + " (python " + platform.python_version() + ") — verified.")
