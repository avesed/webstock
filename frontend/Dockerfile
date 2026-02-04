# =============================================================================
# WebStock Frontend - Production Dockerfile
# Multi-stage build: Node for building, Nginx for serving
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Builder - Build the React/Vue application
# -----------------------------------------------------------------------------
FROM node:20-alpine AS builder

# Set working directory
WORKDIR /app

# Copy package files for dependency caching
COPY package*.json ./

# Install all dependencies (including devDependencies for build)
RUN npm ci

# Copy source code
COPY . .

# Build the application for production
RUN npm run build

# -----------------------------------------------------------------------------
# Stage 2: Runtime - Nginx to serve static files
# -----------------------------------------------------------------------------
FROM nginx:1.25-alpine AS runtime

# Labels for container metadata
LABEL maintainer="WebStock Team" \
      version="1.0.0" \
      description="WebStock Frontend SPA"

# Install dumb-init for proper signal handling
RUN apk add --no-cache dumb-init

# Remove default nginx config
RUN rm -rf /etc/nginx/conf.d/default.conf

# Copy custom nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Create non-root user
RUN addgroup -g 1001 -S nginx-app \
    && adduser -u 1001 -S nginx-app -G nginx-app

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Set proper permissions
RUN chown -R nginx-app:nginx-app /usr/share/nginx/html \
    && chown -R nginx-app:nginx-app /var/cache/nginx \
    && chown -R nginx-app:nginx-app /var/log/nginx \
    && touch /var/run/nginx.pid \
    && chown -R nginx-app:nginx-app /var/run/nginx.pid

# Configure nginx to run as non-root
RUN sed -i 's/user  nginx;/user  nginx-app;/' /etc/nginx/nginx.conf

# Switch to non-root user
USER nginx-app

# Expose port
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:3000/health || exit 1

# Use dumb-init for proper signal handling
ENTRYPOINT ["/usr/bin/dumb-init", "--"]

# Start nginx in foreground
CMD ["nginx", "-g", "daemon off;"]
