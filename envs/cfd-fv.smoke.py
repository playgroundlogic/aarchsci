#!/usr/bin/env python3
# cfd-fv.smoke.py — the D3 verification for the `cfd-fv` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image) for the
# SU2 finite-volume CFD stack. The risky part here is the compiled SU2_CFD solver and its
# OpenMPI parallel layer — either can link and then compute wrong or hang, which a bare
# import cannot catch. So this drives a real Euler solve, serially and on 2 ranks.
#
# HONEST SCOPE, stated where it would otherwise be "strengthened" and be puzzling:
# conda-forge's su2 ships NO meshes and NO runnable tutorial cases (0 *.su2 in the
# package — the reference tutorial cases live in SU2's separate GitHub repo), and the
# compiled `pysu2` binding is not present. So there is no bundled "reproduce a published
# number" case to run, and a runtime download of the tutorial suite is off the table
# (the whitebox wontfix precedent). Unlike a pseudopotential (data we cannot fabricate,
# which is why siesta gets no SCF), a CFD MESH is geometry we can legitimately generate —
# so this test writes its own mesh fixture, exactly as the other smoke tests write their
# netCDF / fdf / QE inputs, and runs a genuine finite-volume solve on it.
#
# The physics check is FREE-STREAM PRESERVATION on a deliberately DISTORTED (non-
# orthogonal) mesh: uniform flow is the exact Euler solution, and a correct finite-volume
# scheme holds it to machine zero even when the cells are skewed — a scheme with wrong
# metric terms does not. So the density residual must fall to ~1e-14, not merely "look
# small". Serial and 2-rank must both reach it, and the 2-rank run must actually
# decompose the domain (ParMETIS), which is how we know MPI did real work.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import math
import os
import re
import shutil
import struct
import subprocess
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


# --- the Euler config, shared by the serial and 2-rank legs ------------------------
# Subsonic inviscid flow, freestream Mach 0.5, in a rectangular channel: slip (Euler)
# walls top/bottom, far-field in/out. The exact solution is the uniform freestream, so a
# correct solver preserves it — that is the check. MESH_FILENAME is written next to it.
EULER_CFG = """SOLVER= EULER
MATH_PROBLEM= DIRECT
RESTART_SOL= NO
MACH_NUMBER= 0.5
AOA= 0.0
FREESTREAM_PRESSURE= 101325.0
FREESTREAM_TEMPERATURE= 288.15
REF_DIMENSIONALIZATION= DIMENSIONAL
MARKER_EULER= ( lower, upper )
MARKER_FAR= ( inlet, outlet )
NUM_METHOD_GRAD= GREEN_GAUSS
CFL_NUMBER= 5.0
ITER= 250
CONV_NUM_METHOD_FLOW= JST
TIME_DISCRE_FLOW= EULER_IMPLICIT
LINEAR_SOLVER= FGMRES
LINEAR_SOLVER_PREC= ILU
LINEAR_SOLVER_ITER= 5
CONV_FIELD= RMS_DENSITY
CONV_RESIDUAL_MINVAL= -10
MESH_FILENAME= mesh.su2
MESH_FORMAT= SU2
SCREEN_OUTPUT= (INNER_ITER, RMS_DENSITY, RMS_MOMENTUM-X)
OUTPUT_FILES= (RESTART)
"""

# Free-stream preservation only means something if the mesh is NOT trivially Cartesian —
# any scheme preserves freestream on an axis-aligned grid. Distorting the interior nodes
# (boundaries stay straight so the four markers are unchanged) makes it a real test.
NX, NY, LX, LY = 24, 12, 3.0, 1.0


def _write_mesh(path):
    def idx(i, j):
        return j * (NX + 1) + i
    pts = []
    for j in range(NY + 1):
        for i in range(NX + 1):
            x, y = LX * i / NX, LY * j / NY
            if 0 < i < NX and 0 < j < NY:      # interior nodes only
                x += 0.35 * (LX / NX) * math.sin(3 * math.pi * y / LY)
                y += 0.35 * (LY / NY) * math.sin(2 * math.pi * x / LX)
            pts.append((x, y))
    with open(path, "w") as f:
        f.write("NDIME= 2\n")
        f.write(f"NPOIN= {(NX + 1) * (NY + 1)}\n")
        for k, (x, y) in enumerate(pts):
            f.write(f"{x:.6f} {y:.6f} {k}\n")
        f.write(f"NELEM= {NX * NY}\n")
        e = 0
        for j in range(NY):
            for i in range(NX):
                f.write(f"9 {idx(i, j)} {idx(i + 1, j)} {idx(i + 1, j + 1)} {idx(i, j + 1)} {e}\n")
                e += 1
        f.write("NMARK= 4\n")
        f.write("MARKER_TAG= inlet\n");  f.write(f"MARKER_ELEMS= {NY}\n")
        for j in range(NY): f.write(f"3 {idx(0, j)} {idx(0, j + 1)}\n")
        f.write("MARKER_TAG= outlet\n"); f.write(f"MARKER_ELEMS= {NY}\n")
        for j in range(NY): f.write(f"3 {idx(NX, j)} {idx(NX, j + 1)}\n")
        f.write("MARKER_TAG= lower\n");  f.write(f"MARKER_ELEMS= {NX}\n")
        for i in range(NX): f.write(f"3 {idx(i, 0)} {idx(i + 1, 0)}\n")
        f.write("MARKER_TAG= upper\n");  f.write(f"MARKER_ELEMS= {NX}\n")
        for i in range(NX): f.write(f"3 {idx(i, NY)} {idx(i + 1, NY)}\n")


def _mpi_env():
    env = dict(os.environ)
    # Container images commonly run as root, CI boxes may have fewer slots than ranks,
    # and Open MPI's UCX one-sided component logs a noisy error in a container with no
    # RDMA — none of which should fail the run.
    env["OMPI_ALLOW_RUN_AS_ROOT"] = "1"
    env["OMPI_ALLOW_RUN_AS_ROOT_CONFIRM"] = "1"
    env["OMPI_MCA_rmaps_base_oversubscribe"] = "yes"
    env["OMPI_MCA_osc"] = "^ucx"
    env["OMP_NUM_THREADS"] = "1"
    return env


def _run_su2(argv):
    """Run SU2_CFD on a fresh copy of the case in its own directory; return the output.

    SU2 writes restart/history files into the working dir, so each leg gets its own.
    """
    d = Path(tempfile.mkdtemp())
    (d / "euler.cfg").write_text(EULER_CFG)
    _write_mesh(d / "mesh.su2")
    proc = subprocess.run(argv + ["euler.cfg"], cwd=str(d), capture_output=True,
                          text=True, env=_mpi_env(), timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def _final_rms_density(out):
    """The last rms[Rho] from SU2's screen iteration table (log10 of the residual)."""
    vals = re.findall(r"^\|\s*\d+\|\s*(-?\d+\.\d+)\|", out, re.M)
    assert vals, f"no rms[Rho] iteration rows in SU2 output:\n{out[-1500:]}"
    return float(vals[-1])


def _elf_machine(path):
    """e_machine from an ELF header, or None if not an ELF. 183 == EM_AARCH64."""
    with open(path, "rb") as fh:
        head = fh.read(20)
    if head[:4] != b"\x7fELF":
        return None
    little = head[5] == 1
    return struct.unpack("<H" if little else ">H", head[18:20])[0]


SERIAL_RMS = {}

# --- 1. imports --------------------------------------------------------------------
# su2's compiled `pysu2` binding is not in the conda package, so the headline imports are
# the numeric/MPI layer the env stands on; SU2 itself is exercised as a binary below.
HEADLINE = ["numpy", "scipy", "mpi4py", "mpi4py.MPI"]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


@check("mpi4py collective over the world communicator")
def _mpi4py():
    from mpi4py import MPI
    comm = MPI.COMM_WORLD
    got = comm.allreduce(comm.rank + 1, op=MPI.SUM)
    want = sum(range(1, comm.size + 1))
    assert got == want, f"allreduce gave {got}, expected {want}"


# --- 2. the SU2 binary -------------------------------------------------------------
print("[smoke] 2. SU2 binary")


@check("SU2_CFD is an aarch64 binary, version 8.x")
def _su2_version():
    su2 = shutil.which("SU2_CFD")
    assert su2, "SU2_CFD not on PATH"
    mach = _elf_machine(os.path.realpath(su2))
    # The binary's own ELF header is a stronger arm64 statement than any subdir label.
    assert mach == 183, f"SU2_CFD ELF e_machine is {mach}, expected 183 (AArch64)"
    rc, out = _run_su2([su2])
    ver = re.search(r"Release\s+(\d+\.\d+\.\d+)", out)
    assert ver, f"could not parse SU2 release from:\n{out[:600]}"
    assert int(ver.group(1).split(".")[0]) >= 8, f"expected SU2 >=8, got {ver.group(1)}"
    print(f"       (SU2 {ver.group(1)}, aarch64)")


# --- 3. a real Euler solve: free-stream preservation on a distorted mesh -----------
print("[smoke] 3. finite-volume Euler solve (serial)")


@check("SU2_CFD converges and preserves free-stream on a distorted mesh (serial)")
def _su2_serial():
    su2 = shutil.which("SU2_CFD")
    rc, out = _run_su2([su2])
    assert rc == 0, f"SU2_CFD exited {rc}:\n{out[-1500:]}"
    assert "Exit Success (SU2_CFD)" in out, f"no clean SU2 exit:\n{out[-1200:]}"
    assert "All convergence criteria satisfied" in out, \
        f"solve did not converge:\n{out[-1200:]}"
    rms = _final_rms_density(out)
    # Free-stream preservation: uniform flow is exact, so the density residual must fall
    # to machine zero (measured ~-14.5 in log10) even though the mesh is non-orthogonal.
    # -8 is a wide margin that still fails any scheme that does not preserve free-stream.
    assert rms < -8.0, f"free-stream not preserved: final rms[Rho] = {rms} (expected < -8)"
    SERIAL_RMS["rms"] = rms
    print(f"       (Euler solve converged, final rms[Rho] = {rms:.2f})")


# --- 4. the same solve on 2 MPI ranks ----------------------------------------------
print("[smoke] 4. parallel (2-rank MPI)")


@check("SU2_CFD decomposes the domain and reproduces the solve on 2 ranks")
def _su2_parallel():
    mpiexec = shutil.which("mpiexec")
    assert mpiexec, "mpiexec not on PATH — the MPI build of this env is not usable"
    serial = SERIAL_RMS.get("rms")
    assert serial is not None, "serial SU2 check did not run; nothing to compare"
    rc, out = _run_su2([mpiexec, "-n", "2", "--oversubscribe", shutil.which("SU2_CFD")])
    assert rc == 0, f"mpiexec -n 2 SU2_CFD exited {rc}:\n{out[-1500:]}"
    # ParMETIS partitioning runs ONLY with >1 rank — serial SU2 skips it entirely. So its
    # presence is SU2's own evidence that the domain was decomposed across ranks, not our
    # assumption about what mpiexec did.
    assert re.search(r"partitioning", out), \
        f"no domain partitioning — SU2 did not run multi-rank:\n{out[-1500:]}"
    assert "Exit Success (SU2_CFD)" in out and "All convergence criteria satisfied" in out, \
        f"2-rank solve did not finish cleanly:\n{out[-1200:]}"
    par = _final_rms_density(out)
    assert par < -8.0, f"2-rank free-stream not preserved: rms[Rho] = {par}"
    # Same physics on both: the domain decomposition must not change the converged state.
    assert abs(par - serial) < 1.0, (
        f"2-rank rms[Rho] {par:.2f} differs from serial {serial:.2f} by more than a "
        "decade — the parallel path changed the answer")
    print(f"       (2-rank converged, rms[Rho] = {par:.2f} vs serial {serial:.2f})")


# --- verdict -----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: cfd-fv env assembles, imports, and works (serial + 2-rank MPI) on "
      + sys.platform + "/" + sys.implementation.name + " — verified.")
