# Generic aarchsci builder — one Dockerfile for every curated env.
#
# DIVERGENCE FROM aarchbio (D2): aarchbio installs a single `PKG=VERSION` from
# bioconda. aarch.science installs a whole CURATED ENVIRONMENT from a committed
# conda-forge env spec (envs/<name>.yaml). conda-forge has no container registry to
# mirror, so the env file *is* the source of truth and "what to build" is curation,
# not discovery.
#
# DIVERGENCE (D3): the smoke test is COPIED INTO the image, not just run at build
# time. Verification then travels with the artifact — anyone who pulls the image
# can re-run `python /opt/aarchsci/smoke.py` (or `Rscript /opt/aarchsci/smoke.R`, for
# an env with no Python) and re-earn the "verified" claim. The builder runs it during
# the build and refuses to tag if it fails.
#
# Base: micromamba (multi-arch). On a NATIVE arm64 host this resolves the
# linux-aarch64 conda-forge packages with NO emulation (D1).
ARG MAMBA_VERSION=1.5.8
FROM mambaorg/micromamba:${MAMBA_VERSION}

# Env identity — provided by build-env.sh.
ARG ENV_NAME
ARG ENV_FILE=${ENV_NAME}.yaml
ARG SMOKE_FILE=${ENV_NAME}.smoke.py
# The in-image name, and thus the interpreter a consumer reaches for. build-env.sh
# derives both from the spec's smoke-test extension: `smoke.py` (nine envs) or
# `smoke.R` (the `r` env, whose base has no Python to run a .py with).
ARG SMOKE_DEST=smoke.py

# Provenance (mirrors aarchbio D6): trace any tag back to its committed spec + the
# builder commit that produced it.
ARG SOURCE_SPEC
ARG BUILDER_GIT_SHA=unknown
ARG TARGETPLATFORM
ARG BUILD_PLATFORM=${TARGETPLATFORM}

USER root

# Install the curated env from its committed conda-forge spec into the base env.
# The channel(s) come from the env YAML itself (conda-forge), so no -c flags here:
# the spec is fully self-describing and stays the single source of truth.
COPY ${ENV_FILE} /opt/aarchsci/env.yaml
# The `rm -rf` is NOT redundant with `clean --all`, and the reason is worth stating so
# nobody deletes it as belt-and-braces. `clean --all` drops tarballs and index cache but
# deliberately keeps the EXTRACTED package directories under $MAMBA_ROOT_PREFIX/pkgs,
# because the base env is using them — conda hardlinks from pkgs into the prefix, so
# from clean's point of view they are all in use. Every package therefore appears twice
# in the filesystem tree (measured on `r`: 329 pkgs entries for 328 packages).
#
# Removing the pkgs path drops ONE of the two links; the file survives at links=1 with
# its blocks intact, which is why this is safe rather than clever. It must be in THIS
# RUN layer — a later layer would only add a whiteout and save nothing.
#
# Honest accounting (issue #10): most of what `du` attributes to pkgs is shared with the
# prefix and stored once in the layer tar, so the win is NOT the ~2.2GB `du -sm
# /opt/conda/pkgs` suggests. Measured on `r`: 3.51GB -> 3.22GB, i.e. 290MB. Small, free,
# and applies to every env.
RUN micromamba install -y -n base -f /opt/aarchsci/env.yaml \
    && micromamba clean --all --yes \
    && rm -rf /opt/conda/pkgs

# Repair conda-forge gromacs's activation script, which otherwise BRICKS THE IMAGE.
#
# This is not a nicety. micromamba-docker's `_entrypoint.sh` runs `set -ef -o pipefail`
# and then sources every `etc/conda/activate.d/*.sh` before `exec "$@"`. The `-f` turns
# OFF pathname expansion. gromacs ships a GMXRC.bash containing
#
#     for cfile in $GMXBIN/gmx-completion-*.bash ; do source $cfile ; done
#
# with no existence test, so with globbing disabled `source` receives the literal
# pattern, fails, and `-e` aborts the entrypoint before it ever reaches the command.
# The result is an image where EVERY invocation dies — `python`, `ls`, the smoke test,
# all of it — with only a confusing "gmx-completion-*.bash: No such file or directory".
# (The completion files do exist; the glob is simply never expanded.) The builder's
# lock step caught it, and the D3 gate held, but nothing about the failure said "gromacs
# activation".
#
# Guarding the loop is also the correct upstream fix — `source $cfile` in a loop over a
# glob is unsafe regardless of who disabled globbing, since a build shipping no
# completion files would fail the same way. Arch-independent: linux-64 has identical
# code under `bin.AVX2_256`. The `for`/`test` wrapper makes this a no-op in the eight
# envs that don't install gromacs.
RUN for rc in /opt/conda/bin.*/GMXRC.bash; do \
      [ -f "$rc" ] || continue; \
      sed -i 's|^\( *\)source \$cfile *$|\1if test -f "$cfile"; then source "$cfile"; fi|' "$rc"; \
      echo "[builder] patched unsafe completion glob in $rc"; \
    done

# Enable gpaw's MPI backend, without which this image's DOCUMENTED parallel interface
# is broken. Like the gromacs repair above, the guard makes this a no-op in every env
# that doesn't install gpaw.
#
# gpaw 26.7 changed how MPI is activated. Through 25.7 an `mpi_openmpi` build was simply
# MPI-capable under plain `python`. From 26.7 the C MPI backend is opt-in: upstream's
# primary interface is the `gpaw python` wrapper, with `GPAW_MPI_BACKEND=cgpaw` as the
# documented alternative. Unset, measured on 26.7.0 `py314_mpi_openmpi_omp_3`:
#   python                   -> gpaw.mpi.world is a SerialCommunicator (size 1)
#   mpiexec -n 2 python      -> FAILS, exit 1
# With the variable exported:
#   python                   -> MPI communicator, size 1
#   mpiexec -n 2 python      -> MPI, size 2 on both ranks
# and it propagates to ranks without needing `mpiexec -x`, so the README's
# `docker run ... dft:latest mpiexec -n 4 python script.py` works untouched. That
# promise is the whole reason this is set in the image rather than left to the caller.
#
# Why activate.d rather than a plain `ENV`: the variable is meaningless to the other
# twelve envs, and `dft` already requires entrypoint activation anyway (nwchem resolves
# its basis-set path there), so this adds no new constraint — `apptainer run`, not
# `exec`, remains the rule for this env. The tradeoff is that skipping activation
# reverts gpaw to serial; that fails LOUDLY (`mpiexec` exits 1) rather than silently
# computing a wrong answer, and dft.smoke.py asserts the variable directly so a
# regression fails the build instead of shipping.
RUN if [ -x /opt/conda/bin/gpaw ]; then \
      mkdir -p /opt/conda/etc/conda/activate.d; \
      printf '%s\n' '#!/bin/sh' 'export GPAW_MPI_BACKEND=cgpaw' \
        > /opt/conda/etc/conda/activate.d/aarchsci_gpaw_mpi.sh; \
      echo "[builder] enabled gpaw MPI backend (GPAW_MPI_BACKEND=cgpaw)"; \
    fi

# Make the env's binaries the default PATH.
ENV PATH=/opt/conda/bin:$PATH

# Bake the D3 smoke test in so verification is reproducible by any consumer.
COPY ${SMOKE_FILE} /opt/aarchsci/${SMOKE_DEST}

# Override misleading labels inherited from the micromamba base.
LABEL org.opencontainers.image.title="${ENV_NAME}" \
      org.opencontainers.image.source="${SOURCE_SPEC}" \
      org.opencontainers.image.url="https://github.com/playgroundlogic/aarchsci" \
      org.opencontainers.image.revision="${BUILDER_GIT_SHA}" \
      org.opencontainers.image.vendor="Playground Logic" \
      org.opencontainers.image.description="Native arm64 build of the conda-forge '${ENV_NAME}' scientific env. Verified (assemble+import+functional smoke test), not just solved. Unofficial community build by Playground Logic; not affiliated with or endorsed by conda-forge, Anaconda, or AWS." \
      science.aarch.env="${ENV_NAME}" \
      science.aarch.builder-git-sha="${BUILDER_GIT_SHA}" \
      science.aarch.build-platform="${BUILD_PLATFORM}" \
      science.aarch.publisher="Playground Logic"

USER mambauser
