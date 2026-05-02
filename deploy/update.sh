#!/usr/bin/env bash
# update.sh — обновление MediaServer.
# Запускается от root: sudo bash /opt/mediasrv/deploy/update.sh

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Запустите от root: sudo bash $0"
  exit 1
fi

cd /opt/mediasrv

echo "==> git pull"
sudo -u mediasrv git pull --ff-only

echo "==> pip install (если requirements.txt изменился)"
sudo -u mediasrv /opt/mediasrv/venv/bin/pip install -r requirements.txt

echo "==> Alembic migrations"
sudo -u mediasrv /opt/mediasrv/venv/bin/alembic -c alembic.ini upgrade head

echo "==> Перезапуск mediasrv (qbittorrent не трогаем — у него своя жизнь)"
systemctl restart mediasrv.service

echo "==> Проверка статуса"
systemctl status mediasrv.service --no-pager | head -n 10

echo
echo "Обновление завершено. Если что-то не так — sudo journalctl -u mediasrv -f"
