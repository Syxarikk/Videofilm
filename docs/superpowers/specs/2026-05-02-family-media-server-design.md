# Семейный медиа-сервер: дизайн-документ

**Дата:** 2026-05-02
**Статус:** черновик, ждёт ревью пользователя

---

## 1. Контекст и цели

Построить домашний медиа-сервер с веб-интерфейсом, через который **5–20 человек закрытого круга** (семья, близкие друзья) могут:

1. Добавлять торренты (magnet-ссылка / `.torrent`-файл) — сервер сам качает.
2. Смотреть готовые фильмы и сериалы прямо в браузере, на мобильном, на Smart TV / через Chromecast.
3. Скачивать оригинальный файл к себе, чтобы открыть в стороннем плеере.
4. Делать всё перечисленное **только через сайт** — без SSH, SFTP, ручной загрузки на сервер.

Сервер крутится дома (i7-2600, 20 ГБ RAM, без GPU, 200 ГБ system-диск + 1 ТБ data-диск, Ubuntu, 50–200 Мбит/с upload). Доступ из интернета — по доменному имени с HTTPS.

## 2. Требования

### Функциональные

| Что | Кто может |
|---|---|
| Логин с 2FA (TOTP) | все пользователи |
| Добавление magnet/torrent → загрузка | любой залогиненный |
| Просмотр прогресса активных загрузок | любой залогиненный |
| Просмотр библиотеки готовых файлов | любой залогиненный |
| Просмотр в браузере (HLS-стрим) | любой залогиненный |
| Скачивание оригинального файла | любой залогиненный |
| Удаление любого файла из библиотеки | любой залогиненный |
| Создание/удаление пользователей | только админ |
| Просмотр здоровья сервера (`/admin/health`) | только админ |

### Нефункциональные

- **Одновременных зрителей:** 1–2 (выше — деградация качества из-за CPU-транскодинга).
- **Транскодинг:** on-the-fly HLS, без предварительного pre-transcode.
- **Доступ:** интернет, HTTPS, домен.
- **Внешние порты:** только `80` и `443` (80 — для Let's Encrypt ACME-challenge).
- **Совместимость:** все основные браузеры, iOS/Android, Smart TV с поддержкой HLS, Chromecast.

## 3. Не-цели (явно НЕ делаем)

- **VPN для торрент-трафика.** Принято сознательное решение не делать. **Известный риск:** сервер раздаёт защищённый авторскими правами контент с домашнего IP. Это видно правообладателям при мониторинге торрент-роёв и может привести к претензиям/штрафам в зависимости от юрисдикции. 2FA на сайте от этого риска **не защищает**. Может быть добавлено позже без серьёзной перестройки архитектуры (отдельный VPN-интерфейс, qBittorrent биндится на него, kill switch на iptables).
- **Мульти-тенантность / квоты на пользователя.** Все равны: любой может добавить и удалить любой файл.
- **Поиск по торрент-трекерам в самом сайте.** Пользователь приносит magnet/torrent сам.
- **Pre-transcode после загрузки.** Только on-the-fly при просмотре. (Если упрёмся в CPU — можно переключиться на гибрид без перестройки.)
- **Автоматическая очистка старого / квоты на диск.** Управление вручную.
- **Telegram/email-алерты.** Не сейчас.
- **2FA через SMS/email.** Только TOTP (Google Authenticator / Authy / 1Password).
- **WAF, Cloudflare, капча.** Нет публичной регистрации, защиты от перебора достаточно (rate limit + fail2ban).

## 4. Архитектура

```
                 ИНТЕРНЕТ
                    │
                    │ HTTPS :443  (+:80 для ACME)
                    ▼
              ┌───────────┐
              │   Caddy   │  ← единственное, что торчит наружу
              │ reverse   │     — Let's Encrypt автосертификаты
              │  proxy    │     — rate limiting на /login
              └─────┬─────┘     — security headers
                    │ HTTP :8000 (только 127.0.0.1)
                    ▼
            ┌───────────────┐
            │   FastAPI     │  ← сайт: API + HTML страницы
            │   backend     │     (HTMX рендерит фрагменты)
            └───┬───────┬───┘
                │       │
        ┌───────┘       └───────┐
        │                       │
        ▼                       ▼
  ┌──────────┐           ┌─────────────┐
  │  SQLite  │           │ qBittorrent │  ← 127.0.0.1:8080
  │  (file)  │           │   daemon    │     управляем по API
  └──────────┘           └──────┬──────┘
                                │
                                ▼
                  ┌───────────────────────────┐
                  │ /srv/Общее/downloads/     │  ← скачанные файлы
                  └──────────┬────────────────┘     (1 ТБ data-диск)
                             │ при просмотре
                             ▼
                      ┌──────────────┐
                      │   ffmpeg     │  ← subprocess из FastAPI
                      │  (HLS live)  │     сегменты в /var/lib/mediasrv/hls/
                      └──────┬───────┘
                             │ .m3u8 + .ts
                             ▼
                       (отдаётся через
                        FastAPI → Caddy
                        → плеер пользователя)
```

**Главный принцип:** наружу торчит только Caddy на 443 (плюс 80 для ACME). Все остальные сервисы (FastAPI, qBittorrent, SQLite) bind на `127.0.0.1` — извне недоступны независимо от номера порта.

**Админский SSH:** через Tailscale (приватная mesh-сеть), порт 22 наружу не светится вообще.

## 5. Компоненты

### 5.1 Caddy

- **Ответственность:** HTTPS на 443, прокси на FastAPI, авто Let's Encrypt, rate limiting на `/login`, security headers (HSTS, CSP, X-Frame-Options и т.п.).
- **Конфиг:** `/etc/caddy/Caddyfile`.
- **Сервис:** `caddy.service` (systemd, рестарт при падении).
- **Зависимости:** домен с DDNS (DuckDNS / Cloudflare DDNS).

### 5.2 qBittorrent daemon

- **Ответственность:** скачивание торрентов, статус (прогресс, скорость), удаление с файлами.
- **Интерфейс:** HTTP API на `127.0.0.1:8080` (логин/пароль из `.env`).
- **Хранение:** `/srv/Общее/downloads/` (1 ТБ data-диск, путь конфигурируется в `.env`).
- **Сервис:** `qbittorrent-nox.service`.
- **Почему именно qBittorrent:** зрелый, документированный API, веб-морда для отладки, не нужно писать торрент-логику самим. Альтернатива (`libtorrent` напрямую из Python) — больше работы и багов в нашем коде, ноль преимуществ для наших масштабов.

### 5.3 FastAPI backend

Разбит на модули с узкой ответственностью:

| Модуль | Ответственность |
|---|---|
| `auth` | логин с TOTP, сессии (cookie), bcrypt-пароли |
| `torrents` | добавить magnet → qBittorrent, список активных, удалить |
| `library` | список готовых файлов, метаданные, поиск по названию |
| `streaming` | старт/стоп ffmpeg, отдача `.m3u8` и `.ts` |
| `download` | стрим оригинального файла с проверкой авторизации |
| `admin` | CRUD пользователей, страница `/admin/health` |

- **Интерфейс:** REST + HTML (HTMX-фрагменты).
- **Сервис:** `mediasrv.service` (uvicorn, рестарт при падении).
- **Зависимости:** SQLite, qBittorrent API, ffmpeg-бинарник, файловая система.

### 5.4 SQLite (`app.db`)

Один файл на system-диске. Бэкапится ежедневно на 1 ТБ диск.

```
users           id, username, password_hash, totp_secret_encrypted,
                totp_enabled, is_admin, created_at
sessions        token, user_id, expires_at, created_at
backup_codes    user_id, code_hash, used_at  -- 10 одноразовых кодов
media_items     id, torrent_hash, title, file_path, size_bytes,
                added_by, added_at
watch_progress  user_id, media_id, position_seconds, updated_at
```

**Почему SQLite, а не PostgreSQL:** при 5–20 пользователях разницы по производительности нет, ноль настройки, бэкап = `cp app.db backup.db`. Если перерастём — миграция на Postgres через SQLAlchemy без переписывания кода.

### 5.5 ffmpeg

- **Ответственность:** транскодинг исходника (mkv/mp4/avi с любым кодеком) в HLS (`.m3u8` + `.ts`), отдаваемый плееру.
- **Запуск:** `subprocess` из FastAPI с параметрами:
  - `-i <source>` — исходник.
  - `-c:v libx264 -preset veryfast -crf 23` — H.264 для совместимости с браузерами/TV.
  - `-c:a aac -b:a 128k` — AAC.
  - `-hls_time 6` — сегменты по 6 секунд.
  - `-hls_list_size 0` — VOD-плейлист.
  - `-hls_segment_filename /var/lib/mediasrv/hls/<stream_id>/seg_%05d.ts`.
- **Жизненный цикл:** один процесс на пару `(media_id, user_id)`. Стартует при первом запросе плейлиста; убивается, если 60 секунд нет heartbeat от плеера; чистит свою папку с сегментами.
- **Перемотка:** kill + restart с `-ss <position>`, плеер автоматически перецепляется к новому плейлисту.

### 5.6 Frontend

- **Шаблоны:** Jinja2 (server-side render), HTMX для динамических обновлений (например, прогресс торрентов через `hx-trigger="every 2s"`).
- **CSS:** обычный CSS, без сборки (без webpack/npm).
- **JS:** минимум — только hls.js для плеера и обвязка для Chromecast SDK.
- **Плеер:** [hls.js](https://github.com/video-dev/hls.js) для Chromium-based и Firefox; нативный HLS в Safari/iOS; Chromecast SDK для каста.
- **Страницы:** `/login`, `/library`, `/media/{id}`, `/add-torrent`, `/downloads`, `/admin/users`, `/admin/health`.

## 6. Data flow

### 6.1 Логин с 2FA

1. GET `/login` → форма username + password + TOTP-код.
2. POST `/login` →
   - bcrypt-проверка пароля.
   - TOTP-проверка (`pyotp.verify`, окно ±1 шаг = ±30 сек).
   - Создание сессии в `sessions`, выставление `Set-Cookie: session=...; HttpOnly; Secure; SameSite=Strict; Max-Age=30d`.
3. Редирект на `/library`.

**Запасной путь при потере телефона:** ввод одного из 10 backup-кодов вместо TOTP. Код после использования помечается как `used_at`.

### 6.2 Добавление magnet → загрузка

1. GET `/add-torrent` → форма ввода magnet.
2. POST `/api/torrents` с magnet → FastAPI:
   - Валидирует magnet регуляркой (`magnet:?xt=urn:btih:[a-fA-F0-9]+...`).
   - Вызывает qBittorrent API: `POST /api/v2/torrents/add` с `urls=<magnet>` и `savepath=/srv/Общее/downloads/`.
3. Редирект на `/downloads`.
4. На `/downloads` HTMX-фрагмент опрашивает `/api/torrents/status` каждые 2 секунды → выводит таблицу: название, прогресс, скорость, ETA.
5. Фоновая задача FastAPI каждые 10 сек проверяет qBittorrent на завершённые торренты:
   - Находит завершённые, которых ещё нет в `media_items`.
   - В папке торрента берёт самый большой видеофайл (`.mkv`, `.mp4`, `.avi`, `.webm`, `.mov`).
   - Парсит название из имени файла/папки (примитивный парсер: `Some.Movie.2024.1080p...mkv` → `Some Movie (2024)`).
   - Создаёт строку в `media_items`.

### 6.3 Просмотр

1. GET `/library` → список карточек (постер-плейсхолдер, название, размер, кто добавил).
2. Клик → `/media/{id}` → страница с плеером (hls.js), кнопками «Скачать» и «Удалить», прогресс-баром (последняя позиция из `watch_progress` для этого пользователя).
3. hls.js делает GET `/api/stream/{id}/playlist.m3u8`:
   - FastAPI проверяет сессию.
   - Если для пары `(media_id, user_id)` ffmpeg не запущен — старт subprocess'а, ждём ~2 секунды появления первого сегмента.
   - Возвращает текст `.m3u8`.
4. hls.js последовательно запрашивает `seg_00000.ts`, `seg_00001.ts`... → FastAPI отдаёт файлы из `/var/lib/mediasrv/hls/<stream_id>/`.
5. Каждые 10 сек плеер шлёт POST `/api/progress` с `{media_id, position_seconds}` → апсерт в `watch_progress`.
6. **Перемотка** на 30:00:
   - Плеер запрашивает сегмент с этой позицией.
   - Если сегмент ещё не сгенерирован — FastAPI убивает текущий ffmpeg для этого стрима, перезапускает с `-ss 00:30:00`.
   - Плеер автоматически перезапрашивает плейлист.
7. **Закрытие вкладки:**
   - Heartbeat (`/api/progress`) перестаёт приходить.
   - Watchdog в FastAPI: если для стрима нет heartbeat 60 секунд — `kill` ffmpeg, удаление папки `/var/lib/mediasrv/hls/<stream_id>/`.

### 6.4 Скачивание оригинала

1. На `/media/{id}` кнопка «Скачать».
2. GET `/api/download/{id}` →
   - Проверка сессии.
   - Стрим файла с `Content-Disposition: attachment; filename="<title>.<ext>"` и `Content-Type: application/octet-stream`.
   - Поддержка `Range`-запросов (для возобновления прерванной загрузки).

### 6.5 Удаление

1. На `/media/{id}` кнопка «Удалить» → JS-confirm.
2. POST `/api/media/{id}/delete` →
   - Если идёт стрим (есть процесс ffmpeg) — kill.
   - qBittorrent API: `POST /api/v2/torrents/delete` с `hashes=<hash>&deleteFiles=true` — удалит и торрент-статус, и файлы.
   - DELETE из `media_items` (CASCADE → `watch_progress`).
3. Редирект на `/library`.

### 6.6 Ошибочные сценарии

| Что произошло | Что делает система |
|---|---|
| ffmpeg упал во время стрима | Плеер получает 404 на следующий сегмент → фронт показывает «Поток упал, перезагрузите страницу». Ошибка логируется. |
| Диск переполнен во время загрузки | qBittorrent ставит торрент на паузу с ошибкой. На `/downloads` статус «Нет места». Пользователь должен удалить старое. |
| qBittorrent не отвечает | FastAPI возвращает 503 на действия с торрентами. Чтение библиотеки и просмотр готовых файлов продолжают работать. |
| Невалидный magnet | 400 + сообщение «Не похоже на magnet-ссылку». |
| 5+ неудачных логинов | Caddy → 429; fail2ban банит IP на 1 час через iptables. |
| 2FA-код не подходит | 401, без раскрытия деталей (валиден ли пароль или TOTP — снаружи неотличимо). |

## 7. Безопасность

### 7.1 Слои защиты

1. **Network exposure minimisation:** наружу только Caddy на 443 (+80 для ACME). FastAPI, qBittorrent, SQLite — bind `127.0.0.1`.
2. **TLS:** Caddy + Let's Encrypt, авто-обновление сертификатов.
3. **Авторизация:** username + bcrypt-пароль (cost=12) + TOTP (`pyotp`, окно ±30 сек) + 10 одноразовых backup-кодов.
4. **Сессии:** 256-битный токен в SQLite, expires 30 дней; cookie `HttpOnly + Secure + SameSite=Strict`.
5. **Rate limiting:** Caddy: ≤10 попыток `/login` с одного IP в минуту → 429.
6. **fail2ban:** 5 неудач за 5 минут → IP в бан на 1 час (iptables).
7. **CSRF:** все POST требуют CSRF-токен (HTMX автоматически кладёт в заголовок из meta-тега).
8. **Валидация входа:** magnet — регулярка; имена файлов — санитизация (никаких `../`, `\0`, длиннее 255); SQL — только параметризованный.
9. **Security headers** (Caddy): HSTS, CSP, X-Content-Type-Options, X-Frame-Options.
10. **Секреты:** `.env` (права `600`, не в git): `SESSION_SECRET`, `QBITTORRENT_PASSWORD`, `TOTP_ENCRYPTION_KEY`.
11. **Обновления:** `unattended-upgrades` для security-патчей Ubuntu.
12. **SSH-доступ админа:** только через Tailscale, порт 22 наружу не светится.

### 7.2 Создание пользователей

- Никакой публичной регистрации. Админ через `/admin/users` создаёт логин + одноразовый временный пароль.
- При первом входе пользователь обязан:
  - Сменить пароль (≥12 символов).
  - Активировать 2FA (сканировать QR, ввести один код для подтверждения).
  - Сохранить 10 backup-кодов.

## 8. Тестирование

### 8.1 Unit-тесты (pytest, < 30 сек)

- bcrypt: hash → verify, неправильный пароль → false.
- Сессии: создание, проверка, истечение.
- TOTP: генерация секрета, проверка валидного кода с фиксированным временем, отказ невалидному.
- Backup-коды: использование одноразовое.
- Валидация magnet: хорошие → ok, мусор → reject.
- Санитизация имён файлов: `../etc/passwd` → безопасный результат.
- Парсинг названия фильма: типичные имена → читаемое название.

### 8.2 Integration-тесты (pytest, изолированный qBittorrent в Docker)

- Полный логин с 2FA.
- Добавление magnet → проверка вызова к qBittorrent.
- Стриминг на тестовом mp4: запрос плейлиста → запрос сегмента → 200.
- Удаление: вызов FastAPI → файл стёрт, запись в БД ушла.

### 8.3 Smoke E2E (Playwright, по требованию)

- Войти → увидеть библиотеку.
- Добавить magnet → видно в `/downloads`.
- Открыть фильм → плеер инициализировался (без проверки фактического воспроизведения).

### 8.4 Чего не тестируем

- Качество транскодинга ffmpeg (это его дело, не наше).
- Реальную связность с реальным трекером (флаки).
- UI-полишинг (глазами).

## 9. Деплой и эксплуатация

### 9.1 Структура на сервере

```
/opt/mediasrv/                       ← код (git clone)
  app/                                  Python модули
  static/                               CSS/JS/иконки
  templates/                            Jinja2 шаблоны
  app.db                                SQLite (на system-диске)
  .env                                  секреты (chmod 600)
  venv/                                 Python venv
/var/lib/mediasrv/hls/                ← временные HLS-сегменты (system)
/var/log/mediasrv/                    ← логи (system, logrotate)
/etc/caddy/Caddyfile                  ← конфиг Caddy
/srv/Общее/downloads/                 ← скачанные медиа (1 ТБ data-диск)
/srv/Общее/backups/                   ← бэкапы app.db (1 ТБ data-диск)
```

Под приложением — пользователь `mediasrv` (не root, без shell).

**Замечание про путь `/srv/Общее/`:** имя содержит кириллицу — это технически работает на Ubuntu с UTF-8 локалью, но требует аккуратности в коде:
- Все вызовы shell/ffmpeg оборачиваем в правильное экранирование (не `$VAR`, а `"$VAR"`).
- Пути в Python используем через `pathlib.Path` (он сам корректно работает с UTF-8).
- В `.env` путь хранится как UTF-8 строка; Locale на сервере должна быть `C.UTF-8` или `ru_RU.UTF-8` (проверим в install.sh).
- Если в будущем захочется подстраховаться — установка может опционально создать ASCII-симлинк `/srv/shared → /srv/Общее` и использовать его в скриптах. По умолчанию не делаем — лишняя сущность.

### 9.2 systemd-сервисы

- `caddy.service` — рестарт при падении.
- `qbittorrent-nox.service` — рестарт при падении.
- `mediasrv.service` (uvicorn) — рестарт при падении.

### 9.3 install.sh (один раз при первичной установке)

1. `apt install caddy qbittorrent-nox ffmpeg python3.11 python3.11-venv fail2ban git`.
2. Создание пользователя `mediasrv` и директорий.
3. Запрос пути к корню data-диска (по умолчанию `/srv/Общее`); проверка, что путь существует и записываем; при необходимости создаются подпапки `downloads/` и `backups/`.
4. Клонирование репо в `/opt/mediasrv/`, `pip install` в venv.
5. Генерация `.env` со случайными секретами.
6. Создание первого админа (запрос username + временный пароль в терминале).
7. Включение `unattended-upgrades`.
8. Установка systemd-юнитов, Caddyfile, fail2ban-jail.
9. Старт всех сервисов.
10. Печать инструкции: «откройте на роутере forward 80 и 443 на этот IP», «настройте DDNS», «зайдите на https://your-domain/login».

### 9.4 update.sh

`git pull → pip install → миграции БД (Alembic) → systemctl restart mediasrv`. Идемпотентно.

### 9.5 Бэкапы

- `app.db` → cron ежедневно в `/srv/Общее/backups/app-YYYY-MM-DD.db`, ротация 30 копий.
- `.env` пользователь сохраняет себе сразу после установки (там секреты).
- `/srv/Общее/downloads/` — **не бэкапим** (большие, в крайнем случае перекачаются).

### 9.6 Логи и мониторинг

- `journalctl -u mediasrv` и `/var/log/mediasrv/app.log` (logrotate).
- `/admin/health` (только для админа): свободное место на дисках, статус qBittorrent, активные ffmpeg-процессы, последние 50 ошибок из лога.

### 9.7 DDNS

DuckDNS (бесплатно) или Cloudflare DDNS. Cron каждые 5 минут обновляет A-запись.

### 9.8 Ожидаемая нагрузка

| Состояние | CPU | RAM | Диск |
|---|---|---|---|
| Idle (Caddy + FastAPI + qBittorrent) | <5% | ~300 МБ | — |
| Активная торрент-загрузка | ~10% | ~500 МБ | по размеру торрента |
| 1 поток HLS H.264 1080p → H.264 1080p | ~70% от 1 ядра | +200 МБ | до ~2 ГБ временных HLS |
| 1 поток HLS H.265/HEVC 4K → H.264 1080p | ~почти всё CPU (i7-2600 без QSV для HEVC) | +500 МБ | то же |

## 10. Стек и зависимости

| Слой | Технология | Версия (минимум) |
|---|---|---|
| ОС | Ubuntu | 22.04 LTS+ |
| Reverse proxy | Caddy | 2.7+ |
| Бэкенд язык | Python | 3.11+ |
| Веб-фреймворк | FastAPI + Uvicorn | актуальный |
| Шаблоны | Jinja2 | актуальный |
| ORM | SQLAlchemy + Alembic | 2.x |
| БД | SQLite | 3.40+ |
| Frontend dynamic | HTMX | 1.9+ |
| Видео плеер | hls.js | 1.5+ |
| Торрент-клиент | qBittorrent-nox | 4.5+ |
| Транскодинг | ffmpeg | 5.0+ |
| Auth | bcrypt + pyotp | актуальный |
| Защита подбора | fail2ban | системный |
| HTTPS | Let's Encrypt через Caddy | — |
| Tests | pytest + Playwright | актуальный |
| Админский доступ | Tailscale | актуальный |

## 11. Структура репозитория

```
/
├── app/
│   ├── __init__.py
│   ├── main.py              FastAPI app + роутинг
│   ├── config.py            чтение .env
│   ├── db.py                SQLAlchemy engine + сессии
│   ├── models.py            ORM-модели
│   ├── auth/
│   │   ├── routes.py
│   │   ├── totp.py
│   │   └── sessions.py
│   ├── torrents/
│   │   ├── routes.py
│   │   ├── qbittorrent_client.py
│   │   └── library_scanner.py    фоновая задача
│   ├── library/
│   │   └── routes.py
│   ├── streaming/
│   │   ├── routes.py
│   │   ├── ffmpeg_runner.py
│   │   └── stream_registry.py    учёт активных стримов
│   ├── download/
│   │   └── routes.py
│   └── admin/
│       └── routes.py
├── static/
│   ├── style.css
│   ├── htmx.min.js
│   └── hls.min.js
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── library.html
│   ├── media.html
│   ├── add_torrent.html
│   ├── downloads.html
│   ├── admin_users.html
│   └── admin_health.html
├── migrations/              Alembic
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── fixtures/
│       └── sample.mp4
├── deploy/
│   ├── install.sh
│   ├── update.sh
│   ├── Caddyfile.template
│   ├── systemd/
│   │   ├── mediasrv.service
│   │   └── qbittorrent-nox.service
│   └── fail2ban/
│       └── mediasrv.conf
├── docs/
│   └── superpowers/specs/
│       └── 2026-05-02-family-media-server-design.md
├── .env.example
├── requirements.txt
├── pyproject.toml
└── README.md
```

## 12. Открытые вопросы

Нет. Все архитектурные решения зафиксированы; для перехода к плану реализации достаточно.
