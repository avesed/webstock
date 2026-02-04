#!/bin/bash
# =============================================================================
# WebStock Stop Script
# =============================================================================
# Gracefully stops all WebStock services
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
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Parse arguments
REMOVE_VOLUMES=false
REMOVE_IMAGES=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--volumes)
            REMOVE_VOLUMES=true
            shift
            ;;
        -i|--images)
            REMOVE_IMAGES=true
            shift
            ;;
        --all)
            REMOVE_VOLUMES=true
            REMOVE_IMAGES=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -v, --volumes    Remove named volumes (WARNING: deletes data!)"
            echo "  -i, --images     Remove built images"
            echo "  --all            Remove volumes and images"
            echo "  -h, --help       Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "=============================================="
echo "       WebStock Stop Script"
echo "=============================================="
echo ""

log_info "Stopping WebStock services..."

# Build the docker-compose down command
CMD="docker-compose down"

if [ "$REMOVE_VOLUMES" = true ]; then
    log_warning "Volumes will be removed. All data will be lost!"
    CMD="$CMD -v"
fi

if [ "$REMOVE_IMAGES" = true ]; then
    log_info "Built images will be removed."
    CMD="$CMD --rmi local"
fi

# Execute
eval "$CMD"

log_success "WebStock services stopped."

if [ "$REMOVE_VOLUMES" = true ]; then
    log_warning "All data volumes have been removed."
fi
