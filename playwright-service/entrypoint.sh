#!/bin/bash
set -e

echo "Playwright Extraction Service starting..."
echo "HEADLESS=$HEADLESS"

if [ "$HEADLESS" = "false" ]; then
    echo "Starting in headful mode with Xvfb..."
    exec xvfb-run --auto-servernum --server-args="-screen 0 1280x720x24" \
        supervisord -c /app/supervisord.conf
else
    echo "Starting in headless mode..."
    exec supervisord -c /app/supervisord.conf
fi
