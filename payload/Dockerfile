# =============================================================================
# STAGE 1: Builder
# =============================================================================
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy build inputs
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Upgrade tooling and install the package into the root user site-packages.
# Using --user keeps the install self-contained under /root/.local, which we
# copy verbatim into the runtime image.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir --user ".[messaging,persistence,telemetry]"

# =============================================================================
# STAGE 2: Runtime
# =============================================================================
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime system dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root runtime user
RUN groupadd -r egregore && \
    useradd -r -g egregore -d /home/egregore egregore && \
    mkdir -p /home/egregore/.local /app/tmp /app/data /app/logs && \
    chown -R egregore:egregore /home/egregore /app

# Copy the installed packages and console scripts from the builder
COPY --from=builder --chown=egregore:egregore /root/.local /home/egregore/.local

ENV PYTHONPATH=/home/egregore/.local/lib/python3.12/site-packages
ENV PATH=/home/egregore/.local/bin:$PATH
ENV EGREGORE_REPO_ROOT=/app
ENV EGREGORE_TMP_DIR=/app/tmp

WORKDIR /app
USER egregore

# Expose API (8000) and metrics (9000) ports
EXPOSE 8000 9000

# Readiness probe: checks DB/Redis/NATS connectivity, not just the HTTP listener
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/ready')" || exit 1

# Entry point: run the canonical ASGI application
CMD ["uvicorn", "egregore.http_api.http.main:app", "--host", "0.0.0.0", "--port", "8000"]
