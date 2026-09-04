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
# can re-run `python /opt/aarchsci/smoke.py` and re-earn the "verified" claim. The
# builder runs it during the build and refuses to tag if it fails.
#
# Base: micromamba (multi-arch). On a NATIVE arm64 host this resolves the
# linux-aarch64 conda-forge packages with NO emulation (D1).
ARG MAMBA_VERSION=1.5.8
FROM mambaorg/micromamba:${MAMBA_VERSION}

# Env identity — provided by build-env.sh.
ARG ENV_NAME
ARG ENV_FILE=${ENV_NAME}.yaml
ARG SMOKE_FILE=${ENV_NAME}.smoke.py

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
RUN micromamba install -y -n base -f /opt/aarchsci/env.yaml \
    && micromamba clean --all --yes

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

# Make the env's binaries the default PATH.
ENV PATH=/opt/conda/bin:$PATH

# Bake the D3 smoke test in so verification is reproducible by any consumer.
COPY ${SMOKE_FILE} /opt/aarchsci/smoke.py

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
