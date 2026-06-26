#!/usr/bin/env python3
# comp-chem.smoke.py — the D3 verification for the `comp-chem` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image)
# for the computational-chemistry stack. The risky natives here are RDKit (C++
# cheminformatics), OpenMM (MD engine with a compute platform), PySCF (compiled
# quantum-chemistry kernels), and xtb (a Fortran semi-empirical QM binary) — all
# can solve yet fail to load/run. We exercise each through its native backend, not
# just import it. Pure stdlib + the env's own packages. Exit 0 = functionally sound.
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


# --- 1. Imports -----------------------------------------------------------------
HEADLINE = [
    "numpy", "scipy", "pandas",
    "rdkit", "rdkit.Chem", "openbabel",
    "openmm", "MDAnalysis", "mdtraj", "parmed", "pdbfixer",
    "ase", "pyscf", "xtb",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. cheminformatics ---------------------------------------------------------
print("[smoke] 2. cheminformatics")


@check("rdkit parse SMILES + descriptors (native C++)")
def _rdkit():
    from rdkit import Chem
    from rdkit.Chem import Descriptors, AllChem
    mol = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")  # aspirin
    assert mol is not None, "RDKit failed to parse SMILES"
    assert mol.GetNumAtoms() == 13, f"atom count {mol.GetNumAtoms()}"
    mw = Descriptors.MolWt(mol)
    assert 179.0 < mw < 181.0, f"aspirin MW off: {mw}"
    # 3D embed exercises the native conformer generator.
    molh = Chem.AddHs(mol)
    assert AllChem.EmbedMolecule(molh, randomSeed=1) == 0, "3D embed failed"


@check("openbabel format conversion")
def _openbabel():
    from openbabel import pybel
    mol = pybel.readstring("smi", "CCO")          # ethanol
    assert abs(mol.molwt - 46.07) < 0.1, f"ethanol MW off: {mol.molwt}"
    out = mol.write("inchi").strip()
    assert out.startswith("InChI="), f"unexpected InChI: {out[:20]}"


# --- 3. molecular dynamics + trajectory analysis --------------------------------
print("[smoke] 3. molecular dynamics")


@check("openmm computes energy on a real platform")
def _openmm():
    import openmm as mm
    from openmm import unit
    # A two-particle harmonic system — minimal, but it runs the native compute
    # platform end to end (context creation + state energy).
    system = mm.System()
    system.addParticle(1.0); system.addParticle(1.0)
    force = mm.HarmonicBondForce()
    force.addBond(0, 1, 0.15, 1000.0)             # 0.15 nm eq, k=1000
    system.addForce(force)
    integ = mm.VerletIntegrator(1.0 * unit.femtosecond)
    ctx = mm.Context(system, integ)
    ctx.setPositions([[0, 0, 0], [0.25, 0, 0]] * unit.nanometer)
    e = ctx.getState(getEnergy=True).getPotentialEnergy()
    assert e.value_in_unit(unit.kilojoule_per_mole) > 0, "expected nonzero strain energy"
    platform = ctx.getPlatform().getName()
    print(f"       (openmm platform: {platform})")


@check("mdtraj builds + measures a trajectory")
def _mdtraj():
    import numpy as np
    import mdtraj as md
    # Build a tiny 3-atom topology and a 2-frame trajectory; measure a distance.
    top = md.Topology()
    ch = top.add_chain(); res = top.add_residue("X", ch)
    a = [top.add_atom(n, md.element.carbon, res) for n in ("C1", "C2", "C3")]
    xyz = np.array([[[0, 0, 0], [0.1, 0, 0], [0.2, 0, 0]],
                    [[0, 0, 0], [0.15, 0, 0], [0.3, 0, 0]]], dtype="float32")
    traj = md.Trajectory(xyz, top)
    d = md.compute_distances(traj, [[0, 1]])
    assert d.shape == (2, 1), f"distance shape {d.shape}"
    assert abs(d[0, 0] - 0.1) < 1e-5 and abs(d[1, 0] - 0.15) < 1e-5


# --- 4. quantum / atomistic -----------------------------------------------------
print("[smoke] 4. quantum / atomistic")


@check("ase builds a molecule")
def _ase():
    from ase.build import molecule
    h2o = molecule("H2O")
    assert len(h2o) == 3, f"H2O atom count {len(h2o)}"
    assert h2o.get_chemical_formula() == "H2O"


@check("pyscf runs an SCF (compiled kernels)")
def _pyscf():
    from pyscf import gto, scf
    mol = gto.M(atom="H 0 0 0; H 0 0 0.74", basis="sto-3g", verbose=0)
    e = scf.RHF(mol).kernel()                     # drives the native integrals + SCF
    assert -1.2 < e < -1.0, f"H2 HF energy off: {e}"


@check("xtb semi-empirical energy (Fortran binary)")
def _xtb():
    import numpy as np
    from xtb.interface import Calculator
    from xtb.utils import get_method
    # H2 at ~0.74 Angstrom = 1.4 Bohr. xtb works in atomic units (Bohr).
    numbers = np.array([1, 1])
    positions = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.4]])
    calc = Calculator(get_method("GFN2-xTB"), numbers, positions)
    res = calc.singlepoint()
    e = res.get_energy()
    assert e < 0, f"xtb energy should be negative, got {e}"


# --- verdict --------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: comp-chem env assembles, imports, and works on "
      + sys.platform + "/" + sys.implementation.name + " — verified.")
