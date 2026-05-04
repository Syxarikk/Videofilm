# Deployment Guide

Инструкции для production-деплоя MediaServer на Ubuntu 22.04+.

## Что вам понадобится

- Машина с Ubuntu 22.04+ (домашний сервер, VPS, мини-ПК).
- Sudo-доступ.
- Доменное имя. Бесплатно: https://www.duckdns.org/ → создать поддомен `media.duckdns.org`.
- Если IP не статический — токен DuckDNS для авто-обновления.
- Роутер с возможностью проброса портов (для домашнего сервера).

## Порядок действий

### 1. Подготовка машины

```bash
# Установить minimal Ubuntu 22.04 server. Залогиниться по SSH локально (ещё не через Tailscale).
sudo apt update && sudo apt upgrade -y
```

### 2. Настройка DuckDNS (если IP динамический)

1. Зарегистрируйтесь на https://www.duckdns.org/ через GitHub/Google.
2. Создайте subdomain (например `media`).
3. Скопируйте `token` со страницы профиля.
4. Запомните `subdomain.duckdns.org` — это ваш будущий URL.

### 3. Проброс портов на роутере

В админке роутера (обычно `192.168.1.1`):
- **Port 80** → IP сервера : 80 (для Let's Encrypt ACME-challenge)
- **Port 443** → IP сервера : 443 (HTTPS)

**Никаких других портов наружу не пробрасываем!**

### 4. Установка MediaServer

```bash
# На сервере, через локальный SSH или прямо у клавиатуры:
git clone https://github.com/<your-username>/<your-repo>.git /tmp/mediasrv
sudo bash /tmp/mediasrv/deploy/install.sh
```

Скрипт интерактивно спросит:
- Корневую папку для медиа (по умолчанию `/srv/Общее`).
- URL репозитория для git clone.
- Пароль для qBittorrent Web UI admin.
- Доменное имя.

После установки — следуйте инструкциям в финальном выводе.

### 5. Установка Tailscale (для SSH без светящего наружу порта 22)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Откроется ссылка для авторизации. Подтвердите, и сервер появится в вашем Tailscale-аккаунте.
Ставьте Tailscale на ноутбук — теперь `ssh administrator@<machine-name>` работает через Tailscale.

После Tailscale **отключите public SSH**:
```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 22/tcp   # SSH доступен только через Tailscale-интерфейс
sudo ufw enable
```

### 6. DDNS auto-update (если нужен)

Отредактируйте `/etc/cron.d/mediasrv-ddns`, замените:
```
*/5 * * * * mediasrv /opt/mediasrv/scripts/ddns_update.sh YOUR_SUBDOMAIN YOUR_TOKEN ...
```
на ваши значения. Установка:
```bash
sudo cp /opt/mediasrv/deploy/cron/ddns-update.cron /etc/cron.d/mediasrv-ddns
sudo nano /etc/cron.d/mediasrv-ddns   # подставить SUBDOMAIN/TOKEN
```

### 7. Финальная проверка

Откройте `https://your-domain.duckdns.org/login` в браузере. Залогиньтесь под админом.

Smoke-чеклист:
- [ ] Login → Change password → /library
- [ ] Скопируйте magnet-ссылку легального контента (например, Big Buck Bunny: `magnet:?xt=urn:btih:...`)
- [ ] /add-torrent → видно прогресс на /downloads
- [ ] Когда скачается → /library показывает фильм → /media/{id} играет в плеере
- [ ] Скачать оригинал → файл качается
- [ ] Удалить медиа → пропадает и из библиотеки и с диска
- [ ] /admin/health показывает: диск ✅, qBittorrent ✅, активные стримы ✅

## Обновление приложения

```bash
sudo bash /opt/mediasrv/deploy/update.sh
```

## Полезные команды

```bash
# Статус сервисов
sudo systemctl status mediasrv qbittorrent-nox caddy fail2ban

# Логи
sudo journalctl -u mediasrv -f             # приложение
sudo tail -f /var/log/caddy/mediasrv-access.log   # HTTP-запросы
sudo fail2ban-client status mediasrv-login        # кто забанен

# Бэкапы
ls -la /srv/Общее/backups/

# Восстановление БД
sudo systemctl stop mediasrv
sudo cp /srv/Общее/backups/app-2026-05-02.db /opt/mediasrv/app.db
sudo chown mediasrv:mediasrv /opt/mediasrv/app.db
sudo systemctl start mediasrv
```

## Известные ограничения

- Транскодинг 4K HEVC на этом железе (i7-2600 без QSV для HEVC) — практически нереально в реальном времени; ограничьтесь 1080p.
- Один зритель 1080p HLS-транскодинг занимает ~70% одного ядра. Два зрителя 4K → CPU умрёт; смотрите оригинал через скачивание.
- Без VPN на торрент-трафике сервер раздаёт защищённый авторскими правами контент с домашнего IP — это в спецификации зафиксировано как сознательное решение, см. `docs/superpowers/specs/2026-05-02-family-media-server-design.md` §3.
