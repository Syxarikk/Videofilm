#!/usr/bin/env bash
# install.sh — установка MediaServer на чистую Ubuntu 22.04+.
# Идемпотентный: можно запускать повторно.
#
# Использование (от root или через sudo):
#   sudo bash install.sh

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Запустите от root: sudo bash $0"
  exit 1
fi

echo "==> Step 1/10: Установка системных пакетов"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    caddy \
    qbittorrent-nox \
    ffmpeg \
    python3.11 python3.11-venv python3.11-dev \
    fail2ban \
    git \
    sqlite3 \
    curl \
    unattended-upgrades

echo "==> Step 2/10: Создание пользователя mediasrv"
if ! id mediasrv >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin --home-dir /opt/mediasrv mediasrv
fi

echo "==> Step 3/10: Создание директорий"
mkdir -p /opt/mediasrv /var/lib/mediasrv/hls /var/log/mediasrv /var/log/caddy
chown -R mediasrv:mediasrv /opt/mediasrv /var/lib/mediasrv /var/log/mediasrv

read -rp "Корневая директория для медиа (по умолчанию /srv/Общее): " MEDIA_ROOT
MEDIA_ROOT="${MEDIA_ROOT:-/srv/Общее}"
mkdir -p "$MEDIA_ROOT/downloads" "$MEDIA_ROOT/backups"
chown -R mediasrv:mediasrv "$MEDIA_ROOT"

echo "==> Step 4/10: Клонирование/обновление репозитория"
if [ ! -d /opt/mediasrv/.git ]; then
  read -rp "URL репозитория (git): " REPO
  sudo -u mediasrv git clone "$REPO" /opt/mediasrv
else
  echo "    репозиторий уже есть, пропускаем clone"
fi

echo "==> Step 5/10: Установка Python зависимостей в venv"
sudo -u mediasrv python3.11 -m venv /opt/mediasrv/venv
sudo -u mediasrv /opt/mediasrv/venv/bin/pip install --upgrade pip
sudo -u mediasrv /opt/mediasrv/venv/bin/pip install -r /opt/mediasrv/requirements.txt

echo "==> Step 6/10: Конфиг .env"
ENV_FILE=/opt/mediasrv/.env
if [ ! -f "$ENV_FILE" ]; then
  SESSION_SECRET=$(/opt/mediasrv/venv/bin/python -c "import secrets; print(secrets.token_hex(32))")
  read -rp "qBittorrent admin password (запоминается в .env): " QB_PWD
  cat > "$ENV_FILE" <<EOF
SESSION_SECRET=$SESSION_SECRET
DATABASE_URL=sqlite:////opt/mediasrv/app.db
MEDIA_ROOT=$MEDIA_ROOT
QBITTORRENT_URL=http://127.0.0.1:8080
QBITTORRENT_USERNAME=admin
QBITTORRENT_PASSWORD=$QB_PWD
TOTP_ISSUER=MediaServer
HLS_WORK_ROOT=/var/lib/mediasrv/hls
EOF
  chmod 600 "$ENV_FILE"
  chown mediasrv:mediasrv "$ENV_FILE"
  echo "    .env создан, SESSION_SECRET сгенерирован случайно"
else
  echo "    .env уже существует, пропускаем"
fi

echo "==> Step 7/10: Применение миграций + создание первого админа"
sudo -u mediasrv /opt/mediasrv/venv/bin/alembic -c /opt/mediasrv/alembic.ini upgrade head
if ! sudo -u mediasrv /opt/mediasrv/venv/bin/python -c "
from sqlalchemy import select
from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import User
e = make_engine(get_settings().database_url)
f = make_session_factory(e)
with f() as s:
    u = s.scalars(select(User).where(User.is_admin == True)).first()
    exit(0 if u else 1)
" 2>/dev/null; then
  echo "    создаём первого админа"
  sudo -u mediasrv /opt/mediasrv/venv/bin/python -m scripts.create_admin
else
  echo "    админ уже есть, пропускаем"
fi

echo "==> Step 8/10: systemd unit-ы"
cp /opt/mediasrv/deploy/systemd/mediasrv.service /etc/systemd/system/
cp /opt/mediasrv/deploy/systemd/qbittorrent-nox.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable mediasrv.service qbittorrent-nox.service
systemctl start qbittorrent-nox.service mediasrv.service

echo "==> Step 9/10: Caddy + fail2ban"
read -rp "Доменное имя (например: media.duckdns.org): " DOMAIN
sed "s|{{DOMAIN}}|$DOMAIN|g" /opt/mediasrv/deploy/Caddyfile.template > /etc/caddy/Caddyfile
systemctl restart caddy

cp /opt/mediasrv/deploy/fail2ban/mediasrv-filter.conf /etc/fail2ban/filter.d/mediasrv.conf
cp /opt/mediasrv/deploy/fail2ban/mediasrv.conf /etc/fail2ban/jail.d/mediasrv.conf
systemctl restart fail2ban

echo "==> Step 10/10: Cron задачи (бэкап + DDNS)"
cp /opt/mediasrv/deploy/cron/backup-db.cron /etc/cron.d/mediasrv-backup
echo "    Установите DuckDNS вручную: отредактируйте /etc/cron.d/mediasrv-ddns с подставленными SUBDOMAIN и TOKEN из вашего DuckDNS аккаунта"
echo "    Шаблон: /opt/mediasrv/deploy/cron/ddns-update.cron"

echo
echo "=========================================================="
echo "Установка завершена!"
echo "=========================================================="
echo "Следующие шаги:"
echo "  1. На роутере пробросьте порты 80 и 443 на этот сервер."
echo "  2. Зайдите на https://$DOMAIN/login"
echo "  3. Войдите под созданным админом, смените пароль, активируйте 2FA"
echo "  4. Создайте остальных пользователей в /admin/users"
echo "  5. Установите Tailscale для безопасного SSH (см. docs/DEPLOYMENT.md)"
echo "  6. Если нужен DDNS — настройте /etc/cron.d/mediasrv-ddns"
echo "=========================================================="
