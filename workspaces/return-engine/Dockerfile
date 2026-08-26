FROM python:3.12.12-slim@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/src
ENV PATH=/app/.venv/bin:$PATH
ENV MPLCONFIGDIR=/tmp/matplotlib
RUN python -m pip install --no-cache-dir uv==0.11.26
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache
COPY src ./src
COPY data ./data
RUN mkdir -p /output && chown 65532:65532 /output
USER 65532:65532
VOLUME ["/output"]
CMD ["python", "-m", "preview_cli", "run"]
