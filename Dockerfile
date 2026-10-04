# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# EXTRAS selects what goes in the image. The default is deliberately narrow:
# the crawl-and-audit path plus the MCP server, which is what this image exists
# for. "cluster" alone pulls scikit-learn and, transitively, scipy — 119 MB for
# keyword clustering, which has nothing to do with crawling a site.
#   docker build .                                    -> slim (default)
#   docker build --build-arg EXTRAS=all .             -> everything
ARG EXTRAS=mcp,reports

COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY seohead ./seohead
# --no-compile skips pip's post-install compileall pass: PYTHONDONTWRITEBYTECODE
# below only stops *runtime* imports from writing .pyc, it does nothing about
# the ~67 MB of __pycache__ that compileall bakes in at install time. pip
# itself (~13 MB) is removed once installation is done — the runtime image
# only ever runs the `seohead` entry point, never pip.
RUN python -m pip install --upgrade pip && \
    python -m pip install --no-compile ".[${EXTRAS}]" && \
    python -m pip uninstall -y pip

FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.source="https://github.com/PavloSEO/seohead-tools" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.title="SEOHEAD Tools"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv
RUN groupadd --gid 10001 seohead && \
    useradd --uid 10001 --gid seohead --no-create-home --shell /usr/sbin/nologin seohead && \
    mkdir -p /data && chown seohead:seohead /data

USER seohead
WORKDIR /data

ENTRYPOINT ["seohead"]
CMD ["--help"]
