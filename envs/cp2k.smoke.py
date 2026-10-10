#!/usr/bin/env python3
# cp2k.smoke.py — the D3 verification for the `cp2k` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image) for a
# Fortran/C++ MPI application. The risky parts are the cp2k binary itself and its large
# native stack — elpa, scalapack, dbcsr, cosma, sirius, libxsmm, libint, libxc — any of
# which can link and then compute wrong.
#
# THIS ENV GETS THE STRONGEST VERIFICATION IN THE CATALOG, and it is not something this
# repo invented: cp2k ships 344 Quickstep regression tests, and each
# `TEST_FILES.toml` carries a committed reference energy AND a committed tolerance:
#
#     "Ar.inp" = [{matcher="E_total", tol=3e-13, ref=-21.04944231395054}]
#
# So D3 runs an upstream input and compares against UPSTREAM'S OWN reference at UPSTREAM'S
# OWN tolerance. Both numbers are read out of that TOML at run time rather than copied in
# here, deliberately: if the package updates its reference, the check follows it instead of
# asserting a value this file remembers. Nothing is staged, nothing is downloaded, and no
# tolerance is chosen by us.
#
# Measured on aarch64: serial −21.049442313950586 and 2-rank −21.049442313950600 against
# the reference −21.04944231395054, i.e. 4.6e-14 and 6.0e-14 against a 3e-13 bound, with
# the two ranks agreeing to 1.4e-14.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []

# The regtest to reproduce. `Ar.inp` is a single argon atom in the GPW regtest set: the
# smallest input that still exercises the full Quickstep path (grids, XC, SCF) and has a
# reference quoted to 3e-13.
REGTEST_DIR = "QS/regtest-gpw-1"
REGTEST_INPUT = "Ar.inp"

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


def _tests_root():
    """Locate cp2k's shipped regtest tree.

    conda puts package tests under etc/conda/test-files/<pkg>/<n>/, not under share/, so
    this is globbed rather than hardcoded — the `<n>` is a conda implementation detail.
    """
    base = Path(sys.prefix) / "etc" / "conda" / "test-files" / "cp2k"
    for cand in sorted(base.glob("*/tests")):
        if cand.is_dir():
            return cand
    return None


def _elf_machine(path):
    """e_machine from an ELF header, or None if not an ELF. 183 == EM_AARCH64."""
    with open(path, "rb") as fh:
        head = fh.read(20)
    if head[:4] != b"\x7fELF":
        return None
    return struct.unpack("<H" if head[5] == 1 else ">H", head[18:20])[0]


def _mpi_env():
    env = dict(os.environ)
    env["OMPI_ALLOW_RUN_AS_ROOT"] = "1"
    env["OMPI_ALLOW_RUN_AS_ROOT_CONFIRM"] = "1"
    env["OMPI_MCA_rmaps_base_oversubscribe"] = "yes"
    env["OMPI_MCA_osc"] = "^ucx"       # no RDMA in a container; silences a noisy error
    env["OMP_NUM_THREADS"] = "1"       # cp2k is an OpenMP build; keep ranks from fighting
    return env


def _run_regtest(ranks):
    """Run REGTEST_INPUT on `ranks` ranks in a scratch copy; return the total energy."""
    root = STATE["tests_root"]
    src = root / REGTEST_DIR
    d = Path(tempfile.mkdtemp())
    # Copy the whole regtest directory: inputs reference sibling files, and cp2k writes
    # restart/wfn files next to them, so the shipped tree must stay read-only.
    for item in src.iterdir():
        if item.is_file():
            shutil.copy2(item, d / item.name)
    exe = shutil.which("cp2k")
    argv = ([shutil.which("mpiexec"), "-n", str(ranks), "--oversubscribe", exe]
            if ranks > 1 else [exe])
    proc = subprocess.run(argv + ["-i", REGTEST_INPUT, "-o", "out.txt"],
                          cwd=str(d), capture_output=True, text=True, timeout=1800,
                          env=_mpi_env())
    out_file = d / "out.txt"
    out = out_file.read_text() if out_file.is_file() else (proc.stdout + proc.stderr)
    assert proc.returncode == 0, f"cp2k on {ranks} rank(s) exited {proc.returncode}:\n{out[-1500:]}"
    assert "SCF run converged" in out, f"SCF did not converge on {ranks} rank(s):\n{out[-1500:]}"
    m = re.findall(r"ENERGY\|\s+Total FORCE_EVAL.*?(-?\d+\.\d+)", out)
    assert m, f"no total energy in cp2k output on {ranks} rank(s):\n{out[-1500:]}"
    return float(m[-1])


# --- 1. imports -------------------------------------------------------------------
HEADLINE = ["numpy", "mpi4py", "mpi4py.MPI"]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. binary, data wiring, and the shipped references --------------------------
print("[smoke] 2. binary + data + reference availability")


@check("cp2k is an aarch64 binary reporting version 2026.x with MPI and ELPA")
def _binary():
    exe = shutil.which("cp2k")
    assert exe, "cp2k not on PATH"
    mach = _elf_machine(os.path.realpath(exe))
    # The binary's own ELF header beats any subdir label as arm64 evidence.
    assert mach == 183, f"cp2k ELF e_machine is {mach}, expected 183 (AArch64)"
    out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                         timeout=600, env=_mpi_env())
    text = out.stdout + out.stderr
    ver = re.search(r"CP2K version\s+(\S+)", text)
    assert ver, f"could not parse a CP2K version from:\n{text[:400]}"
    assert int(ver.group(1).split(".")[0]) >= 2026, f"unexpected cp2k version {ver.group(1)}"
    # cp2kflags names the features actually compiled in. `parallel` and `elpa` are the
    # ones this env exists for: a serial or elpa-less build would be a different thing
    # wearing the same name, and the lock file cannot record that (DESIGN OQ2).
    flags = re.search(r"cp2kflags:\s*(.*)", text)
    assert flags, "cp2k printed no cp2kflags line"
    blob = text[flags.start():flags.start() + 400]
    for feature in ("parallel", "elpa", "scalapack", "libxc"):
        assert feature in blob, f"cp2k was not built with {feature!r}: {blob[:200]}"
    print(f"       (CP2K {ver.group(1)}, aarch64, parallel+elpa+scalapack+libxc)")


@check("CP2K_DATA_DIR is wired to the shipped basis sets and pseudopotentials")
def _data_dir():
    # conda-forge's cp2k installs the data but sets no CP2K_DATA_DIR and ships no
    # activate.d, so cp2k aborts on the first calculation. builder/Dockerfile exports it.
    # Unlike siesta and dftbplus, the data IS here — it was only unwired.
    val = os.environ.get("CP2K_DATA_DIR")
    assert val, ("CP2K_DATA_DIR is unset — activate.d did not run, and cp2k cannot find "
                 "its basis sets without it; use `run`, not `exec`")
    d = Path(val)
    assert d.is_dir(), f"CP2K_DATA_DIR points at nothing: {d}"
    for required in ("BASIS_MOLOPT", "GTH_POTENTIALS"):
        assert (d / required).is_file(), f"{required} missing from {d}"
    n = sum(1 for _ in d.iterdir())
    print(f"       ({n} data files at {d})")


@check("cp2k ships regtests with committed reference energies and tolerances")
def _refs():
    root = _tests_root()
    assert root, "cp2k's regtest tree is not installed under etc/conda/test-files"
    toml_path = root / REGTEST_DIR / "TEST_FILES.toml"
    assert toml_path.is_file(), f"no TEST_FILES.toml at {toml_path}"
    # tomllib is stdlib from 3.11; this env is py3.14.
    import tomllib
    with open(toml_path, "rb") as fh:
        spec = tomllib.load(fh)
    assert REGTEST_INPUT in spec, f"{REGTEST_INPUT} not listed in {toml_path}"
    entry = spec[REGTEST_INPUT][0]
    ref, tol = float(entry["ref"]), float(entry["tol"])
    assert entry.get("matcher") == "E_total", \
        f"{REGTEST_INPUT} matcher is {entry.get('matcher')!r}, expected E_total"
    STATE.update(tests_root=root, ref=ref, tol=tol)
    n_qs = len(list((root / "QS").glob("regtest*"))) if (root / "QS").is_dir() else 0
    print(f"       ({n_qs} QS regtest dirs; {REGTEST_INPUT} ref={ref!r} tol={tol:g})")


# --- 3. reproduce upstream's reference, serially ----------------------------------
print("[smoke] 3. reproduce the committed reference (serial)")


@check("cp2k reproduces its own regtest reference within upstream's tolerance")
def _serial():
    assert "ref" in STATE, "the reference was not read; nothing to compare against"
    e = _run_regtest(1)
    ref, tol = STATE["ref"], STATE["tol"]
    delta = abs(e - ref)
    # Upstream's number and upstream's bound. Nothing here was chosen by this project,
    # which is what makes it a reproduction rather than a self-consistency check.
    assert delta <= tol, (
        f"total energy {e!r} vs committed reference {ref!r}: delta {delta:.3e} exceeds "
        f"upstream's tolerance {tol:g}")
    STATE["serial"] = e
    print(f"       (E={e:.12f} vs ref {ref:.12f}, delta {delta:.2e} <= tol {tol:g})")


# --- 4. the same calculation on 2 MPI ranks ---------------------------------------
print("[smoke] 4. parallel (2-rank MPI)")


@check("cp2k on 2 ranks reproduces the reference and agrees with the serial run")
def _parallel():
    serial = STATE.get("serial")
    assert serial is not None, "the serial run did not complete; nothing to compare"
    assert shutil.which("mpiexec"), "mpiexec not on PATH — the MPI build is unusable"
    e = _run_regtest(2)
    ref, tol = STATE["ref"], STATE["tol"]
    delta_ref = abs(e - ref)
    assert delta_ref <= tol, (
        f"2-rank energy {e!r} vs reference {ref!r}: delta {delta_ref:.3e} exceeds {tol:g}")
    # Domain decomposition must not change the physics. Both runs are already inside
    # upstream's tolerance of the same reference, so they must be within 2x of it of
    # each other.
    delta_par = abs(e - serial)
    assert delta_par <= 2 * tol, (
        f"2-rank {e!r} and serial {serial!r} differ by {delta_par:.3e}, more than 2x "
        f"upstream's tolerance — the parallel path changed the answer")
    print(f"       (E={e:.12f}, delta from ref {delta_ref:.2e}, "
          f"from serial {delta_par:.2e})")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: cp2k env assembles and reproduces an upstream regtest reference "
      "serially and on 2 ranks on " + platform.machine() + " (python "
      + platform.python_version() + ") — verified.")
