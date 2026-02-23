#!/bin/bash
set -e

echo "=========================================="
echo "  WebStock App Container Starting"
echo "=========================================="

# Create directories
mkdir -p /app/logs /app/data
chmod 777 /app/logs
chown -R appuser:appgroup /app/data

# ============ Configure Nginx ============
echo "[1/5] Configuring Nginx..."

cat > /etc/nginx/nginx.conf << 'NGINXEOF'
user www-data;
worker_processes auto;
pid /run/nginx.pid;
error_log /dev/stderr warn;

events {
    worker_connections 1024;
    use epoll;
    multi_accept on;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" rt=$request_time rid=$request_id';

    access_log /dev/stdout main;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    client_max_body_size 10M;

    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml application/json application/javascript
               application/xml application/xml+rss text/javascript;

    # Security headers
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;
    limit_req_zone $binary_remote_addr zone=auth_limit:10m rate=5r/s;
    limit_req_zone $binary_remote_addr zone=chat_limit:10m rate=20r/s;
    limit_req_zone $binary_remote_addr zone=qlib_limit:10m rate=5r/s;

    upstream backend {
        server 127.0.0.1:8000;
        keepalive 32;
    }

    server {
        listen 80 default_server;
        server_name _;
        root /var/www/html;
        index index.html;

        # Internal API endpoints (service-to-service, longer timeout, no rate limit)
        # Restricted to Docker-internal networks only (defense-in-depth)
        location /api/v1/internal/ {
            allow 172.16.0.0/12;
            allow 10.0.0.0/8;
            allow 192.168.0.0/16;
            allow 127.0.0.0/8;
            deny all;

            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Request-ID $request_id;
            proxy_set_header Connection "";

            proxy_connect_timeout 30s;
            proxy_send_timeout 180s;
            proxy_read_timeout 180s;
        }

        # Qlib quantitative endpoints (longer timeout for factor computation)
        location /api/v1/qlib/ {
            limit_req zone=qlib_limit burst=10 nodelay;

            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Request-ID $request_id;
            proxy_set_header Connection "";

            proxy_connect_timeout 30s;
            proxy_send_timeout 120s;
            proxy_read_timeout 120s;
        }

        # Auth endpoints (stricter rate limit)
        location /api/v1/auth/ {
            limit_req zone=auth_limit burst=10 nodelay;

            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Request-ID $request_id;
            proxy_set_header Connection "";
        }

        # SSE endpoints (analysis streaming)
        location /api/v1/analysis/ {
            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Request-ID $request_id;
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_cache off;
            add_header X-Accel-Buffering "no" always;
            # Re-add security headers (Nginx drops parent add_header when child uses add_header)
            add_header X-Frame-Options "DENY" always;
            add_header X-Content-Type-Options "nosniff" always;
            add_header X-XSS-Protection "1; mode=block" always;
            add_header Referrer-Policy "strict-origin-when-cross-origin" always;
            proxy_read_timeout 86400s;
            proxy_send_timeout 86400s;
            chunked_transfer_encoding on;
        }

        # SSE endpoints (discussion streaming, 600s timeout)
        location /api/v1/discussion/ {
            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Request-ID $request_id;
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_cache off;
            add_header X-Accel-Buffering "no" always;
            # Re-add security headers (Nginx drops parent add_header when child uses add_header)
            add_header X-Frame-Options "DENY" always;
            add_header X-Content-Type-Options "nosniff" always;
            add_header X-XSS-Protection "1; mode=block" always;
            add_header Referrer-Policy "strict-origin-when-cross-origin" always;
            proxy_read_timeout 600s;
            proxy_send_timeout 600s;
            chunked_transfer_encoding on;
        }

        # SSE endpoints (chat streaming)
        location /api/v1/chat/ {
            limit_req zone=chat_limit burst=30 nodelay;

            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Request-ID $request_id;
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_cache off;
            add_header X-Accel-Buffering "no" always;
            # Re-add security headers (Nginx drops parent add_header when child uses add_header)
            add_header X-Frame-Options "DENY" always;
            add_header X-Content-Type-Options "nosniff" always;
            add_header X-XSS-Protection "1; mode=block" always;
            add_header Referrer-Policy "strict-origin-when-cross-origin" always;
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
            chunked_transfer_encoding on;
        }

        # API endpoints
        location /api/ {
            limit_req zone=api_limit burst=20 nodelay;

            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header X-Request-ID $request_id;
            proxy_set_header Connection "";
            proxy_connect_timeout 60s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;
        }

        # Frontend SPA - index.html must not be cached to ensure new deployments are picked up
        location / {
            try_files $uri $uri/ /index.html;
            add_header Cache-Control "no-cache" always;
            # Re-add security headers
            add_header X-Frame-Options "DENY" always;
            add_header X-Content-Type-Options "nosniff" always;
            add_header X-XSS-Protection "1; mode=block" always;
            add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        }

        # Service worker - must not be cached aggressively
        location = /sw.js {
            add_header Cache-Control "no-cache" always;
            add_header Service-Worker-Allowed "/" always;
            # Re-add security headers
            add_header X-Frame-Options "DENY" always;
            add_header X-Content-Type-Options "nosniff" always;
            add_header X-XSS-Protection "1; mode=block" always;
            add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        }

        # PWA manifest
        location = /manifest.webmanifest {
            add_header Cache-Control "no-cache" always;
            # Re-add security headers
            add_header X-Frame-Options "DENY" always;
            add_header X-Content-Type-Options "nosniff" always;
            add_header X-XSS-Protection "1; mode=block" always;
            add_header Referrer-Policy "strict-origin-when-cross-origin" always;
            default_type application/manifest+json;
        }

        # Static asset caching
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
            expires 1y;
            add_header Cache-Control "public, immutable" always;
            # Re-add security headers
            add_header X-Frame-Options "DENY" always;
            add_header X-Content-Type-Options "nosniff" always;
            add_header X-XSS-Protection "1; mode=block" always;
            add_header Referrer-Policy "strict-origin-when-cross-origin" always;
            access_log off;
        }

        # Health check
        location /health {
            access_log off;
            return 200 "OK";
            add_header Content-Type text/plain always;
            # Re-add security headers
            add_header X-Frame-Options "DENY" always;
            add_header X-Content-Type-Options "nosniff" always;
            add_header X-XSS-Protection "1; mode=block" always;
            add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        }

        # Deny hidden files
        location ~ /\. {
            deny all;
            access_log off;
            log_not_found off;
        }
    }
}
NGINXEOF

# ============ Wait for Redis ============
echo "[2/5] Waiting for Redis..."

REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
REDIS_HOST=$(echo "$REDIS_URL" | sed -n 's|redis://\([^:]*\):.*|\1|p')
REDIS_PORT=$(echo "$REDIS_URL" | sed -n 's|redis://[^:]*:\([0-9]*\).*|\1|p')
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"

for i in $(seq 1 30); do
    if python3 -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('$REDIS_HOST', $REDIS_PORT)); s.close()" 2>/dev/null; then
        echo "  -> Redis is reachable at ${REDIS_HOST}:${REDIS_PORT}"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  -> WARNING: Redis not reachable after 30s, starting anyway"
    fi
    sleep 1
done

# ============ Clean stale Celery Beat schedule ============
# Celery Beat's PersistentScheduler stores last_run_at per task in a shelve file.
# On container restart, it recreates entries for never-run tasks with last_run_at=now,
# which pushes weekly tasks (like build_stock_knowledge_base) perpetually into the
# future — they never become "due" if the container restarts within the week.
# Deleting the schedule file forces a clean start; frequent tasks (every minute)
# recover instantly, and weekly tasks will fire on the next matching crontab slot.
BEAT_SCHEDULE="/app/data/celerybeat-schedule"
if [ -f "$BEAT_SCHEDULE" ]; then
    rm -f "$BEAT_SCHEDULE"
    echo "  -> Removed stale celerybeat-schedule (weekly tasks will re-sync)"
else
    echo "  -> No celerybeat-schedule found (clean start)"
fi

# ============ Clean orphan Redis locks ============
# On container restart, any locks in Redis are orphans — the Celery
# workers that held them died with the old container.  Clean them up before
# starting new workers so tasks are not blocked for hours.
python3 -c "
import redis, os
try:
    r = redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), decode_responses=True)
    cleaned = []
    # Daily bar locks
    markets = ('cn', 'us', 'hk', 'metal')
    for m in markets:
        for suffix in ('lock', 'progress', 'queued'):
            key = f'kb:daily_bars:{m}:{suffix}'
            if r.delete(key):
                cleaned.append(key)
    # Stock profile knowledge base locks (global + per-market)
    for key in ('stock_profile:build_lock', 'stock_profile:sync_lock', 'kb:stock_profile:progress'):
        if r.delete(key):
            cleaned.append(key)
    for m in ('cn', 'us', 'hk'):
        for suffix in ('lock', 'progress', 'queued'):
            key = f'kb:stock_profile:{m}:{suffix}'
            if r.delete(key):
                cleaned.append(key)
    if cleaned:
        print(f'  -> Cleaned {len(cleaned)} orphan key(s): {cleaned}')
    else:
        print('  -> No orphan locks found')
except Exception as e:
    print(f'  -> WARNING: Could not clean orphan locks: {e}')
"

# ============ Wait for PostgreSQL ============
echo "[3/5] Waiting for PostgreSQL..."

DB_HOST=$(echo "$DATABASE_URL" | sed -n 's/.*@\([^:\/]*\).*/\1/p')
DB_PORT=5432

for i in $(seq 1 60); do
    if python3 -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('$DB_HOST', $DB_PORT)); s.close()" 2>/dev/null; then
        echo "  -> PostgreSQL is reachable at ${DB_HOST}:${DB_PORT}"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "  -> WARNING: PostgreSQL not reachable after 60s, starting anyway"
    fi
    sleep 1
done

# ============ Run Database Migrations ============
echo "[4/5] Running database migrations..."

cd /app/backend
if alembic upgrade head; then
    echo "  -> Database migrations completed successfully"
else
    echo "  -> WARNING: Database migrations failed, continuing anyway"
    echo "  -> You may need to run migrations manually: alembic upgrade head"
fi
cd /app

# ============ Ready ============
echo "[5/5] Starting services..."
echo ""
echo "=========================================="
echo "  Redis        : ${REDIS_HOST}:${REDIS_PORT} (external container)"
echo "  Backend      : 127.0.0.1:8000 (internal)"
echo "  Nginx        : 0.0.0.0:80 (exposed)"
echo "  PostgreSQL   : ${DB_HOST}:${DB_PORT} (external container)"
echo "  Qlib Service : qlib-service:8001 (external container)"
echo "=========================================="
echo ""

exec "$@"
