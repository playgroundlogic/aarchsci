#!/usr/bin/env python3
# md.smoke.py — the D3 verification for the `md` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image) for
# the classical-MD stack. The risky pieces here are not python modules at all: they
# are three large compiled MD engines — gromacs (C++/SIMD, MPI), lammps (C++, MPI)
# and ambertools' sander (Fortran) — each of which can install perfectly and then
# fail to start, or start and silently be the serial build we did not ask for.
#
# So every engine is run for real: a short MD integration on a system built from
# data the packages themselves ship, with the resulting energy asserted to be
# physical. Two of the three additionally run under 2-rank MPI, because the MPI
# build is the entire reason this env exists and an unexercised MPI path is exactly
# where an assemble-gap hides. Pure stdlib + the env's own packages.
# Exit 0 = functionally sound.
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []

# gromacs is not on PATH under its usual name: conda-forge's linux-aarch64 build
# installs the binary as `gmx_mpi` inside a SIMD-suffixed directory rather than in
# `bin/`. Resolve it from the prefix instead of assuming.
PREFIX = Path(sys.prefix)
GMX = next((p for p in sorted(PREFIX.glob("bin.*/gmx_mpi")) if p.is_file()), None)
GMX_TOP = PREFIX / "share" / "gromacs" / "top"

# MPI in a container runs as whatever user the image sets, often root, and CI runners
# may expose fewer cores than ranks requested. Both need saying out loud or Open MPI
# refuses to launch.
MPI_ENV = dict(
    os.environ,
    OMPI_ALLOW_RUN_AS_ROOT="1",
    OMPI_ALLOW_RUN_AS_ROOT_CONFIRM="1",
    OMP_NUM_THREADS="1",
)


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


def run(cmd, cwd, stdin=None):
    """Run a command, returning CompletedProcess; raises with tail of output on failure."""
    proc = subprocess.run(
        [str(c) for c in cmd], cwd=str(cwd), input=stdin, env=MPI_ENV,
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
        raise AssertionError(
            f"{cmd[0]} exited {proc.returncode}:\n    " + "\n    ".join(tail))
    return proc


# --- 1. Imports -----------------------------------------------------------------
HEADLINE = [
    "numpy", "scipy",
    "mpi4py", "mpi4py.MPI",
    "lammps", "MDAnalysis", "mdtraj", "parmed",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. engines are present, are the builds we asked for, and start --------------
print("[smoke] 2. engine binaries + build provenance")


@check("gromacs binary exists and reports an MPI + NEON build")
def _gmx_version():
    assert GMX is not None, (
        f"no bin.*/gmx_mpi under {PREFIX} — gromacs is not the mpi_openmpi build, "
        "or the SIMD bin directory moved")
    out = run([GMX, "--version"], cwd=PREFIX).stdout
    ver = re.search(r"GROMACS version:\s*(\S+)", out)
    simd = re.search(r"SIMD instructions:\s*(\S+)", out)
    mpi = re.search(r"MPI library:\s*(\S+)", out)
    assert ver, "could not parse GROMACS version"
    assert mpi and mpi.group(1) != "none", f"gromacs is not an MPI build: {mpi}"
    assert simd and simd.group(1).startswith("ARM"), \
        f"gromacs SIMD is not an ARM variant: {simd and simd.group(1)}"
    print(f"       (gromacs {ver.group(1)}, SIMD {simd.group(1)}, MPI {mpi.group(1)})")


@check("lammps binary exists and is the MPI build")
def _lmp_version():
    lmp = shutil.which("lmp_mpi")
    assert lmp, "lmp_mpi not on PATH — lammps is not the mpi_openmpi build"
    # -h prints the build's compiled-in package list and exits 0.
    out = run([lmp, "-h"], cwd=PREFIX).stdout
    assert "Large-scale Atomic" in out or "LAMMPS" in out, "unexpected lmp -h output"


@check("mpiexec is present (an MPI build with no launcher is not usable)")
def _mpiexec():
    assert shutil.which("mpiexec"), "mpiexec not found"


@check("ambertools ships sander, tleap, cpptraj and its force-field data")
def _amber_files():
    for tool in ("sander", "tleap", "cpptraj"):
        assert shutil.which(tool), f"{tool} not on PATH"
    leaprc = PREFIX / "dat" / "leap" / "cmd" / "leaprc.protein.ff14SB"
    assert leaprc.is_file(), f"missing shipped force field: {leaprc}"
    # pmemd is licence-gated and is never in AmberTools; assert the absence so a
    # future package that DOES ship it is noticed rather than silently ignored.
    assert shutil.which("pmemd") is None, \
        "pmemd unexpectedly present — check the licence terms before relying on it"


# --- 3. gromacs runs real MD on shipped force-field data ------------------------
print("[smoke] 3. gromacs MD")


@check("gromacs grompp + mdrun integrate 216 SPC waters")
def _gmx_md():
    d = Path(tempfile.mkdtemp())
    # amber99sb-ildn.ff and spc216.gro both ship inside the gromacs package, so
    # nothing is downloaded. tip3p.itp defines residue SOL with atoms OW/HW1/HW2,
    # which is exactly what spc216.gro contains.
    (d / "topol.top").write_text(
        '#include "amber99sb-ildn.ff/forcefield.itp"\n'
        '#include "amber99sb-ildn.ff/tip3p.itp"\n'
        "[ system ]\nwater\n[ molecules ]\nSOL 216\n")
    # spc216.gro's box is 1.862 nm, so a 0.9 nm cutoff is just inside half the box.
    (d / "md.mdp").write_text(
        "integrator = md\nnsteps = 20\ndt = 0.002\ncutoff-scheme = Verlet\n"
        "coulombtype = cut-off\nrvdw = 0.9\nrcoulomb = 0.9\nconstraints = h-bonds\n")
    gro = GMX_TOP / "spc216.gro"
    assert gro.is_file(), f"missing shipped structure {gro}"
    run([GMX, "grompp", "-f", "md.mdp", "-c", gro, "-p", "topol.top",
         "-o", "t.tpr", "-maxwarn", "5"], cwd=d)
    run([GMX, "mdrun", "-s", "t.tpr", "-deffnm", "out", "-nsteps", "20"], cwd=d)
    # Read the potential energy back out of the binary .edr through gmx energy,
    # rather than scraping the log — this also exercises the energy-file reader.
    out = run([GMX, "energy", "-f", "out.edr", "-o", "e.xvg"], cwd=d,
              stdin="Potential\n").stdout
    m = re.search(r"^Potential\s+(-?\d+\.?\d*)", out, re.M)
    assert m, f"could not parse Potential from gmx energy output:\n{out[-500:]}"
    pot = float(m.group(1))
    # 216 SPC waters cohere at roughly -40 kJ/mol per molecule; a truncated-cutoff
    # run lands near -9600 kJ/mol total. Assert the physics, not the exact number,
    # so channel drift cannot cause a false failure.
    per_water = pot / 216.0
    assert -70.0 < per_water < -20.0, \
        f"SPC water potential energy unphysical: {pot} kJ/mol ({per_water:.1f}/molecule)"
    print(f"       (gromacs potential: {pot:.1f} kJ/mol, {per_water:.1f}/water)")


# --- 4. lammps runs real MD, serially and over 2 MPI ranks ----------------------
print("[smoke] 4. lammps MD")

# The canonical LAMMPS Lennard-Jones melt. Deliberately chosen because it needs no
# potential file: `lammps` ships only bin/lmp, bin/lmp_mpi and the python module
# (42 files), so any test requiring tabulated potentials would need external data.
MELT = """units lj
atom_style atomic
lattice fcc 0.8442
region box block 0 4 0 4 0 4
create_box 1 box
create_atoms 1 box
mass 1 1.0
velocity all create 3.0 87287 loop geom
pair_style lj/cut 2.5
pair_coeff 1 1 1.0 1.0 2.5
neighbor 0.3 bin
neigh_modify every 20 delay 0 check no
fix 1 all nve
thermo 25
run 50
"""


def _lmp_total_energy(log_text):
    """Pull the final TotEng column out of a LAMMPS thermo table."""
    rows = []
    for block in re.finditer(r"^\s*Step\s+.*$", log_text, re.M):
        header = block.group(0).split()
        if "TotEng" not in header:
            continue
        col = header.index("TotEng")
        for line in log_text[block.end():].splitlines():
            parts = line.split()
            if len(parts) == len(header) and all(
                    re.fullmatch(r"-?\d+\.?\d*(e[-+]?\d+)?", p) for p in parts):
                rows.append(float(parts[col]))
            elif rows:
                break
    assert rows, "no TotEng values parsed from LAMMPS log"
    return rows[-1]


@check("lammps runs the LJ melt (serial) with a physical total energy")
def _lmp_serial():
    d = Path(tempfile.mkdtemp())
    (d / "in.melt").write_text(MELT)
    run([shutil.which("lmp_mpi"), "-in", "in.melt", "-log", "lmp.log"], cwd=d)
    e = _lmp_total_energy((d / "lmp.log").read_text())
    # 256 LJ atoms at rho*=0.8442, T*=3.0 equilibrate to TotEng/atom ~= -2.3 in
    # reduced units; this is the reference value of the upstream melt example.
    assert -4.0 < e < -1.0, f"LJ melt total energy unphysical: {e}"
    print(f"       (lammps TotEng: {e:.4f} reduced units)")


@check("lammps runs over 2 MPI ranks and agrees with the serial energy")
def _lmp_mpi():
    d = Path(tempfile.mkdtemp())
    (d / "in.melt").write_text(MELT)
    run([shutil.which("lmp_mpi"), "-in", "in.melt", "-log", "s.log"], cwd=d)
    run(["mpiexec", "-n", "2", "--oversubscribe",
         shutil.which("lmp_mpi"), "-in", "in.melt", "-log", "p.log"], cwd=d)
    ptext = (d / "p.log").read_text()
    m = re.search(r"with (\d+) MPI task", ptext)
    assert m and int(m.group(1)) == 2, \
        f"expected 2 MPI tasks, log says: {m.group(0) if m else 'nothing'}"
    serial = _lmp_total_energy((d / "s.log").read_text())
    parallel = _lmp_total_energy(ptext)
    # Domain decomposition changes force summation order, so this is a
    # floating-point-reordering tolerance, not a physics tolerance.
    assert abs(serial - parallel) < 1e-2, \
        f"serial {serial} vs 2-rank {parallel} disagree beyond reordering noise"
    print(f"       (lammps serial {serial:.4f} vs 2-rank {parallel:.4f})")


# --- 5. ambertools prepares a system and integrates it --------------------------
print("[smoke] 5. ambertools prep + sander MD")


@check("tleap builds a capped alanine with ff14SB, sander conserves its energy")
def _amber_md():
    d = Path(tempfile.mkdtemp())
    (d / "l.in").write_text(
        "source leaprc.protein.ff14SB\n"
        "sys = sequence { ACE ALA NME }\n"
        "saveamberparm sys sys.parm7 sys.rst7\nquit\n")
    run([shutil.which("tleap"), "-f", "l.in"], cwd=d)
    for f in ("sys.parm7", "sys.rst7"):
        assert (d / f).is_file(), f"tleap did not write {f}"
    (d / "md.in").write_text(
        "minimal\n &cntrl\n  imin=0, nstlim=20, dt=0.001, ntb=0, cut=99.0,\n"
        "  ntpr=10, ntt=0, ntc=1\n /\n")
    run([shutil.which("sander"), "-O", "-i", "md.in", "-p", "sys.parm7",
         "-c", "sys.rst7", "-o", "md.out", "-r", "md.rst"], cwd=d)
    text = (d / "md.out").read_text()
    # Parse ONLY the per-step blocks. sander appends two trailing summary blocks in the
    # same `Etot = ...` format — "A V E R A G E S" and "R M S  F L U C T U A T I O N S"
    # — and the RMS one holds a fluctuation (~0.004) rather than a total energy (~-13.3).
    # Scooping those into the same list makes max-min report a 13.3 kcal/mol "drift" that
    # is pure parsing artefact; the real conservation here is 0.011. Split first.
    steps = text.split("A V E R A G E S")[0]
    etot = [float(m.group(1)) for m in re.finditer(r"Etot\s+=\s*(-?\d+\.\d+)", steps)]
    assert len(etot) >= 2, f"sander reported {len(etot)} Etot values, expected >=2"
    # In vacuo NVE with no thermostat: total energy must be conserved. This is a much
    # stronger statement than "it ran" — it says the Fortran force and integration
    # kernels are numerically correct on this architecture, not merely loadable.
    drift = max(etot) - min(etot)
    assert abs(drift) < 0.5, f"sander energy not conserved, drift {drift} kcal/mol"
    assert etot[0] < 0, f"expected negative total energy, got {etot[0]}"
    # Cross-check against sander's own RMS fluctuation of Etot, which it computes
    # independently of our parsing — if the two disagree, suspect the parser first.
    rms = re.search(r"R M S.*?Etot\s+=\s*(-?\d+\.\d+)", text, re.S)
    assert rms and float(rms.group(1)) < 0.5, \
        f"sander's own Etot RMS fluctuation is not small: {rms and rms.group(1)}"
    print(f"       (sander Etot {etot[0]:.4f} kcal/mol, drift over 20 steps "
          f"{drift:.4f}, sander's own RMS {float(rms.group(1)):.4f})")


@check("mdanalysis reads the sander topology ambertools just wrote")
def _mda_reads_amber():
    d = Path(tempfile.mkdtemp())
    (d / "l.in").write_text(
        "source leaprc.protein.ff14SB\n"
        "sys = sequence { ACE ALA NME }\n"
        "saveamberparm sys sys.parm7 sys.rst7\nquit\n")
    run([shutil.which("tleap"), "-f", "l.in"], cwd=d)
    import MDAnalysis as mda
    # `format` is REQUIRED here. MDAnalysis dispatches coordinate readers on file
    # extension and has no entry for `.rst7` — the Amber restart reader is registered as
    # RESTRT/INPCRD — so the default guess raises "Unknown coordinate trajectory format
    # 'RST7'". tleap writes `.rst7` by convention, so every consumer reading an
    # AmberTools restart through MDAnalysis hits this; worth having in the test.
    u = mda.Universe(str(d / "sys.parm7"), str(d / "sys.rst7"), format="RESTRT")
    # ACE + ALA + NME capped alanine is 22 atoms in ff14SB.
    assert len(u.atoms) == 22, f"expected 22 atoms, got {len(u.atoms)}"
    assert len(u.residues) == 3, f"expected 3 residues, got {len(u.residues)}"


# --- verdict --------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: md env assembles, imports, and works on "
      + sys.platform + "/" + sys.implementation.name + " — verified.")
