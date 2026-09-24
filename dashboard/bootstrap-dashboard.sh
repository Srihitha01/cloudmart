#!/bin/bash
set -Eeuo pipefail

LOG_FILE=/var/log/cloudmart-dashboard-bootstrap.log
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE" | logger -t cloudmart-dashboard-bootstrap -s 2>/dev/console) 2>&1

: "${AWS_REGION:?AWS_REGION is required}"
: "${ENVIRONMENT:?ENVIRONMENT is required}"
: "${ARTIFACT_BUCKET:?ARTIFACT_BUCKET is required}"
: "${REPORT_BUCKET:?REPORT_BUCKET is required}"
: "${DASHBOARD_PORT:?DASHBOARD_PORT is required}"
: "${NGINX_PORT:?NGINX_PORT is required}"

DB_NAME_PARAMETER="/cloudmart/${ENVIRONMENT}/db/name"
DB_ENDPOINT_PARAMETER="/cloudmart/${ENVIRONMENT}/db/endpoint"
DB_PORT_PARAMETER="/cloudmart/${ENVIRONMENT}/db/port"
DB_USERNAME_PARAMETER="/cloudmart/${ENVIRONMENT}/db/username"
DB_PASSWORD_PARAMETER="/cloudmart/${ENVIRONMENT}/db/password"
AUTH_TOKEN_PARAMETER="/cloudmart/${ENVIRONMENT}/auth/token"
REPORT_BUCKET_PARAMETER="/cloudmart/${ENVIRONMENT}/s3/report-bucket"

DASHBOARD_DIR=/opt/cloudmart/dashboard
ENV_FILE=/etc/cloudmart/dashboard.env
SERVICE_FILE=/etc/systemd/system/cloudmart-dashboard.service
NGINX_FILE=/etc/nginx/conf.d/cloudmart-dashboard.conf

log() {
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
}

log "Starting CloudMart dashboard bootstrap"

log "Installing OS packages"
dnf clean all
dnf makecache
dnf install -y python3 python3-pip python3-devel gcc nginx curl

if ! command -v aws >/dev/null 2>&1; then
  log "AWS CLI not found; installing awscli package"
  dnf install -y awscli
fi

install -d -m 0755 "$DASHBOARD_DIR"
install -d -m 0755 /var/log/cloudmart
install -d -m 0755 /etc/cloudmart

log "Waiting for EC2 IAM credentials"
for attempt in {1..24}; do
  if aws sts get-caller-identity --region "$AWS_REGION" >/dev/null 2>&1; then
    log "AWS credentials are available"
    break
  fi
  if [ "$attempt" -eq 24 ]; then
    log "ERROR: EC2 IAM credentials were not available after waiting"
    exit 1
  fi
  sleep 5
done

log "Downloading dashboard artifact from S3"
for attempt in {1..5}; do
  if aws s3 sync "s3://${ARTIFACT_BUCKET}/dashboard/" "$DASHBOARD_DIR/" --region "$AWS_REGION" --delete; then
    break
  fi
  if [ "$attempt" -eq 5 ]; then
    log "ERROR: dashboard artifact download failed"
    exit 1
  fi
  sleep 10
done

test -s "$DASHBOARD_DIR/app.py"
log "Dashboard source is present"

log "Creating Python virtual environment"
rm -rf "$DASHBOARD_DIR/.venv"
python3 -m venv "$DASHBOARD_DIR/.venv"

"$DASHBOARD_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel

if [ -s "$DASHBOARD_DIR/requirements.txt" ]; then
  "$DASHBOARD_DIR/.venv/bin/python" -m pip install -r "$DASHBOARD_DIR/requirements.txt"
else
  "$DASHBOARD_DIR/.venv/bin/python" -m pip install flask pymysql boto3 gunicorn
fi

log "Validating Flask application import"
"$DASHBOARD_DIR/.venv/bin/python" -c 'import app; assert app.app is not None; print("CloudMart Flask import OK")'

log "Creating persistent Flask session secret"
if [ ! -s "$ENV_FILE" ]; then
  umask 077
  SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  printf 'FLASK_SECRET_KEY=%s\n' "$SECRET" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

log "Writing systemd service"
cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=CloudMart Flask Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DASHBOARD_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
Environment=AWS_REGION=$AWS_REGION
Environment=DB_ENDPOINT_PARAMETER=$DB_ENDPOINT_PARAMETER
Environment=DB_PORT_PARAMETER=$DB_PORT_PARAMETER
Environment=DB_NAME_PARAMETER=$DB_NAME_PARAMETER
Environment=DB_USERNAME_PARAMETER=$DB_USERNAME_PARAMETER
Environment=DB_PASSWORD_PARAMETER=$DB_PASSWORD_PARAMETER
Environment=AUTH_TOKEN_PARAMETER=$AUTH_TOKEN_PARAMETER
Environment=REPORT_BUCKET=$REPORT_BUCKET
Environment=REPORT_BUCKET_PARAMETER=$REPORT_BUCKET_PARAMETER
Environment=ENVIRONMENT=$ENVIRONMENT
ExecStart=$DASHBOARD_DIR/.venv/bin/gunicorn --workers 2 --threads 4 --timeout 120 --access-logfile - --error-logfile - --bind 127.0.0.1:$DASHBOARD_PORT app:app
Restart=always
RestartSec=5
StartLimitIntervalSec=0
User=root

[Install]
WantedBy=multi-user.target
SERVICE

log "Writing Nginx reverse proxy"
cat > "$NGINX_FILE" <<NGINX
server {
    listen $NGINX_PORT;
    listen [::]:$NGINX_PORT;
    server_name _;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:$DASHBOARD_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
NGINX

log "Validating Nginx configuration"
nginx -t

log "Enabling and restarting services"
systemctl daemon-reload
systemctl enable nginx
systemctl enable cloudmart-dashboard
systemctl restart nginx
systemctl restart cloudmart-dashboard

sleep 5

systemctl is-active --quiet nginx
systemctl is-active --quiet cloudmart-dashboard

log "Checking application health on Gunicorn"
curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${DASHBOARD_PORT}/health" >/dev/null

log "Checking application health through Nginx"
curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${NGINX_PORT}/health" >/dev/null

log "CloudMart dashboard bootstrap completed successfully"
