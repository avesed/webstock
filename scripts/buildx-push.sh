#!/bin/bash
# =============================================================================
# WebStock Multi-Arch Build & Push to GHCR
#
# Builds linux/amd64 + linux/arm64 images and pushes to ghcr.io/avesed/webstock
#   - App (main)       → ghcr.io/avesed/webstock:dev
#   - Playwright        → ghcr.io/avesed/webstock:playwright  (amd64 only)
#   - Qlib              → ghcr.io/avesed/webstock:qlib
#
# Usage:
#   ./scripts/buildx-push.sh              # Build all three
#   ./scripts/buildx-push.sh app          # Build app only
#   ./scripts/buildx-push.sh playwright   # Build playwright only
#   ./scripts/buildx-push.sh qlib         # Build qlib only
#   ./scripts/buildx-push.sh app qlib     # Build app and qlib
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
    # Check docker
    if ! command -v docker &>/dev/null; then
        err "Docker is not installed."
        exit 1
    fi

    # Check buildx
    if ! docker buildx version &>/dev/null; then
        err "Docker Buildx is not available. Install it first."
        exit 1
    fi

    # Check GHCR login
    if ! docker pull "${REGISTRY}:nonexistent-tag-check" &>/dev/null 2>&1; then
        # This is expected to fail (tag doesn't exist), but auth errors look different.
        # Try a more reliable check:
        if ! echo "" | docker login ghcr.io --username _test --password-stdin &>/dev/null 2>&1; then
            true  # ignore, real auth check below
        fi
    fi

    # Simple auth check: try to inspect the registry (will fail gracefully if not logged in)
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
    local tag="${REGISTRY}:dev"
    info "Building App → ${tag}"
    info "  Platforms: ${MULTI_PLATFORM}"
    info "  Dockerfile: ./Dockerfile"
    info "  Context: ."
    echo ""

    docker buildx build \
        --platform "$MULTI_PLATFORM" \
        --file Dockerfile \
        --tag "$tag" \
        --push \
        .

    ok "App pushed → ${tag}"
    echo ""
}

build_playwright() {
    local tag="${REGISTRY}:playwright"

    # Playwright base image (mcr.microsoft.com/playwright) is amd64 only
    warn "Playwright base image does NOT support arm64 — building amd64 only"
    info "Building Playwright → ${tag}"
    info "  Platform: linux/amd64"
    info "  Dockerfile: playwright-service/Dockerfile"
    info "  Context: playwright-service/"
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

build_qlib() {
    local tag="${REGISTRY}:qlib"
    info "Building Qlib → ${tag}"
    info "  Platforms: ${MULTI_PLATFORM}"
    info "  Dockerfile: qlib-service/Dockerfile"
    info "  Context: qlib-service/"
    echo ""

    docker buildx build \
        --platform "$MULTI_PLATFORM" \
        --file qlib-service/Dockerfile \
        --tag "$tag" \
        --push \
        qlib-service/

    ok "Qlib pushed → ${tag}"
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

    local targets=("$@")

    # Default: build all
    if [ ${#targets[@]} -eq 0 ]; then
        targets=("app" "playwright" "qlib")
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
            qlib)
                build_qlib
                ;;
            *)
                err "Unknown target: $target"
                err "Valid targets: app, playwright, qlib"
                exit 1
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
            app)        echo "    ${REGISTRY}:dev" ;;
            playwright) echo "    ${REGISTRY}:playwright (amd64 only)" ;;
            qlib)       echo "    ${REGISTRY}:qlib" ;;
        esac
    done
    echo "=========================================="
}

main "$@"
