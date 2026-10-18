#!/usr/bin/env python3
# comp-chem.smoke.py — the D3 verification for the `comp-chem` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image)
# for the computational-chemistry stack. The risky natives here are RDKit (C++
# cheminformatics), OpenMM (MD engine with a compute platform), PySCF (compiled
# quantum-chemistry kernels) and AutoDock Vina — all
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
    "ase", "pyscf",
    "vina",
    "cantera",
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


# xtb was removed from this env on 2026-10-18 (issue #46): every conda-forge aarch64 build
# returns wrong analytic gradients with correct energies. The check that lived here asserted
# only `energy < 0` on H2, which passed the defective build — a reminder that an energy-only
# check cannot see a gradient bug. See envs/comp-chem.yaml for the measurements.


# --- 5. docking -----------------------------------------------------------------
print("[smoke] 5. docking")

# PDBQT is a fixed-column format and the `vina` package ships no example data (17
# files: the module, plus bin/vina and bin/vina_split), so the test system is built
# here. Column positions matter: partial charge at 69-76, AutoDock type at 78-79.
_PDBQT = ("ATOM  %5d %-4s %3s %1s%4d    %8.3f%8.3f%8.3f%6.2f%6.2f    %6.3f %-2s")


def _atom(serial, res, chain, x, y, z, kind="C"):
    return _PDBQT % (serial, kind, res, chain, 1, x, y, z, 1.0, 0.0, 0.0, kind)


@check("vina scores a ligand against a receptor (native scoring + grid)")
def _vina():
    from vina import Vina
    tmp = Path(tempfile.mkdtemp())
    # Six carbons on the axes form a crude pocket centred on the origin; a single
    # carbon sits in it. Chemically meaningless, but it drives the real code path:
    # receptor parse, affinity-map computation, scoring-function evaluation.
    rec = "\n".join(_atom(i + 1, "ALA", "A", x, y, z) for i, (x, y, z) in enumerate(
        [(-4, 0, 0), (4, 0, 0), (0, -4, 0), (0, 4, 0), (0, 0, -4), (0, 0, 4)]))
    (tmp / "rec.pdbqt").write_text(rec + "\nTER\n")
    lig = _atom(1, "UNL", " ", 0.0, 0.0, 0.0)
    (tmp / "lig.pdbqt").write_text("ROOT\n" + lig + "\nENDROOT\nTORSDOF 0\n")

    v = Vina(sf_name="vina", verbosity=0)
    v.set_receptor(str(tmp / "rec.pdbqt"))
    v.set_ligand_from_file(str(tmp / "lig.pdbqt"))
    v.compute_vina_maps(center=[0, 0, 0], box_size=[20, 20, 20])
    energies = v.score()
    total = float(energies[0])
    # A carbon in a carbon cage must be net attractive, and Vina's scoring
    # function is bounded well inside this range for any physical pose.
    assert -50.0 < total < 0.0, f"vina total score implausible: {total}"
    print(f"       (vina total score: {total:.3f} kcal/mol)")


# --- thermochemistry and kinetics (issue #38) -----------------------------------
print("[smoke] 7. cantera: conservation identities, not bands")


@check("cantera ships its mechanisms and obeys the ideal-gas law exactly")
def _cantera_state():
    import glob
    import os
    import cantera as ct
    # The mechanisms ship IN the package — 23 YAML files including gri30 — so this needs
    # no download, which is what made it acceptable here at all (the `whitebox` wontfix
    # precedent).
    bundled = glob.glob(os.path.join(ct.__path__[0], "data", "*.yaml"))
    assert len(bundled) >= 10, f"only {len(bundled)} bundled mechanisms"
    assert os.path.exists(os.path.join(ct.__path__[0], "data", "gri30.yaml")), \
        "gri30.yaml is not bundled"
    g = ct.Solution("gri30.yaml")
    g.TPX = 1200.0, ct.one_atm, "CH4:1, O2:2, N2:7.52"
    assert g.n_species == 53 and g.n_reactions == 325, \
        f"gri30 is {g.n_species} species / {g.n_reactions} reactions, expected 53/325"
    # Mass fractions sum to EXACTLY one — equality, not a tolerance.
    assert sum(g.Y) - 1.0 == 0.0, f"sum(Y) - 1 = {sum(g.Y) - 1.0!r}, expected exactly 0"
    # P = rho*R*T/M is the definition of the ideal-gas phase, so the only error allowed
    # is round-off. Measured 1.4e-16.
    rhs = g.density * ct.gas_constant * g.T / g.mean_molecular_weight
    rel = abs(rhs - g.P) / g.P
    assert rel < 1e-13, f"ideal-gas law closure {rel:.3e}, expected round-off"
    print(f"       (cantera {ct.__version__}, {len(bundled)} bundled mechanisms; "
          f"gri30 {g.n_species} species/{g.n_reactions} reactions; "
          f"sum(Y)-1 = {sum(g.Y) - 1.0:.1f} exactly; PV=nRT closure {rel:.1e})")


@check("constant-enthalpy equilibration conserves enthalpy and every element")
def _cantera_equilibrate():
    import cantera as ct
    g = ct.Solution("gri30.yaml")
    g.TPX = 1200.0, ct.one_atm, "CH4:1, O2:2, N2:7.52"
    h0 = g.enthalpy_mass
    el0 = {e: g.elemental_mass_fraction(e) for e in g.element_names}
    # 'HP' holds enthalpy and pressure fixed while the composition relaxes to
    # equilibrium, so h is an INVARIANT the solver must preserve and the elements are
    # conserved by construction. Both are identities; neither is a band on an observed
    # number. Burning methane also moves the state a long way (T 1200 -> ~2620 K), so a
    # solver that silently did nothing would fail the temperature check below.
    g.equilibrate("HP")
    dh = abs(g.enthalpy_mass - h0) / abs(h0)
    assert dh < 1e-9, f"enthalpy drifted {dh:.3e} under HP equilibration"
    worst = 0.0
    for e, v0 in el0.items():
        worst = max(worst, abs(g.elemental_mass_fraction(e) - v0))
    assert worst < 1e-10, f"element mass fraction drifted {worst:.3e}"
    assert sum(g.Y) - 1.0 == 0.0, "mass fractions do not sum to exactly 1 after equilibrium"
    # Adiabatic flame temperature for this mixture from 1200 K; a wide window, because
    # the point is that combustion actually happened, not to pin the value.
    assert 2000.0 < g.T < 3200.0, f"adiabatic flame temperature {g.T:.1f} K is implausible"
    print(f"       (HP equilibrate: |dh/h| = {dh:.2e}, worst element drift {worst:.2e}, "
          f"T_ad = {g.T:.1f} K)")


# --- verdict --------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: comp-chem env assembles, imports, and works on "
      + sys.platform + "/" + sys.implementation.name + " — verified.")
