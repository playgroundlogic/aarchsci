#!/usr/bin/env python3
# dft.smoke.py — the D3 verification for the `dft` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image) for
# the density-functional-theory stack. The risky natives here are the compiled `_gpaw`
# extension, libxc/libvdwxc (exchange-correlation), and the parallel layer —
# OpenMPI + ELPA + ScaLAPACK + FFTW — all of which can solve and import yet fail to
# actually compute. So we drive real SCF calculations, not just imports — three
# independent ones, through gpaw, psi4 and nwchem, which share no integral or SCF code.
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
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

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
    # This assertion is the whole reason the parallel leg means anything. A nompi gpaw
    # under `mpiexec -n 2` does not fail — it runs TWO independent serial calculations,
    # each of which believes it is rank 0 of a 1-rank world, and both print the same
    # energy. The parent's "parallel matches serial" check would then pass while nothing
    # parallel had happened. Refusing to proceed unless the communicator is actually
    # larger than 1 is what closes that hole.
    assert world.size > 1, (
        f"child sees world.size={world.size} under mpiexec -n 2 — gpaw is not an MPI "
        "build (the nompi variant ties on build number; see the pin in dft.yaml)"
    )
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
    # `import psi4` is a genuine check, not a formality: psi4 1.10 installs cleanly
    # against libxc-c 7.1.2 and then dies right here, during import, building its
    # functional table — "Fatal Error: Could not find required LibXC functional".
    # This line is what the `psi4 >=1.11` floor in dft.yaml protects.
    "psi4",
]

# Packages whose conda BUILD STRING carries meaning this repo's lock file cannot record.
# The lock is `name version` only (DESIGN OQ2), so an MPI-flavor flip does not move the
# lock-hash and the reconciler cannot see it. Asserting the build string here is the
# direct mitigation: the check runs inside the built image, against conda-meta, so a
# resolver that quietly swapped a parallel build for a serial one fails the build.
# Measured 2026-09-04: `gpaw` really does flip to `py314_nompi_omp_3` unpinned, and
# `plumed` to a `nompi_*` build, so neither of these is hypothetical.
BUILD_STRING_MUST_CONTAIN = {
    "gpaw": "mpi_openmpi",
    "elpa": "mpi_openmpi",
    "fftw": "mpi_openmpi",
    "libvdwxc": "mpi_openmpi",
    "siesta": "mpi_openmpi",
    "plumed": "mpi_openmpi",
    # nwchem's ARMCI network lives in the build string and nowhere else — it is not in
    # the version, and the binary does not print it. `_pr` cannot run on one rank at
    # all, so shipping it would break the serial/parallel comparison below.
    "nwchem": "mpi_ts",
}
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


@check("gpaw is the MPI build, not the nompi one")
def _gpaw_is_mpi():
    import gpaw.mpi
    # Two independent statements of the same fact: the compile-time flag, and the type
    # of the world communicator (a nompi gpaw hands out a SerialCommunicator).
    assert getattr(gpaw.mpi, "have_mpi", False), \
        "gpaw.mpi.have_mpi is False — this is a nompi gpaw and this env's whole premise"
    assert type(gpaw.mpi.world).__name__ != "SerialCommunicator", \
        f"gpaw world communicator is {type(gpaw.mpi.world).__name__}, not an MPI one"


@check("MPI-flavor build strings are what the spec pinned (conda-meta, not the lock)")
def _build_strings():
    import json
    meta = Path(sys.prefix) / "conda-meta"
    assert meta.is_dir(), f"no conda-meta at {meta}"
    seen = {}
    for pkg, want in sorted(BUILD_STRING_MUST_CONTAIN.items()):
        recs = []
        for f in meta.glob(f"{pkg}-*.json"):
            try:
                rec = json.loads(f.read_text())
            except Exception:  # noqa: BLE001
                continue
            if rec.get("name") == pkg:
                recs.append(rec)
        assert recs, f"{pkg} not installed (no conda-meta record)"
        build = recs[0].get("build", "")
        assert want in build, (
            f"{pkg} build string is {build!r}, expected it to contain {want!r} — the "
            "resolver swapped the variant and the lock-hash cannot see that"
        )
        seen[pkg] = build
    for pkg in ("gpaw", "nwchem"):
        print(f"       ({pkg} {seen[pkg]})")


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


@check("psi4 Hartree-Fock on H2 (independent ab-initio kernel)")
def _psi4():
    import psi4
    scratch = Path(tempfile.mkdtemp())
    # psi4 writes an output file and scratch files relative to cwd by default; the
    # image runs as an unprivileged user, so point both at a temp dir.
    os.environ.setdefault("PSI_SCRATCH", str(scratch))
    psi4.core.set_output_file(str(scratch / "psi4.out"), False)
    psi4.set_memory("500 MB")
    psi4.geometry("0 1\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\nunits angstrom\n")
    e = psi4.energy("scf/sto-3g")
    # H2 at 0.74 A in a minimal basis: the textbook RHF/STO-3G total energy is
    # -1.1167 Hartree. This is a tight, well-known number, so unlike the gpaw checks
    # it can be asserted narrowly — psi4 computes it through an entirely separate
    # native integral/SCF stack from gpaw's, which is what makes it worth having here.
    assert -1.15 < e < -1.09, f"H2 RHF/STO-3G energy off: {e} Hartree"
    print(f"       (H2 RHF/STO-3G = {e:.6f} Hartree)")


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


# --- 5. siesta ---------------------------------------------------------------------
# READ THIS BEFORE STRENGTHENING THESE CHECKS. siesta gets weaker verification than
# gpaw, deliberately and for a reason that cannot be engineered around here: siesta
# needs a pseudopotential per element, and conda-forge's siesta ships NONE. The
# package is 359 files — binaries, libpsml and the psml2psf converter — with no .psf,
# .vps or .psml data and no example inputs. (Issue #1 assumed a bundled example
# exists; measured, it does not.) The obvious source, conda-forge `pseudo_dojo`, is
# 0.2 MB of code with no tables, and pins numpy <1.25 / pymatgen <=2023.9.10, so it
# would wreck this env twice over. Fetching pseudopotentials at run time is what made
# `whitebox` a wontfix (GAPS.md), so that is off the table too.
#
# So there is no SCF here. What IS verified is everything up to the point where the
# missing data stops it: the binary is aarch64, it is the MPI build, it parses a real
# fdf input, it initialises OpenMPI, it distributes over 2 ranks, and it runs its full
# setup path. That is a real process executing real work — much stronger than an
# import, and honestly weaker than gpaw's. Do not let the README claim otherwise.
print("[smoke] 5. siesta (binary + MPI; no SCF — see comment)")

SIESTA_FDF = """SystemName      si-probe
SystemLabel     si-probe
NumberOfAtoms   1
NumberOfSpecies 1
%block ChemicalSpeciesLabel
 1 14 Si
%endblock ChemicalSpeciesLabel
LatticeConstant 5.43 Ang
%block LatticeVectors
 0.0 0.5 0.5
 0.5 0.0 0.5
 0.5 0.5 0.0
%endblock LatticeVectors
AtomicCoordinatesFormat Fractional
%block AtomicCoordinatesAndAtomicSpecies
 0.0 0.0 0.0 1
%endblock AtomicCoordinatesAndAtomicSpecies
"""


def _mpi_env():
    env = dict(os.environ)
    # Container images commonly run as root, and CI boxes may have fewer slots than
    # ranks; Open MPI refuses to launch on both counts unless told.
    env["OMPI_ALLOW_RUN_AS_ROOT"] = "1"
    env["OMPI_ALLOW_RUN_AS_ROOT_CONFIRM"] = "1"
    env["OMPI_MCA_rmaps_base_oversubscribe"] = "yes"
    env["OMP_NUM_THREADS"] = "1"
    return env


def _run_siesta(argv, cwd, stdin=None):
    """Run siesta (optionally under mpiexec) and return its combined output.

    The return code is deliberately not asserted: with no pseudopotential shipped,
    siesta correctly calls MPI_ABORT, so a nonzero exit is the expected outcome and
    the evidence lives in the output.
    """
    proc = subprocess.run(argv, cwd=str(cwd), input=stdin, capture_output=True,
                          text=True, env=_mpi_env(), timeout=900)
    return proc.stdout + proc.stderr


@check("siesta reports an aarch64, MPI-parallel build")
def _siesta_version():
    siesta = shutil.which("siesta")
    assert siesta, "siesta not on PATH — check the mpi_openmpi build-string pin"
    out = _run_siesta([siesta, "--version"], cwd=tempfile.mkdtemp())
    ver = re.search(r"^Version\s*:\s*(\S+)", out, re.M)
    arch = re.search(r"^Architecture\s*:\s*(\S+)", out, re.M)
    par = re.search(r"^Parallelisations\s*:\s*(.+)$", out, re.M)
    assert ver, f"could not parse siesta version from:\n{out[:400]}"
    assert int(ver.group(1).split(".")[0]) >= 5, f"expected siesta >=5, got {ver.group(1)}"
    # The binary self-reporting its architecture is a stronger arm64 statement than
    # any subdir label: it is what the compiler actually targeted.
    assert arch and arch.group(1) == "aarch64", \
        f"siesta reports architecture {arch and arch.group(1)!r}, expected aarch64"
    assert par and "MPI" in par.group(1), \
        f"siesta is not an MPI build ({par and par.group(1)!r}) — the nompi variant " \
        "has a higher build number and wins unless the build string is pinned"
    print(f"       (siesta {ver.group(1)}, {arch.group(1)}, {par.group(1).strip()})")


@check("siesta parses a real fdf input and runs its setup path")
def _siesta_run():
    d = Path(tempfile.mkdtemp())
    out = _run_siesta([shutil.which("siesta")], cwd=d, stdin=SIESTA_FDF)
    # Reaching pseudo_read means the fdf was parsed, the species/lattice/coordinates
    # blocks were accepted and the whole initialisation ran. It stops here only
    # because no pseudopotential ships — see the section comment.
    assert "pseudo_read" in out, (
        f"siesta did not reach pseudopotential reading; output:\n{out[-1200:]}")
    assert "Si.{vps,psf,psml}" in out or "Pseudopotential file not found" in out, (
        "siesta failed before the pseudopotential stage, which means something other "
        f"than the missing data is wrong:\n{out[-1200:]}")


@check("siesta distributes over 2 MPI ranks")
def _siesta_mpi():
    mpiexec = shutil.which("mpiexec")
    assert mpiexec, "mpiexec not on PATH"
    d = Path(tempfile.mkdtemp())
    out = _run_siesta([mpiexec, "-n", "2", "--oversubscribe", shutil.which("siesta")],
                      cwd=d, stdin=SIESTA_FDF)
    # siesta announces its rank count itself, so this is siesta's own view of the
    # communicator rather than our assumption about what mpiexec did.
    m = re.search(r"Running on\s+(\d+)\s+nodes in parallel", out)
    assert m, f"siesta printed no parallel banner:\n{out[-1200:]}"
    assert int(m.group(1)) == 2, f"siesta saw {m.group(1)} ranks, expected 2"
    print(f"       (siesta: Running on {m.group(1)} nodes in parallel)")


# --- 6. nwchem ---------------------------------------------------------------------
# nwchem gets the FULL treatment that siesta cannot get, because unlike siesta it ships
# its own data: 607 basis-set files under $NWCHEM_BASIS_LIBRARY. So there is a real SCF
# here, serially and on 2 ranks, with the two answers compared.
print("[smoke] 6. nwchem (real SCF, serial + 2-rank)")

# H2O at its experimental geometry. RHF/STO-3G on this geometry is -74.963023 Hartree —
# a small, fast, completely determined number that comes out of an integral/SCF stack
# with nothing in common with gpaw's or psi4's.
NWCHEM_INPUT = """echo
start h2o
memory total 400 mb
geometry units angstrom
  O  0.00000  0.00000  0.11730
  H  0.00000  0.75720 -0.46920
  H  0.00000 -0.75720 -0.46920
end
basis
  * library sto-3g
end
task scf energy
"""

NWCHEM_SERIAL = {}


def _run_nwchem(argv):
    """Run nwchem on NWCHEM_INPUT in a fresh directory; return (rc, output).

    nwchem writes its database, movecs and scratch files into the working directory, so
    each leg gets its own. Output is read as bytes and decoded loosely: nwchem's stdout
    is not reliably valid UTF-8 when a rank aborts.
    """
    d = Path(tempfile.mkdtemp())
    (d / "h2o.nw").write_text(NWCHEM_INPUT)
    proc = subprocess.run(argv + ["h2o.nw"], cwd=str(d), capture_output=True,
                          env=_mpi_env(), timeout=900)
    out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    return proc.returncode, out


def _nwchem_energy(out):
    m = re.search(r"Total SCF energy\s*=\s*(-?\d+\.\d+)", out)
    assert m, f"no 'Total SCF energy' in nwchem output:\n{out[-1500:]}"
    return float(m.group(1))


def _nwchem_nproc(out):
    """nwchem's own count of its ranks, from its job banner."""
    m = re.search(r"^\s*nproc\s*=\s*(\d+)", out, re.M)
    assert m, f"nwchem printed no nproc line:\n{out[-1500:]}"
    return int(m.group(1))


@check("nwchem basis libraries come from activation, not from the binary's build path")
def _nwchem_basis():
    lib = os.environ.get("NWCHEM_BASIS_LIBRARY")
    # This is not a formality. conda-forge's nwchem has the FEEDSTOCK BUILD DIRECTORY
    # compiled into the binary as its default basis path, and sets the real one in
    # etc/conda/activate.d/nwchem_env.sh from $CONDA_PREFIX. Unactivated, nwchem exits
    # 255 looking for basis sets under /home/conda/feedstock_root/... (measured).
    # Consequence recorded in README: `apptainer exec` skips activate.d, so `dft` is now
    # the first env in this catalog where `exec` genuinely breaks and `run` is required.
    assert lib, ("NWCHEM_BASIS_LIBRARY is unset — activate.d did not run, and nwchem "
                 "will fall back to the feedstock build path baked into the binary")
    d = Path(lib)
    assert d.is_dir(), f"NWCHEM_BASIS_LIBRARY points at nothing: {d}"
    assert (d / "sto-3g").is_file(), f"no sto-3g basis under {d}"
    assert os.environ.get("NWCHEM_NWPW_LIBRARY"), "NWCHEM_NWPW_LIBRARY is unset"
    print(f"       ({sum(1 for _ in d.iterdir())} basis files at {d})")


@check("nwchem runs RHF/STO-3G on H2O (serial)")
def _nwchem_serial():
    nwchem = shutil.which("nwchem")
    assert nwchem, "nwchem not on PATH"
    rc, out = _run_nwchem([nwchem])
    assert rc == 0, f"nwchem exited {rc}:\n{out[-1500:]}"
    # The package is 7.3.1 but the binary's own banner says 7.3.0 (upstream's internal
    # version string lags the feedstock's), so match on the series, not the point release.
    ver = re.search(r"\(NWChem\)\s+(\d+\.\d+)", out)
    assert ver and ver.group(1) == "7.3", f"unexpected nwchem series: {ver and ver.group(1)!r}"
    assert _nwchem_nproc(out) == 1, "serial run did not report nproc = 1"
    e = _nwchem_energy(out)
    # Range rather than the exact -74.963023, per this file's convention; tight enough
    # that a broken integral or SCF path cannot land inside it.
    assert -75.05 < e < -74.85, f"H2O RHF/STO-3G energy off: {e} Hartree"
    NWCHEM_SERIAL["energy"] = e
    print(f"       (NWChem {ver.group(1)}, H2O RHF/STO-3G = {e:.6f} Hartree)")


@check("nwchem on 2 MPI ranks reproduces the serial energy")
def _nwchem_parallel():
    mpiexec = shutil.which("mpiexec")
    assert mpiexec, "mpiexec not on PATH"
    serial = NWCHEM_SERIAL.get("energy")
    assert serial is not None, "serial nwchem check did not run; nothing to compare"
    rc, out = _run_nwchem([mpiexec, "-n", "2", "--oversubscribe", shutil.which("nwchem")])
    assert rc == 0, f"mpiexec -n 2 nwchem exited {rc}:\n{out[-1500:]}"
    # nwchem's own rank count, not our assumption about mpiexec. With the `_pr` variant
    # this reads 1 even under -n 2, because a rank is spent as a data server — which is
    # the measurement behind the `mpi_ts` build-string pin in dft.yaml.
    nproc = _nwchem_nproc(out)
    assert nproc == 2, f"nwchem saw {nproc} compute ranks under -n 2, expected 2"
    par = _nwchem_energy(out)
    delta = abs(par - serial)
    assert delta < 1e-6, (
        f"2-rank energy {par:.9f} disagrees with serial {serial:.9f} "
        f"(delta {delta:.2e} Hartree)")
    print(f"       (nproc=2, serial {serial:.9f} vs 2-rank {par:.9f} Hartree, "
          f"delta {delta:.1e})")


# --- 7. parallel: the same DFT calculation on 2 ranks -----------------------------
# The whole point of this env. If OpenMPI/ELPA/ScaLAPACK link but do not compute, a
# serial run cannot tell — the numbers only diverge (or the job hangs) under ranks>1.
print("[smoke] 7. parallel gpaw (2-rank MPI)")


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
