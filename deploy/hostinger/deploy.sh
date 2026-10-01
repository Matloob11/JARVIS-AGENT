#!/usr/bin/env bash
# Deploy / refresh JARVIS on Hostinger without touching other apps.
# Run on VPS from /opt/jarvis-agent after syncing files.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOSTINGER="$ROOT/deploy/hostinger"
DOMAIN="jarvis.srv1802669.hstgr.cloud"

cd "$ROOT"

echo "[1/5] Docker compose rebuild (jarvis project only)..."
docker compose -p jarvis -f "$HOSTINGER/docker-compose.yml" --env-file "$HOSTINGER/.env" up -d --build

echo "[2/5] Install nginx site (new file only)..."
cp "$HOSTINGER/jarvis.conf" /etc/nginx/conf.d/jarvis.conf

# If certs do not exist yet, install HTTP-only bootstrap so certbot can run
if [[ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
  cat > /etc/nginx/conf.d/jarvis.conf <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }
    location / {
        return 200 'jarvis-acme-ready';
        add_header Content-Type text/plain;
    }
}
EOF
  mkdir -p /var/www/html
  nginx -t && systemctl reload nginx
  echo "[3/5] Requesting Let's Encrypt cert for $DOMAIN..."
  certbot certonly --webroot -w /var/www/html -d "$DOMAIN" --non-interactive --agree-tos \
    --register-unsafely-without-email || certbot certonly --nginx -d "$DOMAIN" --non-interactive --agree-tos \
    --register-unsafely-without-email || true
  # Restore full HTTPS config
  cp "$HOSTINGER/jarvis.conf" /etc/nginx/conf.d/jarvis.conf
fi

echo "[4/5] Nginx test + reload..."
# Ensure ssl helper files exist (certbot installs these)
if [[ ! -f /etc/letsencrypt/options-ssl-nginx.conf ]]; then
  # minimal fallback if certbot helpers missing
  cat > /etc/letsencrypt/options-ssl-nginx.conf <<'SSLOPT'
ssl_session_cache shared:le_nginx_SSL:10m;
ssl_session_timeout 1440m;
ssl_protocols TLSv1.2 TLSv1.3;
ssl_prefer_server_ciphers off;
SSLOPT
fi
if [[ ! -f /etc/letsencrypt/ssl-dhparams.pem ]]; then
  openssl dhparam -out /etc/letsencrypt/ssl-dhparams.pem 2048
fi

nginx -t
systemctl reload nginx

echo "[5/5] Health checks..."
curl -fsS "http://127.0.0.1:5101/health" | head -c 200; echo
curl -fsS -o /dev/null -w "frontend_local:%{http_code}\n" "http://127.0.0.1:5100/" || true
curl -fsS -o /dev/null -w "https:%{http_code}\n" "https://$DOMAIN/" || true
echo "Other nginx sites (should be unchanged):"
ls -la /etc/nginx/conf.d/ | head -40
ls -la /etc/nginx/sites-enabled/ 2>/dev/null | head -40 || true
echo "DONE"
