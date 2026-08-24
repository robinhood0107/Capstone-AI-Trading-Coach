# syntax=docker/dockerfile:1.10@sha256:865e5dd094beca432e8c0a1d5e1c465db5f998dca4e439981029b3b81fb39ed5
# CI deliberately produces one amd64 artifact; the constant platform prevents host-dependent inputs.
# hadolint ignore=DL3029
FROM --platform=linux/amd64 ghcr.io/astral-sh/uv:0.8.13@sha256:4de5495181a281bc744845b9579acf7b221d6791f99bcc211b9ec13f417c2853 AS uv
# CI deliberately produces one amd64 artifact; the constant platform prevents host-dependent inputs.
# hadolint ignore=DL3029
FROM --platform=linux/amd64 cgr.dev/chainguard/python:latest-dev@sha256:4bf7e945777010672b8ccd5d2ae2c41c91ad6d3478878347c731ae536d506bef AS build
# This disposable build stage needs root only to install the pinned uv binary.
# hadolint ignore=DL3002,DL3066
USER root
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /source/workspaces/decision-platform/python-services
COPY workspaces/decision-platform/python-services/pyproject.toml workspaces/decision-platform/python-services/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY workspaces/decision-platform/python-services/app app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev && \
    /opt/venv/bin/python -c "import confluent_kafka,cryptography,grpc,lightgbm,numpy,onnxruntime,PIL,pyarrow; assert cryptography.__version__ == '50.0.0'; assert PIL.__version__ == '12.3.0'"

# CI deliberately produces one amd64 artifact; the constant platform prevents host-dependent inputs.
# hadolint ignore=DL3029
FROM --platform=linux/amd64 cgr.dev/chainguard/wolfi-base:latest@sha256:a31344ab2cb8618db84f535eec56f76f6178b142cb92cb2e48676cc2dcebea72
ARG SOURCE_REVISION=unknown
ARG RELEASE_VERSION=dev
LABEL org.opencontainers.image.title="Capstone Decision Platform Python Services" \
      org.opencontainers.image.description="P1_OFFLINE_DEMO async worker" \
      org.opencontainers.image.source="https://github.com/robinhood0107/Capstone-AI-Trading-Coach" \
      org.opencontainers.image.revision="$SOURCE_REVISION" \
      org.opencontainers.image.version="$RELEASE_VERSION" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
RUN apk add --no-cache python-3.14=3.14.7-r1 libgomp=16.2.0-r0
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=build --chown=65532:65532 /opt/venv /opt/venv
COPY --from=build --chown=65532:65532 /source/workspaces/decision-platform/python-services/app /app/app
COPY --chown=65532:65532 deploy/p1/docker/secret-entrypoint.sh /usr/local/bin/p1-secret-entrypoint
RUN chmod 0555 /usr/local/bin/p1-secret-entrypoint
USER 65532:65532
EXPOSE 50056
ENTRYPOINT ["/usr/local/bin/p1-secret-entrypoint", "python"]
CMD ["python", "-m", "app.async_worker.grpc_server"]
