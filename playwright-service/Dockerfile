# Playwright Extraction Service
# Dual-mode: HTTP API (port 8002) + MCP Server (port 8931)

FROM mcr.microsoft.com/playwright:v1.50.0-jammy

LABEL maintainer="WebStock" \
      description="Playwright content extraction + MCP server"

# System dependencies: Python, Xvfb, supervisord
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    xvfb supervisor curl \
    && rm -rf /var/lib/apt/lists/*

# Install @playwright/mcp (Microsoft's official MCP server)
RUN npm install -g @playwright/mcp

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copy application code
COPY . .
RUN chmod +x /app/entrypoint.sh

EXPOSE 8002 8931

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8002/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
