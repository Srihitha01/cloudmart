#!/bin/bash

set -Eeuo pipefail

# ==========================================================
# LOGGING
# ==========================================================

LOG_FILE="/var/log/cloudmart-dashboard-bootstrap.log"

mkdir -p "$(dirname "$LOG_FILE")"

exec > >(tee -a "$LOG_FILE" | logger -t cloudmart-dashboard-bootstrap -s 2>/dev/console) 2>&1


# ==========================================================
# REQUIRED ENVIRONMENT VARIABLES
# ==========================================================

: "${AWS_REGION:?AWS_REGION is required}"
: "${ENVIRONMENT:?ENVIRONMENT is required}"
: "${ARTIFACT_BUCKET:?ARTIFACT_BUCKET is required}"
: "${REPORT_BUCKET:?REPORT_BUCKET is required}"
: "${DASHBOARD_PORT:?DASHBOARD_PORT is required}"
: "${NGINX_PORT:?NGINX_PORT is required}"


# ==========================================================
# SSM PARAMETERS
# ==========================================================

DB_NAME_PARAMETER="/cloudmart/${ENVIRONMENT}/db/name"
DB_ENDPOINT_PARAMETER="/cloudmart/${ENVIRONMENT}/db/endpoint"
DB_PORT_PARAMETER="/cloudmart/${ENVIRONMENT}/db/port"
DB_USERNAME_PARAMETER="/cloudmart/${ENVIRONMENT}/db/username"
DB_PASSWORD_PARAMETER="/cloudmart/${ENVIRONMENT}/db/password"
AUTH_TOKEN_PARAMETER="/cloudmart/${ENVIRONMENT}/auth/token"
REPORT_BUCKET_PARAMETER="/cloudmart/${ENVIRONMENT}/s3/report-bucket"


# ==========================================================
# PATHS
# ==========================================================

DASHBOARD_DIR="/opt/cloudmart/dashboard"
RELEASE_DIR="/tmp/cloudmart-dashboard-release-bootstrap"
ENV_FILE="/etc/cloudmart/dashboard.env"
SERVICE_FILE="/etc/systemd/system/cloudmart-dashboard.service"
NGINX_FILE="/etc/nginx/conf.d/cloudmart-dashboard.conf"


# ==========================================================
# LOG FUNCTION
# ==========================================================

log() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
}


# ==========================================================
# START
# ==========================================================

log "=========================================================="
log "Starting CloudMart dashboard bootstrap"
log "Environment: ${ENVIRONMENT}"
log "AWS Region: ${AWS_REGION}"
log "Artifact Bucket: ${ARTIFACT_BUCKET}"
log "Report Bucket: ${REPORT_BUCKET}"
log "Dashboard Port: ${DASHBOARD_PORT}"
log "Nginx Port: ${NGINX_PORT}"
log "=========================================================="


# ==========================================================
# INSTALL SYSTEM PACKAGES
# ==========================================================

log "Refreshing DNF package metadata"

dnf clean all
dnf makecache

log "Installing required system packages"

dnf install -y \
    python3 \
    python3-pip \
    python3-devel \
    gcc \
    nginx \
    curl


# ==========================================================
# INSTALL AWS CLI IF REQUIRED
# ==========================================================

if ! command -v aws >/dev/null 2>&1; then
    log "AWS CLI not found. Installing AWS CLI."
    dnf install -y awscli
fi

log "AWS CLI version:"
aws --version


# ==========================================================
# CREATE REQUIRED DIRECTORIES
# ==========================================================

install -d -m 0755 "$DASHBOARD_DIR"
install -d -m 0755 /var/log/cloudmart
install -d -m 0755 /etc/cloudmart


# ==========================================================
# WAIT FOR EC2 IAM ROLE CREDENTIALS
# ==========================================================

log "Waiting for EC2 IAM credentials"

IAM_READY="false"

for attempt in $(seq 1 24); do

    if aws sts get-caller-identity \
        --region "$AWS_REGION" >/dev/null 2>&1; then

        IAM_READY="true"
        log "EC2 IAM credentials are available"
        break
    fi

    log "Waiting for EC2 IAM credentials... attempt ${attempt}/24"

    sleep 5
done

if [ "$IAM_READY" != "true" ]; then
    log "ERROR: EC2 IAM credentials were not available"
    exit 1
fi


# ==========================================================
# DOWNLOAD DASHBOARD RELEASE
# ==========================================================

log "Preparing temporary dashboard release directory"

rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"


log "Downloading dashboard files from S3"

DOWNLOAD_SUCCESS="false"

for attempt in $(seq 1 5); do

    log "S3 download attempt ${attempt}/5"

    if aws s3 sync \
        "s3://${ARTIFACT_BUCKET}/dashboard/" \
        "$RELEASE_DIR/" \
        --region "$AWS_REGION" \
        --delete \
        --exclude ".venv/*" \
        --exclude "__pycache__/*" \
        --exclude "*.pyc"; then

        DOWNLOAD_SUCCESS="true"
        log "Dashboard files downloaded successfully"
        break
    fi

    if [ "$attempt" -eq 5 ]; then
        log "ERROR: Dashboard artifact download failed after 5 attempts"
        exit 1
    fi

    sleep 10
done

if [ "$DOWNLOAD_SUCCESS" != "true" ]; then
    log "ERROR: Dashboard release download failed"
    exit 1
fi


# ==========================================================
# VALIDATE RELEASE CONTENT
# ==========================================================

log "Validating downloaded dashboard files"

test -s "$RELEASE_DIR/app.py"

test -s "$RELEASE_DIR/requirements.txt"

test -s "$RELEASE_DIR/templates/index.html"

log "Dashboard release validation successful"


# ==========================================================
# STOP EXISTING DASHBOARD SERVICE
# ==========================================================

log "Stopping existing CloudMart dashboard service if present"

if systemctl list-unit-files \
    | grep -q '^cloudmart-dashboard.service'; then

    systemctl stop cloudmart-dashboard.service || true

else
    log "cloudmart-dashboard.service does not exist yet"
fi


# ==========================================================
# REPLACE DASHBOARD APPLICATION
# ==========================================================

log "Replacing dashboard application files"

find "$DASHBOARD_DIR" \
    -mindepth 1 \
    -maxdepth 1 \
    ! -name ".venv" \
    -exec rm -rf {} +

cp -a "$RELEASE_DIR/." "$DASHBOARD_DIR/"

rm -rf "$RELEASE_DIR"

log "Dashboard application files installed"


# ==========================================================
# CREATE / RECREATE PYTHON VIRTUAL ENVIRONMENT
# ==========================================================

log "Preparing Python virtual environment"

if [ ! -x "$DASHBOARD_DIR/.venv/bin/python" ]; then

    log "Creating new Python virtual environment"

    rm -rf "$DASHBOARD_DIR/.venv"

    python3 -m venv "$DASHBOARD_DIR/.venv"

fi


# ==========================================================
# UPDATE PYTHON PACKAGES
# ==========================================================

log "Upgrading Python packaging tools"

"$DASHBOARD_DIR/.venv/bin/python" \
    -m pip install \
    --upgrade \
    pip \
    setuptools \
    wheel


if [ -s "$DASHBOARD_DIR/requirements.txt" ]; then

    log "Installing dashboard requirements"

    "$DASHBOARD_DIR/.venv/bin/python" \
        -m pip install \
        --upgrade \
        -r "$DASHBOARD_DIR/requirements.txt"

else

    log "requirements.txt is empty or missing dependencies"

    log "Installing required dashboard packages"

    "$DASHBOARD_DIR/.venv/bin/python" \
        -m pip install \
        --upgrade \
        flask \
        pymysql \
        boto3 \
        gunicorn

fi


# ==========================================================
# VALIDATE FLASK APPLICATION
# ==========================================================

log "Validating Flask application"

cd "$DASHBOARD_DIR"

"$DASHBOARD_DIR/.venv/bin/python" \
    -c 'import app; assert app.app is not None; print("CloudMart Flask import OK")'

log "Flask application validation successful"


# ==========================================================
# CREATE DASHBOARD ENVIRONMENT FILE
# ==========================================================

log "Preparing dashboard environment file"

if [ ! -s "$ENV_FILE" ]; then

    log "Creating new Flask secret"

    umask 077

    SECRET="$(
        python3 -c \
        'import secrets; print(secrets.token_hex(32))'
    )"

    printf 'FLASK_SECRET_KEY=%s\n' "$SECRET" > "$ENV_FILE"

    chmod 600 "$ENV_FILE"

else

    log "Existing dashboard environment file found"

fi


# ==========================================================
# CREATE SYSTEMD SERVICE
# ==========================================================

log "Creating CloudMart dashboard systemd service"

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


# ==========================================================
# VALIDATE SYSTEMD SERVICE FILE
# ==========================================================

log "Validating systemd service configuration"

systemd-analyze verify "$SERVICE_FILE"


# ==========================================================
# CREATE NGINX CONFIGURATION
# ==========================================================

log "Creating Nginx configuration"

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


# ==========================================================
# VALIDATE NGINX
# ==========================================================

log "Validating Nginx configuration"

nginx -t


# ==========================================================
# RELOAD SYSTEMD
# ==========================================================

log "Reloading systemd"

systemctl daemon-reload


# ==========================================================
# ENABLE SERVICES
# ==========================================================

log "Enabling Nginx"

systemctl enable nginx.service


log "Enabling CloudMart dashboard service"

systemctl enable cloudmart-dashboard.service


# ==========================================================
# START NGINX
# ==========================================================

log "Restarting Nginx"

systemctl restart nginx.service


# ==========================================================
# START CLOUDMART DASHBOARD
# ==========================================================

log "Starting CloudMart dashboard service"

systemctl restart cloudmart-dashboard.service


# ==========================================================
# WAIT FOR DASHBOARD SERVICE
# ==========================================================

log "Waiting for CloudMart dashboard service"

DASHBOARD_READY="false"

for attempt in $(seq 1 30); do

    if systemctl is-active --quiet cloudmart-dashboard.service; then

        DASHBOARD_READY="true"

        log "CloudMart dashboard service is active"

        break
    fi

    log "Dashboard service is not active yet... attempt ${attempt}/30"

    sleep 2
done


if [ "$DASHBOARD_READY" != "true" ]; then

    log "ERROR: CloudMart dashboard service failed to start"

    log "================ SYSTEMD STATUS ================"

    systemctl status \
        cloudmart-dashboard.service \
        --no-pager \
        -l || true

    log "================ JOURNAL ================"

    journalctl \
        -u cloudmart-dashboard.service \
        --no-pager \
        -n 100 || true

    exit 1
fi


# ==========================================================
# VERIFY NGINX
# ==========================================================

log "Checking Nginx service"

if ! systemctl is-active --quiet nginx.service; then

    log "ERROR: Nginx service is not active"

    systemctl status \
        nginx.service \
        --no-pager \
        -l || true

    exit 1
fi


# ==========================================================
# VERIFY DASHBOARD FILES
# ==========================================================

log "Checking dashboard files"

test -s "$DASHBOARD_DIR/app.py"

test -s "$DASHBOARD_DIR/requirements.txt"

test -s "$DASHBOARD_DIR/templates/index.html"


# ==========================================================
# WAIT FOR APPLICATION
# ==========================================================

log "Waiting for Flask application"

APP_READY="false"

for attempt in $(seq 1 30); do

    if curl \
        --fail \
        --silent \
        --show-error \
        --max-time 10 \
        "http://127.0.0.1:${DASHBOARD_PORT}/health" \
        >/dev/null 2>&1; then

        APP_READY="true"

        log "Flask health endpoint is responding"

        break
    fi

    log "Flask application not ready yet... attempt ${attempt}/30"

    sleep 2
done


if [ "$APP_READY" != "true" ]; then

    log "ERROR: Flask dashboard health check failed"

    systemctl status \
        cloudmart-dashboard.service \
        --no-pager \
        -l || true

    journalctl \
        -u cloudmart-dashboard.service \
        --no-pager \
        -n 100 || true

    exit 1
fi


# ==========================================================
# VERIFY NGINX HEALTH ENDPOINT
# ==========================================================

log "Checking Nginx health endpoint"

NGINX_READY="false"

for attempt in $(seq 1 15); do

    if curl \
        --fail \
        --silent \
        --show-error \
        --max-time 10 \
        "http://127.0.0.1:${NGINX_PORT}/health" \
        >/dev/null 2>&1; then

        NGINX_READY="true"

        log "Nginx health endpoint is responding"

        break
    fi

    log "Nginx health endpoint not ready yet... attempt ${attempt}/15"

    sleep 2
done


if [ "$NGINX_READY" != "true" ]; then

    log "ERROR: Nginx health check failed"

    systemctl status \
        nginx.service \
        --no-pager \
        -l || true

    nginx -t || true

    exit 1
fi


# ==========================================================
# FINAL STATUS
# ==========================================================

log "=========================================================="
log "CloudMart dashboard bootstrap completed successfully"
log "=========================================================="

log "Dashboard service:"
systemctl is-active cloudmart-dashboard.service

log "Nginx service:"
systemctl is-active nginx.service

log "Dashboard health:"
curl \
    --fail \
    --silent \
    --show-error \
    --max-time 10 \
    "http://127.0.0.1:${DASHBOARD_PORT}/health"

log "Nginx health:"
curl \
    --fail \
    --silent \
    --show-error \
    --max-time 10 \
    "http://127.0.0.1:${NGINX_PORT}/health"

log "CloudMart dashboard bootstrap completed successfully"

exit 0