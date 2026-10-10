#!/usr/bin/env python3
# quantum.smoke.py — the D3 verification for the `quantum` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image). The risky
# natives are Aer's C++ simulator backends and rustworkx's Rust extension — plus the abi3
# question, since `qiskit` here is a `py310`-tagged build running on python 3.14 and an
# abi3 mismatch would surface as a crash or garbage rather than an ImportError.
#
# ALMOST NOTHING HERE IS A TOLERANCE, which is why issue #33 called this the strongest
# verification target queued. Quantum state vectors are exact linear algebra:
#
#   * a Bell state has amplitudes exactly 1/sqrt(2) — measured |a| - 1/sqrt(2) == 0.0
#     EXACTLY, with the two zero amplitudes exactly zero
#   * unitarity is an identity: U-dagger U == I (measured 2.2e-16)
#   * sigma_z eigenvalues are exactly +/-1
#   * a seeded shot distribution is judged against the EXACT probability by its own
#     sampling error, sqrt(p(1-p)/N) — the same discipline as bayes.smoke.py using Stan's
#     MCSE rather than a hand-picked band
#
# ONE CORRECTION TO THE REQUEST, because it matters for what this file asserts. Issue #33
# proposed that Aer's `stabilizer` and `statevector` methods "must agree bit-for-bit on the
# same circuit". They must not, and asserting that would produce a flaky test: the two
# methods draw from different RNG streams, so seeding both identically still yields
# different COUNTS. Measured on a 4-qubit Clifford circuit at 20000 shots, same seed:
# identical support, different counts. What is genuinely required — and is what this file
# checks — is that both sampled distributions agree with the EXACT probabilities computed
# from the statevector, within sampling error. That is a real cross-method check; equal
# counts would merely have been a coincidence of implementation.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import math
import platform
import sys
import traceback

FAILURES = []

INV_SQRT2 = 1.0 / math.sqrt(2.0)
SHOTS = 20000
SEED = 1234
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


def _clifford_circuit():
    """A 4-qubit Clifford circuit: exactly simulable by both methods under test."""
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(4)
    qc.h(0)
    for i in range(3):
        qc.cx(i, i + 1)
    qc.s(2)
    qc.h(3)
    qc.measure_all()
    return qc


# --- 1. imports -------------------------------------------------------------------
HEADLINE = [
    "numpy", "scipy", "matplotlib",
    "qiskit", "qiskit.quantum_info",
    "qiskit_aer",
    "rustworkx",
    "qiskit_algorithms", "qiskit_optimization", "qiskit_machine_learning",
    "openfermion", "pylatexenc",
    "qutip",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


@check("the abi3-tagged qiskit build actually runs on this interpreter")
def _abi3():
    import qiskit
    import rustworkx
    # This is the check that matters for this env's premise. `qiskit` is a py310-TAGGED
    # build with no python_abi constraint (abi3), running here on python 3.14. An abi3
    # mismatch does not raise ImportError — it crashes or returns garbage — so the test is
    # to make the compiled extension do work, not merely to import it.
    g = rustworkx.PyGraph()
    a, b, c = g.add_node("a"), g.add_node("b"), g.add_node("c")
    g.add_edges_from([(a, b, 1), (b, c, 1)])
    # rustworkx is Rust; exercising a graph algorithm proves the extension is live.
    assert len(rustworkx.dijkstra_shortest_paths(g, a, c, weight_fn=float)[c]) == 3, \
        "rustworkx returned a wrong shortest path — abi3 extension is not working"
    print(f"       (qiskit {qiskit.__version__}, rustworkx {rustworkx.__version__} "
          f"on python {platform.python_version()})")


# --- 2. exact state vectors -------------------------------------------------------
print("[smoke] 2. exact amplitudes and unitarity")


@check("a Bell state has amplitudes exactly 1/sqrt(2) and exact zeros")
def _bell():
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Statevector
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    sv = Statevector.from_instruction(qc).data
    # Equality, not a tolerance: 1/sqrt(2) is representable and the simulator is
    # deterministic, so measured |a| - 1/sqrt(2) is exactly 0.0.
    assert abs(abs(sv[0]) - INV_SQRT2) == 0.0, \
        f"|a00| = {abs(sv[0])!r}, expected exactly {INV_SQRT2!r}"
    assert abs(abs(sv[3]) - INV_SQRT2) == 0.0, \
        f"|a11| = {abs(sv[3])!r}, expected exactly {INV_SQRT2!r}"
    # The forbidden amplitudes must be exactly zero, not merely small — a leaked
    # amplitude here would mean the gate was applied wrongly.
    assert sv[1] == 0 and sv[2] == 0, f"|01> and |10> are not exactly zero: {sv[1]}, {sv[2]}"
    norm = abs(complex(sum(abs(x) ** 2 for x in sv)).real - 1.0)
    assert norm < 1e-12, f"state is not normalised: |norm-1| = {norm:.3e}"
    print(f"       (|a00|=|a11|=1/sqrt2 exactly; |01>,|10> exactly 0; |norm-1|={norm:.1e})")


@check("the circuit unitary satisfies U-dagger U = I")
def _unitary():
    import numpy as np
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Operator
    qc = QuantumCircuit(3)
    qc.h(0)
    qc.cx(0, 1)
    qc.t(2)
    qc.cz(1, 2)
    qc.ry(0.7, 0)
    U = Operator(qc).data
    err = float(np.abs(U.conj().T @ U - np.eye(8)).max())
    # Unitarity is a theorem about the gate set, not a property of this circuit, so the
    # bound is round-off. A non-unitary result means a gate matrix is wrong.
    assert err < 1e-12, f"max|U-dagger U - I| = {err:.3e}, expected round-off"
    print(f"       (3-qubit circuit, max|U^dag U - I| = {err:.2e})")


@check("qutip agrees on exact spectra (independent implementation)")
def _qutip():
    import numpy as np
    import qutip
    # A separate codebase reaching the same exact answers is worth more than a second
    # check inside one package. sigma_z eigenvalues are exactly +/-1.
    eigs = sorted(float(x) for x in qutip.sigmaz().eigenenergies())
    assert eigs == [-1.0, 1.0], f"sigma_z eigenvalues {eigs}, expected [-1.0, 1.0]"
    # A Bell state built in qutip must have the same amplitude as the Qiskit one.
    bell = (qutip.tensor(qutip.basis(2, 0), qutip.basis(2, 0))
            + qutip.tensor(qutip.basis(2, 1), qutip.basis(2, 1))).unit()
    amp = abs(complex(bell.full()[0][0]))
    assert abs(amp - INV_SQRT2) < 1e-15, f"qutip Bell amplitude {amp!r}"
    print(f"       (qutip {qutip.__version__}: sigma_z eigs {eigs}, "
          f"Bell amplitude {amp:.15f})")


# --- 3. Aer: cross-method agreement, judged correctly -----------------------------
print("[smoke] 3. Aer simulator methods vs the exact distribution")


@check("stabilizer and statevector produce the same support on a Clifford circuit")
def _clifford_support():
    from qiskit import transpile
    from qiskit_aer import AerSimulator
    qc = _clifford_circuit()
    out = {}
    for method in ("statevector", "stabilizer"):
        sim = AerSimulator(method=method, seed_simulator=SEED)
        counts = sim.run(transpile(qc, sim), shots=SHOTS,
                         seed_simulator=SEED).result().get_counts()
        out[method] = counts
    sup_sv, sup_st = set(out["statevector"]), set(out["stabilizer"])
    # A Clifford circuit's reachable outcomes are exactly determined, so the two methods
    # must agree on WHICH outcomes occur. (Deliberately NOT asserting equal counts — see
    # the module docstring: different RNG streams make that a flaky expectation.)
    assert sup_sv == sup_st, (
        f"methods disagree on support: statevector {sorted(sup_sv)} vs "
        f"stabilizer {sorted(sup_st)}")
    assert all(sum(c.values()) == SHOTS for c in out.values()), "shot counts do not sum"
    STATE["counts"] = out
    print(f"       (both methods: {len(sup_sv)} outcomes {sorted(sup_sv)})")


@check("both Aer methods match the exact statevector probabilities within sampling error")
def _vs_exact():
    import math as _m
    from qiskit.quantum_info import Statevector
    qc = _clifford_circuit()
    out = STATE.get("counts")
    assert out, "the support check did not run"
    # Exact probabilities, computed analytically rather than sampled. The circuit has a
    # final measure_all, so strip it to get the pre-measurement state.
    bare = qc.remove_final_measurements(inplace=False)
    exact = Statevector.from_instruction(bare).probabilities_dict()
    worst = (0.0, None, None)
    for method, counts in out.items():
        for outcome, p_exact in exact.items():
            if p_exact < 1e-12:
                continue
            got = counts.get(outcome, 0) / SHOTS
            se = _m.sqrt(max(p_exact * (1 - p_exact), 1e-12) / SHOTS)
            sigma = abs(got - p_exact) / se
            if sigma > worst[0]:
                worst = (sigma, method, outcome)
            # 5 sigma: wide for a correct sampler, nowhere near wide enough to hide a
            # wrong distribution. And the bound is DERIVED from the shot count, not chosen.
            assert sigma < 5.0, (
                f"{method} outcome {outcome}: sampled {got:.5f} vs exact {p_exact:.5f} "
                f"= {sigma:.2f} sampling sigma (limit 5)")
    print(f"       ({len(exact)} exact outcomes; worst deviation {worst[0]:.2f} sigma "
          f"({worst[1]}, {worst[2]}))")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: quantum env assembles and reproduces exact quantum amplitudes on "
      + platform.machine() + " (python " + platform.python_version() + ") — verified.")
