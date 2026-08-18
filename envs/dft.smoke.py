#!/usr/bin/env python3
# dft.smoke.py — the D3 verification for the `dft` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image) for
# the density-functional-theory stack. The risky natives here are the compiled `_gpaw`
# extension, libxc/libvdwxc (exchange-correlation), and the parallel layer —
# OpenMPI + ELPA + ScaLAPACK + FFTW — all of which can solve and import yet fail to
# actually compute. So we drive real SCF calculations, not just imports.
#
# This env is the first in the catalog whose headline capability is PARALLEL, so D3
# here also runs the same DFT calculation under `mpiexec -n 2` and asserts the answer
# matches the serial one. An MPI stack that links but computes wrong (or hangs) is
# exactly the assemble-gap this project exists to catch, and a single-process check
# cannot see it.
#
# It stays a SINGLE entrypoint — `python /opt/aarchsci/smoke.py` — because that one
# command is what the README promises consumers for re-earning the "verified" claim.
# The parallel leg is reached by re-invoking this same file under mpiexec (see
# AARCHSCI_MPI_CHILD below), not by a second CI step.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import os
import shutil
import subprocess
import sys
import traceback

FAILURES = []

# Bulk-Si reference calculation, shared by the serial and parallel legs so the two are
# comparing the identical problem. Small enough to be quick, big enough to be periodic
# and to exercise the k-point/eigensolver path.
SI_LATTICE = 5.43
PW_CUTOFF = 200
SI_KPTS = (2, 2, 2)

# Set in the child process to select the parallel leg. Without this the child would
# re-run the whole serial suite and recurse.
CHILD = os.environ.get("AARCHSCI_MPI_CHILD") == "1"


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


def si_energy():
    """The reference calculation. Identical code on 1 rank and on N ranks."""
    from ase.build import bulk
    from gpaw import GPAW, PW
    si = bulk("Si", "diamond", a=SI_LATTICE)
    si.calc = GPAW(mode=PW(PW_CUTOFF), xc="LDA", kpts=SI_KPTS, txt=None)
    return si.get_potential_energy()


# --- parallel leg -----------------------------------------------------------------
# Reached only via the mpiexec re-invocation below. Emits the energy as a bare float
# on stdout from rank 0 so the parent can compare it against the serial result.
if CHILD:
    from gpaw.mpi import world
    e = si_energy()
    if world.rank == 0:
        print(f"{e:.6f}")
    sys.exit(0)


# --- 1. Imports -------------------------------------------------------------------
HEADLINE = [
    "numpy", "scipy",
    "ase", "gpaw", "_gpaw",
    "mpi4py", "mpi4py.MPI",
    "spglib", "phonopy", "pymatgen",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. native + MPI layer --------------------------------------------------------
print("[smoke] 2. native + MPI layer")


@check("_gpaw is a native extension with the vdW-xc backend linked in")
def _native():
    import _gpaw
    from gpaw.mpi import world
    # libvdwxc is a separate native library; if the flavor lock slipped or the build
    # was the wrong variant, this symbol is the first thing to go missing.
    assert hasattr(_gpaw, "libvdwxc_create"), "libvdwxc not linked into _gpaw"
    print(f"       ({os.path.basename(_gpaw.__file__)}, world.size={world.size})")


@check("PAW datasets resolve from gpaw-data on disk (no runtime download)")
def _setups():
    from pathlib import Path
    from gpaw import setup_paths
    # gpaw needs PAW setups to compute anything. They must come from the packaged
    # gpaw-data, NOT be fetched at run time — a runtime binary/data fetch is what
    # made `whitebox` a wontfix (GAPS.md), so this is a principle check, not trivia.
    assert setup_paths, "gpaw.setup_paths is empty — no PAW datasets configured"
    d = Path(setup_paths[0])
    assert d.is_dir(), f"setup path does not exist: {d}"
    n = sum(1 for _ in d.iterdir())
    assert n > 0, f"no PAW setup files in {d}"
    print(f"       ({n} setup files at {d})")


@check("mpi4py collective over the world communicator")
def _mpi4py():
    from mpi4py import MPI
    comm = MPI.COMM_WORLD
    got = comm.allreduce(comm.rank + 1, op=MPI.SUM)
    want = sum(range(1, comm.size + 1))
    assert got == want, f"allreduce gave {got}, expected {want}"


# --- 3. functional DFT ------------------------------------------------------------
print("[smoke] 3. functional DFT")

SERIAL_SI = {}


@check("gpaw LCAO + libxc PBE on H2 (molecular, non-periodic)")
def _h2():
    from ase import Atoms
    from gpaw import GPAW
    h2 = Atoms("H2", positions=[(0, 0, 0), (0, 0, 0.74)], cell=(6, 6, 6))
    h2.center()
    # xc="PBE" routes through libxc rather than gpaw's builtin LDA, so this covers
    # the libxc native library as well as the LCAO code path.
    h2.calc = GPAW(mode="lcao", basis="sz(dzp)", xc="PBE", txt=None, h=0.25)
    e = h2.get_potential_energy()
    # Range, not an exact value: the channel moves and we do not want a false failure
    # from a 3rd-decimal change. ~-5.0 eV at this basis/grid.
    assert -10.0 < e < 0.0, f"H2 PBE energy unphysical: {e}"
    f = h2.get_forces()
    assert f.shape == (2, 3), f"force array shape {f.shape}"
    print(f"       (H2 LCAO/PBE = {e:.4f} eV)")


@check("gpaw plane-wave + k-points on bulk Si (periodic, BLAS/eigensolver path)")
def _si():
    e = si_energy()
    assert -20.0 < e < -5.0, f"bulk Si LDA energy unphysical: {e}"
    SERIAL_SI["energy"] = e
    print(f"       (Si bulk PW({PW_CUTOFF})/LDA, kpts={SI_KPTS} = {e:.4f} eV)")


# --- 4. structure / symmetry / phonons --------------------------------------------
print("[smoke] 4. structure / symmetry / phonons")


@check("spglib finds the diamond spacegroup (native)")
def _spglib():
    import spglib
    from ase.build import bulk
    si = bulk("Si", "diamond", a=SI_LATTICE)
    cell = (si.get_cell(), si.get_scaled_positions(), si.get_atomic_numbers())
    sg = spglib.get_spacegroup(cell, symprec=1e-4)
    assert sg and "227" in sg, f"expected Fd-3m (227) for diamond Si, got {sg!r}"
    print(f"       ({sg})")


@check("phonopy builds a supercell + symmetry-reduced displacements")
def _phonopy():
    from phonopy import Phonopy
    from phonopy.structure.atoms import PhonopyAtoms
    a = SI_LATTICE / 2
    cell = PhonopyAtoms(symbols=["Si"] * 2,
                        cell=[[0, a, a], [a, 0, a], [a, a, 0]],
                        scaled_positions=[[0, 0, 0], [0.25, 0.25, 0.25]])
    ph = Phonopy(cell, supercell_matrix=[[2, 0, 0], [0, 2, 0], [0, 0, 2]])
    ph.generate_displacements(distance=0.01)
    disps = ph.supercells_with_displacements
    assert len(ph.supercell) == 16, f"expected a 16-atom supercell, got {len(ph.supercell)}"
    assert len(disps) > 0, "no displacements generated"
    print(f"       ({len(ph.supercell)}-atom supercell, {len(disps)} displacement(s))")


@check("pymatgen structure + CIF round-trip")
def _pymatgen():
    from pymatgen.core import Lattice, Structure
    s = Structure(Lattice.cubic(SI_LATTICE), ["Si", "Si"],
                  [[0, 0, 0], [0.25, 0.25, 0.25]])
    assert s.composition.reduced_formula == "Si"
    assert abs(s.volume - SI_LATTICE ** 3) < 1e-6, f"volume {s.volume}"
    back = Structure.from_str(s.to(fmt="cif"), fmt="cif")
    assert back.composition.reduced_formula == "Si", "CIF round-trip lost the formula"


# --- 5. parallel: the same DFT calculation on 2 ranks -----------------------------
# The whole point of this env. If OpenMPI/ELPA/ScaLAPACK link but do not compute, a
# serial run cannot tell — the numbers only diverge (or the job hangs) under ranks>1.
print("[smoke] 5. parallel (2-rank MPI)")


@check("mpiexec -n 2 reproduces the serial bulk-Si energy")
def _parallel():
    mpiexec = shutil.which("mpiexec")
    assert mpiexec, "mpiexec not on PATH — the MPI build of this env is not usable"
    serial = SERIAL_SI.get("energy")
    assert serial is not None, "serial bulk-Si check did not run; nothing to compare"

    env = dict(os.environ)
    env["AARCHSCI_MPI_CHILD"] = "1"
    # gpaw is an OpenMP build: without this each rank would also spawn a thread pool
    # and oversubscribe the box, making this check slow and flaky.
    env["OMP_NUM_THREADS"] = "1"
    # CI runners and small instances may expose fewer slots than ranks.
    env["OMPI_MCA_rmaps_base_oversubscribe"] = "yes"

    proc = subprocess.run(
        [mpiexec, "-n", "2", sys.executable, os.path.abspath(__file__)],
        capture_output=True, text=True, env=env, timeout=1800,
    )
    assert proc.returncode == 0, (
        f"mpiexec -n 2 failed (rc={proc.returncode}): {proc.stderr[-800:]}"
    )
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    assert lines, f"child produced no energy on stdout; stderr: {proc.stderr[-400:]}"
    parallel = float(lines[-1])
    delta = abs(parallel - serial)
    # Domain/k-point decomposition must not change the physics. Loose enough for
    # summation-order noise, tight enough to catch a genuinely broken parallel path.
    assert delta < 1e-4, (
        f"2-rank energy {parallel:.6f} eV disagrees with serial {serial:.6f} eV "
        f"(delta {delta:.2e}) — parallel path is wrong"
    )
    print(f"       (serial {serial:.6f} eV vs 2-rank {parallel:.6f} eV, "
          f"delta {delta:.2e} eV)")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: dft env assembles, imports, and works (serial + 2-rank MPI) on "
      + sys.platform + "/" + sys.implementation.name + " — verified.")
