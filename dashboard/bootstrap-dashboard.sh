#!/bin/bash
set -Eeuo pipefail

LOG_FILE="/var/log/cloudmart-dashboard-bootstrap.log"
mkdir -p "$(dirname "$LOG_FILE")"

# Keep output visible to SSM/GitHub Actions AND save a local log.
exec > >(tee -a "$LOG_FILE") 2>&1

on_error() {
  rc=$?
  echo ""
  echo "=========================================================="
  echo "CLOUDMART DASHBOARD BOOTSTRAP FAILED"
  echo "Exit code : $rc"
  echo "Line      : ${BASH_LINENO[0]:-unknown}"
  echo "Command   : ${BASH_COMMAND:-unknown}"
  echo "=========================================================="

  echo ""
  echo "--- systemd service status ---"
  systemctl status cloudmart-dashboard.service --no-pager -l || true

  echo ""
  echo "--- systemd journal ---"
  journalctl -u cloudmart-dashboard.service --no-pager -n 100 || true

  echo ""
  echo "--- nginx status ---"
  systemctl status nginx.service --no-pager -l || true

  echo ""
  echo "--- bootstrap log tail ---"
  tail -n 100 "$LOG_FILE" || true

  exit "$rc"
}
trap on_error ERR

log() {
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
}

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

DASHBOARD_DIR="/opt/cloudmart/dashboard"
RELEASE_DIR="/tmp/cloudmart-dashboard-release-bootstrap"
ENV_FILE="/etc/cloudmart/dashboard.env"
SERVICE_FILE="/etc/systemd/system/cloudmart-dashboard.service"
NGINX_FILE="/etc/nginx/conf.d/cloudmart-dashboard.conf"

log "=========================================================="
log "Starting CloudMart dashboard bootstrap"
log "Environment      : $ENVIRONMENT"
log "AWS Region       : $AWS_REGION"
log "Artifact Bucket  : $ARTIFACT_BUCKET"
log "Report Bucket    : $REPORT_BUCKET"
log "Dashboard Port   : $DASHBOARD_PORT"
log "Nginx Port       : $NGINX_PORT"
log "=========================================================="

# ==========================================================
# REQUIRED DIRECTORIES
# ==========================================================

install -d -m 0755 "$DASHBOARD_DIR"
install -d -m 0755 /var/log/cloudmart
install -d -m 0755 /etc/cloudmart

# ==========================================================
# SYSTEM PACKAGES
# ==========================================================

log "Checking required system packages"

if ! command -v python3 >/dev/null 2>&1 \
   || ! command -v pip3 >/dev/null 2>&1 \
   || ! command -v nginx >/dev/null 2>&1 \
   || ! command -v curl >/dev/null 2>&1 \
   || ! command -v gcc >/dev/null 2>&1; then

  log "Installing missing system packages"

  dnf clean all
  dnf makecache
  dnf install -y \
    python3 \
    python3-pip \
    python3-devel \
    gcc \
    nginx \
    curl
else
  log "Required system packages are already installed"
fi

# ==========================================================
# AWS CLI
# ==========================================================

if ! command -v aws >/dev/null 2>&1; then
  log "AWS CLI not found. Installing awscli."
  dnf install -y awscli
fi

log "AWS CLI: $(aws --version 2>&1)"
log "Python: $(python3 --version 2>&1)"

# ==========================================================
# WAIT FOR EC2 INSTANCE PROFILE
# ==========================================================

log "Waiting for EC2 IAM credentials"

IAM_READY="false"

for attempt in $(seq 1 24); do
  if aws sts get-caller-identity --region "$AWS_REGION" >/dev/null 2>&1; then
    IAM_READY="true"
    log "EC2 IAM credentials are available"
    break
  fi

  log "Waiting for IAM credentials... attempt ${attempt}/24"
  sleep 5
done

if [ "$IAM_READY" != "true" ]; then
  echo "ERROR: EC2 IAM credentials were not available"
  exit 1
fi

# ==========================================================
# DOWNLOAD DASHBOARD RELEASE
# ==========================================================

log "Downloading dashboard release from S3"

rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"

DOWNLOAD_OK="false"

for attempt in $(seq 1 5); do
  log "S3 dashboard download attempt ${attempt}/5"

  if aws s3 sync \
      "s3://${ARTIFACT_BUCKET}/dashboard/" \
      "$RELEASE_DIR/" \
      --region "$AWS_REGION" \
      --delete \
      --exclude ".venv/*" \
      --exclude "__pycache__/*" \
      --exclude "*.pyc"; then

    DOWNLOAD_OK="true"
    break
  fi

  if [ "$attempt" -lt 5 ]; then
    sleep 10
  fi
done

if [ "$DOWNLOAD_OK" != "true" ]; then
  echo "ERROR: Dashboard artifact download failed"
  exit 1
fi

# ==========================================================
# VALIDATE RELEASE
# ==========================================================

log "Validating dashboard release"

test -s "$RELEASE_DIR/app.py"
test -s "$RELEASE_DIR/requirements.txt"
test -s "$RELEASE_DIR/templates/index.html"

log "Dashboard release validation passed"

# ==========================================================
# STOP EXISTING SERVICE
# ==========================================================

log "Stopping existing dashboard service if present"

systemctl stop cloudmart-dashboard.service || true

# ==========================================================
# INSTALL APPLICATION FILES
# ==========================================================

log "Installing dashboard application files"

find "$DASHBOARD_DIR" \
  -mindepth 1 \
  -maxdepth 1 \
  ! -name ".venv" \
  -exec rm -rf {} +

cp -a "$RELEASE_DIR/." "$DASHBOARD_DIR/"
rm -rf "$RELEASE_DIR"

# ==========================================================
# PYTHON VIRTUAL ENVIRONMENT
# ==========================================================

log "Preparing Python virtual environment"

if [ ! -x "$DASHBOARD_DIR/.venv/bin/python" ]; then
  log "Creating Python virtual environment"
  rm -rf "$DASHBOARD_DIR/.venv"
  python3 -m venv "$DASHBOARD_DIR/.venv"
fi

# ==========================================================
# PYTHON DEPENDENCIES
# ==========================================================

log "Upgrading Python packaging tools"

"$DASHBOARD_DIR/.venv/bin/python" -m pip install --upgrade \
  pip \
  setuptools \
  wheel

if [ -s "$DASHBOARD_DIR/requirements.txt" ]; then
  log "Installing/updating dashboard requirements"
  "$DASHBOARD_DIR/.venv/bin/python" -m pip install --upgrade \
    -r "$DASHBOARD_DIR/requirements.txt"
else
  log "requirements.txt is empty; installing required runtime packages"
  "$DASHBOARD_DIR/.venv/bin/python" -m pip install --upgrade \
    flask \
    pymysql \
    boto3 \
    gunicorn
fi

# Make absolutely sure Gunicorn exists.
if [ ! -x "$DASHBOARD_DIR/.venv/bin/gunicorn" ]; then
  log "Gunicorn missing after requirements installation; installing explicitly"
  "$DASHBOARD_DIR/.venv/bin/python" -m pip install --upgrade gunicorn
fi

log "Python dependency installation completed"

# ==========================================================
# FLASK IMPORT CHECK
# ==========================================================

cd "$DASHBOARD_DIR"

log "Testing Flask application import"

"$DASHBOARD_DIR/.venv/bin/python" -c \
  'import app; assert app.app is not None; print("CloudMart Flask import OK")'

# ==========================================================
# FLASK SECRET
# ==========================================================

log "Preparing dashboard environment file"

if [ ! -s "$ENV_FILE" ]; then
  umask 077
  SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  printf 'FLASK_SECRET_KEY=%s\n' "$SECRET" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

# ==========================================================
# SYSTEMD SERVICE
# ==========================================================

log "Writing systemd service: $SERVICE_FILE"

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

systemd-analyze verify "$SERVICE_FILE"

# ==========================================================
# NGINX
# ==========================================================

log "Writing Nginx configuration: $NGINX_FILE"

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
        proxy_connect_timeout 120s;
        proxy_send_timeout 120s;
    }
}
NGINX

nginx -t

# ==========================================================
# ENABLE + START SERVICES
# ==========================================================

log "Reloading systemd"
systemctl daemon-reload

log "Enabling services"
systemctl enable nginx.service
systemctl enable cloudmart-dashboard.service

log "Starting Nginx"
systemctl restart nginx.service

log "Starting CloudMart dashboard"
systemctl restart cloudmart-dashboard.service

# ==========================================================
# WAIT FOR SERVICES
# ==========================================================

log "Waiting for CloudMart dashboard service"

DASHBOARD_READY="false"

for attempt in $(seq 1 30); do
  if systemctl is-active --quiet cloudmart-dashboard.service; then
    DASHBOARD_READY="true"
    break
  fi

  log "Dashboard service not ready... attempt ${attempt}/30"
  sleep 2
done

if [ "$DASHBOARD_READY" != "true" ]; then
  echo "ERROR: CloudMart dashboard service failed to become active"
  systemctl status cloudmart-dashboard.service --no-pager -l || true
  journalctl -u cloudmart-dashboard.service --no-pager -n 100 || true
  exit 1
fi

if ! systemctl is-active --quiet nginx.service; then
  echo "ERROR: Nginx service is not active"
  systemctl status nginx.service --no-pager -l || true
  exit 1
fi

# ==========================================================
# HEALTH CHECKS
# ==========================================================

log "Checking Flask health endpoint"

FLASK_READY="false"

for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 10 \
      "http://127.0.0.1:${DASHBOARD_PORT}/health" >/dev/null 2>&1; then
    FLASK_READY="true"
    break
  fi

  log "Flask health endpoint not ready... attempt ${attempt}/30"
  sleep 2
done

if [ "$FLASK_READY" != "true" ]; then
  echo "ERROR: Flask health check failed"
  systemctl status cloudmart-dashboard.service --no-pager -l || true
  journalctl -u cloudmart-dashboard.service --no-pager -n 100 || true
  exit 1
fi

log "Checking Nginx health endpoint"

NGINX_READY="false"

for attempt in $(seq 1 15); do
  if curl --fail --silent --show-error --max-time 10 \
      "http://127.0.0.1:${NGINX_PORT}/health" >/dev/null 2>&1; then
    NGINX_READY="true"
    break
  fi

  log "Nginx health endpoint not ready... attempt ${attempt}/15"
  sleep 2
done

if [ "$NGINX_READY" != "true" ]; then
  echo "ERROR: Nginx health check failed"
  systemctl status nginx.service --no-pager -l || true
  nginx -t || true
  exit 1
fi

# ==========================================================
# FINAL VERIFICATION
# ==========================================================

log "=========================================================="
log "CloudMart dashboard bootstrap completed successfully"
log "Dashboard service: $(systemctl is-active cloudmart-dashboard.service)"
log "Nginx service:     $(systemctl is-active nginx.service)"
log "=========================================================="

exit 0
