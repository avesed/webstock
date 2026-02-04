#!/bin/bash
# =============================================================================
# WebStock Health Check Script
# =============================================================================
# Checks the health status of all WebStock services
# =============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=============================================="
echo "       WebStock Health Check"
echo "=============================================="
echo ""

EXIT_CODE=0

# Check Docker
log_info "Checking Docker..."
if docker info &> /dev/null; then
    log_success "Docker is running"
else
    log_error "Docker is not running"
    EXIT_CODE=1
fi

echo ""

# Check each service
services=("postgres" "redis" "backend" "worker" "beat" "frontend" "nginx")

for service in "${services[@]}"; do
    status=$(docker-compose ps -q "$service" 2>/dev/null)

    if [ -z "$status" ]; then
        log_error "$service: Not running"
        EXIT_CODE=1
    else
        health=$(docker inspect --format='{{.State.Health.Status}}' "webstock-$service" 2>/dev/null || echo "none")
        state=$(docker inspect --format='{{.State.Status}}' "webstock-$service" 2>/dev/null || echo "unknown")

        if [ "$state" != "running" ]; then
            log_error "$service: $state"
            EXIT_CODE=1
        elif [ "$health" = "healthy" ]; then
            log_success "$service: healthy"
        elif [ "$health" = "unhealthy" ]; then
            log_error "$service: unhealthy"
            EXIT_CODE=1
        elif [ "$health" = "starting" ]; then
            log_warning "$service: starting"
        else
            log_success "$service: running (no health check)"
        fi
    fi
done

echo ""

# Check API endpoints
log_info "Checking API endpoints..."

# Health endpoint
if curl -sf http://localhost/api/v1/health &> /dev/null; then
    log_success "API Health: OK"
else
    log_error "API Health: FAILED"
    EXIT_CODE=1
fi

# Readiness endpoint
readiness=$(curl -sf http://localhost/api/v1/health/ready 2>/dev/null || echo '{"status":"failed"}')
if echo "$readiness" | grep -q '"status":"ready"'; then
    log_success "API Readiness: OK"
else
    log_warning "API Readiness: Not ready"
    echo "  Response: $readiness"
fi

# Liveness endpoint
if curl -sf http://localhost/api/v1/health/live &> /dev/null; then
    log_success "API Liveness: OK"
else
    log_error "API Liveness: FAILED"
    EXIT_CODE=1
fi

echo ""

# Check database connection
log_info "Checking database..."
if docker-compose exec -T postgres pg_isready -U webstock -d webstock &> /dev/null; then
    log_success "PostgreSQL: accepting connections"
else
    log_error "PostgreSQL: not accepting connections"
    EXIT_CODE=1
fi

# Check Redis connection
log_info "Checking Redis..."
if docker-compose exec -T redis redis-cli ping 2>/dev/null | grep -q "PONG"; then
    log_success "Redis: responding to ping"
else
    log_error "Redis: not responding"
    EXIT_CODE=1
fi

echo ""
echo "=============================================="

if [ $EXIT_CODE -eq 0 ]; then
    log_success "All health checks passed!"
else
    log_error "Some health checks failed!"
fi

exit $EXIT_CODE
