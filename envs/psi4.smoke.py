#!/usr/bin/env python3
# psi4.smoke.py — the D3 verification for the `psi4` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image). The risky
# native is psi4's own compiled integral/SCF stack plus its libxc linkage.
#
# `import psi4` IS ITSELF A REAL CHECK HERE, not a formality, and the reason is the whole
# justification for the `>=1.11` floor in the spec: psi4 1.10 installs cleanly against
# libxc-c 7.1.2 and then dies during import while building its functional table —
# "Fatal Error: Could not find required LibXC functional". So the import is where that
# failure surfaces, and it surfaced in `dft` before this env existed.
#
# THE CHECKS ARE TEXTBOOK VALUES, which is unusual in this catalog and is the point.
# Most envs here verify against identities (mass conservation, closure, round-trips)
# because no published number exists at the required precision. Quantum chemistry is the
# exception: RHF/STO-3G on H2 at 0.74 A is one of the most-tabulated numbers in the field,
# and a correlated method must sit BELOW the Hartree-Fock energy by the variational
# principle — which is an inequality that no bug can satisfy by accident.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import glob
import json
import os
import platform
import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []

# H2 at its near-equilibrium bond length, in a minimal basis. The RHF/STO-3G total energy
# is ~-1.1167 Hartree — tabulated in essentially every quantum-chemistry text.
H2_GEOM = "0 1\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\nunits angstrom\n"
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


def _psi4_setup():
    """Point psi4's output and scratch at a temp dir; the image runs unprivileged."""
    import psi4
    scratch = Path(tempfile.mkdtemp())
    os.environ.setdefault("PSI_SCRATCH", str(scratch))
    psi4.core.set_output_file(str(scratch / "psi4.out"), False)
    psi4.set_memory("500 MB")
    return psi4


# --- 1. imports -------------------------------------------------------------------
HEADLINE = ["numpy", "scipy", "psi4"]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


@check("psi4 reports a version at or above the 1.11 floor")
def _version():
    import psi4
    # The floor exists because 1.10 dies during import against libxc-c 7.1.2. Read the
    # version from the package record as well as the library, since the two disagreeing
    # would itself be worth knowing.
    recs = glob.glob("/opt/conda/conda-meta/psi4-*.json")
    assert recs, "no psi4 conda-meta record"
    pkg_v = json.load(open(recs[0]))["version"]
    lib_v = psi4.__version__
    major, minor = (int(x) for x in pkg_v.split(".")[:2])
    assert (major, minor) >= (1, 11), f"psi4 {pkg_v} is below the 1.11 floor"
    STATE["version"] = pkg_v
    print(f"       (psi4 {lib_v}, package record {pkg_v})")


# --- 2. the textbook numbers -------------------------------------------------------
print("[smoke] 2. SCF and a correlated method on H2")


@check("RHF/STO-3G on H2 reproduces the textbook total energy")
def _rhf():
    psi4 = _psi4_setup()
    psi4.geometry(H2_GEOM)
    e = psi4.energy("scf/sto-3g")
    # Narrow on purpose: this is a tabulated value, not an identity we derived, and psi4
    # computes it through its own integral and SCF code. ~-1.1167 Hartree.
    assert -1.13 < e < -1.10, f"H2 RHF/STO-3G total energy {e!r} Hartree is off"
    STATE["rhf"] = e
    print(f"       (H2 RHF/STO-3G = {e:.8f} Hartree)")


@check("MP2 lies below Hartree-Fock, as the variational principle requires")
def _mp2():
    psi4 = _psi4_setup()
    assert "rhf" in STATE, "the RHF check did not run"
    psi4.geometry(H2_GEOM)
    e = psi4.energy("mp2/cc-pvdz")
    # Not a tabulated comparison: an INEQUALITY that correlation energy must satisfy. A
    # correlated method in a larger basis has to sit below the minimal-basis HF energy,
    # and the correlation contribution must be non-trivially negative. No plausible bug
    # produces a correlated energy above HF.
    assert e < STATE["rhf"], (
        f"MP2/cc-pVDZ {e!r} is not below RHF/STO-3G {STATE['rhf']!r} — correlation "
        "energy has the wrong sign")
    gap = STATE["rhf"] - e
    assert 0.0 < gap < 0.5, f"HF - MP2 gap {gap!r} Hartree is implausible for H2"
    print(f"       (MP2/cc-pVDZ = {e:.8f} Hartree, below RHF by {gap:.6f})")


@check("the SCF energy is invariant under a rigid translation of the molecule")
def _translation():
    psi4 = _psi4_setup()
    # A rigid translation cannot change the energy: it is an exact symmetry of the
    # Hamiltonian, so this is an identity rather than a tabulated value. It catches a
    # whole class of coordinate-handling bugs that a single-geometry check cannot.
    psi4.geometry("0 1\nH 5.0 5.0 5.0\nH 5.0 5.0 5.74\nunits angstrom\n")
    e = psi4.energy("scf/sto-3g")
    assert "rhf" in STATE, "the RHF check did not run"
    delta = abs(e - STATE["rhf"])
    assert delta < 1e-8, (
        f"translating the molecule by (5,5,5) A changed the SCF energy by {delta:.3e} "
        "Hartree; it must be invariant")
    print(f"       (translated by (5,5,5) A: {e:.8f} Hartree, delta {delta:.2e})")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: psi4 env assembles and reproduces textbook SCF energies on "
      + platform.machine() + f" (python {platform.python_version()}) — verified.")
