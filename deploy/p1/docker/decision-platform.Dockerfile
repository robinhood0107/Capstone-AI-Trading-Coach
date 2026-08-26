# syntax=docker/dockerfile:1.10@sha256:865e5dd094beca432e8c0a1d5e1c465db5f998dca4e439981029b3b81fb39ed5
# CI deliberately produces one amd64 artifact; the constant platform prevents host-dependent inputs.
# hadolint ignore=DL3029
FROM --platform=linux/amd64 eclipse-temurin:25-jdk-jammy@sha256:89565961a318534f01c971c7b1d030e60713c66995b887c94010cef938dbc53e AS spring-build
WORKDIR /source
COPY workspaces/decision-platform/spring-api/gradlew workspaces/decision-platform/spring-api/gradlew
COPY workspaces/decision-platform/spring-api/gradle workspaces/decision-platform/spring-api/gradle
COPY workspaces/decision-platform/spring-api/settings.gradle.kts workspaces/decision-platform/spring-api/build.gradle.kts workspaces/decision-platform/spring-api/
COPY contracts contracts
COPY workspaces/decision-platform/spring-api/src workspaces/decision-platform/spring-api/src
RUN --mount=type=cache,target=/root/.gradle \
    workspaces/decision-platform/spring-api/gradlew \
      -p workspaces/decision-platform/spring-api --no-daemon bootJar

# hadolint ignore=DL3029
FROM --platform=linux/amd64 ghcr.io/astral-sh/uv:0.8.13@sha256:4de5495181a281bc744845b9579acf7b221d6791f99bcc211b9ec13f417c2853 AS uv
# hadolint ignore=DL3029
FROM --platform=linux/amd64 cgr.dev/chainguard/python:latest-dev@sha256:4bf7e945777010672b8ccd5d2ae2c41c91ad6d3478878347c731ae536d506bef AS python-build
USER root
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /source/workspaces/decision-platform/python-services
COPY workspaces/decision-platform/python-services/pyproject.toml workspaces/decision-platform/python-services/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY workspaces/decision-platform/python-services/app app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# hadolint ignore=DL3029
FROM --platform=linux/amd64 cgr.dev/chainguard/wolfi-base:latest@sha256:a31344ab2cb8618db84f535eec56f76f6178b142cb92cb2e48676cc2dcebea72
ARG SOURCE_REVISION=unknown
ARG RELEASE_VERSION=dev
LABEL org.opencontainers.image.title="Capstone Decision Platform" \
      org.opencontainers.image.description="Spring API and Python worker functional module" \
      org.opencontainers.image.source="https://github.com/robinhood0107/Capstone-AI-Trading-Coach" \
      org.opencontainers.image.revision="$SOURCE_REVISION" \
      org.opencontainers.image.version="$RELEASE_VERSION" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
RUN apk add --no-cache openjdk-25-jre=25.0.4.1-r0 python-3.14=3.14.7-r1 libgomp=16.2.0-r0
ENV JAVA_HOME=/usr/lib/jvm/java-25-openjdk \
    PATH=/opt/venv/bin:/usr/lib/jvm/java-25-openjdk/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=spring-build --chown=65532:65532 /source/workspaces/decision-platform/spring-api/build/libs/decision-platform-api-0.0.1-SNAPSHOT.jar /app/app.jar
COPY --from=python-build --chown=65532:65532 /opt/venv /opt/venv
COPY --from=python-build --chown=65532:65532 /source/workspaces/decision-platform/python-services/app /app/app
COPY --chown=65532:65532 deploy/p1/docker/decision-platform-supervisor.py /app/decision-platform-supervisor.py
COPY --chown=65532:65532 deploy/p1/docker/decision-platform-health.py /app/decision-platform-health.py
COPY --chown=65532:65532 deploy/p1/docker/secret-entrypoint.sh /usr/local/bin/p1-secret-entrypoint
RUN chmod 0555 /usr/local/bin/p1-secret-entrypoint
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/p1-secret-entrypoint", "decision-platform"]
CMD ["python", "/app/decision-platform-supervisor.py"]
