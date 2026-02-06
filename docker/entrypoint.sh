#!/bin/bash
set -e

echo "=========================================="
echo "  WebStock App Container Starting"
echo "=========================================="

# Create directories
mkdir -p /app/logs /app/data /var/run/redis
chmod 777 /app/logs
chown -R appuser:appgroup /app/data

# ============ Configure Nginx ============
echo "[1/4] Configuring Nginx..."

cat > /etc/nginx/nginx.conf << 'NGINXEOF'
user www-data;
worker_processes auto;
pid /run/nginx.pid;
error_log /app/logs/nginx-error.log warn;

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
                    '"$http_user_agent" rt=$request_time';

    access_log /app/logs/nginx-access.log main;

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

    upstream backend {
        server 127.0.0.1:8000;
        keepalive 32;
    }

    server {
        listen 80 default_server;
        server_name _;
        root /var/www/html;
        index index.html;

        # API endpoints
        location /api/ {
            limit_req zone=api_limit burst=20 nodelay;

            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header Connection "";
            proxy_connect_timeout 60s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;
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
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_cache off;
            add_header X-Accel-Buffering "no";
            proxy_read_timeout 86400s;
            proxy_send_timeout 86400s;
            chunked_transfer_encoding on;
        }

        # SSE endpoints (chat streaming)
        location /api/v1/chat/ {
            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_cache off;
            add_header X-Accel-Buffering "no";
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
            chunked_transfer_encoding on;
        }

        # Frontend SPA
        location / {
            try_files $uri $uri/ /index.html;
        }

        # Static asset caching
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
            expires 1y;
            add_header Cache-Control "public, immutable";
            access_log off;
        }

        # Health check
        location /health {
            access_log off;
            return 200 "OK";
            add_header Content-Type text/plain;
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

# ============ Wait for PostgreSQL ============
echo "[2/4] Waiting for PostgreSQL..."

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
echo "[3/4] Running database migrations..."

cd /app/backend
if alembic upgrade head; then
    echo "  -> Database migrations completed successfully"
else
    echo "  -> WARNING: Database migrations failed, continuing anyway"
    echo "  -> You may need to run migrations manually: alembic upgrade head"
fi
cd /app

# ============ Ready ============
echo "[4/4] Starting services..."
echo ""
echo "=========================================="
echo "  Redis      : 127.0.0.1:6379 (internal)"
echo "  Backend    : 127.0.0.1:8000 (internal)"
echo "  Nginx      : 0.0.0.0:80 (exposed)"
echo "  PostgreSQL : ${DB_HOST}:${DB_PORT} (external)"
echo "=========================================="
echo ""

exec "$@"
