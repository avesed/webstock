#!/bin/bash
# Build WebStock All-in-One Container

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=========================================="
echo "  Building WebStock All-in-One Container"
echo "=========================================="

# Build the image
docker build \
    -f Dockerfile.allinone \
    -t webstock:allinone \
    -t webstock:latest \
    .

echo ""
echo "Build complete!"
echo ""
echo "To run the container:"
echo "  ./scripts/run-allinone.sh"
echo ""
echo "Or manually:"
echo "  docker run -d -p 80:80 --name webstock webstock:allinone"
