# Stage 1: Builder — install dependencies with build tools for pyqlib Cython compilation
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    cmake \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt


# Stage 2: Runtime — lean image with only runtime dependencies
FROM python:3.11-slim AS runtime

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root app user
RUN groupadd -g 1000 app && \
    useradd -u 1000 -g app -m -s /bin/bash app

WORKDIR /app

# Copy application code
COPY --chown=app:app . /app

# Create data directory
RUN mkdir -p /app/data/qlib && chown -R app:app /app/data

USER app

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# Single worker is critical — Qlib has global state and is not safe for multi-process
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
