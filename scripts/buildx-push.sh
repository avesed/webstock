#!/bin/bash
# =============================================================================
# WebStock Multi-Arch Build & Push to GHCR
#
# Builds linux/amd64 + linux/arm64 images and pushes to ghcr.io/avesed/webstock
#   - App (main)       → ghcr.io/avesed/webstock:latest  (+ optional version tag)
#   - Playwright       → ghcr.io/avesed/webstock:playwright  (amd64 only)
#
# Usage:
#   ./scripts/buildx-push.sh                      # Build app only (latest)
#   ./scripts/buildx-push.sh --tag v0.2.0         # Build app with version tag
#   ./scripts/buildx-push.sh playwright           # Build playwright only
#   ./scripts/buildx-push.sh app playwright       # Build both
# =============================================================================

set -euo pipefail

REGISTRY="ghcr.io/avesed/webstock"
BUILDER_NAME="webstock-builder"
MULTI_PLATFORM="linux/amd64,linux/arm64"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# -----------------------------------------------------------------------------
# Colors
# -----------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# -----------------------------------------------------------------------------
# Pre-flight checks
# -----------------------------------------------------------------------------
preflight() {
    if ! command -v docker &>/dev/null; then
        err "Docker is not installed."
        exit 1
    fi

    if ! docker buildx version &>/dev/null; then
        err "Docker Buildx is not available. Install it first."
        exit 1
    fi

    info "Ensure you are logged in to ghcr.io:"
    info "  echo \$GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin"
    echo ""
}

# -----------------------------------------------------------------------------
# Ensure buildx builder exists with multi-platform support
# -----------------------------------------------------------------------------
ensure_builder() {
    if docker buildx inspect "$BUILDER_NAME" &>/dev/null; then
        info "Using existing builder: $BUILDER_NAME"
    else
        info "Creating buildx builder: $BUILDER_NAME"
        docker buildx create \
            --name "$BUILDER_NAME" \
            --driver docker-container \
            --bootstrap \
            --use
    fi
    docker buildx use "$BUILDER_NAME"
}

# -----------------------------------------------------------------------------
# Build functions
# -----------------------------------------------------------------------------
build_app() {
    local tags=("--tag" "${REGISTRY}:latest")

    if [ -n "${VERSION_TAG:-}" ]; then
        tags+=("--tag" "${REGISTRY}:${VERSION_TAG}")
    fi

    info "Building App → ${REGISTRY}:latest${VERSION_TAG:+ + ${REGISTRY}:${VERSION_TAG}}"
    info "  Platforms: ${MULTI_PLATFORM}"
    info "  Dockerfile: ./Dockerfile"
    echo ""

    docker buildx build \
        --platform "$MULTI_PLATFORM" \
        --file Dockerfile \
        "${tags[@]}" \
        --push \
        .

    ok "App pushed → ${REGISTRY}:latest${VERSION_TAG:+ + ${REGISTRY}:${VERSION_TAG}}"
    echo ""
}

build_playwright() {
    local tag="${REGISTRY}:playwright"

    warn "Playwright base image does NOT support arm64 — building amd64 only"
    info "Building Playwright → ${tag}"
    info "  Platform: linux/amd64"
    info "  Dockerfile: playwright-service/Dockerfile"
    echo ""

    docker buildx build \
        --platform "linux/amd64" \
        --file playwright-service/Dockerfile \
        --tag "$tag" \
        --push \
        playwright-service/

    ok "Playwright pushed → ${tag}"
    echo ""
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
main() {
    echo "=========================================="
    echo "  WebStock Multi-Arch Build & Push"
    echo "=========================================="
    echo ""

    preflight
    ensure_builder

    local targets=()
    VERSION_TAG=""

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --tag)
                VERSION_TAG="$2"
                shift 2
                ;;
            app|playwright)
                targets+=("$1")
                shift
                ;;
            *)
                err "Unknown argument: $1"
                err "Usage: $0 [app|playwright] [--tag VERSION]"
                exit 1
                ;;
        esac
    done

    # Default: build app only
    if [ ${#targets[@]} -eq 0 ]; then
        targets=("app")
    fi

    local start_time=$SECONDS

    for target in "${targets[@]}"; do
        case "$target" in
            app)
                build_app
                ;;
            playwright)
                build_playwright
                ;;
        esac
    done

    local elapsed=$(( SECONDS - start_time ))
    local mins=$(( elapsed / 60 ))
    local secs=$(( elapsed % 60 ))

    echo "=========================================="
    ok "All done! (${mins}m ${secs}s)"
    echo ""
    echo "  Images pushed to:"
    for target in "${targets[@]}"; do
        case "$target" in
            app)        echo "    ${REGISTRY}:latest${VERSION_TAG:+ / ${REGISTRY}:${VERSION_TAG}}" ;;
            playwright) echo "    ${REGISTRY}:playwright (amd64 only)" ;;
        esac
    done
    echo "=========================================="
}

main "$@"
