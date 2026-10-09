#!/usr/bin/env python3
# bayes.smoke.py — the D3 verification for the `bayes` env.
#
# Same contract (assemble + import + do real work, inside the built arm64 image). The
# risky parts here are unusual: CmdStan does not ship a runnable model, it ships a
# TRANSLATOR AND A BUILD SYSTEM. So "does it work" means "can it translate Stan to C++,
# compile that C++ on aarch64, run the resulting sampler, and get the right answer" —
# four things that can each fail independently and none of which an import reveals.
#
# THE CHECK IS A CONJUGATE IDENTITY, which is the strongest assertion shape available
# anywhere in this catalog. For a Bernoulli likelihood with a Beta(a, b) prior, the
# posterior is Beta(a + k, b + n - k) exactly — no approximation, no reference file. The
# example below has n = 10 with k = 2 successes under Beta(1, 1), so:
#
#     posterior = Beta(3, 9)
#     mean      = 3/12                      = 0.25          exactly
#     sd        = sqrt(3*9 / (12^2 * 13))   = 0.120096       exactly
#
# And the tolerance is COMPUTED rather than chosen: the sampler's error on the mean is
# Monte Carlo noise, so the bound is a multiple of sd/sqrt(n_draws). A sampler that is
# subtly wrong — bad gradients, bad adaptation, a miscompiled transform — misses a
# conjugate posterior by far more than its own standard error. Measured on aarch64:
# sampled mean 0.249395 against exact 0.250000, a delta of 0.00060 with an MCMC standard
# error of 0.00094, i.e. 0.64 SE. R-hat 1.0007.
#
# Nothing is staged and nothing is downloaded: the model and its data are written here.
# That is legitimate for the same reason a generated mesh is (cfd-fv, fem-cfd) — a Stan
# program and ten coin flips are inputs with a derivable answer, not fitted data.
#
# Pure stdlib + the env's own packages. Exit 0 = functionally sound.
import json
import math
import os
import platform
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

FAILURES = []

# The Bernoulli example, matching cmdstan's own examples/bernoulli at the version tag.
BERNOULLI_STAN = """
data {
  int<lower=0> N;
  array[N] int<lower=0,upper=1> y;
}
parameters {
  real<lower=0,upper=1> theta;
}
model {
  theta ~ beta(1, 1);
  y ~ bernoulli(theta);
}
"""
BERNOULLI_DATA = {"N": 10, "y": [0, 1, 0, 0, 0, 0, 0, 0, 0, 1]}

# Beta(1,1) prior + k successes of n  ->  Beta(1+k, 1+n-k)
_K = sum(BERNOULLI_DATA["y"])
_N = BERNOULLI_DATA["N"]
POST_A, POST_B = 1 + _K, 1 + _N - _K
POST_MEAN = POST_A / (POST_A + POST_B)
POST_SD = math.sqrt(POST_A * POST_B / ((POST_A + POST_B) ** 2 * (POST_A + POST_B + 1)))

CHAINS, WARMUP, SAMPLING = 4, 1000, 4000
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
HEADLINE = ["numpy", "scipy", "pandas", "xarray", "cmdstanpy", "arviz"]
print("[smoke] 1. imports")
for mod in HEADLINE:
    @check(f"import {mod}")
    def _imp(mod=mod):
        __import__(mod)


# --- 2. the two runtime prerequisites a naive spec would miss ---------------------
print("[smoke] 2. runtime prerequisites (activation + C++ toolchain)")


@check("CMDSTAN is exported by activate.d, so cmdstanpy can find the install")
def _cmdstan_env():
    # conda-forge's cmdstan sets this in etc/conda/activate.d/cmdstan_activate.sh.
    # Unset, cmdstanpy raises "No CmdStan installation found, run command
    # install_cmdstan" — so this env needs `apptainer run`, not `exec`, exactly like
    # `dft` does for nwchem's basis paths and gpaw's MPI backend.
    val = os.environ.get("CMDSTAN")
    assert val, ("CMDSTAN is unset — activate.d did not run. cmdstanpy cannot locate "
                 "CmdStan without it; use `run`, not `exec`")
    assert Path(val).is_dir(), f"CMDSTAN points at nothing: {val}"
    import cmdstanpy
    resolved = cmdstanpy.cmdstan_path()
    assert resolved, "cmdstanpy.cmdstan_path() returned nothing"
    print(f"       (CMDSTAN={val}, cmdstanpy resolves {resolved})")


@check("a C++ compiler is present, because Stan compiles every model at run time")
def _compiler():
    # Stan ships no precompiled models: stanc emits C++ and make builds it, per model,
    # on first use. The `cmdstan` package pulls make and stanc but NOT a compiler, so
    # without cxx-compiler this env dies at the first sample() with
    # "make: g++: No such file or directory". Same shape as fem-cfd needing gcc for FFCx.
    cxx = shutil.which("g++") or shutil.which("c++")
    assert cxx, ("no C++ compiler on PATH — Stan compiles each model at run time, so "
                 "this env is unusable without one (see cxx-compiler in bayes.yaml)")
    assert shutil.which("make"), "make not on PATH; CmdStan's build system needs it"
    print(f"       ({os.path.basename(cxx)} at {cxx}, make present)")


# --- 3. translate + compile + sample, then check against the exact posterior ------
print("[smoke] 3. Stan: compile a model and recover a conjugate posterior")


@check("CmdStan translates and compiles a Stan model on aarch64")
def _compile():
    import cmdstanpy
    d = Path(tempfile.mkdtemp())
    stan = d / "bernoulli.stan"
    stan.write_text(BERNOULLI_STAN)
    (d / "bernoulli.data.json").write_text(json.dumps(BERNOULLI_DATA))
    # This is the step that exercises stanc + the C++ toolchain. It is slow by nature
    # (seconds), and it is the single most likely thing to break on a new base image.
    model = cmdstanpy.CmdStanModel(stan_file=str(stan))
    exe = Path(model.exe_file)
    assert exe.is_file(), f"no compiled executable produced at {exe}"
    assert exe.stat().st_size > 0, "compiled executable is empty"
    STATE["model"] = model
    STATE["data"] = str(d / "bernoulli.data.json")
    print(f"       (compiled {exe.name}, {exe.stat().st_size // 1024} KiB)")


@check("the sampler recovers the exact Beta(3,9) posterior to within MCMC error")
def _posterior():
    model = STATE.get("model")
    assert model is not None, "the model did not compile; nothing to sample"
    fit = model.sample(data=STATE["data"], chains=CHAINS, iter_warmup=WARMUP,
                       iter_sampling=SAMPLING, seed=1,
                       show_progress=False, show_console=False)
    theta = fit.stan_variable("theta")
    n_draws = int(theta.size)
    assert n_draws == CHAINS * SAMPLING, \
        f"expected {CHAINS * SAMPLING} draws, got {n_draws}"
    mean, sd = float(theta.mean()), float(theta.std())

    # The tolerance is DERIVED, not chosen: the sampler's error on the mean is Monte
    # Carlo noise of size sd/sqrt(n_draws). 5 SE is a wide berth for a correct sampler
    # and nowhere near wide enough to hide a broken one — a wrong posterior is off by
    # the width of the distribution, not by a few standard errors.
    mcse = POST_SD / math.sqrt(n_draws)
    delta = abs(mean - POST_MEAN)
    assert delta < 5 * mcse, (
        f"posterior mean {mean:.6f} vs exact {POST_MEAN:.6f} (Beta({POST_A},{POST_B})): "
        f"delta {delta:.5f} = {delta / mcse:.1f} MCMC standard errors, limit 5")
    # The spread is a second, independent check on the same analytic result.
    assert abs(sd - POST_SD) < 0.05 * POST_SD, \
        f"posterior sd {sd:.6f} vs exact {POST_SD:.6f} (>5% off)"
    STATE["fit"] = fit
    print(f"       (Beta({POST_A},{POST_B}): mean exact {POST_MEAN:.6f} vs sampled "
          f"{mean:.6f} = {delta / mcse:.2f} SE; sd exact {POST_SD:.6f} vs {sd:.6f})")


# --- 4. ArviZ: convergence is checkable, so check it ------------------------------
print("[smoke] 4. ArviZ diagnostics")


@check("ArviZ reports converged chains (R-hat ~ 1, healthy ESS)")
def _arviz():
    import arviz as az
    fit = STATE.get("fit")
    assert fit is not None, "no fit to diagnose"
    idata = az.from_cmdstanpy(fit)
    summ = az.summary(idata, var_names=["theta"])
    rhat = float(summ["r_hat"].iloc[0])
    ess = float(summ["ess_bulk"].iloc[0])
    # R-hat compares between- and within-chain variance; >1.01 is the conventional
    # warning line and means the chains have not mixed. A real sampler failure shows up
    # here even when the mean happens to look plausible.
    assert rhat < 1.01, f"R-hat {rhat:.4f} — chains did not mix"
    assert ess > 400, f"bulk ESS {ess:.0f} is implausibly low for {CHAINS * SAMPLING} draws"
    # `groups` is a tuple in arviz 1.x and was a method in 0.x — accept either rather
    # than pinning this check to one arviz API. (The first build failed here with
    # "'tuple' object is not callable" AFTER every assertion above had passed, i.e. a
    # bug in this test's reporting line, not in the env.)
    _g = getattr(idata, "groups", ())
    n_groups = len(list(_g() if callable(_g) else _g))
    print(f"       (R-hat {rhat:.4f}, bulk ESS {ess:.0f}, "
          f"InferenceData groups: {n_groups})")


# --- verdict ----------------------------------------------------------------------
print("[smoke] " + ("-" * 50))
if FAILURES:
    print(f"[smoke] FAILED: {len(FAILURES)} check(s): " + ", ".join(n for n, _ in FAILURES))
    sys.exit(1)
print("[smoke] PASSED: bayes env assembles, compiles a Stan model, and recovers an "
      "analytic posterior on " + platform.machine() + " (python "
      + platform.python_version() + ") — verified.")
