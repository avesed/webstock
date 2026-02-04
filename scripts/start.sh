#!/bin/bash
# =============================================================================
# WebStock Startup Script
# =============================================================================
# This script handles the complete startup sequence for WebStock:
# 1. Validates environment configuration
# 2. Waits for dependencies (PostgreSQL, Redis)
# 3. Runs database migrations
# 4. Starts all services
# =============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Change to project directory
cd "$PROJECT_DIR"

# =============================================================================
# Configuration
# =============================================================================
MAX_RETRIES=30
RETRY_INTERVAL=2

# =============================================================================
# Functions
# =============================================================================

check_dependencies() {
    log_info "Checking required dependencies..."

    local missing_deps=()

    if ! command -v docker &> /dev/null; then
        missing_deps+=("docker")
    fi

    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        missing_deps+=("docker-compose")
    fi

    if [ ${#missing_deps[@]} -ne 0 ]; then
        log_error "Missing dependencies: ${missing_deps[*]}"
        log_error "Please install the missing dependencies and try again."
        exit 1
    fi

    log_success "All dependencies are installed."
}

check_env_file() {
    log_info "Checking environment configuration..."

    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            log_warning ".env file not found. Creating from .env.example..."
            cp .env.example .env
            log_warning "Please review and update .env with your configuration."
            log_warning "At minimum, update:"
            log_warning "  - POSTGRES_PASSWORD"
            log_warning "  - JWT_SECRET_KEY"
            log_warning "  - OPENAI_API_KEY (if using AI features)"
            log_warning "  - FINNHUB_API_KEY (for stock data)"
            exit 1
        else
            log_error ".env file not found and .env.example is missing."
            exit 1
        fi
    fi

    # Check for critical variables
    source .env

    local warnings=()

    if [ "${JWT_SECRET_KEY:-}" = "change-me-in-production" ] || \
       [ "${JWT_SECRET_KEY:-}" = "generate-a-secure-random-key-here-minimum-32-characters" ] || \
       [ -z "${JWT_SECRET_KEY:-}" ]; then
        warnings+=("JWT_SECRET_KEY is not set or using default value")
    fi

    if [ "${POSTGRES_PASSWORD:-}" = "webstock" ] || \
       [ "${POSTGRES_PASSWORD:-}" = "change-this-secure-password" ]; then
        warnings+=("POSTGRES_PASSWORD is using a default/weak value")
    fi

    if [ ${#warnings[@]} -ne 0 ]; then
        log_warning "Configuration warnings:"
        for warning in "${warnings[@]}"; do
            log_warning "  - $warning"
        done

        if [ "${ENVIRONMENT:-development}" = "production" ]; then
            log_error "Cannot start in production mode with insecure configuration!"
            exit 1
        fi
    fi

    log_success "Environment configuration validated."
}

wait_for_postgres() {
    log_info "Waiting for PostgreSQL to be ready..."

    local retries=0
    while [ $retries -lt $MAX_RETRIES ]; do
        if docker-compose exec -T postgres pg_isready -U webstock -d webstock &> /dev/null; then
            log_success "PostgreSQL is ready."
            return 0
        fi

        retries=$((retries + 1))
        log_info "PostgreSQL not ready yet. Retry $retries/$MAX_RETRIES..."
        sleep $RETRY_INTERVAL
    done

    log_error "PostgreSQL failed to become ready within the timeout period."
    return 1
}

wait_for_redis() {
    log_info "Waiting for Redis to be ready..."

    local retries=0
    while [ $retries -lt $MAX_RETRIES ]; do
        if docker-compose exec -T redis redis-cli ping 2>/dev/null | grep -q "PONG"; then
            log_success "Redis is ready."
            return 0
        fi

        retries=$((retries + 1))
        log_info "Redis not ready yet. Retry $retries/$MAX_RETRIES..."
        sleep $RETRY_INTERVAL
    done

    log_error "Redis failed to become ready within the timeout period."
    return 1
}

run_migrations() {
    log_info "Running database migrations..."

    # Check if alembic is configured
    if [ -f "backend/alembic.ini" ]; then
        docker-compose exec -T backend alembic upgrade head
        log_success "Database migrations completed."
    else
        log_info "No Alembic configuration found. Skipping migrations."
        log_info "Database tables will be created by SQLAlchemy on first run."
    fi
}

start_infrastructure() {
    log_info "Starting infrastructure services (PostgreSQL, Redis)..."

    docker-compose up -d postgres redis

    wait_for_postgres
    wait_for_redis

    log_success "Infrastructure services are running."
}

start_backend() {
    log_info "Starting backend services..."

    docker-compose up -d backend worker beat

    # Wait for backend to be healthy
    local retries=0
    while [ $retries -lt $MAX_RETRIES ]; do
        if docker-compose exec -T backend curl -sf http://localhost:8000/api/v1/health &> /dev/null; then
            log_success "Backend is healthy."
            return 0
        fi

        retries=$((retries + 1))
        log_info "Backend not ready yet. Retry $retries/$MAX_RETRIES..."
        sleep $RETRY_INTERVAL
    done

    log_warning "Backend health check timed out. Check logs for issues."
}

start_frontend() {
    log_info "Starting frontend service..."

    docker-compose up -d frontend

    log_success "Frontend service started."
}

start_nginx() {
    log_info "Starting Nginx reverse proxy..."

    docker-compose up -d nginx

    log_success "Nginx is running."
}

show_status() {
    echo ""
    log_info "Service Status:"
    echo "----------------------------------------"
    docker-compose ps
    echo "----------------------------------------"
    echo ""
    log_success "WebStock is now running!"
    echo ""
    log_info "Access points:"
    echo "  - Web UI:      http://localhost"
    echo "  - API:         http://localhost/api/v1"
    echo "  - Health:      http://localhost/api/v1/health"
    echo "  - Readiness:   http://localhost/api/v1/health/ready"
    echo ""
    log_info "Useful commands:"
    echo "  - View logs:        docker-compose logs -f"
    echo "  - Stop services:    docker-compose down"
    echo "  - Restart service:  docker-compose restart <service>"
    echo ""
}

# =============================================================================
# Main
# =============================================================================

main() {
    echo "=============================================="
    echo "       WebStock Startup Script"
    echo "=============================================="
    echo ""

    # Parse arguments
    local skip_checks=false
    local service=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            --skip-checks)
                skip_checks=true
                shift
                ;;
            --service)
                service="$2"
                shift 2
                ;;
            -h|--help)
                echo "Usage: $0 [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --skip-checks    Skip dependency and environment checks"
                echo "  --service NAME   Start only a specific service"
                echo "  -h, --help       Show this help message"
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done

    # Run checks
    if [ "$skip_checks" = false ]; then
        check_dependencies
        check_env_file
    fi

    # Start services
    if [ -n "$service" ]; then
        log_info "Starting service: $service"
        docker-compose up -d "$service"
    else
        start_infrastructure
        run_migrations
        start_backend
        start_frontend
        start_nginx
    fi

    show_status
}

# Run main function
main "$@"
