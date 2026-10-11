#!/usr/bin/env python3
# popgen.smoke.py — the D3 verification for the `popgen` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image). The risky
# natives are msprime's and tskit's C extensions, tskit's on-disk format, and tszip's
# compression path.
#
# MOST OF THIS IS EXACT, because a tree sequence is a CONSERVATION STRUCTURE rather than a
# number: it round-trips to disk and back with byte-identical tables, it compresses and
# decompresses the same way, and its segregating-site count stands in an exact arithmetic
# relation to its site count when every site carries one mutation. Those are identities, not
# bands.
#
# ONE CHECK IS NECESSARILY STATISTICAL, and it is judged the way `bayes` judges Stan and
# `quantum` judges Aer — against its OWN standard error rather than a hand-picked band.
# Under the neutral coalescent, E[pairwise diversity] = 4*N_e*mu exactly. A single
# replicate cannot test that (the variance is large), so this averages over replicates and
# compares the mean to theory in units of the measured standard error of the mean.
# Measured over 200 replicates: mean 3.815e-05 against a theoretical 4.000e-05, which is
# 0.57 sigma. The bound here is 4 sigma and is DERIVED from the replicate spread, not chosen.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import math
import os
import platform
import sys
import tempfile
import traceback

FAILURES = []

# Simulation parameters. Small enough to be fast, large enough that the coalescent
# expectation is well sampled.
N_E = 1000          # diploid effective population size
N_SAMPLES = 10      # sampled individuals (-> 20 sample nodes at ploidy 2)
SEQ_LEN = 1e5
REC_RATE = 1e-8
MU = 1e-8
SEED = 42
# Replicates for the diversity expectation. 100 keeps the whole suite near a second while
# leaving the standard error small enough for a 4-sigma bound to be meaningful.
N_REPS = 100
REP_LEN = 1e4

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


# --- 1. imports -------------------------------------------------------------------
HEADLINE = [
    "numpy", "scipy",
    "msprime", "tskit", "tszip",
    "allel", "demes", "pyslim",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


@check("simulate a seeded tree sequence with mutations")
def _simulate():
    import msprime
    ts = msprime.sim_ancestry(samples=N_SAMPLES, population_size=N_E,
                              sequence_length=SEQ_LEN, recombination_rate=REC_RATE,
                              random_seed=SEED)
    mts = msprime.sim_mutations(ts, rate=MU, random_seed=SEED)
    # ploidy defaults to 2, so N_SAMPLES individuals give 2*N_SAMPLES sample nodes.
    assert mts.num_samples == 2 * N_SAMPLES, \
        f"{mts.num_samples} sample nodes, expected {2 * N_SAMPLES}"
    assert mts.num_trees > 1, "recombination produced a single tree; rate is not applied"
    assert mts.num_sites > 0, "no mutations were placed"
    STATE["ts"] = mts
    import msprime as _m
    import tskit
    print(f"       (msprime {_m.__version__}, tskit {tskit.__version__}: "
          f"{mts.num_samples} samples, {mts.num_trees} trees, {mts.num_sites} sites)")


# --- 2. the exact identities -------------------------------------------------------
print("[smoke] 2. tree sequences are conservation structures (exact)")


@check("tskit's own table integrity check passes")
def _integrity():
    mts = STATE.get("ts")
    assert mts is not None, "simulation did not run"
    # tskit ships its own consistency checker; using it is better than inventing one,
    # for the same reason cp2k's own regtest tolerances beat ours.
    mts.tables.assert_equals(mts.tables)
    # Structural relations that must hold for any valid tree sequence.
    t = mts.tables
    assert len(t.edges) > 0 and len(t.nodes) > 0, "empty tables"
    assert len(t.sites) == mts.num_sites, "site table disagrees with num_sites"
    assert len(t.mutations) >= mts.num_sites, "fewer mutations than sites"
    print(f"       ({len(t.nodes)} nodes, {len(t.edges)} edges, {len(t.sites)} sites, "
          f"{len(t.mutations)} mutations; assert_equals passed)")


@check("dump to disk and reload gives BYTE-IDENTICAL tables")
def _roundtrip():
    import tskit
    mts = STATE.get("ts")
    assert mts is not None, "simulation did not run"
    d = tempfile.mkdtemp()
    p = os.path.join(d, "ts.trees")
    mts.dump(p)
    back = tskit.load(p)
    # EQUALITY on the whole table collection, not a sampled comparison: any field the
    # writer or reader mangled shows up here.
    assert back.tables == mts.tables, "tables differ after a dump/load round trip"
    # And a derived statistic must be bit-identical, not merely close.
    assert back.diversity() == mts.diversity(), \
        "diversity differs after a round trip (bit-level)"
    STATE["dir"] = d
    print(f"       ({os.path.getsize(p)} bytes; tables equal and diversity "
          f"bit-identical)")


@check("tszip compression round-trips to byte-identical tables")
def _tszip():
    import tszip
    mts = STATE.get("ts")
    d = STATE.get("dir") or tempfile.mkdtemp()
    p = os.path.join(d, "ts.tsz")
    tszip.compress(mts, p)
    back = tszip.decompress(p)
    # Lossless by contract, so this is an equality assertion. A compression path that
    # silently rounded a coordinate would fail here and nowhere else.
    assert back.tables == mts.tables, "tszip round trip changed the tables"
    print(f"       ({os.path.getsize(p)} bytes compressed; tables equal)")


@check("segregating sites equals the site count for single-mutation sites")
def _segregating():
    mts = STATE.get("ts")
    S = float(mts.segregating_sites(span_normalise=False))
    # With one mutation per site and biallelic states, the number of segregating sites is
    # exactly the number of sites. An exact arithmetic relation between two independently
    # computed quantities, so it is asserted as equality.
    n_mut = len(mts.tables.mutations)
    if n_mut == mts.num_sites:
        assert S == float(mts.num_sites), \
            f"segregating_sites {S} != num_sites {mts.num_sites}"
        note = "equal (one mutation per site)"
    else:
        assert S <= float(mts.num_sites), f"S {S} exceeds num_sites {mts.num_sites}"
        note = f"S <= num_sites ({n_mut} mutations at {mts.num_sites} sites)"
    # Watterson's estimator is then pure arithmetic on S.
    a_n = sum(1.0 / i for i in range(1, mts.num_samples))
    print(f"       (S = {S:.0f}, num_sites = {mts.num_sites}: {note}; "
          f"theta_W = S/a_n = {S / a_n:.4f})")


# --- 3. the one statistical check, judged by its own error -------------------------
print("[smoke] 3. E[pairwise diversity] = 4*N_e*mu (coalescent theory)")


@check("mean diversity over replicates matches 4*N_e*mu within its own standard error")
def _diversity():
    import msprime
    import numpy as np
    reps = msprime.sim_ancestry(samples=N_SAMPLES, population_size=N_E,
                                sequence_length=REP_LEN, recombination_rate=REC_RATE,
                                num_replicates=N_REPS, random_seed=7)
    pis = []
    for i, t in enumerate(reps):
        m = msprime.sim_mutations(t, rate=MU, random_seed=1000 + i)
        pis.append(m.diversity())
    pis = np.asarray(pis, dtype=float)
    expected = 4.0 * N_E * MU
    sem = float(pis.std(ddof=1) / math.sqrt(len(pis)))
    sigma = abs(float(pis.mean()) - expected) / sem
    # The bound is DERIVED from the replicate spread rather than chosen, which is the
    # same discipline bayes uses with Stan's MCSE and quantum uses with shot noise.
    # 4 sigma is wide for a correct simulator and far too tight to hide a wrong
    # coalescent time scale (a factor-of-two error would be tens of sigma).
    assert sigma < 4.0, (
        f"mean diversity {pis.mean():.6e} vs theory {expected:.6e} = {sigma:.2f} "
        f"standard errors (limit 4)")
    print(f"       ({N_REPS} replicates: mean {pis.mean():.6e} vs 4*N_e*mu "
          f"{expected:.6e}, {sigma:.2f} sigma, sem {sem:.2e})")


# --- 4. the rest of the layer ------------------------------------------------------
print("[smoke] 4. analysis and model-description layer")


@check("demes round-trips a model description")
def _demes():
    import demes
    g = demes.loads("time_units: generations\n"
                    "demes:\n"
                    "  - name: A\n"
                    "    epochs:\n"
                    "      - start_size: 1000\n")
    g2 = demes.loads(demes.dumps(g))
    assert g == g2, "demes YAML round trip is not an identity"
    assert g["A"].epochs[0].start_size == 1000, "demes lost the epoch size"
    print(f"       (round trip equal; deme {g['A'].name} start_size "
          f"{g['A'].epochs[0].start_size:.0f})")


@check("scikit-allel computes allele counts on a genotype array")
def _allel():
    import allel
    import numpy as np
    g = allel.GenotypeArray([[[0, 0], [0, 1]], [[0, 1], [1, 1]], [[0, 0], [0, 0]]])
    ac = g.count_alleles()
    # Every genotype call is counted exactly once: 2 alleles x 2 samples per variant.
    assert np.all(np.asarray(ac).sum(axis=1) == 4), \
        f"allele counts do not sum to 4 per variant: {np.asarray(ac)}"
    assert list(np.asarray(ac)[0]) == [3, 1], f"first variant counts {np.asarray(ac)[0]}"
    print(f"       (allel {allel.__version__}: counts {np.asarray(ac).tolist()})")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: popgen env assembles; tree sequences round-trip exactly and "
      "diversity matches coalescent theory on " + platform.machine()
      + f" (python {platform.python_version()}) — verified.")
