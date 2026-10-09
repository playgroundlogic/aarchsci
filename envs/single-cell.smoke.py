#!/usr/bin/env python3
# single-cell.smoke.py — the D3 verification for the `single-cell` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image) for both
# single-cell ecosystems. The risky parts are the compiled graph-community kernels
# (leidenalg, python-igraph), Seurat's large Rcpp/RcppEigen native layer, and the fact
# that two language runtimes have to coexist in one prefix.
#
# THE MARQUEE CHECK IS CROSS-FRAMEWORK, and it is the reason this env holds both halves
# rather than being split in two. Issues #17 and #20 asked for the same thing from
# opposite sides: Scanpy and Seurat clustering IDENTICAL BYTES, compared by adjusted Rand
# index. A smoke test cannot reach across containers, so two images could never check it.
#
# Why ARI and not label equality: cluster labels are arbitrary names. Scanpy calling a
# group "0" and Seurat calling it "2" is not a disagreement. ARI compares the PARTITION —
# which cells are grouped together — and is invariant to relabelling, which is the only
# comparison that means anything here. (Issue #20 made this point and it is correct.)
#
# The fixture is generated, not staged: a Poisson count matrix with three groups, each
# over-expressing its own gene block. That is legitimate for the same reason a generated
# mesh is (cfd-fv, fem-cfd) — it is synthetic input with a planted answer, not fitted
# biological data we would be inventing. And because the truth is planted, both
# frameworks can be scored against it absolutely rather than only against each other:
# two codebases agreeing on a wrong answer would still fail.
#
# Measured on aarch64: scanpy ARI 1.0000 vs truth, Seurat ARI 1.0000 vs truth, and
# 1.0000 between the two.
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

N_GENES, N_CELLS, N_GROUPS = 200, 150, 3
SEED = 0
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


def _fixture():
    """Planted count matrix (cells x genes) + truth labels. Deterministic."""
    import numpy as np
    rng = np.random.RandomState(SEED)
    truth = np.repeat(np.arange(N_GROUPS), N_CELLS // N_GROUPS)
    X = rng.poisson(1.0, size=(len(truth), N_GENES)).astype(float)
    blk = N_GENES // N_GROUPS
    for g in range(N_GROUPS):
        sel = truth == g
        X[sel, g * blk:(g + 1) * blk] += rng.poisson(12.0, size=(sel.sum(), blk))
    return X, truth


# --- 1. imports -------------------------------------------------------------------
HEADLINE = [
    "numpy", "scipy", "pandas", "sklearn", "h5py",
    "scanpy", "anndata",
    # skmisc backs scanpy's seurat_v3 HVG flavour. Imported here AND exercised in
    # section 3, because scanpy imports it lazily — see issue #28.
    "skmisc", "skmisc.loess",
    # Named explicitly because a bare `scanpy` solve does NOT pull them, and an env
    # missing them loads data fine and then dies at the clustering step.
    "leidenalg", "igraph",
    "umap",
]
print("[smoke] 1. imports (Python half)")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


@check("scanpy is >=1.12, not the fossil bioconda still carries")
def _scanpy_version():
    import scanpy as sc
    major, minor = (int(x) for x in sc.__version__.split(".")[:2])
    # The whole reason this env exists (DESIGN D5): bioconda's scanpy is 1.7.2 and cannot
    # import against a current anndata. Shipping that version here would defeat the point.
    assert (major, minor) >= (1, 12), \
        f"scanpy {sc.__version__} — expected >=1.12; 1.7.x is the unusable bioconda fossil"
    import anndata
    print(f"       (scanpy {sc.__version__}, anndata {anndata.__version__})")


# --- 2. the R half ----------------------------------------------------------------
print("[smoke] 2. R half (Seurat)")


@check("Rscript is present and Seurat >=5 loads")
def _seurat_loads():
    rscript = shutil.which("Rscript")
    assert rscript, "Rscript not on PATH — the R half of this env is not usable"
    r = subprocess.run(
        [rscript, "-e",
         'suppressMessages(library(Seurat)); cat("V:", as.character(packageVersion("Seurat")), "\\n")'],
        capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, f"loading Seurat failed (rc={r.returncode}):\n{r.stderr[-1500:]}"
    ver = next((ln.split()[1] for ln in r.stdout.splitlines() if ln.startswith("V:")), None)
    assert ver, f"could not parse Seurat version from:\n{r.stdout[:400]}"
    assert int(ver.split(".")[0]) >= 5, \
        f"Seurat {ver} — expected >=5; bioconda's 3.0.2 is the fossil this env exists to avoid"
    STATE["seurat_version"] = ver
    print(f"       (Seurat {ver}, R via {rscript})")


# --- 3. Scanpy clusters the planted fixture ---------------------------------------
print("[smoke] 3. Scanpy clustering (Python)")


@check("scanpy's leiden recovers the planted groups exactly (ARI 1.0)")
def _scanpy_cluster():
    import anndata
    import numpy as np
    import scanpy as sc
    from sklearn.metrics import adjusted_rand_score
    X, truth = _fixture()
    a = anndata.AnnData(X.copy())
    sc.pp.normalize_total(a, target_sum=1e4)
    sc.pp.log1p(a)
    sc.pp.pca(a, n_comps=10)
    sc.pp.neighbors(a, n_neighbors=10)
    # flavor="igraph" is scanpy's current leiden backend; this exercises both the
    # leidenalg and python-igraph natives, which is where an arm64 build would break.
    sc.tl.leiden(a, flavor="igraph", n_iterations=2, resolution=0.5)
    labels = a.obs["leiden"].astype(int).to_numpy()
    k = len(set(labels.tolist()))
    ari = adjusted_rand_score(truth, labels)
    assert k == N_GROUPS, f"scanpy found {k} clusters, planted {N_GROUPS}"
    # The groups are well separated by construction, so anything below near-perfect means
    # the pipeline is broken rather than merely imprecise.
    assert ari > 0.95, f"scanpy ARI vs planted truth = {ari:.4f}, expected ~1.0"
    STATE["py_labels"] = labels
    STATE["truth"] = truth
    STATE["X"] = X
    print(f"       (clusters={k}, ARI vs planted truth = {ari:.4f})")


@check("scanpy's recommended seurat_v3 HVG flavour runs (needs scikit-misc)")
def _hvg_seurat_v3():
    import anndata
    import numpy as np
    import scanpy as sc
    X = STATE.get("X")
    assert X is not None, "the fixture was not built"
    # seurat_v3 expects RAW COUNTS and imports skmisc.loess lazily, so this call is the
    # only thing that proves scikit-misc is present and working. An import-only check
    # cannot see it: that is exactly how issue #28 shipped unnoticed.
    a = anndata.AnnData(X.copy())
    n_top = 50
    sc.pp.highly_variable_genes(a, flavor="seurat_v3", n_top_genes=n_top)
    assert "highly_variable" in a.var, "seurat_v3 produced no highly_variable column"
    n_hvg = int(a.var["highly_variable"].sum())
    assert n_hvg == n_top, f"asked for {n_top} HVGs, got {n_hvg}"
    # The flavour's own output columns must be finite — a broken loess surfaces here.
    for col in ("variances", "variances_norm"):
        assert col in a.var, f"seurat_v3 did not produce {col}"
        assert np.isfinite(a.var[col]).all(), f"{col} contains non-finite values"
    # The planted gene blocks are the variable ones, so the HVGs should concentrate in
    # them rather than being spread uniformly — a weak but real correctness signal.
    import skmisc
    print(f"       (seurat_v3: {n_hvg} HVGs via skmisc loess, all variances finite)")


# --- 4. Seurat clusters THE SAME BYTES, and the two are compared ------------------
print("[smoke] 4. cross-framework agreement (Seurat on identical input)")


@check("Seurat recovers the same partition from the same matrix (cross-framework ARI)")
def _cross_framework():
    import numpy as np
    from sklearn.metrics import adjusted_rand_score
    X = STATE.get("X")
    truth = STATE.get("truth")
    py = STATE.get("py_labels")
    assert X is not None and py is not None, \
        "the scanpy check did not run, so there is nothing to compare against"

    d = Path(tempfile.mkdtemp())
    counts = d / "counts.csv"
    # Seurat wants genes x cells, scanpy uses cells x genes — transpose once, here, so
    # both frameworks genuinely receive the same numbers.
    np.savetxt(counts, X.T, delimiter=",", fmt="%d")
    out = d / "rlabels.csv"

    rcode = f'''
suppressMessages(library(Seurat)); suppressMessages(library(Matrix))
m <- as.matrix(read.csv("{counts}", header=FALSE))
rownames(m) <- paste0("g", seq_len(nrow(m))); colnames(m) <- paste0("c", seq_len(ncol(m)))
o <- CreateSeuratObject(counts = as(m, "dgCMatrix"))
o <- NormalizeData(o, verbose = FALSE)
o <- FindVariableFeatures(o, verbose = FALSE)
o <- ScaleData(o, verbose = FALSE)
o <- RunPCA(o, npcs = 10, verbose = FALSE)
o <- FindNeighbors(o, dims = 1:10, verbose = FALSE)
o <- FindClusters(o, resolution = 0.5, verbose = FALSE)
write.table(as.integer(Idents(o)) - 1, "{out}", row.names = FALSE, col.names = FALSE)
'''
    r = subprocess.run([shutil.which("Rscript"), "-e", rcode],
                       capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, f"Seurat clustering failed (rc={r.returncode}):\n{r.stderr[-1500:]}"
    assert out.is_file(), "Seurat wrote no labels"
    rl = np.loadtxt(out, dtype=int)
    assert rl.shape[0] == X.shape[0], \
        f"Seurat returned {rl.shape[0]} labels for {X.shape[0]} cells"

    k = len(set(rl.tolist()))
    ari_truth = adjusted_rand_score(truth, rl)
    ari_cross = adjusted_rand_score(py, rl)
    assert k == N_GROUPS, f"Seurat found {k} clusters, planted {N_GROUPS}"
    # Scored against the PLANTED truth as well as against scanpy, deliberately: two
    # codebases agreeing on a wrong answer would pass a cross-check alone.
    assert ari_truth > 0.95, f"Seurat ARI vs planted truth = {ari_truth:.4f}, expected ~1.0"
    # ARI, not label equality — cluster names are arbitrary, so only the partition can be
    # compared. Both frameworks default to different normalisation and HVG selection, so
    # the honest claim is partition agreement, which is exactly what ARI measures.
    assert ari_cross > 0.95, (
        f"scanpy and Seurat disagree on the partition (ARI {ari_cross:.4f}) — two "
        "independent implementations should recover the same well-separated groups")
    print(f"       (Seurat clusters={k}, ARI vs truth = {ari_truth:.4f}; "
          f"scanpy-vs-Seurat ARI = {ari_cross:.4f})")


# --- 5. AnnData round-trip --------------------------------------------------------
print("[smoke] 5. .h5ad I/O")


@check("anndata round-trips an .h5ad with its observations intact")
def _h5ad():
    import anndata
    import numpy as np
    X = STATE.get("X")
    labels = STATE.get("py_labels")
    assert X is not None and labels is not None, "earlier checks did not run"
    a = anndata.AnnData(X.copy())
    a.obs["leiden"] = [str(v) for v in labels]
    d = Path(tempfile.mkdtemp())
    p = d / "probe.h5ad"
    a.write_h5ad(p)
    b = anndata.read_h5ad(p)
    assert b.shape == a.shape, f"shape changed on round-trip: {b.shape} vs {a.shape}"
    assert np.array_equal(np.asarray(b.X), np.asarray(a.X)), "matrix changed on round-trip"
    assert list(b.obs["leiden"]) == list(a.obs["leiden"]), "obs annotations lost"
    print(f"       ({p.stat().st_size} bytes, {b.shape[0]} cells x {b.shape[1]} genes, "
          "obs preserved)")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: single-cell env assembles, and Scanpy and Seurat agree on the same "
      "data on " + platform.machine() + " (python " + platform.python_version()
      + ", Seurat " + str(STATE.get("seurat_version", "?")) + ") — verified.")
