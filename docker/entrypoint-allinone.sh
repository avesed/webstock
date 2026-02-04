#!/bin/bash
set -e

echo "=========================================="
echo "  WebStock All-in-One Container Starting"
echo "=========================================="

# Create directories with proper permissions
mkdir -p /app/logs /app/data /var/run/postgresql /var/run/redis
chmod 777 /app/logs
chown -R webstock:webstock /app/data
chown -R postgres:postgres /var/run/postgresql
chown -R redis:redis /var/run/redis

# ============ Initialize PostgreSQL ============
echo "[1/5] Initializing PostgreSQL..."

PG_DATA="/var/lib/postgresql/14/main"

# Check if database cluster exists, if not initialize
if [ ! -f "$PG_DATA/PG_VERSION" ]; then
    echo "  -> Creating new database cluster..."
    mkdir -p "$PG_DATA"
    chown postgres:postgres "$PG_DATA"
    chmod 700 "$PG_DATA"
    su - postgres -c "/usr/lib/postgresql/14/bin/initdb -D $PG_DATA"
fi

# Configure PostgreSQL
echo "  -> Configuring PostgreSQL..."
cat > "$PG_DATA/postgresql.conf" << EOF
listen_addresses = 'localhost'
port = 5432
max_connections = 100
shared_buffers = 128MB
effective_cache_size = 256MB
work_mem = 4MB
maintenance_work_mem = 64MB
dynamic_shared_memory_type = posix
logging_collector = off
log_destination = 'stderr'
EOF

cat > "$PG_DATA/pg_hba.conf" << EOF
local   all             postgres                                peer
local   all             all                                     md5
host    all             all             127.0.0.1/32            md5
host    all             all             ::1/128                 md5
EOF

chown postgres:postgres "$PG_DATA/postgresql.conf" "$PG_DATA/pg_hba.conf"

# Start PostgreSQL temporarily
echo "  -> Starting PostgreSQL for initialization..."
su - postgres -c "/usr/lib/postgresql/14/bin/pg_ctl -D $PG_DATA -w start" || {
    echo "  -> PostgreSQL start failed, checking logs..."
    cat "$PG_DATA/log/"* 2>/dev/null || true
    exit 1
}

# Wait for PostgreSQL
echo "  -> Waiting for PostgreSQL to be ready..."
for i in {1..30}; do
    if su - postgres -c "psql -c 'SELECT 1'" &>/dev/null; then
        echo "  -> PostgreSQL is ready"
        break
    fi
    sleep 1
done

# Create database and user
echo "  -> Creating database and user..."
su - postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='webstock'\" | grep -q 1" || \
    su - postgres -c "psql -c \"CREATE USER webstock WITH PASSWORD 'webstock';\""
su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='webstock'\" | grep -q 1" || \
    su - postgres -c "psql -c \"CREATE DATABASE webstock OWNER webstock;\""
su - postgres -c "psql -c \"GRANT ALL PRIVILEGES ON DATABASE webstock TO webstock;\""

# Run init-db.sql
if [ -f "/docker-entrypoint-initdb.d/init-db.sql" ]; then
    echo "  -> Running init-db.sql..."
    su - postgres -c "PGPASSWORD=webstock psql -U webstock -d webstock -f /docker-entrypoint-initdb.d/init-db.sql" || {
        echo "  -> Warning: init-db.sql had errors (may be OK if tables exist)"
    }
fi

# Stop PostgreSQL (supervisor will manage it)
echo "  -> Stopping PostgreSQL (will restart under supervisor)..."
su - postgres -c "/usr/lib/postgresql/14/bin/pg_ctl -D $PG_DATA -w stop"

# ============ Configure Redis ============
echo "[2/5] Redis configured"

# ============ Configure Nginx ============
echo "[3/5] Configuring Nginx..."

cat > /etc/nginx/nginx.conf << 'NGINXEOF'
user www-data;
worker_processes auto;
pid /run/nginx.pid;
error_log /dev/stderr;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent"';

    access_log /dev/stdout main;

    sendfile on;
    tcp_nopush on;
    keepalive_timeout 65;
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;

    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    upstream backend {
        server 127.0.0.1:8000;
        keepalive 32;
    }

    server {
        listen 80 default_server;
        server_name _;
        root /var/www/html;
        index index.html;

        location /api/ {
            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_connect_timeout 60s;
            proxy_send_timeout 60s;
            proxy_read_timeout 60s;
        }

        location ~ ^/api/v1/(analysis|sse)/ {
            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header Connection "";
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 86400s;
            chunked_transfer_encoding on;
            add_header X-Accel-Buffering "no";
        }

        location / {
            try_files $uri $uri/ /index.html;
        }

        location /health {
            access_log off;
            return 200 "OK";
            add_header Content-Type text/plain;
        }
    }
}
NGINXEOF

# ============ Set permissions ============
echo "[4/5] Setting permissions..."
chown -R webstock:webstock /app/backend /app/worker /app/data

# ============ Ready ============
echo "[5/5] Environment ready!"
echo ""
echo "=========================================="
echo "  Services will be started by supervisor"
echo "=========================================="
echo "  PostgreSQL : localhost:5432"
echo "  Redis      : localhost:6379"
echo "  Backend    : localhost:8000"
echo "  Nginx      : localhost:80"
echo "=========================================="
echo ""

exec "$@"
