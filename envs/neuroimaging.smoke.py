#!/usr/bin/env python3
# neuroimaging.smoke.py — the D3 verification for the `neuroimaging` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image). The
# risky natives here are AFNI's C binaries and DIPY's compiled reconstruction kernels,
# both of which can import or launch and still compute wrong.
#
# NOTHING IS STAGED AND NOTHING IS DOWNLOADED, which matters because this is a field whose
# test data normally lives in remote archives (OpenNeuro, TemplateFlow). Everything below
# is either generated or derivable:
#
#   1. nibabel NIfTI round-trip — the affine and the voxel data must survive write/read
#      BIT-EXACTLY. Not a tolerance: an affine is the image's spatial contract, and a
#      header that silently rescales or transposes is the classic neuroimaging data-loss
#      bug. Asserted with array_equal, not allclose.
#   2. DIPY diffusion tensor fit on a PLANTED tensor. Fractional anisotropy has a closed
#      form in the eigenvalues, FA = sqrt(1/2)*||l - mean|| / ||l||, so a noiseless
#      synthetic signal from known eigenvalues gives an exactly-derivable target. Measured
#      agreement: 5.6e-16 — machine precision against analysis, not against a number we
#      once recorded. A broken kernel cannot land there by luck.
#   3. CROSS-TOOLKIT geometry agreement — AFNI's `3dinfo`, an unrelated C codebase, must
#      read the file nibabel wrote and report the same dimensions and voxel sizes. This
#      is the interoperability claim the env actually makes, and neither library can fake
#      the other's agreement.
#
# Synthetic diffusion signal and a generated gradient scheme are legitimate for the same
# reason a generated mesh is (cfd-fv, fem-cfd): they are acquisition geometry and forward
# -model inputs, not fitted physical data of the kind that keeps `siesta` and `dftbplus`
# from having a real solve.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import platform
import shutil
import subprocess
import sys
import tempfile
import traceback
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

FAILURES = []

# A deliberately non-trivial affine: negative x (radiological LAS-ish), anisotropic
# voxels (2.0, 2.0, 2.5) and a nonzero origin. Symmetric or isotropic affines hide
# transposition and sign bugs, which is exactly what this check exists to catch.
AFFINE = [[-2.0, 0.0, 0.0, 90.0],
          [0.0, 2.0, 0.0, -126.0],
          [0.0, 0.0, 2.5, -72.0],
          [0.0, 0.0, 0.0, 1.0]]
SHAPE = (4, 5, 6)
VOXEL_SIZES = (2.0, 2.0, 2.5)

# Planted diffusion eigenvalues (mm^2/s), a plausible anisotropic white-matter voxel.
DTI_EVALS = (1.7e-3, 0.3e-3, 0.2e-3)

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
    "numpy", "scipy", "pandas", "sklearn", "h5py",
    "nibabel",
    "dipy", "dipy.reconst.dti", "dipy.sims.voxel", "dipy.core.gradients",
    "nilearn", "nilearn.image",
    "nipype",
]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. NIfTI I/O: the affine is a contract, so assert it exactly -----------------
print("[smoke] 2. NIfTI I/O (bit-exact round-trip)")


@check("nibabel round-trips voxel data and the affine bit-exactly")
def _nifti():
    import numpy as np
    import nibabel as nib
    aff = np.array(AFFINE)
    data = np.arange(int(np.prod(SHAPE)), dtype=np.float32).reshape(SHAPE)
    d = Path(tempfile.mkdtemp())
    p = d / "probe.nii.gz"
    nib.save(nib.Nifti1Image(data, aff), str(p))
    img = nib.load(str(p))
    # array_equal, not allclose: a spatial transform that is "nearly" right is a bug.
    assert np.array_equal(img.affine, aff), \
        f"affine did not survive the round-trip:\n{img.affine}\nvs\n{aff}"
    assert np.array_equal(np.asarray(img.dataobj), data), "voxel data changed on round-trip"
    assert img.shape == SHAPE, f"shape {img.shape}, expected {SHAPE}"
    # Voxel sizes derive from the affine, so this cross-checks the header's own view.
    zooms = tuple(round(float(z), 6) for z in img.header.get_zooms()[:3])
    assert zooms == VOXEL_SIZES, f"zooms {zooms}, expected {VOXEL_SIZES}"
    STATE["nifti"] = str(p)
    print(f"       ({nib.__version__}, shape={img.shape}, zooms={zooms}, affine exact)")


# --- 3. diffusion: FA against its closed form -------------------------------------
print("[smoke] 3. diffusion MRI (DTI vs closed-form FA)")


@check("dipy recovers the planted tensor's FA and MD to machine precision")
def _dti():
    import numpy as np
    import dipy.reconst.dti as dti
    from dipy.core.gradients import gradient_table
    from dipy.sims.voxel import single_tensor
    # A generated single-shell scheme: one b=0 plus 13 non-collinear directions. Six
    # independent directions is the minimum for a tensor; this is comfortably over.
    s2 = float(np.sqrt(0.5))
    s3 = float(np.sqrt(1.0 / 3.0))
    bvecs = np.array([
        [0, 0, 0],
        [1, 0, 0], [0, 1, 0], [0, 0, 1],
        [s2, s2, 0], [s2, 0, s2], [0, s2, s2],
        [-s2, s2, 0], [-s2, 0, s2], [0, -s2, s2],
        [s3, s3, s3], [-s3, s3, s3], [s3, -s3, s3], [s3, s3, -s3],
    ])
    bvals = np.array([0] + [1000] * 13)
    gtab = gradient_table(bvals, bvecs=bvecs)

    evals = np.array(DTI_EVALS)
    # Noiseless forward model (snr=None) so the inverse problem has an exact answer.
    sig = single_tensor(gtab, S0=100.0, evals=evals, evecs=np.eye(3), snr=None)
    fit = dti.TensorModel(gtab).fit(sig[None, None, None, :])

    l1, l2, l3 = evals
    fa_exact = float(np.sqrt(0.5) * np.sqrt((l1 - l2) ** 2 + (l2 - l3) ** 2 + (l3 - l1) ** 2)
                     / np.sqrt(l1 ** 2 + l2 ** 2 + l3 ** 2))
    md_exact = float(evals.mean())
    fa = float(fit.fa[0, 0, 0])
    md = float(fit.md[0, 0, 0])
    # Closed form, noiseless signal: this is an identity, so the tolerance is round-off.
    assert abs(fa - fa_exact) < 1e-9, \
        f"FA {fa!r} vs closed form {fa_exact!r} (delta {abs(fa - fa_exact):.3e})"
    assert abs(md - md_exact) / md_exact < 1e-9, \
        f"MD {md!r} vs closed form {md_exact!r}"
    # The principal eigenvector must align with the planted one (x), up to sign.
    pev = np.asarray(fit.evecs[0, 0, 0])[:, 0]
    assert abs(abs(float(pev[0])) - 1.0) < 1e-6, \
        f"principal eigenvector {pev} is not along x as planted"
    print(f"       (FA {fa:.10f} vs exact {fa_exact:.10f}, delta {abs(fa - fa_exact):.1e}; "
          f"MD {md:.3e})")


# --- 4. AFNI: an independent C toolkit reading nibabel's output -------------------
print("[smoke] 4. AFNI binaries (cross-toolkit geometry agreement)")


@check("AFNI 3dinfo reads the nibabel-written NIfTI and agrees on its geometry")
def _afni():
    exe = shutil.which("3dinfo")
    assert exe, "3dinfo not on PATH — the AFNI binaries are not usable"
    path = STATE.get("nifti")
    assert path, "the NIfTI check did not run, so there is nothing for AFNI to read"
    # -n4 prints ni nj nk nv; -ad3 prints the three voxel sizes. Asking AFNI for the same
    # facts nibabel reported is the interoperability assertion: two unrelated
    # implementations of the NIfTI spec must describe the same file identically.
    r = subprocess.run([exe, "-n4", "-ad3", path], capture_output=True, text=True,
                       timeout=600)
    out = (r.stdout + r.stderr).strip()
    nums = [t for t in out.replace("\n", "\t").split("\t") if t.strip()]
    assert len(nums) >= 7, f"could not parse 3dinfo output:\n{out[:400]}"
    dims = tuple(int(float(v)) for v in nums[:3])
    nv = int(float(nums[3]))
    zooms = tuple(round(float(v), 6) for v in nums[4:7])
    assert dims == SHAPE, f"AFNI reports dims {dims}, nibabel wrote {SHAPE}"
    assert nv == 1, f"AFNI reports {nv} volumes, expected 1"
    assert zooms == VOXEL_SIZES, f"AFNI reports zooms {zooms}, expected {VOXEL_SIZES}"
    print(f"       (3dinfo: dims={dims}, nv={nv}, zooms={zooms} — matches nibabel)")


# --- 5. nilearn: the statistical/image layer --------------------------------------
print("[smoke] 5. nilearn image operations")


@check("nilearn smooths an image while preserving geometry and finiteness")
def _nilearn():
    import numpy as np
    import nibabel as nib
    from nilearn.image import smooth_img
    path = STATE.get("nifti")
    assert path, "the NIfTI check did not run"
    src = nib.load(path)
    sm = smooth_img(src, fwhm=4)
    arr = np.asarray(sm.dataobj)
    assert sm.shape == SHAPE, f"smoothing changed the shape to {sm.shape}"
    # Smoothing must not move the image in space.
    assert np.array_equal(sm.affine, src.affine), "smoothing altered the affine"
    assert np.isfinite(arr).all(), "smoothed image contains non-finite values"
    # A Gaussian kernel is mass-preserving in the interior and must not amplify: the
    # smoothed range cannot exceed the original's.
    s0 = np.asarray(src.dataobj)
    assert arr.min() >= s0.min() - 1e-6 and arr.max() <= s0.max() + 1e-6, \
        f"smoothed range [{arr.min()}, {arr.max()}] exceeds source [{s0.min()}, {s0.max()}]"
    print(f"       (fwhm=4 smoothed, shape={sm.shape}, affine preserved, "
          f"range [{arr.min():.2f}, {arr.max():.2f}])")


@check("nilearn masking round-trips a 4D series through a boolean mask")
def _nilearn_mask():
    import numpy as np
    import nibabel as nib
    from nilearn.maskers import NiftiMasker
    rng = np.random.RandomState(0)
    series = rng.rand(*SHAPE, 8).astype(np.float32)   # 8 "timepoints"
    aff = np.array(AFFINE)
    d = Path(tempfile.mkdtemp())
    f4 = d / "series.nii.gz"
    nib.save(nib.Nifti1Image(series, aff), str(f4))
    mask = np.zeros(SHAPE, dtype=np.uint8)
    mask[1:3, 1:4, 1:5] = 1
    fm = d / "mask.nii.gz"
    nib.save(nib.Nifti1Image(mask, aff), str(fm))
    m = NiftiMasker(mask_img=str(fm), standardize=False)
    X = m.fit_transform(str(f4))
    n_vox = int(mask.sum())
    # Shape is (timepoints, voxels-in-mask) — an exact, countable property.
    assert X.shape == (8, n_vox), f"masked matrix {X.shape}, expected (8, {n_vox})"
    # And the values must be the masked voxels, not a rescaling of them.
    expect = series[mask.astype(bool)].reshape(n_vox, 8).T
    assert np.allclose(np.sort(X, axis=None), np.sort(expect, axis=None), atol=1e-6), \
        "masked values do not match the source voxels"
    print(f"       (masker: {X.shape[0]} timepoints x {X.shape[1]} in-mask voxels)")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: neuroimaging env assembles, imports, and does real MRI work on "
      + platform.machine() + " (python " + platform.python_version() + ") — verified.")
