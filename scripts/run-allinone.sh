#!/bin/bash
# Run WebStock All-in-One Container

set -e

CONTAINER_NAME="webstock"
IMAGE_NAME="webstock:allinone"
DATA_VOLUME="webstock_data"
LOGS_VOLUME="webstock_logs"
PG_VOLUME="webstock_postgres"

# Parse arguments
DETACH="-d"
FORCE_RECREATE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -i|--interactive)
            DETACH=""
            shift
            ;;
        -f|--force)
            FORCE_RECREATE=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  -i, --interactive  Run in foreground (see logs)"
            echo "  -f, --force        Force recreate container"
            echo "  -h, --help         Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "=========================================="
echo "  Starting WebStock All-in-One Container"
echo "=========================================="

# Stop and remove existing container if force recreate
if [ "$FORCE_RECREATE" = true ]; then
    echo "Removing existing container..."
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
fi

# Check if container already exists
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    # Container exists, check if running
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "Container '$CONTAINER_NAME' is already running."
        echo ""
        echo "Access the application at: http://localhost"
        echo ""
        echo "To stop: docker stop $CONTAINER_NAME"
        echo "To view logs: docker logs -f $CONTAINER_NAME"
        exit 0
    else
        echo "Starting existing container..."
        docker start "$CONTAINER_NAME"
    fi
else
    # Create and run new container
    echo "Creating new container..."

    # Create volumes if they don't exist
    docker volume create "$DATA_VOLUME" 2>/dev/null || true
    docker volume create "$LOGS_VOLUME" 2>/dev/null || true
    docker volume create "$PG_VOLUME" 2>/dev/null || true

    docker run $DETACH \
        --name "$CONTAINER_NAME" \
        -p 80:80 \
        -v "$PG_VOLUME":/var/lib/postgresql/14/main \
        -v "$DATA_VOLUME":/app/data \
        -v "$LOGS_VOLUME":/app/logs \
        -e JWT_SECRET_KEY="${JWT_SECRET_KEY:-$(openssl rand -hex 32)}" \
        -e OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
        -e FINNHUB_API_KEY="${FINNHUB_API_KEY:-}" \
        --restart unless-stopped \
        "$IMAGE_NAME"
fi

if [ -n "$DETACH" ]; then
    echo ""
    echo "Container started in background."
    echo ""
    echo "Waiting for services to be ready..."

    # Wait for health check
    for i in {1..60}; do
        if curl -sf http://localhost/health > /dev/null 2>&1; then
            echo ""
            echo "=========================================="
            echo "  WebStock is ready!"
            echo "=========================================="
            echo ""
            echo "  Access:     http://localhost"
            echo "  API Docs:   http://localhost/api/v1/docs"
            echo "  Health:     http://localhost/api/v1/health"
            echo ""
            echo "  View logs:  docker logs -f $CONTAINER_NAME"
            echo "  Stop:       docker stop $CONTAINER_NAME"
            echo "  Remove:     docker rm -f $CONTAINER_NAME"
            echo ""
            exit 0
        fi
        echo -n "."
        sleep 2
    done

    echo ""
    echo "Warning: Health check timed out. Check logs:"
    echo "  docker logs $CONTAINER_NAME"
fi
