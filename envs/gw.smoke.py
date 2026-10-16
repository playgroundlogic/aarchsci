#!/usr/bin/env python3
# gw.smoke.py — the D3 verification for the `gw` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image). The risky
# natives are LALSuite's C libraries and their SWIG bindings, and PyCBC's compiled FFT and
# filtering backends.
#
# THE IMPORTS ARE NOT A FORMALITY HERE, and this env is the reason the `pycbc >=2.10` floor
# exists. `pycbc` itself imports fine at 2.8.2 while `pycbc.waveform` does not —
# `pycbc.cosmology` does `from astropy.cosmology.core import CosmologyError`, a module
# removed in modern astropy, and the astropy that solves here is 8.0.1. So a top-level
# `import pycbc` would have passed a naive gate while every useful submodule was dead. The
# submodules are imported explicitly below for that reason.
#
# THREE KINDS OF CHECK, in descending exactness:
#
#   1. AN IDENTITY AMONG LAL'S OWN CONSTANTS. MTSUN_SI is the solar mass in seconds, i.e.
#      M_sun * G / c^3, and LAL also publishes MSUN_SI, G_SI and C_SI separately. Those
#      four must be mutually consistent, which is a closed-form relation needing no
#      external reference — measured, exactly equal.
#   2. A CLOSED FORM vs A LIBRARY FUNCTION. Chirp mass is
#      (m1*m2)^(3/5) / (m1+m2)^(1/5); bilby computes it independently. Measured
#      bit-identical, and the component-mass round trip closes to 2e-16.
#   3. NORMALISATION AND DISCRIMINATION OF THE MATCHED FILTER. A template's match with
#      itself must be 1, and with a 2.5x scaled copy of itself also 1 (the filter is
#      normalised), while a different-mass template must be clearly below 1. Note this is
#      asserted with a tolerance, not as equality: measured, the self-match is
#      1.0000000000000002 — one ulp ABOVE one — so `m <= 1.0` would fail.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import math
import platform
import sys
import traceback

FAILURES = []

# GW150914-like masses, in solar masses: a well-conditioned binary for a quick waveform.
M1, M2 = 36.0, 29.0
DELTA_T = 1.0 / 4096
DELTA_F = 1.0 / 32
F_LOWER = 30.0
APPROX = "IMRPhenomD"
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


# --- 1. imports, including the submodules that actually broke ---------------------
HEADLINE = [
    "numpy", "scipy", "matplotlib",
    "lal", "lalsimulation",
    "bilby", "bilby.gw", "bilby.gw.conversion", "bilby.core.prior",
    "pycbc",
    # These four are the point. `import pycbc` succeeds at 2.8.2; these do not, because
    # pycbc.cosmology imports a module astropy removed. See the docstring.
    "pycbc.cosmology", "pycbc.conversions", "pycbc.waveform", "pycbc.filter", "pycbc.psd",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


@check("the installed versions are the combination that actually works")
def _versions():
    import astropy
    import bilby
    import lal
    import pycbc
    major, minor = (int(x) for x in pycbc.__version__.split(".")[:2])
    # The floor is the whole reason this env is pinned; assert it rather than trusting the
    # spec to have been read.
    assert (major, minor) >= (2, 10), (
        f"pycbc {pycbc.__version__} is below the 2.10 floor — its waveform module cannot "
        "import against current astropy")
    STATE["versions"] = (bilby.__version__, lal.__version__, pycbc.__version__,
                         astropy.__version__)
    print(f"       (bilby {bilby.__version__}, lal {lal.__version__}, "
          f"pycbc {pycbc.__version__}, astropy {astropy.__version__})")


# --- 2. identities ----------------------------------------------------------------
print("[smoke] 2. closed-form identities")


@check("LAL's own physical constants are mutually consistent")
def _lal_constants():
    import lal
    # MTSUN_SI (solar mass in seconds) must equal MSUN_SI * G_SI / C_SI^3. All four are
    # published independently by LAL, so this needs no external reference value — it is a
    # closed-form relation among the library's own numbers, and it exercises the SWIG
    # binding layer that exposes them.
    lhs = lal.MTSUN_SI
    rhs = lal.MSUN_SI * lal.G_SI / lal.C_SI ** 3
    assert lhs == rhs, (
        f"MTSUN_SI {lhs!r} != MSUN_SI*G_SI/C_SI^3 {rhs!r} — LAL's constants are "
        "inconsistent, or the bindings are returning the wrong ones")
    print(f"       (MTSUN_SI = MSUN*G/c^3 = {lhs!r} exactly)")


@check("chirp mass matches the closed form and the component-mass round trip closes")
def _chirp_mass():
    import bilby
    # (m1*m2)^(3/5) / (m1+m2)^(1/5), computed here in pure Python against bilby's own
    # implementation. Measured bit-identical.
    want = (M1 * M2) ** 0.6 / (M1 + M2) ** 0.2
    got = bilby.gw.conversion.component_masses_to_chirp_mass(M1, M2)
    assert got == want, f"bilby chirp mass {got!r} != closed form {want!r}"
    # And the inverse transform must return the masses it started from.
    q = M2 / M1
    m1b, m2b = bilby.gw.conversion.chirp_mass_and_mass_ratio_to_component_masses(got, q)
    err = max(abs(m1b - M1) / M1, abs(m2b - M2) / M2)
    assert err < 1e-12, (
        f"component-mass round trip gave ({m1b!r}, {m2b!r}) from ({M1}, {M2}), "
        f"relative error {err:.3e}")
    print(f"       (M_c = {got!r} exactly; round trip closes to {err:.2e})")


@check("a uniform prior's rescale/cdf round trip is exact")
def _prior():
    import numpy as np
    from bilby.core.prior import Uniform
    p = Uniform(minimum=10.0, maximum=80.0, name="chirp_mass")
    u = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    # rescale maps the unit cube to the prior's support and cdf maps back; they are exact
    # inverses for a uniform prior, so this is an equality on the whole array.
    back = p.cdf(p.rescale(u))
    err = float(np.max(np.abs(back - u)))
    assert err == 0.0, f"rescale/cdf round trip is not exact: max error {err!r}"
    # The density is 1/(b-a) inside the support and exactly zero outside.
    assert p.prob(45.0) == 1.0 / 70.0, f"prob inside = {p.prob(45.0)!r}, expected 1/70"
    assert p.prob(5.0) == 0.0, "prob outside the support is not exactly zero"
    print(f"       (rescale/cdf round trip exact at {len(u)} points; "
          "density 1/70 inside, 0 outside)")


# --- 3. waveforms and the matched filter -------------------------------------------
print("[smoke] 3. waveform generation and matched-filter normalisation")


@check("a time-domain waveform is generated at the requested sample rate")
def _waveform():
    import numpy as np
    from pycbc.waveform import get_td_waveform
    hp, hc = get_td_waveform(approximant=APPROX, mass1=M1, mass2=M2,
                             delta_t=DELTA_T, f_lower=F_LOWER)
    # The sample spacing must be exactly what was asked for; a silently resampled waveform
    # would corrupt every downstream time estimate.
    assert hp.delta_t == DELTA_T, f"delta_t is {hp.delta_t!r}, requested {DELTA_T!r}"
    assert len(hp) == len(hc), "plus and cross polarisations differ in length"
    assert len(hp) > 1000, f"waveform is only {len(hp)} samples"
    amp = float(np.max(np.abs(np.asarray(hp))))
    assert amp > 0.0 and math.isfinite(amp), f"waveform amplitude is {amp!r}"
    # Both polarisations must be non-trivial: a bug that zeroed the cross polarisation
    # would pass a plus-only check.
    assert float(np.max(np.abs(np.asarray(hc)))) > 0.0, "cross polarisation is all zeros"
    dur = len(hp) * hp.delta_t
    print(f"       ({APPROX}, {M1}+{M2} Msun: {len(hp)} samples, {dur:.2f} s, "
          f"delta_t exact)")


@check("the matched filter is normalised and discriminates between templates")
def _match():
    from pycbc.filter import match
    from pycbc.psd import aLIGOZeroDetHighPower
    from pycbc.waveform import get_fd_waveform
    hf, _ = get_fd_waveform(approximant=APPROX, mass1=M1, mass2=M2,
                            delta_f=DELTA_F, f_lower=F_LOWER)
    psd = aLIGOZeroDetHighPower(len(hf), hf.delta_f, 20.0)
    m_self, _ = match(hf, hf, psd=psd, low_frequency_cutoff=F_LOWER)
    # A TOLERANCE, NOT AN EQUALITY, and deliberately: measured, the self-match is
    # 1.0000000000000002 — one ulp ABOVE one — so asserting `<= 1.0` would fail on
    # correct behaviour. The normalisation is what is being checked, to round-off.
    assert abs(float(m_self) - 1.0) < 1e-12, \
        f"self-match is {float(m_self)!r}, expected 1 to round-off"
    # Scale invariance: the filter normalises, so a 2.5x louder copy is still a perfect
    # match. This catches a missing normalisation that the self-match alone cannot.
    m_scaled, _ = match(hf, hf * 2.5, psd=psd, low_frequency_cutoff=F_LOWER)
    assert abs(float(m_scaled) - 1.0) < 1e-12, \
        f"match against a 2.5x scaled copy is {float(m_scaled)!r}, expected 1"
    # Discrimination: a different binary must NOT match well. Without this the two checks
    # above would pass for a filter that returns 1 unconditionally.
    hf2, _ = get_fd_waveform(approximant=APPROX, mass1=20.0, mass2=20.0,
                             delta_f=DELTA_F, f_lower=F_LOWER)
    m_other, _ = match(hf, hf2, psd=psd, low_frequency_cutoff=F_LOWER)
    assert float(m_other) < 0.9, (
        f"a (20,20) template matches the ({M1},{M2}) one at {float(m_other):.4f}; the "
        "filter is not discriminating")
    print(f"       (self {float(m_self):.15f}, 2.5x copy {float(m_scaled):.15f}, "
          f"(20,20) template {float(m_other):.4f})")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: gw env assembles; LAL's constants are self-consistent and the "
      "matched filter is normalised on " + platform.machine()
      + f" (python {platform.python_version()}) — verified.")
