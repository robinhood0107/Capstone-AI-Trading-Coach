FROM python:3.12.14-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/src
ENV PATH=/app/.venv/bin:$PATH
ENV MPLCONFIGDIR=/tmp/matplotlib
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libssl3t64=3.5.7-1~deb13u2 \
        openssl=3.5.7-1~deb13u2 \
        openssl-provider-legacy=3.5.7-1~deb13u2 \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir uv==0.11.26
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache
COPY src ./src
COPY data ./data
RUN mkdir -p /output && chown 65532:65532 /output
USER 65532:65532
VOLUME ["/output"]
CMD ["python", "-m", "preview_cli", "run"]
