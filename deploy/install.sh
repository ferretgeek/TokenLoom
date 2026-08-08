#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${1:-$(pwd)}"
BOOTSTRAP_ENV="${2:-/root/token-admin-bootstrap.env}"

read_bootstrap_value() {
  local key="$1"
  local line
  line="$(grep -m1 -E "^${key}='[^']*'$" "$BOOTSTRAP_ENV" || true)"
  if [[ -z "$line" ]]; then
    echo "一次性部署密钥缺少或错误：$key" >&2
    exit 2
  fi
  line="${line#*=}"
  line="${line#\'}"
  line="${line%\'}"
  printf '%s' "$line"
}

if [[ ! -f "$SOURCE_DIR/requirements.txt" || ! -f "$SOURCE_DIR/app/main.py" ]]; then
  echo "部署源目录不完整：$SOURCE_DIR" >&2
  exit 2
fi
FIRST_INSTALL=0
if [[ ! -f /etc/token-admin.env ]]; then
  FIRST_INSTALL=1
  if [[ ! -f "$BOOTSTRAP_ENV" ]]; then
    echo "缺少一次性部署密钥文件：$BOOTSTRAP_ENV" >&2
    exit 2
  fi
  # Bootstrap files are commonly generated on Windows. Normalize CRLF before
  # sourcing so carriage returns never become part of credentials.
  sed -i 's/\r$//' "$BOOTSTRAP_ENV"
  DB_PASSWORD="$(read_bootstrap_value DB_PASSWORD)"
  ADMIN_KEY_HASH="$(read_bootstrap_value ADMIN_KEY_HASH)"
  SESSION_SECRET="$(read_bootstrap_value SESSION_SECRET)"
  ENCRYPTION_KEY="$(read_bootstrap_value ENCRYPTION_KEY)"
  [[ "$DB_PASSWORD" =~ ^[A-Za-z0-9_-]{32,}$ ]] || { echo "DB_PASSWORD 格式无效" >&2; exit 2; }
  [[ "$ADMIN_KEY_HASH" == \$argon2* ]] || { echo "ADMIN_KEY_HASH 格式无效" >&2; exit 2; }
  [[ ${#SESSION_SECRET} -ge 32 ]] || { echo "SESSION_SECRET 过短" >&2; exit 2; }
  [[ ${#ENCRYPTION_KEY} -ge 43 ]] || { echo "ENCRYPTION_KEY 格式无效" >&2; exit 2; }
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql python3-venv ca-certificates curl rsync >/dev/null
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "需要 Python 3.11 或更高版本" >&2
  exit 2
}
systemctl enable --now postgresql >/dev/null

if ! id tokenadmin >/dev/null 2>&1; then
  useradd --system --home /var/lib/token-admin --shell /usr/sbin/nologin tokenadmin
fi
install -d -m 0750 -o tokenadmin -g tokenadmin /var/lib/token-admin /var/lib/token-admin/imports
install -d -m 0755 /opt/token-admin/releases

if [[ "$FIRST_INSTALL" == "1" ]]; then
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 --quiet -v db_password="$DB_PASSWORD" <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'token_admin') THEN
    CREATE ROLE token_admin LOGIN;
  END IF;
END
$$;
ALTER ROLE token_admin PASSWORD :'db_password';
SQL
  if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='token_admin'" | grep -q 1; then
    runuser -u postgres -- createdb --owner=token_admin --encoding=UTF8 token_admin
  fi
fi

previous_release="$(readlink -f /opt/token-admin/current 2>/dev/null || true)"
release="/opt/token-admin/releases/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0755 "$release"
rsync -a --delete \
  --exclude='.git/' --exclude='.venv/' --exclude='.pytest_cache/' \
  --exclude='data/' --exclude='tests/' --exclude='__pycache__/' --exclude='*.py[co]' \
  "$SOURCE_DIR/" "$release/"
chown -R root:root "$release"

if [[ ! -x /opt/token-admin/venv/bin/python ]]; then
  python3 -m venv /opt/token-admin/venv
fi
/opt/token-admin/venv/bin/pip install --disable-pip-version-check --quiet --upgrade pip
/opt/token-admin/venv/bin/pip install --disable-pip-version-check --quiet -r "$release/requirements.txt"

if [[ "$FIRST_INSTALL" == "1" ]]; then
  umask 077
  cat > /etc/token-admin.env <<ENV
APP_NAME=令牌续航台
APP_ENV=production
DATABASE_URL=postgresql+asyncpg://token_admin:${DB_PASSWORD}@127.0.0.1/token_admin
ADMIN_KEY_HASH=${ADMIN_KEY_HASH}
SESSION_SECRET=${SESSION_SECRET}
ENCRYPTION_KEY=${ENCRYPTION_KEY}
DATA_DIR=/var/lib/token-admin
BIND_HOST=127.0.0.1
BIND_PORT=8787
COOKIE_SECURE=true
TRUST_PROXY_HEADERS=true
TRUSTED_PROXY_IPS=127.0.0.1,::1
ALLOWED_HOSTS=127.0.0.1,localhost
SESSION_HOURS=12
MAX_UPLOAD_BYTES=2147483648
MIN_FREE_BYTES=2147483648
MAX_IMPORT_LINE_BYTES=262144
IMPORT_BATCH_SIZE=1000
WORKER_BATCH_SIZE=500
AUDIT_RETENTION_DAYS=180
JOB_RETENTION_DAYS=90
LOG_LEVEL=INFO
ENV
  chmod 0600 /etc/token-admin.env
fi

ensure_env_setting() {
  local key="$1"
  local value="$2"
  if ! grep -q "^${key}=" /etc/token-admin.env; then
    printf '%s=%s\n' "$key" "$value" >> /etc/token-admin.env
  fi
}
ensure_env_setting APP_ENV production
ensure_env_setting TRUST_PROXY_HEADERS true
ensure_env_setting TRUSTED_PROXY_IPS '127.0.0.1,::1'
ensure_env_setting ALLOWED_HOSTS '127.0.0.1,localhost'
ensure_env_setting MIN_FREE_BYTES 2147483648
ensure_env_setting MAX_IMPORT_LINE_BYTES 262144
ensure_env_setting AUDIT_RETENTION_DAYS 180
ensure_env_setting JOB_RETENTION_DAYS 90
chmod 0600 /etc/token-admin.env

install -m 0644 "$release/deploy/token-admin.service" /etc/systemd/system/token-admin.service
install -m 0644 "$release/deploy/token-admin-worker.service" /etc/systemd/system/token-admin-worker.service
systemctl daemon-reload
systemctl enable token-admin.service token-admin-worker.service >/dev/null
systemctl stop token-admin-worker.service >/dev/null 2>&1 || true
ln -sfn "$release" /opt/token-admin/current

# Let the web process create/migrate the schema first. This removes first-boot
# DDL races with the worker and turns a failed health check into a failed deploy.
systemctl restart token-admin.service
healthy=0
for _ in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:8787/healthz >/dev/null; then
    healthy=1
    break
  fi
  sleep 1
done
if [[ "$healthy" != "1" ]]; then
  systemctl --no-pager --full status token-admin.service >&2 || true
  journalctl -u token-admin.service -n 80 --no-pager >&2 || true
  if [[ -n "$previous_release" && -d "$previous_release" ]]; then
    ln -sfn "$previous_release" /opt/token-admin/current
    systemctl restart token-admin.service token-admin-worker.service || true
  fi
  exit 1
fi
systemctl restart token-admin-worker.service
sleep 2
if ! systemctl is-active --quiet token-admin-worker.service; then
  systemctl --no-pager --full status token-admin-worker.service >&2 || true
  journalctl -u token-admin-worker.service -n 80 --no-pager >&2 || true
  exit 1
fi

rm -f -- "$BOOTSTRAP_ENV"

# Keep the current release and two rollback candidates. Resolve every target
# beneath the dedicated release directory before removing anything.
mapfile -t old_releases < <(find /opt/token-admin/releases -mindepth 1 -maxdepth 1 -type d -printf '%p\n' | sort -r | tail -n +4)
for target in "${old_releases[@]}"; do
  resolved="$(readlink -f "$target")"
  if [[ "$resolved" == /opt/token-admin/releases/* && "$resolved" != "$(readlink -f /opt/token-admin/current)" ]]; then
    rm -rf -- "$resolved"
  fi
done
echo "令牌续航台部署完成，服务仅监听 127.0.0.1:8787"
