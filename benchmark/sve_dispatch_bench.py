#!/usr/bin/env python
"""Measure ARM SIMD/microarch runtime dispatch inside an aarch.science image.

Answers two questions the catalog previously only asserted:

  1. WHICH kernels does the shipped image actually select on a given Graviton
     generation? (OpenBLAS is built DYNAMIC_ARCH; NumPy compiles an SVE dispatch
     path. Neither can be observed from the build — only at runtime, on the
     silicon.)
  2. WHAT is that dispatch worth? Same image, same host, same workload — only
     OPENBLAS_CORETYPE differs, which pins the kernel family. `auto` is what a
     user gets; `neoversen1` is the same code with SVE unavailable; `armv8` is
     the generic armv8-a baseline.

OPENBLAS_CORETYPE is read once when libopenblas loads, so each variant needs its
own process. The script re-invokes itself (AARCHSCI_CORETYPE set) the same way
envs/dft.smoke.py re-invokes itself under mpiexec — one entrypoint, no wrapper.

Emits one JSON object per line on stdout, prefixed `RESULT ` so an SSM Run
Command transcript can be sieved without parsing the whole log.
"""
import ctypes
import glob
import json
import os
import re
import subprocess
import sys
import time

CORETYPES = ["auto", "neoversen1", "armv8"]
# Sizes are env-overridable so the same script can scale from a 2-vCPU instance to a
# 16-vCPU one. A benchmark whose problem fits in 2 cores tells you very little about 16,
# and the reverse wastes an hour. This earned its keep: the "SVE2 does nothing on
# Graviton4" null was first seen at 2 vCPU, and only re-running it at 16 vCPU with 4x the
# problem established that it was a property of the kernel and not of the problem size.
GEMM_N = int(os.environ.get("AARCHSCI_GEMM_N", 3072))
GEMM_REPS = int(os.environ.get("AARCHSCI_GEMM_REPS", 5))
# NxNxN repeat of the 2-atom diamond cell. Sized so the host spends tens of seconds in
# the SCF loop rather than in setup — the 2-atom cell that D3 uses finishes in 0.25s,
# which measures process startup, not the science.
SI_REPEAT = int(os.environ.get("AARCHSCI_SI_REPEAT", 2))
SI_CUTOFF = int(os.environ.get("AARCHSCI_SI_CUTOFF", 400))
SI_KPTS = int(os.environ.get("AARCHSCI_SI_KPTS", 2))


# ---------------------------------------------------------------- host identity

def cpuinfo_facts():
    """HWCAP-derived feature list + core IDs, straight from the kernel."""
    txt = ""
    try:
        with open("/proc/cpuinfo") as fh:
            txt = fh.read()
    except OSError:
        return {}
    def first(pat):
        m = re.search(pat, txt, re.I | re.M)
        return m.group(1).strip() if m else None
    feats = (first(r"^Features\s*:\s*(.*)$") or "").split()
    return {
        "features": feats,
        "has_asimd": "asimd" in feats,   # NEON — architecturally mandatory on aarch64
        "has_sve": "sve" in feats,
        "has_sve2": "sve2" in feats,
        "has_i8mm": "i8mm" in feats,
        "has_bf16": "bf16" in feats,
        "implementer": first(r"^CPU implementer\s*:\s*(\S+)$"),
        "part": first(r"^CPU part\s*:\s*(\S+)$"),
        "variant": first(r"^CPU variant\s*:\s*(\S+)$"),
        "ncpu": len(re.findall(r"^processor\s*:", txt, re.M)),
    }


def sve_vector_length_bits():
    """prctl(PR_SVE_GET_VL) — Graviton3 (V1) is 2x256-bit, Graviton4 (V2) 4x128-bit.

    Returns None when SVE is absent (prctl returns EINVAL), which is itself a
    result: it distinguishes "no SVE" from "SVE at some width".
    """
    PR_SVE_GET_VL = 51
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        rc = libc.prctl(PR_SVE_GET_VL, 0, 0, 0, 0)
    except Exception:
        return None
    if rc < 0:
        return None
    return (rc & 0xFFFF) * 8   # low 16 bits = vector length in bytes


def numpy_simd():
    import numpy as np
    try:
        m = __import__("numpy._core._multiarray_umath", fromlist=["x"])
    except ImportError:
        m = __import__("numpy.core._multiarray_umath", fromlist=["x"])
    feats = m.__cpu_features__
    return {
        "numpy": np.__version__,
        "baseline": list(m.__cpu_baseline__),
        "dispatch": list(m.__cpu_dispatch__),
        # only the aarch64-relevant flags; the x86 half is all False on arm64
        "detected": {k: v for k, v in sorted(feats.items())
                     if k.startswith(("NEON", "ASIMD", "SVE", "FPHP"))},
    }


def openblas_handle():
    libs = sorted(glob.glob("/opt/conda/lib/libopenblas*.so*"))
    if not libs:
        libs = sorted(glob.glob("/opt/conda/lib/libopenblasp*"))
    return ctypes.CDLL(libs[0]) if libs else None


def openblas_facts():
    lib = openblas_handle()
    if lib is None:
        return {"error": "libopenblas not found"}
    out = {}
    for fn, key in (("openblas_get_config", "config"),
                    ("openblas_get_corename", "corename")):
        try:
            f = getattr(lib, fn)
            f.restype = ctypes.c_char_p
            out[key] = f().decode()
        except Exception as exc:
            out[key] = f"unavailable: {type(exc).__name__}"
    try:
        lib.openblas_get_num_threads.restype = ctypes.c_int
        out["threads"] = lib.openblas_get_num_threads()
    except Exception:
        pass
    return out


# -------------------------------------------------------------------- workloads

def bench_gemm(dtype, n=GEMM_N, reps=GEMM_REPS):
    """Best-of-N GEMM. BLAS3 is where a microarch kernel swap shows up hardest."""
    import numpy as np
    rng = np.random.default_rng(0)
    a = rng.random((n, n), dtype=dtype)
    b = rng.random((n, n), dtype=dtype)
    a @ b                                    # warm pages + thread pool
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        a @ b
        best = min(best, time.perf_counter() - t0)
    return {"n": n, "best_s": round(best, 5),
            "gflops": round(2.0 * n ** 3 / best / 1e9, 2)}


def bench_gpaw_si():
    """A scaled-up cousin of the bulk-Si PW/LDA calculation envs/dft.smoke.py verifies.

    Reported alongside GEMM so the result is not purely a microbenchmark: it says
    what dispatch is worth to a real DFT step, where BLAS is only part of the time.

    `energy_per_atom_eV` doubles as a correctness cross-check — a kernel swap must
    change the speed and nothing else. If the SVE path disagrees with the armv8
    path, the fast kernel is wrong and the D3 doctrine says the speed is worthless.
    """
    try:
        from ase.build import bulk
        from gpaw import GPAW, PW
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    t0 = time.perf_counter()
    try:
        # Parenthesized: `atoms * (n,) * 3` binds left-to-right and multiplies the
        # Atoms by a 1-tuple first, which raises IndexError inside ASE.
        si = bulk("Si", "diamond", a=5.43) * ((SI_REPEAT,) * 3)
        si.calc = GPAW(mode=PW(SI_CUTOFF), xc="LDA",
                       kpts=(SI_KPTS,) * 3, txt="/tmp/gpaw-bench.txt")
        energy = si.get_potential_energy()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"atoms": len(si), "cutoff_eV": SI_CUTOFF, "kpts": SI_KPTS,
            "wall_s": round(time.perf_counter() - t0, 3),
            "energy_per_atom_eV": round(float(energy) / len(si), 6)}


# ----------------------------------------------------------------- orchestration

def run_one(coretype):
    """One measurement process. OPENBLAS_CORETYPE is already applied by the parent."""
    ob = openblas_facts()
    rec = {
        "kind": "variant",
        "coretype_requested": coretype,
        "coretype_selected": ob.get("corename"),
        "openblas": ob,
        "numpy_simd": numpy_simd(),
        "dgemm_f64": bench_gemm("float64"),
        "sgemm_f32": bench_gemm("float32"),
        "gpaw_si": bench_gpaw_si(),
    }
    print("RESULT " + json.dumps(rec), flush=True)


def main():
    child = os.environ.get("AARCHSCI_CORETYPE")
    if child:
        run_one(child)
        return 0

    # Parent: emit host identity once, then fan out one child per kernel family.
    host = {
        "kind": "host",
        "instance_type": os.environ.get("AARCHSCI_INSTANCE_TYPE", "unknown"),
        "uname": list(os.uname()),
        "cpuinfo": cpuinfo_facts(),
        "sve_vector_bits": sve_vector_length_bits(),
        "image": os.environ.get("AARCHSCI_IMAGE", "unknown"),
        "sizes": {"gemm_n": GEMM_N, "gemm_reps": GEMM_REPS, "si_repeat": SI_REPEAT,
                  "si_cutoff": SI_CUTOFF, "si_kpts": SI_KPTS},
    }
    print("RESULT " + json.dumps(host), flush=True)
    print(f"[bench] host: {host['cpuinfo'].get('implementer')}/"
          f"{host['cpuinfo'].get('part')} sve={host['cpuinfo'].get('has_sve')} "
          f"sve2={host['cpuinfo'].get('has_sve2')} vl={host['sve_vector_bits']}b",
          file=sys.stderr, flush=True)

    rc = 0
    for ct in CORETYPES:
        env = dict(os.environ, AARCHSCI_CORETYPE=ct)
        if ct == "auto":
            env.pop("OPENBLAS_CORETYPE", None)
        else:
            env["OPENBLAS_CORETYPE"] = ct
        print(f"[bench] --- coretype={ct} ---", file=sys.stderr, flush=True)
        proc = subprocess.run([sys.executable, os.path.abspath(__file__)], env=env)
        if proc.returncode != 0:
            # A pinned coretype the library rejects is a finding, not a crash.
            print("RESULT " + json.dumps({
                "kind": "variant", "coretype_requested": ct,
                "error": f"child exited {proc.returncode}"}), flush=True)
            if ct == "auto":
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
