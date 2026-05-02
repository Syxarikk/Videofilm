# MediaServer (План 1: Auth + UI skeleton)

Семейный медиа-сервер для домашнего использования. Этот шаг — фундамент: авторизация, управление пользователями, пустой UI. Торрент-логика и стриминг — в Плане 2; продакшн-деплой — в Плане 3.

## Локальный запуск (development)

```bash
# 1. Установка
python -m venv venv
source venv/Scripts/activate         # Windows Git Bash
# source venv/bin/activate           # Linux/Mac
pip install -r requirements.txt

# 2. Конфиг
cp .env.example .env
# Заполнить SESSION_SECRET. Можно сгенерировать:
#   python -c "import secrets; print(secrets.token_hex(32))"

# 3. Миграции
alembic upgrade head

# 4. Создать первого админа
python -m scripts.create_admin
# → введите логин и временный пароль

# 5. Запуск
uvicorn app.main:app --reload --port 8000
```

Открыть в браузере `http://127.0.0.1:8000/`. Зайти под админом → сменить пароль → активировать 2FA (отсканировать QR в Google Authenticator) → сохранить backup-коды → пустая библиотека.

В админке `/admin/users` создать ещё одного пользователя; зайти под ним вторым окном/режимом инкогнито.

## Тесты

```bash
pytest -v                              # все тесты
pytest tests/unit -v                   # только unit
pytest tests/integration -v            # только integration
```

## Структура

См. `docs/superpowers/specs/2026-05-02-family-media-server-design.md` (раздел §11).

## Production deployment

См. **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — пошаговое руководство по установке на Ubuntu, настройке HTTPS, fail2ban, бэкапов, Tailscale для SSH.

## Что дальше

- План 2 — торренты, библиотека, стриминг, скачивание.
- План 3 — production-деплой, Caddy, HTTPS, fail2ban, systemd, install.sh.
