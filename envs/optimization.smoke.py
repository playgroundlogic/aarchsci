#!/usr/bin/env python3
# optimization.smoke.py — the D3 verification for the `optimization` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image) for the
# LP/MIP solver stack. The risky parts are the compiled solver cores (HiGHS, SCIP, CBC)
# and their native Python bindings, every one of which can import and then compute wrong.
#
# WHY THIS ENV'S VERIFICATION IS UNUSUALLY STRONG, and why no fixture is staged:
# optimization has exact identities available, so nothing here is a tolerance band.
#
#   1. The optimum itself. The LP below is the textbook Wyndor Glass problem, whose
#      solution is x=2, y=6, objective 36 EXACTLY — not a measured value we once observed
#      and now assert, a derivable one.
#   2. STRONG DUALITY. At an LP optimum the primal and dual objectives are equal, by
#      theorem. So asserting a zero duality gap tests the solver against mathematics
#      rather than against a recorded number. Measured gap: 0.0.
#   3. CROSS-SOLVER AGREEMENT. An LP optimum is unique in VALUE even when the optimal
#      vertex is not, so HiGHS and SCIP — unrelated codebases — must return the same
#      objective on identical input. Measured delta: 0.0.
#
# Issue #23 proposed the Netlib `afiro` instance and its 1985 published optimum. That is
# a good fixture and is deliberately NOT used: fetching it at run time is the `whitebox`
# wontfix pattern, and the constructed LP above gives an exactly-known optimum with no
# staged bytes at all. Constructing a problem instance is legitimate for the same reason
# generating a mesh is (cfd-fv, fem-cfd) — it is input geometry, not fitted physical data.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import platform
import shutil
import subprocess
import sys
import traceback

FAILURES = []

# max 3x + 5y  s.t.  x <= 4 ; 2y <= 12 ; 3x + 2y <= 18 ; x,y >= 0
LP_OBJ_EXACT = 36.0
LP_X_EXACT = (2.0, 6.0)
LP_RHS = (4.0, 12.0, 18.0)

RESULTS = {}


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


# --- 1. imports -------------------------------------------------------------------
HEADLINE = ["numpy", "scipy", "pandas", "highspy", "pyscipopt"]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. solver binaries present ---------------------------------------------------
print("[smoke] 2. solver binaries")


@check("the CBC solver binary is present and reports its version")
def _cbc():
    # coin-or-cbc ships no python binding here, so it is exercised as a binary. Its value
    # in this env is as a third independent implementation, not as an API.
    exe = shutil.which("cbc")
    assert exe, "cbc not on PATH"
    out = subprocess.run([exe, "-version"], capture_output=True, text=True,
                         timeout=300).stdout
    assert "Version" in out or "version" in out.lower(), f"unexpected cbc output:\n{out[:300]}"
    first = next((ln for ln in out.splitlines() if ln.strip()), "")
    print(f"       ({first.strip()[:70]})")


# --- 3. the LP: exact optimum, strong duality, cross-solver agreement -------------
print("[smoke] 3. linear programming (exact optimum + strong duality)")


@check("HiGHS finds the exact LP optimum and closes the duality gap to zero")
def _highs():
    import numpy as np
    import highspy
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    inf = highspy.kHighsInf
    h.addVars(2, np.array([0.0, 0.0]), np.array([inf, inf]))
    # HiGHS minimises, so the maximisation is posed with negated costs.
    h.changeColsCost(2, np.array([0, 1], dtype=np.int32), np.array([-3.0, -5.0]))
    h.addRow(-inf, LP_RHS[0], 1, np.array([0], dtype=np.int32), np.array([1.0]))
    h.addRow(-inf, LP_RHS[1], 1, np.array([1], dtype=np.int32), np.array([2.0]))
    h.addRow(-inf, LP_RHS[2], 2, np.array([0, 1], dtype=np.int32), np.array([3.0, 2.0]))
    h.run()
    status = h.modelStatusToString(h.getModelStatus())
    assert status == "Optimal", f"HiGHS status {status!r}, expected Optimal"
    sol = h.getSolution()
    primal = -h.getInfo().objective_function_value
    assert abs(primal - LP_OBJ_EXACT) < 1e-9, \
        f"HiGHS objective {primal!r}, exact optimum is {LP_OBJ_EXACT}"
    x, y = (float(v) for v in list(sol.col_value)[:2])
    assert abs(x - LP_X_EXACT[0]) < 1e-9 and abs(y - LP_X_EXACT[1]) < 1e-9, \
        f"HiGHS vertex ({x}, {y}), exact optimum is {LP_X_EXACT}"
    # Strong duality is a theorem, so this is an identity and not a tolerance: the dual
    # objective (duals dotted with the right-hand side) must equal the primal objective.
    dual = -sum(float(d) * r for d, r in zip(list(sol.row_dual), LP_RHS))
    gap = abs(primal - dual)
    assert gap < 1e-9, (
        f"duality gap {gap:.3e} — primal {primal!r} vs dual {dual!r}. At an LP optimum "
        "these are equal by strong duality, so a nonzero gap means the solver is wrong")
    RESULTS["highs"] = primal
    print(f"       (x={x:.6f}, y={y:.6f}, objective={primal:.6f}, duality gap={gap:.1e})")


@check("SCIP independently reproduces the same LP optimum")
def _scip():
    import pyscipopt
    m = pyscipopt.Model()
    m.hideOutput()
    X = m.addVar("x", lb=0)
    Y = m.addVar("y", lb=0)
    m.setObjective(3 * X + 5 * Y, "maximize")
    m.addCons(X <= LP_RHS[0])
    m.addCons(2 * Y <= LP_RHS[1])
    m.addCons(3 * X + 2 * Y <= LP_RHS[2])
    m.optimize()
    assert m.getStatus() == "optimal", f"SCIP status {m.getStatus()!r}"
    obj = m.getObjVal()
    assert abs(obj - LP_OBJ_EXACT) < 1e-9, \
        f"SCIP objective {obj!r}, exact optimum is {LP_OBJ_EXACT}"
    # The real point of a second solver: an LP optimum is unique in value even when the
    # vertex is not, so two unrelated implementations must agree on the objective.
    other = RESULTS.get("highs")
    assert other is not None, "HiGHS check did not run; nothing to cross-validate against"
    delta = abs(obj - other)
    assert delta < 1e-9, f"SCIP {obj!r} and HiGHS {other!r} disagree by {delta:.3e}"
    print(f"       (SCIP objective={obj:.6f}, delta from HiGHS={delta:.1e})")


# --- 4. mixed-integer programming -------------------------------------------------
print("[smoke] 4. mixed-integer programming")


@check("SCIP solves a 0/1 knapsack to its exactly-known optimum")
def _mip():
    import pyscipopt
    # Items (value, weight) with capacity 10. The optimal set is exactly derivable by
    # enumeration: take weights 5+4=9 for value 10+7=17; nothing reaches 18 at or under
    # capacity. Integrality makes the answer exact rather than approximate.
    vals = [10, 7, 4, 3]
    wts = [5, 4, 3, 2]
    cap = 10
    best = 0
    for mask in range(1 << len(vals)):
        w = sum(wts[i] for i in range(len(vals)) if mask >> i & 1)
        if w <= cap:
            best = max(best, sum(vals[i] for i in range(len(vals)) if mask >> i & 1))
    m = pyscipopt.Model()
    m.hideOutput()
    xs = [m.addVar(f"x{i}", vtype="B") for i in range(len(vals))]
    m.setObjective(sum(v * x for v, x in zip(vals, xs)), "maximize")
    m.addCons(sum(w * x for w, x in zip(wts, xs)) <= cap)
    m.optimize()
    assert m.getStatus() == "optimal", f"SCIP MIP status {m.getStatus()!r}"
    got = round(m.getObjVal())
    assert got == best, f"knapsack optimum {got}, brute force says {best}"
    # The solution must actually be integral, not merely optimal in value.
    chosen = [round(m.getVal(x)) for x in xs]
    assert all(c in (0, 1) for c in chosen), f"non-integral solution {chosen}"
    assert sum(w * c for w, c in zip(wts, chosen)) <= cap, "solution violates capacity"
    print(f"       (knapsack optimum={got}, brute force={best}, x={chosen})")


# --- 5. scipy's own LP, as a fourth opinion ---------------------------------------
print("[smoke] 5. scipy.optimize.linprog (HiGHS-backed)")


@check("scipy.optimize.linprog agrees, exercising its bundled HiGHS path")
def _scipy_lp():
    import numpy as np
    from scipy.optimize import linprog
    # scipy's default LP method is HiGHS, reached through scipy's own vendored binding
    # rather than highspy — a different code path to the same solver, so it checks the
    # scipy build as well as the answer.
    res = linprog(c=[-3.0, -5.0],
                  A_ub=[[1.0, 0.0], [0.0, 2.0], [3.0, 2.0]], b_ub=list(LP_RHS),
                  bounds=[(0, None), (0, None)])
    assert res.status == 0, f"linprog failed: {res.message}"
    obj = -res.fun
    assert abs(obj - LP_OBJ_EXACT) < 1e-9, f"linprog objective {obj!r}, expected {LP_OBJ_EXACT}"
    print(f"       (linprog objective={obj:.6f}, x={np.round(res.x, 6).tolist()})")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: optimization env assembles, imports, and solves LP/MIP to exact "
      "optima on " + platform.machine() + " (python " + platform.python_version()
      + ") — verified.")
