#!/usr/bin/env python3
# structbio.smoke.py — the D3 verification for the `structbio` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image). The risky
# natives are cctbx's large C++/boost-python stack and gemmi's compiled extension.
#
# THE STRONGEST CHECK HERE IS CROSS-IMPLEMENTATION, and this env was specified to make it
# possible: cctbx and gemmi are two INDEPENDENT implementations of crystallographic
# symmetry, with separate space-group tables and separate cell arithmetic. So they can be
# held against each other AND against closed forms, rather than either being checked
# against itself. Measured: their unit-cell volumes are bit-identical to each other and to
# the closed-form expression (relative difference exactly 0.0), and they agree on
# space-group order for every group tested.
#
# ONE LESSON IS BAKED INTO THE SPACE-GROUP TABLE BELOW, because writing this test taught
# it. The first draft asserted order 3 for `R 3` from a hand-typed table value. BOTH
# libraries independently returned 9 — and they are right: `R 3` defaults to HEXAGONAL
# axes, where the order is 9 (3 rotations x 3 centring translations); only the explicit
# rhombohedral setting `R 3 :R` has order 3. Two independent implementations agreeing
# against a hand-typed constant is the signal to re-check the constant, not the libraries.
# Both settings are now asserted, so the test encodes the trap instead of tripping on it.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import math
import platform
import sys
import traceback

FAILURES = []

# (Hermann-Mauguin symbol, order). Orders are from the International Tables; the two R3
# rows are the setting trap described above and are deliberately kept as a pair.
SPACE_GROUPS = [
    ("P 1", 1), ("P -1", 2), ("P 21/c", 4), ("C 2/c", 8), ("P 21 21 21", 4),
    ("I 4/m m m", 32), ("F m -3 m", 192), ("P 63/m m c", 24),
    ("R 3", 9),        # hexagonal axes (the default) -> 9, NOT 3
    ("R 3 :R", 3),     # rhombohedral axes -> 3
]

# A deliberately triclinic cell: no angle is 90 degrees, so every term of the closed-form
# volume expression is exercised rather than cancelling.
CELL = (11.1, 12.2, 13.3, 88.0, 99.0, 101.0)
# Silicon, for the cubic d-spacing identity d = a / sqrt(h^2+k^2+l^2).
A_SI = 5.4310
HKLS = [(1, 1, 1), (2, 2, 0), (3, 1, 1), (4, 0, 0), (3, 3, 1)]

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


def _closed_form_volume(a, b, c, al, be, ga):
    ar, br, cr = math.radians(al), math.radians(be), math.radians(ga)
    return a * b * c * math.sqrt(
        1.0 - math.cos(ar) ** 2 - math.cos(br) ** 2 - math.cos(cr) ** 2
        + 2.0 * math.cos(ar) * math.cos(br) * math.cos(cr))


def _gemmi_order(hm):
    import gemmi
    ops = gemmi.SpaceGroup(hm).operations()
    return len(ops.sym_ops) * len(ops.cen_ops)


# --- 1. imports -------------------------------------------------------------------
HEADLINE = [
    "numpy", "scipy",
    "gemmi",
    "cctbx", "cctbx.uctbx", "cctbx.sgtbx", "cctbx.miller",
    "iotbx", "scitbx", "libtbx",
    "Bio", "Bio.Seq", "Bio.SeqUtils",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


@check("both symmetry libraries report their versions")
def _versions():
    import Bio
    import gemmi
    # cctbx has no single __version__; its package record is the honest source, same rule
    # as warpx and geant4 (ask the package, not the program).
    import glob
    import json
    recs = glob.glob("/opt/conda/conda-meta/cctbx-base-*.json")
    assert recs, "no cctbx-base conda-meta record"
    cctbx_v = json.load(open(recs[0]))["version"]
    assert gemmi.__version__, "gemmi reports no version"
    STATE["versions"] = (gemmi.__version__, cctbx_v, Bio.__version__)
    print(f"       (gemmi {gemmi.__version__}, cctbx-base {cctbx_v}, biopython {Bio.__version__})")


# --- 2. the cross-implementation checks -------------------------------------------
print("[smoke] 2. cctbx vs gemmi vs closed form (independent implementations)")


@check("cctbx and gemmi agree with the International Tables on space-group order")
def _orders():
    from cctbx import sgtbx
    rows = []
    for hm, want in SPACE_GROUPS:
        g = _gemmi_order(hm)
        c = sgtbx.space_group_info(symbol=hm).group().order_z()
        rows.append((hm, want, g, c))
        assert g == want, f"gemmi says order {g} for {hm!r}, tables say {want}"
        assert c == want, f"cctbx says order {c} for {hm!r}, tables say {want}"
    r3 = {hm: w for hm, w, _, _ in rows if hm.startswith("R 3")}
    print(f"       ({len(rows)} groups, all three sources agree; the R3 setting pair "
          f"is {r3})")


@check("unit-cell volume is BIT-IDENTICAL across both libraries and the closed form")
def _volume():
    import gemmi
    from cctbx import uctbx
    want = _closed_form_volume(*CELL)
    v_gemmi = gemmi.UnitCell(*CELL).volume
    v_cctbx = uctbx.unit_cell(CELL).volume()
    # EQUALITY, not a tolerance. Two independent implementations of the same closed form
    # in double precision land on the same bits; measured relative difference exactly 0.0
    # against each other and against the formula computed here in pure Python.
    assert v_gemmi == want, f"gemmi {v_gemmi!r} != closed form {want!r}"
    assert v_cctbx == want, f"cctbx {v_cctbx!r} != closed form {want!r}"
    print(f"       (triclinic {CELL}: all three give {want!r} exactly)")


@check("cubic d-spacings match d = a/sqrt(h^2+k^2+l^2) in both libraries")
def _dspacing():
    import gemmi
    from cctbx import uctbx
    cell_g = gemmi.UnitCell(A_SI, A_SI, A_SI, 90.0, 90.0, 90.0)
    cell_c = uctbx.unit_cell((A_SI, A_SI, A_SI, 90, 90, 90))
    worst = 0.0
    for hkl in HKLS:
        want = A_SI / math.sqrt(sum(i * i for i in hkl))
        d_c = cell_c.d(hkl)
        d_g = 1.0 / math.sqrt(cell_g.calculate_1_d2(hkl))
        worst = max(worst, abs(d_c - want) / want, abs(d_g - want) / want)
    # Round-off only: this is a closed form both libraries implement, not a fitted value.
    assert worst < 1e-14, f"worst d-spacing deviation {worst:.3e}, expected round-off"
    print(f"       (silicon a={A_SI}, {len(HKLS)} reflections, worst relative "
          f"deviation {worst:.2e})")


@check("symmetry operations close under composition")
def _closure():
    import gemmi
    # A group is closed under composition — a structural identity, not a number to
    # compare. Any operator product landing outside the set means the table is wrong.
    for hm in ("P 21/c", "I 4/m m m", "F m -3 m"):
        ops = gemmi.SpaceGroup(hm).operations()
        triplets = {op.triplet() for op in ops}
        n = len(triplets)
        outside = 0
        for a in ops:
            for b in ops:
                if gemmi.Op(a.triplet()).combine(gemmi.Op(b.triplet())).wrap().triplet() \
                        not in triplets:
                    outside += 1
        assert outside == 0, f"{hm}: {outside} of {n * n} products fall outside the group"
        STATE.setdefault("closure", []).append((hm, n, n * n))
    print("       (" + "; ".join(f"{hm}: {n} ops, {sq} products, 0 outside"
                                 for hm, n, sq in STATE["closure"]) + ")")


# --- 3. sequence layer ------------------------------------------------------------
print("[smoke] 3. biopython")


@check("translation and reverse-complement obey their exact identities")
def _biopython():
    from Bio.Seq import Seq
    from Bio.SeqUtils import gc_fraction
    s = Seq("ATGGCCATTGTAATGGGCCGCTGAAAGGGTGCCCGATAG")
    # The standard genetic code is a lookup table, so this is an exact expected string,
    # not an approximation.
    assert str(s.translate()) == "MAIVMGR*KGAR*", f"translate gave {s.translate()!r}"
    # Reverse-complement is an involution: applying it twice is the identity.
    assert s.reverse_complement().reverse_complement() == s, \
        "reverse_complement is not an involution"
    gc = gc_fraction(s)
    assert abs(gc - 22.0 / 39.0) < 1e-12, f"gc_fraction {gc!r}"
    print(f"       (translate -> {s.translate()}, revcomp involution holds, "
          f"GC = {gc:.6f})")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: structbio env assembles; cctbx and gemmi agree exactly with each "
      "other and with closed forms on " + platform.machine()
      + f" (python {platform.python_version()}) — verified.")
