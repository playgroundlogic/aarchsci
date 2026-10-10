#!/usr/bin/env python3
# hep.smoke.py — the D3 verification for the `hep` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image). The risky
# natives are ROOT's enormous C++ framework with its Python bindings, Pythia8's bindings,
# and Geant4's shared libraries plus its twelve physics datasets.
#
# THE STRONGEST CHECK HERE IS CROSS-IMPLEMENTATION, and it is not the one issue #34
# proposed. The request wanted ROOT to read what Geant4 wrote. That is unavailable (see the
# Geant4 note below), so instead: ROOT writes a histogram and `uproot` reads it back.
# uproot is an independent pure-Python/NumPy implementation of the ROOT file format that
# shares none of ROOT's I/O code, so agreement between them is a real check on the format
# and the bytes. ROOT reading its own file would only prove self-consistency.
#
# GEANT4 GETS THE WEAKEST VERIFICATION IN THIS ENV, DELIBERATELY AND FOR A REASON WORTH
# READING BEFORE ANYONE "STRENGTHENS" IT. Issue #34 proposed a per-event energy-
# conservation identity (deposited + escaped + remaining == primary), which would be
# excellent. It cannot be done here: conda-forge's geant4 ships NO python bindings in any
# variant — the `py*` builds install zero files into site-packages while still pinning
# python_abi, and the `noqt_*` build this env uses declares no python at all. Geant4's
# examples ship as C++ source, and this project does not compile from source (DESIGN
# non-goals). So there is no route to a simulation from inside this container.
#
# What is verified for Geant4 is the version and that all twelve physics datasets are
# present and wired through their G4*DATA variables. That is real — a missing dataset is
# the single most common Geant4 runtime failure, and these are tens of thousands of files
# — and it is honestly weaker than ROOT's checks. Same shape as `siesta` in `dft`.
#
# THIS ENV NEEDS ACTIVATION — `apptainer run`, not `exec`, and it is the fourth such env
# after dft, bayes and cp2k. root, pythia8 and all twelve geant4 data packages ship
# activate.d scripts, and measured on the published image with activation skipped: ROOT
# still works, Pythia8 aborts on a *misleading* version-mismatch error, and every G4*DATA
# variable is unset. Both failures are asserted below so a regression fails the build.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import os
import platform
import subprocess
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []

# Geant4 datasets, by the environment variable each is located through. A missing one of
# these is the classic "Geant4 installed but aborts at first event" failure.
G4_DATA_VARS = [
    "G4ABLADATA", "G4CHANNELINGDATA", "G4ENSDFSTATEDATA", "G4INCLDATA", "G4LEDATA",
    "G4LEVELGAMMADATA", "G4NEUTRONHPDATA", "G4PARTICLEXSDATA", "G4PIIDATA",
    "G4RADIOACTIVEDATA", "G4REALSURFACEDATA", "G4SAIDXSDATA",
]

HIST_ENTRIES = 1000
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
    "numpy", "scipy", "pandas",
    "ROOT",
    "pythia8",
    "uproot", "awkward",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. ROOT: exact histogram arithmetic ------------------------------------------
print("[smoke] 2. ROOT histogram identities")


@check("ROOT computes exact histogram integrals and a closed-form Gaussian fit")
def _root_hist():
    import ROOT
    ROOT.gROOT.SetBatch(True)
    ver = ROOT.gROOT.GetVersion()
    assert ver, "ROOT reports no version"
    h = ROOT.TH1D("h", "h", 100, -5, 5)
    # Fill a known number of entries at a single bin centre: the integral is then exactly
    # the entry count, with no binning or floating-point ambiguity.
    for _ in range(HIST_ENTRIES):
        h.Fill(0.0)
    assert h.GetEntries() == HIST_ENTRIES, f"GetEntries {h.GetEntries()}"
    assert h.Integral() == float(HIST_ENTRIES), \
        f"Integral {h.Integral()!r}, expected exactly {float(HIST_ENTRIES)!r}"
    # The mean of a delta at 0 is exactly 0 and the RMS exactly 0.
    assert abs(h.GetMean()) < 1e-12, f"mean {h.GetMean()!r}, expected 0"
    assert abs(h.GetRMS()) < 1e-12, f"RMS {h.GetRMS()!r}, expected 0"
    STATE["root_version"] = ver
    print(f"       (ROOT {ver}: integral {h.Integral():.1f} == entries "
          f"{h.GetEntries():.0f}, mean {h.GetMean():.1e})")


@check("ROOT recovers the parameters of a Gaussian it generated")
def _root_fit():
    import ROOT
    ROOT.gROOT.SetBatch(True)
    MU, SIGMA, N = 1.5, 0.75, 200000
    rng = ROOT.TRandom3(4357)
    h = ROOT.TH1D("hg", "hg", 200, -5, 8)
    for _ in range(N):
        h.Fill(rng.Gaus(MU, SIGMA))
    h.Fit("gaus", "Q0")
    f = h.GetFunction("gaus")
    assert f, "no fit function attached"
    mu, mu_err = f.GetParameter(1), f.GetParError(1)
    sg, sg_err = f.GetParameter(2), f.GetParError(2)
    # Judged by the fit's OWN reported uncertainty rather than a hand-picked band — the
    # same discipline bayes.smoke.py uses with Stan's MCSE. 5 sigma is generous for a
    # correct fitter and far too tight to hide a broken one.
    assert abs(mu - MU) < 5 * mu_err, \
        f"fitted mean {mu:.5f} +/- {mu_err:.5f} vs generated {MU} ({abs(mu-MU)/mu_err:.1f} sigma)"
    assert abs(sg - SIGMA) < 5 * sg_err, \
        f"fitted sigma {sg:.5f} +/- {sg_err:.5f} vs generated {SIGMA}"
    print(f"       (fit mu={mu:.5f}+/-{mu_err:.5f} vs {MU}; "
          f"sigma={sg:.5f}+/-{sg_err:.5f} vs {SIGMA})")


# --- 3. the cross-implementation check --------------------------------------------
print("[smoke] 3. ROOT writes, uproot reads (independent implementations)")


@check("uproot reads a ROOT-written histogram and agrees on the contents")
def _cross_read():
    import numpy as np
    import ROOT
    import uproot
    ROOT.gROOT.SetBatch(True)
    d = Path(tempfile.mkdtemp())
    path = d / "cross.root"
    h = ROOT.TH1D("hx", "hx", 10, 0.0, 10.0)
    # A distinct, known count per bin, so any off-by-one or axis flip is visible rather
    # than averaged away.
    planted = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3]
    for i, n in enumerate(planted):
        for _ in range(n):
            h.Fill(i + 0.5)
    total = float(sum(planted))
    assert h.Integral() == total, f"ROOT integral {h.Integral()!r} != {total!r}"
    f = ROOT.TFile(str(path), "RECREATE")
    h.Write()
    f.Close()
    assert path.is_file() and path.stat().st_size > 0, "ROOT wrote no file"

    # uproot shares none of ROOT's I/O code, so this is a genuine second opinion on the
    # bytes rather than a round-trip through one implementation.
    with uproot.open(str(path)) as fh:
        keys = [k.split(";")[0] for k in fh.keys()]
        assert "hx" in keys, f"uproot does not see the histogram; keys={fh.keys()}"
        vals = fh["hx"].values()
    got = [int(round(v)) for v in np.asarray(vals)]
    assert got == planted, f"uproot read {got}, ROOT wrote {planted}"
    print(f"       ({path.stat().st_size} bytes; uproot read {got} == planted)")


# --- 4. Pythia8 -------------------------------------------------------------------
print("[smoke] 4. Pythia8 event generation")


@check("Pythia8 generates events whose final-state charge equals the incoming charge")
def _pythia():
    import pythia8
    p = pythia8.Pythia("", False)      # quiet banner
    # Pythia's default beams are protons, so this is Drell-Yan Z production in pp at
    # 91.2 GeV. The expected charge is read OUT OF THE EVENT RECORD rather than assumed
    # from the process: entries 1 and 2 are the incoming beams, so summing their charge
    # gives the right-hand side whatever the beams are. Writing the expected value by
    # hand is how this check was wrong on its first run — it asserted 0 for an e+e-
    # initial state that was never configured, and measured +2 for the pp default. The
    # physics was right and the expectation was not, which is the failure mode an
    # identity read from the data cannot have.
    # Assert the activation variable FIRST, because Pythia's own symptom for a missing
    # PYTHIA8DATA is actively misleading. Measured in the published image with activation
    # skipped (`--entrypoint python`, the Apptainer `exec` equivalent):
    #
    #   PYTHIA Error in Settings::mode: unknown key Tune:ee
    #   PYTHIA Abort from Pythia::checkVersion: unmatched version numbers :
    #           in code 8.312 but in XML 0.000
    #
    # Nothing there says "the XML data path is unset" — it reads as a packaging version
    # mismatch, which is a long way from the real cause. Same reason dft asserts
    # GPAW_MPI_BACKEND and cp2k asserts CP2K_DATA_DIR directly.
    xmldoc = os.environ.get("PYTHIA8DATA")
    assert xmldoc and Path(xmldoc).is_dir(), (
        f"PYTHIA8DATA is {xmldoc!r} — Pythia8's XML data path comes from the package's "
        "activate.d, so activation did not run; use `apptainer run`, not `exec`")
    for setting in ("Beams:eCM = 91.2", "WeakSingleBoson:ffbar2gmZ = on",
                    "PhaseSpace:mHatMin = 80.", "Random:setSeed = on",
                    "Random:seed = 12345", "Print:quiet = on"):
        p.readString(setting)
    assert p.init(), "Pythia8 failed to initialise"
    n_ok = 0
    charges = []
    beam_ids, q_in = None, None
    for _ in range(5):
        if not p.next():
            continue
        n_ok += 1
        if q_in is None:
            # Read after the first event: the record's beam entries are filled by next(),
            # not by init().
            beams = [p.event[i] for i in (1, 2)]
            beam_ids = [b.id() for b in beams]
            q_in = round(sum(b.charge() for b in beams))
        # Charge is exactly conserved, so this is an integer identity rather than a band:
        # the summed charge of the final state must equal the incoming charge, event by
        # event, with no tolerance.
        q = sum(p.event[i].charge() for i in range(p.event.size())
                if p.event[i].isFinal())
        charges.append(round(q))
    assert n_ok >= 3, f"only {n_ok}/5 events generated"
    assert all(q == q_in for q in charges), (
        f"charge not conserved: incoming {q_in} from beams {beam_ids}, "
        f"final states {charges}")
    print(f"       ({n_ok}/5 events at 91.2 GeV, beams {beam_ids}, "
          f"final-state charge {charges} == incoming {q_in})")


# --- 5. Geant4: version + datasets only (see the module docstring) ----------------
print("[smoke] 5. Geant4 libraries and physics datasets (no simulation — see comment)")


@check("geant4-config reports a usable version")
def _g4_version():
    exe = shutil.which("geant4-config")
    assert exe, "geant4-config not on PATH"
    out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                         timeout=300).stdout.strip()
    parts = out.split(".")
    assert len(parts) >= 2 and parts[0].isdigit(), f"unexpected version {out!r}"
    assert int(parts[0]) >= 11, f"expected Geant4 >=11, got {out}"
    STATE["g4"] = out
    print(f"       (Geant4 {out})")


@check("all twelve Geant4 physics datasets are present and wired via G4*DATA")
def _g4_data():
    # A missing dataset is Geant4's most common runtime failure: the library loads, the
    # application starts, and the first event aborts. Since no simulation can be run from
    # this container, checking the data is present and located is the strongest available
    # substitute — and it is not trivial, these are tens of thousands of files.
    missing, found = [], []
    for var in G4_DATA_VARS:
        val = os.environ.get(var)
        if not val:
            missing.append(f"{var} unset")
            continue
        d = Path(val)
        if not d.is_dir():
            missing.append(f"{var} -> {val} (not a directory)")
            continue
        # Counted RECURSIVELY and counting files only, because several of these datasets
        # (G4LEDATA/EMLOW above all) keep their content in subdirectories — a top-level
        # `iterdir` count undercounts them by more than an order of magnitude and would
        # make this line misreport what it found.
        n = sum(1 for q in d.rglob("*") if q.is_file())
        if n == 0:
            missing.append(f"{var} -> {val} (empty)")
            continue
        found.append((var, n))
    assert not missing, ("Geant4 datasets not usable: " + "; ".join(missing)
                         + " — activate.d did not run, or the data packages are absent; "
                           "use `run`, not `exec`")
    total = sum(n for _, n in found)
    biggest = max(found, key=lambda x: x[1])
    print(f"       ({len(found)}/{len(G4_DATA_VARS)} datasets wired, {total} files total; "
          f"largest {biggest[0]} with {biggest[1]})")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: hep env assembles; ROOT and uproot agree on the same bytes on "
      + platform.machine() + " (python " + platform.python_version() + ") — verified.")
