#!/usr/bin/env python3
# fem-cfd.smoke.py — the D3 verification for the `fem-cfd` env.
#
# Same contract as dft.smoke.py, and the same reason for existing: this env is a tower of
# natives (OpenMPI -> PETSc -> MUMPS/SuperLU_DIST/hypre -> dolfinx's C++ core, plus HDF5
# and ADIOS2 for I/O and NVPL for BLAS). Every one of those can install and import while
# producing wrong numbers or silently dropping to one rank, which is exactly the
# assemble-gap D3 exists to catch.
#
# WHAT MAKES THIS TESTABLE TO MACHINE PRECISION. The finite-element method has a property
# a smoke test can exploit: if the exact solution of the PDE happens to lie in the finite
# element space, the discrete solution is that exact solution, up to round-off. So this
# file solves the Poisson problem
#
#     -laplacian(u) = f   on the unit square,   u = u_exact on the boundary
#     u_exact = 1 + x^2 + 2y^2   =>   laplacian(u_exact) = 2 + 4 = 6   =>   f = -6
#
# with P2 (quadratic) elements. u_exact is a quadratic polynomial, so it lies exactly in
# the P2 space and the L2 error must be ~1e-15, not "small". Getting 1e-3 there would mean
# something in the tower is broken. The same problem with P1 (linear) elements CANNOT
# represent it, and then the error must fall by a factor of 4 when the mesh is halved
# (O(h^2) convergence) — a second, independent statement that the discretisation is real
# and not accidentally right.
#
# BUILD STRINGS ARE ASSERTED, NOT ASSUMED. See BUILD_STRING_MUST_CONTAIN below: the
# MPI flavor and the PETSc scalar/device variant are checked out of conda-meta, because
# `envs/fem-cfd.lock.txt` records `name version` only (DESIGN OQ2) and cannot express
# "openmpi, not mpich" or "real, not cuda13_real".
#
# ONE ENTRYPOINT, TWO RANKS. As in dft.smoke.py, the parallel leg lives inside this same
# script via AARCHSCI_MPI_CHILD self-reinvocation, so the consumer-facing one-liner
# (`docker run ... python /opt/aarchsci/smoke.py`) really does re-earn the whole
# verification, MPI included.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import glob
import json
import os
import platform
import shutil
import subprocess
import sys
import traceback

FAILURES = []
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


def conda_build_string(pkg):
    """Read a package's build string out of conda-meta, or raise."""
    matches = glob.glob(f"/opt/conda/conda-meta/{pkg}-*.json")
    # Guard against prefix collisions (`petsc-` also matching `petsc4py-`): the filename
    # is name-version-build.json, so the segment before the version must be exactly pkg.
    for path in matches:
        stem = os.path.basename(path)[: -len(".json")]
        parts = stem.rsplit("-", 2)
        if len(parts) == 3 and parts[0] == pkg:
            return parts[2]
    raise AssertionError(f"{pkg} is not installed (no conda-meta entry)")


def package_depends(pkg):
    """Read a package's declared runtime dependencies out of conda-meta."""
    for path in glob.glob(f"/opt/conda/conda-meta/{pkg}-*.json"):
        stem = os.path.basename(path)[: -len(".json")]
        parts = stem.rsplit("-", 2)
        if len(parts) == 3 and parts[0] == pkg:
            with open(path) as fh:
                return json.load(fh).get("depends", [])
    raise AssertionError(f"{pkg} is not installed (no conda-meta entry)")


def installed(pkg):
    try:
        conda_build_string(pkg)
        return True
    except AssertionError:
        return False


# ----------------------------------------------------------------------------------
# The child process runs ONLY the parallel leg. Everything else is serial and would
# just be duplicated work under mpiexec.
# ----------------------------------------------------------------------------------
def poisson_l2_error(n, degree, comm):
    """Solve -lap(u)=−6 with u=1+x^2+2y^2 on the boundary; return the L2 error.

    Returned error is the global (allreduced) norm, so it is identical on every rank and
    directly comparable between a 1-rank and a 2-rank run.
    """
    import numpy as np
    import ufl
    from dolfinx import default_scalar_type, fem, mesh
    from dolfinx.fem.petsc import LinearProblem

    msh = mesh.create_unit_square(comm, n, n, mesh.CellType.triangle)
    V = fem.functionspace(msh, ("Lagrange", degree))

    uD = fem.Function(V)
    uD.interpolate(lambda x: 1.0 + x[0] ** 2 + 2.0 * x[1] ** 2)
    tdim = msh.topology.dim
    msh.topology.create_connectivity(tdim - 1, tdim)
    facets = mesh.exterior_facet_indices(msh.topology)
    bc = fem.dirichletbc(uD, fem.locate_dofs_topological(V, tdim - 1, facets))

    u, v = ufl.TrialFunction(V), ufl.TestFunction(V)
    f = fem.Constant(msh, default_scalar_type(-6.0))
    a = ufl.inner(ufl.grad(u), ufl.grad(v)) * ufl.dx
    L = f * v * ufl.dx

    # dolfinx 0.11 made `petsc_options_prefix` a REQUIRED keyword-only argument to
    # LinearProblem (it was absent in 0.9). Verified against the installed 0.11.0, not
    # recalled from the docs. A direct LU factorisation is used so the residual is not a
    # source of error in a test whose whole point is that the error is ~1e-15.
    problem = LinearProblem(
        a, L, bcs=[bc],
        petsc_options_prefix="aarchsci_smoke_",
        petsc_options={"ksp_type": "preonly", "pc_type": "lu",
                       "ksp_error_if_not_converged": True},
    )
    uh = problem.solve()

    x = ufl.SpatialCoordinate(msh)
    u_ex = 1.0 + x[0] ** 2 + 2.0 * x[1] ** 2
    sq = fem.assemble_scalar(fem.form((uh - u_ex) ** 2 * ufl.dx))
    return float(np.sqrt(comm.allreduce(sq, op=__import__("mpi4py").MPI.SUM)))


if CHILD:
    from mpi4py import MPI
    comm = MPI.COMM_WORLD
    # If MPI silently degraded to a single rank this env's entire reason for existing is
    # unverified, so it is a hard failure rather than a skip.
    assert comm.size > 1, f"child ran with only {comm.size} rank(s); MPI is not working"
    err = poisson_l2_error(16, 2, comm)
    if comm.rank == 0:
        # Bare float on stdout: the parent parses this and compares to its serial run.
        print(f"{err!r}")
    sys.exit(0)


# --- 1. imports -------------------------------------------------------------------
HEADLINE = [
    "numpy", "scipy",
    "mpi4py", "mpi4py.MPI",
    "petsc4py", "petsc4py.PETSc",
    # slepc4py is here even though fem-cfd.yaml never asks for it: fenics-dolfinx lists
    # it (with petsc4py and mpi4py) in its own runtime depends, so it is in the image
    # either way. Anything shipped gets verified, whether or not we requested it.
    "slepc4py", "slepc4py.SLEPc",
    "basix", "ufl", "ffcx",
    "dolfinx", "dolfinx.fem", "dolfinx.mesh", "dolfinx.fem.petsc", "dolfinx.io",
]
print("[smoke] 1. imports")
for mod in dict.fromkeys(HEADLINE):
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. build-string / flavor layer ------------------------------------------------
# The lock file records `name version` only, so none of the following can be expressed
# there. Assert them from conda-meta instead. Every entry here corresponds to a pin or a
# documented risk in fem-cfd.yaml — if a pin stops working, this section is what notices.
print("[smoke] 2. flavor + variant assertions (what the lock cannot express)")

BUILD_STRING_MUST_CONTAIN = {
    "petsc": "real_",             # not complex_, and see the cuda check below
    "slepc": "real_",
    "hdf5": "mpi_openmpi",        # parallel I/O must follow the env's MPI flavor
    "fftw": "mpi_openmpi",
    "libadios2": "mpi_openmpi",
}


@check("pinned build strings are what fem-cfd.yaml claims")
def _build_strings():
    got = {}
    for pkg, needle in sorted(BUILD_STRING_MUST_CONTAIN.items()):
        bs = conda_build_string(pkg)
        got[pkg] = bs
        assert needle in bs, f"{pkg} build string {bs!r} does not contain {needle!r}"
    for pkg, bs in got.items():
        print(f"       {pkg:12} {bs}")


@check("PETSc is not a CUDA build (D4: Graviton has no NVIDIA GPU)")
def _no_cuda():
    bs = conda_build_string("petsc")
    assert "cuda" not in bs, f"petsc is a CUDA build ({bs}); `petsc=*=real_*` did not hold"
    # And the CUDA runtime it would have dragged in must be absent from the image.
    for pkg in ("cuda-cudart", "libcublas", "cuda-version"):
        assert not installed(pkg), f"{pkg} is installed; something pulled the CUDA stack in"
    print(f"       (petsc {bs}, no cuda-* packages present)")


@check("exactly one MPI implementation is installed, and it is OpenMPI")
def _one_mpi():
    assert installed("openmpi"), "openmpi is not installed"
    # The `mpi=*=openmpi` metapackage lock is what enforces this. MPICH would work in
    # isolation; what must never happen is BOTH, or a silent switch (fem-cfd.yaml
    # explains why the flavor is env-wide on conda-forge).
    assert not installed("mpich"), "mpich is installed alongside openmpi — flavors mixed"
    mpi_bs = conda_build_string("mpi")
    assert mpi_bs == "openmpi", f"mpi metapackage build string is {mpi_bs!r}, expected openmpi"
    print(f"       (mpi {mpi_bs}, openmpi {conda_build_string('openmpi')})")


@check("no nompi build slipped through (the trap that bit gpaw and siesta)")
def _no_nompi():
    # A `nompi_*` build declares no mpi dependency at all, so it never contradicts
    # `mpi=*=openmpi` and the metapackage lock cannot exclude it. fem-cfd.yaml records
    # that fenics-dolfinx 0.11.0 has no nompi aarch64 variant, which is why it needs no
    # build-string pin. That reasoning is only true until upstream adds one, so verify it
    # rather than trusting the comment to stay correct.
    for pkg in ("fenics-dolfinx", "fenics-libdolfinx", "petsc", "slepc", "hdf5",
                "fftw", "libadios2"):
        if installed(pkg):
            bs = conda_build_string(pkg)
            assert "nompi" not in bs, f"{pkg} is a nompi build ({bs}) — MPI is not wired in"
    print("       (no nompi variants among dolfinx/petsc/slepc/hdf5/fftw/adios2)")


@check("dolfinx's py3NN build string is NOT a Python-ABI claim, per its own metadata")
def _nanobind_abi():
    # Documented and asserted because it looks alarming and is not: fenics-dolfinx and
    # fenics-basix carry py312 build strings while this env runs a newer interpreter.
    # The evidence is in dolfinx's own dependency list, so this check reads it rather
    # than inferring from "well, the import worked": it declares `cpython >=3.12`,
    # `_python_abi3_support` and `nanobind-abi ==19`, and declares NO `python_abi 3.N.*`
    # pin at all. That is what makes the py312 tag inert. Contrast su2 (see fem-cfd.yaml),
    # whose builds DO carry `python_abi 3.N.* *_cpNN` and therefore really do cap the
    # env's interpreter. A py3NN tag is no more a platform claim than a missing subdir
    # listing is an arm64 gap — but a python_abi dependency is a real one, and the way to
    # tell them apart is to read the metadata.
    import dolfinx
    deps = package_depends("fenics-dolfinx")
    abi_pins = [d for d in deps if d.split()[0] == "python_abi"]
    assert not abi_pins, f"fenics-dolfinx DOES pin python_abi ({abi_pins}); the tag is real"
    assert any(d.startswith("cpython") for d in deps), \
        f"expected a `cpython >=` floor in fenics-dolfinx depends, got {deps}"
    bs = conda_build_string("fenics-dolfinx")
    print(f"       (dolfinx {dolfinx.__version__} build {bs} on python "
          f"{platform.python_version()}; no python_abi pin, nanobind stable ABI)")


@check("BLAS backend is recorded and is one of the two known variants")
def _blas_backend():
    # This env resolves NVPL rather than OpenBLAS — the only one in the catalog that does
    # (see fem-cfd.yaml). Deliberately NOT pinned, so this check records the choice
    # rather than enforcing one; what it refuses is a third, unrecognised backend
    # appearing unnoticed. The numerical check below is what makes the choice safe.
    nvpl, openblas = installed("libnvpl-blas0"), installed("libopenblas")
    assert nvpl or openblas, "neither NVPL nor OpenBLAS is installed; BLAS provider unknown"
    assert not (nvpl and openblas), "both NVPL and OpenBLAS present — ambiguous BLAS"
    backend = "nvpl" if nvpl else "openblas"
    assert backend in conda_build_string("libblas"), \
        f"libblas build string {conda_build_string('libblas')!r} disagrees with {backend}"
    print(f"       (libblas {conda_build_string('libblas')} -> {backend})")


@check("interpreter reports aarch64 and mpiexec is on PATH")
def _arch():
    m = platform.machine()
    assert m == "aarch64", f"platform.machine() is {m!r}, expected aarch64"
    # No mpiexec means the MPI build is not actually usable, whatever the build strings
    # say. Fail D3 rather than skip the parallel leg.
    assert shutil.which("mpiexec"), "mpiexec is not on PATH; the MPI build is unusable"
    print(f"       ({m}, python {platform.python_version()}, mpiexec present)")


# --- 3. BLAS/LAPACK numerics ------------------------------------------------------
print("[smoke] 3. BLAS/LAPACK numerics (NVPL on aarch64)")


@check("dgemm and a symmetric eigensolve return exact/known answers")
def _blas_numerics():
    import numpy as np
    # A small integer matrix product is exactly representable in float64, so this is an
    # equality test on dgemm, not a tolerance test.
    A = np.array([[1.0, 2.0], [3.0, 4.0]])
    B = np.array([[5.0, 6.0], [7.0, 8.0]])
    assert np.array_equal(A @ B, np.array([[19.0, 22.0], [43.0, 50.0]])), "dgemm is wrong"
    # LAPACK: eigenvalues of [[2,-1],[-1,2]] are exactly 1 and 3.
    w = np.linalg.eigvalsh(np.array([[2.0, -1.0], [-1.0, 2.0]]))
    assert np.allclose(w, [1.0, 3.0], atol=1e-12), f"eigvalsh gave {w}, expected [1, 3]"
    # A larger SPD solve, to exercise a blocked path rather than the 2x2 special case.
    rng = np.random.default_rng(0)
    M = rng.standard_normal((128, 128))
    S = M @ M.T + 128 * np.eye(128)
    b = rng.standard_normal(128)
    x = np.linalg.solve(S, b)
    resid = float(np.max(np.abs(S @ x - b)))
    assert resid < 1e-8, f"128x128 SPD solve residual {resid}"
    print(f"       (dgemm exact, eigvalsh exact, 128x128 solve residual {resid:.2e})")


# --- 4. PETSc / MPI layer ---------------------------------------------------------
print("[smoke] 4. PETSc + MPI layer")


@check("petsc4py builds a Vec and computes an exact norm")
def _petsc_vec():
    from petsc4py import PETSc
    v = PETSc.Vec().createSeq(2)
    v.setValues([0, 1], [3.0, 4.0])
    v.assemble()
    # |(3,4)| = 5 exactly. This is also the honest answer to the petsc4py build string
    # looking like `np2py310*` on a newer interpreter: it does not merely import, it
    # computes. (petsc4py builds against the limited API, hence the misleading tag.)
    n = v.norm()
    assert n == 5.0, f"|(3,4)| = {n!r}, expected exactly 5.0"
    assert v.sum() == 7.0, f"sum = {v.sum()!r}, expected exactly 7.0"
    print(f"       (petsc4py {conda_build_string('petsc4py')}: norm {n})")


@check("PETSc solves a linear system through a KSP")
def _petsc_ksp():
    from petsc4py import PETSc
    # 3x3 tridiagonal (2 on the diagonal, -1 off): the discrete 1-D Laplacian. With
    # rhs = (1,1,1) the exact solution is (1.5, 2, 1.5).
    n = 3
    A = PETSc.Mat().createAIJ([n, n], comm=PETSc.COMM_SELF)
    A.setUp()
    for i in range(n):
        A.setValue(i, i, 2.0)
        if i > 0:
            A.setValue(i, i - 1, -1.0)
        if i < n - 1:
            A.setValue(i, i + 1, -1.0)
    A.assemble()
    b = A.createVecRight()
    b.set(1.0)
    x = A.createVecLeft()
    ksp = PETSc.KSP().create(comm=PETSc.COMM_SELF)
    ksp.setOperators(A)
    ksp.setType("preonly")
    ksp.getPC().setType("lu")
    ksp.solve(b, x)
    assert ksp.getConvergedReason() > 0, f"KSP did not converge: {ksp.getConvergedReason()}"
    got = x.getArray().copy()
    want = [1.5, 2.0, 1.5]
    err = max(abs(g - w) for g, w in zip(got, want))
    assert err < 1e-12, f"KSP solution {got} vs exact {want}, error {err}"
    print(f"       (LU on the 1-D Laplacian, max error {err:.2e})")


@check("SLEPc solves an eigenvalue problem to its known spectrum")
def _slepc():
    import math
    from petsc4py import PETSc
    from slepc4py import SLEPc
    # The n x n tridiagonal Laplacian has eigenvalues 2 - 2*cos(k*pi/(n+1)) in closed
    # form, so SLEPc's answers can be checked against analysis rather than against
    # itself. slepc is otherwise only verified by build string, which proves nothing
    # about whether it runs.
    n = 20
    A = PETSc.Mat().createAIJ([n, n], comm=PETSc.COMM_SELF)
    A.setUp()
    for i in range(n):
        A.setValue(i, i, 2.0)
        if i > 0:
            A.setValue(i, i - 1, -1.0)
        if i < n - 1:
            A.setValue(i, i + 1, -1.0)
    A.assemble()
    eps = SLEPc.EPS().create(comm=PETSc.COMM_SELF)
    eps.setOperators(A)
    eps.setProblemType(SLEPc.EPS.ProblemType.HEP)     # Hermitian
    eps.setWhichEigenpairs(SLEPc.EPS.Which.SMALLEST_REAL)
    eps.setDimensions(nev=3)
    eps.solve()
    assert eps.getConverged() >= 3, f"SLEPc converged only {eps.getConverged()} eigenpairs"
    got = sorted(eps.getEigenvalue(i).real for i in range(3))
    want = sorted(2.0 - 2.0 * math.cos(k * math.pi / (n + 1)) for k in (1, 2, 3))
    err = max(abs(g - w) for g, w in zip(got, want))
    assert err < 1e-9, f"SLEPc eigenvalues {got} vs exact {want}, max error {err}"
    print(f"       (3 smallest eigenvalues of the {n}x{n} Laplacian, max error {err:.2e})")


@check("a C compiler is present, because FFCx JIT-compiles forms at runtime")
def _jit_toolchain():
    # Not a formality. dolfinx does not ship precompiled variational forms — FFCx
    # generates C for each form and compiles it when the script runs, which is why
    # fenics-dolfinx lists `gcc` and `pkg-config` in its RUNTIME depends. An image that
    # dropped the toolchain to save space would import cleanly and then fail on the first
    # solve, so this is the same trap as an `r` image where Rcpp::sourceCpp() fails.
    cc = shutil.which("gcc") or shutil.which("cc")
    assert cc, "no C compiler on PATH; FFCx will fail to JIT-compile any variational form"
    out = subprocess.run([cc, "--version"], capture_output=True, text=True)
    assert out.returncode == 0, f"{cc} --version failed: {out.stderr}"
    print(f"       ({cc}: {out.stdout.splitlines()[0]})")


@check("mpi4py collectives work in the serial (1-rank) case")
def _mpi4py():
    from mpi4py import MPI
    comm = MPI.COMM_WORLD
    total = comm.allreduce(comm.rank + 1, op=MPI.SUM)
    expect = comm.size * (comm.size + 1) // 2
    assert total == expect, f"allreduce gave {total}, expected {expect}"
    print(f"       (mpi4py {MPI.Get_version()}, {comm.size} rank(s), allreduce ok)")


# --- 5. basix: the element tabulation underneath everything -----------------------
print("[smoke] 5. basix (finite element tabulation)")


@check("P1 and P2 basis functions form a partition of unity on the reference triangle")
def _basix():
    import numpy as np
    import basix
    for degree, ndofs in ((1, 3), (2, 6)):
        el = basix.create_element(basix.ElementFamily.P, basix.CellType.triangle, degree)
        pts = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0],
                        [1 / 3, 1 / 3], [0.25, 0.5]])
        tab = el.tabulate(0, pts)[0]         # values only, no derivatives
        assert tab.shape[-2] == ndofs, f"P{degree} has {tab.shape[-2]} dofs, expected {ndofs}"
        # Lagrange bases sum to exactly 1 at every point — this is what "partition of
        # unity" means, and it is the single strongest one-line check on a tabulator.
        sums = tab.sum(axis=1).ravel()
        worst = float(np.max(np.abs(sums - 1.0)))
        assert worst < 1e-13, f"P{degree} basis sums deviate from 1 by {worst}"
    print("       (P1/P2 on a triangle: dof counts correct, basis sums to 1)")


# --- 6. the actual FEM solve ------------------------------------------------------
print("[smoke] 6. dolfinx: Poisson on the unit square")

SERIAL_P2_ERROR = {}


@check("P2 reproduces a quadratic solution to machine precision")
def _p2_exact():
    from mpi4py import MPI
    err = poisson_l2_error(16, 2, MPI.COMM_SELF)
    SERIAL_P2_ERROR["value"] = err
    # u_exact = 1 + x^2 + 2y^2 lies IN the P2 space, so the discrete solution is the
    # exact one up to round-off and the direct solve's conditioning. Anything above
    # 1e-10 on a 16x16 mesh means assembly, the boundary conditions, or the solver is
    # wrong — a tolerance loose enough to hide that would make this check pointless.
    assert err < 1e-10, f"P2 L2 error {err:.3e}, expected ~1e-15 (solution is in the space)"
    print(f"       (16x16 P2: L2 error {err:.3e})")


@check("P1 converges at the O(h^2) rate the method promises")
def _p1_rate():
    from mpi4py import MPI
    coarse = poisson_l2_error(8, 1, MPI.COMM_SELF)
    fine = poisson_l2_error(16, 1, MPI.COMM_SELF)
    # P1 cannot represent a quadratic, so here the error is genuinely nonzero and must
    # shrink by ~4 when h is halved. This is the independent statement: P2 being exact
    # could in principle come from a degenerate solve, but a correct convergence RATE
    # cannot.
    assert coarse > 1e-6, f"P1 coarse error {coarse:.3e} is suspiciously small"
    ratio = coarse / fine
    assert 3.5 < ratio < 4.5, f"P1 error ratio {ratio:.3f}, expected ~4 for O(h^2)"
    print(f"       (P1 8x8 {coarse:.3e} -> 16x16 {fine:.3e}, ratio {ratio:.3f} ~ 4)")


# --- 7. parallel: the same solve under mpiexec -n 2 -------------------------------
print("[smoke] 7. parallel (mpiexec -n 2, same script)")


@check("2-rank run partitions the mesh and agrees with the serial answer")
def _parallel():
    env = dict(os.environ)
    env["AARCHSCI_MPI_CHILD"] = "1"
    # Containers commonly expose fewer cores than ranks and run as a non-root or root
    # user OpenMPI refuses by default; these four settings are what dft.smoke.py
    # established as necessary. OMP_NUM_THREADS=1 stops the two ranks oversubscribing.
    env["OMPI_ALLOW_RUN_AS_ROOT"] = "1"
    env["OMPI_ALLOW_RUN_AS_ROOT_CONFIRM"] = "1"
    env["OMPI_MCA_rmaps_base_oversubscribe"] = "yes"
    env["OMP_NUM_THREADS"] = "1"
    proc = subprocess.run(
        [shutil.which("mpiexec"), "-n", "2", sys.executable, os.path.abspath(__file__)],
        env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"2-rank run exited {proc.returncode}\n--- stdout ---\n{proc.stdout}"
            f"\n--- stderr ---\n{proc.stderr}"
        )
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    par = float(tail[-1])
    ser = SERIAL_P2_ERROR.get("value")
    assert ser is not None, "serial P2 check did not run, so there is nothing to compare"
    # Domain decomposition must not change the answer. Both errors are ~1e-15, so compare
    # in absolute terms; a real partitioning or ghost-exchange bug shows up orders of
    # magnitude above this.
    assert par < 1e-10, f"parallel P2 L2 error {par:.3e}, expected ~1e-15"
    assert abs(par - ser) < 1e-10, f"parallel {par:.3e} disagrees with serial {ser:.3e}"
    print(f"       (2 ranks: L2 error {par:.3e} vs serial {ser:.3e})")


# --- 8. I/O -----------------------------------------------------------------------
print("[smoke] 8. mesh I/O (the HDF5/XDMF path dolfinx links)")


@check("a mesh and a solution round-trip through XDMF/HDF5")
def _io():
    import tempfile
    from pathlib import Path
    from mpi4py import MPI
    from dolfinx import fem, io, mesh
    d = Path(tempfile.mkdtemp())
    msh = mesh.create_unit_square(MPI.COMM_SELF, 4, 4, mesh.CellType.triangle)
    V = fem.functionspace(msh, ("Lagrange", 1))
    u = fem.Function(V)
    u.interpolate(lambda x: x[0] + 2.0 * x[1])
    path = d / "out.xdmf"
    with io.XDMFFile(msh.comm, str(path), "w") as f:
        f.write_mesh(msh)
        f.write_function(u)
    h5 = path.with_suffix(".h5")
    # Proving the parallel HDF5 build is actually wired up: without it this raises
    # rather than producing a file.
    assert h5.exists() and h5.stat().st_size > 0, "no HDF5 payload was written"
    with io.XDMFFile(MPI.COMM_SELF, str(path), "r") as f:
        msh2 = f.read_mesh()
    n1 = msh.topology.index_map(msh.topology.dim).size_global
    n2 = msh2.topology.index_map(msh2.topology.dim).size_global
    assert n1 == n2 == 32, f"cell count changed across I/O: {n1} -> {n2} (expected 32)"
    print(f"       ({h5.stat().st_size} bytes HDF5, {n2} cells read back)")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: fem-cfd env assembles, imports, and solves a PDE on "
      + platform.machine() + " (python " + platform.python_version() + ") — serial and "
      "2-rank verified.")
