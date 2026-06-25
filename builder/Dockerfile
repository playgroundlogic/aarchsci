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
