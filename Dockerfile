# =============================================================================
# WebStock App Container
# Contains: FastAPI Backend, Celery Worker/Beat, Nginx + Frontend static
# PostgreSQL is external (separate container)
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Frontend build
# -----------------------------------------------------------------------------
FROM node:20-alpine AS frontend-builder

WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# -----------------------------------------------------------------------------
# Stage 2: Backend dependencies
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS backend-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 3: Runtime
# -----------------------------------------------------------------------------
FROM python:3.11-slim

LABEL maintainer="WebStock Team" \
      description="WebStock App (Backend + Worker + Nginx)"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend:/app \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install runtime packages: nginx, supervisor, libpq, curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    supervisor \
    libpq5 \
    curl \
    dumb-init \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create app user
RUN groupadd --gid 1000 appgroup \
    && useradd --uid 1000 --gid 1000 --shell /bin/bash --create-home appuser

# Copy Python venv from builder
COPY --from=backend-builder /opt/venv /opt/venv

# Copy backend code
COPY --chown=appuser:appgroup backend/ /app/backend/

# Copy worker code
COPY --chown=appuser:appgroup worker/ /app/worker/

# Copy frontend static build into nginx webroot
COPY --from=frontend-builder /build/dist /var/www/html
RUN chown -R www-data:www-data /var/www/html

# Create required directories
RUN mkdir -p /app/logs /app/data /var/log/nginx \
    && chown -R appuser:appgroup /app/logs /app/data

# Copy pre-built stock list data for fast search on first startup
COPY --chown=appuser:appgroup docker/seed/stock_list/ /app/data/stock_list/

# Copy supervisor config
COPY docker/supervisord.conf /etc/supervisor/conf.d/webstock.conf

# Copy entrypoint
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Copy nginx SSL directory placeholder
RUN mkdir -p /etc/nginx/ssl

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://127.0.0.1/api/v1/health || exit 1

VOLUME ["/app/data", "/app/logs"]

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/app/entrypoint.sh"]
CMD ["supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
