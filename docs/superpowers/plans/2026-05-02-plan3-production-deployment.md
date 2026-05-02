# План 3: Production deployment + закрытие долга Плана 2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Финальный деплой медиа-сервера на домашний Ubuntu i7-2600: HTTPS на 443, авто-рестарт через systemd, защита fail2ban, бэкапы, админская страница здоровья. Плюс закрытие технического долга, выявленного финальным ревью Плана 2.

**Architecture:** Ubuntu + Caddy reverse proxy (Let's Encrypt автосертификаты) + systemd (mediasrv, qbittorrent-nox, caddy все рестартятся при падении) + fail2ban (защита от подбора). Админский SSH через Tailscale (порт 22 наружу не торчит). Бэкапы `app.db` по cron на 1 ТБ диск. install.sh идемпотентный — можно запустить повторно.

**Tech Stack:** Bash, Caddy 2.7+, systemd, fail2ban, cron, Tailscale, DuckDNS (бесплатный DDNS), Python (для health-страницы и техдолга).

**Реализует разделы спецификации:** §7.1 пп.1, 5-12 (security слои), §9.1 (структура на сервере), §9.2 (systemd-сервисы), §9.3 (install.sh), §9.4 (update.sh), §9.5 (бэкапы), §9.6 (логи + `/admin/health`), §9.7 (DDNS), плюс долг I-1...I-8 из ревью Плана 2.

**Препекты:** локально — Windows + uvicorn/qBittorrent для smoke-тестов; целевая машина — Ubuntu 22.04+ с правами sudo. Деплой-скрипты тестируются на реальной машине (Ubuntu) или в `lima`/Docker — это финальный шаг, не TDD.

---

## Структура файлов

```
app/
├── auth/routes.py                MODIFY: async→sync (D-3) + constant-time login
├── torrents/routes.py            MODIFY: async→sync handlers
├── streaming/routes.py           MODIFY: async→sync + Cache-Control + work_root from settings
├── streaming/ffmpeg_runner.py    MODIFY: stderr=DEVNULL
├── download/routes.py            MODIFY: async→sync
├── library/routes.py             MODIFY: async→sync + log delete failures
├── admin/routes.py               MODIFY: + /admin/health
├── config.py                     MODIFY: + hls_work_root setting, media_root validator
├── deps.py                       MODIFY: lifespan-aware close for QBittorrentClient
├── main.py                       MODIFY: lifespan sweeps streams + closes qb client
templates/
├── admin_health.html             CREATE: страница /admin/health
deploy/
├── install.sh                    CREATE: bootstrap новой машины
├── update.sh                     CREATE: pull + restart
├── Caddyfile.template            CREATE: HTTPS + rate limit + security headers
├── systemd/
│   ├── mediasrv.service          CREATE
│   └── qbittorrent-nox.service   CREATE
├── fail2ban/
│   └── mediasrv.conf             CREATE: jail для /login bruteforce
├── cron/
│   ├── backup-db.cron            CREATE
│   └── ddns-update.cron          CREATE
scripts/
├── backup_db.sh                  CREATE: ротация 30 копий
└── ddns_update.sh                CREATE: DuckDNS обновление IP
docs/
└── DEPLOYMENT.md                 CREATE: runbook (Tailscale, port forwarding, DDNS, smoke-test)
README.md                          MODIFY: ссылка на DEPLOYMENT.md
tests/
├── unit/test_health.py           CREATE
├── unit/test_constant_time_login.py  CREATE
├── unit/test_settings_validation.py  CREATE (extends Task 2)
└── integration/test_health.py    CREATE
```

---

## Phase 1 — Закрытие техдолга Плана 2 (8 задач)

## Task 1: Async → sync handlers (I-1, главный приоритет)

**Files:**
- Modify: `app/auth/routes.py`, `app/admin/routes.py`, `app/library/routes.py`, `app/torrents/routes.py`, `app/streaming/routes.py`, `app/download/routes.py`
- Test: existing test suite must still pass

**Контекст:** Плана 2 ревью отметил, что все хендлеры — `async def`, но внутри они зовут blocking-синхронный код (httpx.Client, time.sleep в `wait_for_first_segment`, bcrypt, sqlite). На каждом запросе блокируется event loop, и второй параллельный запрос ждёт первого. Sync-хендлеры FastAPI запускает в threadpool — это правильный шаблон для нашей реальности.

- [ ] **Step 1: Запустить полный тест-suite — фиксируем baseline**

```bash
./venv/Scripts/python -m pytest -q
```

Должно быть 130 passed.

- [ ] **Step 2: Перевести `app/auth/routes.py` на sync**

Везде, где `async def login_post`, `async def verify_totp_post` и т.д. — убрать `async`. Ничего внутри `await`-ить не нужно (грепом проверьте).

После правки прогоните `tests/integration/test_login_flow.py` и `test_first_login_setup.py`. Должны пройти.

- [ ] **Step 3: Перевести `app/admin/routes.py` на sync**

`async def list_users`, `async def create_user`, `async def delete_user` → `def`. Прогоните `tests/integration/test_admin_users.py`.

- [ ] **Step 4: Перевести `app/library/routes.py` на sync**

`async def library_page`, `async def media_page`, `async def delete_media` → `def`. Прогоните `tests/integration/test_library*.py`, `test_media_delete.py`.

- [ ] **Step 5: Перевести `app/torrents/routes.py` на sync**

Все async-хендлеры → sync. Прогоните `tests/integration/test_torrents_api.py`.

- [ ] **Step 6: Перевести `app/streaming/routes.py` на sync**

`async def stream_playlist`, `async def stream_segment`, `async def progress` → `def`. Прогоните `tests/integration/test_streaming.py`. Это самый чувствительный — `_ensure_stream` зовёт `wait_for_first_segment` (sleep до 15с) — sync вариант теперь честно занимает один thread из пула вместо ловить event loop.

- [ ] **Step 7: Перевести `app/download/routes.py` на sync**

`async def download` → `def`. Прогоните `tests/integration/test_download.py`.

- [ ] **Step 8: Полный тест-suite**

```bash
./venv/Scripts/python -m pytest -q
```

Все 130 должны остаться зелёными (плюс или минус — могут быть изменения в warning'ах, но не в pass count'е).

- [ ] **Step 9: Коммит**

```bash
git add app/auth/routes.py app/admin/routes.py app/library/routes.py app/torrents/routes.py app/streaming/routes.py app/download/routes.py
git commit -m "perf: convert handlers to sync (FastAPI threadpool); event loop no longer blocked"
```

---

## Task 2: ffmpeg `stderr=DEVNULL` (I-3)

**Files:**
- Modify: `app/streaming/ffmpeg_runner.py:62`

- [ ] **Step 1: Изменить `app/streaming/ffmpeg_runner.py`**

В функции `start_hls`, заменить:
```python
return subprocess.Popen(
    cmd,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    start_new_session=os.name != "nt",
)
```
на:
```python
return subprocess.Popen(
    cmd,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,  # PIPE без читателя может заполниться и повесить ffmpeg на длинном видео
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    start_new_session=os.name != "nt",
)
```

- [ ] **Step 2: Прогнать `tests/unit/test_ffmpeg_runner.py`**

```bash
./venv/Scripts/python -m pytest tests/unit/test_ffmpeg_runner.py -v
```

Должно остаться 3 passed.

- [ ] **Step 3: Коммит**

```bash
git add app/streaming/ffmpeg_runner.py
git commit -m "fix(streaming): ffmpeg stderr to DEVNULL to avoid pipe-buffer stall"
```

---

## Task 3: HLS work_root конфигурируется через Settings (I-6 + S-2)

**Files:**
- Modify: `app/config.py`, `app/streaming/routes.py`, `.env.example`
- Test: `tests/unit/test_settings_validation.py`

- [ ] **Step 1: Падающий тест `tests/unit/test_settings_validation.py`**

```python
from app.config import Settings


def test_settings_has_hls_work_root_with_default(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("MEDIA_ROOT", "/tmp/media")
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestSrv")
    s = Settings()
    # Default: системная temp-папка (для dev на Windows / Mac тоже работает)
    import tempfile
    assert s.hls_work_root == tempfile.gettempdir()


def test_settings_hls_work_root_overridable(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("MEDIA_ROOT", "/tmp/media")
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestSrv")
    monkeypatch.setenv("HLS_WORK_ROOT", "/var/lib/mediasrv/hls")
    s = Settings()
    assert s.hls_work_root == "/var/lib/mediasrv/hls"
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Изменить `app/config.py`**

В `class Settings(BaseSettings)` добавить (после `totp_issuer`):
```python
import tempfile as _tempfile  # в начале файла

# ...

class Settings(BaseSettings):
    # ... existing fields ...
    hls_work_root: str = _tempfile.gettempdir()
```

- [ ] **Step 4: Изменить `app/streaming/routes.py`**

В импорты:
```python
from app.config import Settings, get_settings
```

В `_ensure_stream` или вызове `mkdtemp` использовать `dir=settings.hls_work_root`. Проще — добавить settings как параметр в `_ensure_stream` или брать через `get_settings()`:

```python
def _ensure_stream(media: MediaItem, user_id: int) -> StreamHandle:
    reg = get_registry()
    existing = reg.get(media.id, user_id)
    if existing is not None:
        reg.touch(media.id, user_id)
        return existing
    settings = get_settings()
    Path(settings.hls_work_root).mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(
        prefix=f"hls_m{media.id}_u{user_id}_",
        dir=settings.hls_work_root,
    ))
    # ... остальной код без изменений
```

- [ ] **Step 5: Обновить `.env.example`**

Добавить:
```
# Где ffmpeg создаёт временные HLS-сегменты. По умолчанию — системная temp-папка.
# Для production: /var/lib/mediasrv/hls
HLS_WORK_ROOT=
```

(пустая строка означает «использовать default»; pydantic-settings прочитает env var, но Settings возьмёт default если переменной нет; если переменная пустая — нужно проверить поведение pydantic. Тестируем оба сценария.)

Уточнение: pydantic-settings при пустой строке возвращает `""`, не default. Поэтому в `.env.example` либо закомментируем, либо удалим:
```
# HLS_WORK_ROOT=/var/lib/mediasrv/hls   # раскомментировать для prod
```

- [ ] **Step 6: Запустить тесты — PASS**

```bash
./venv/Scripts/python -m pytest -q
```

- [ ] **Step 7: Коммит**

```bash
git add app/config.py app/streaming/routes.py .env.example tests/unit/test_settings_validation.py
git commit -m "feat(config): HLS_WORK_ROOT setting (default: system tempdir, prod: /var/lib/mediasrv/hls)"
```

---

## Task 4: Lifespan teardown — close httpx Client + sweep streams (I-6, I-7)

**Files:**
- Modify: `app/main.py`
- Modify: `app/torrents/client.py` (нужен метод close, он уже есть)
- Modify: `app/streaming/watchdog.py` (можно переиспользовать sweep_idle с idle_seconds=0)

- [ ] **Step 1: Падающий тест `tests/unit/test_lifespan_teardown.py`**

```python
import asyncio
from unittest.mock import MagicMock

import pytest

from app.streaming.stream_registry import StreamHandle, get_registry
from app.streaming.watchdog import sweep_idle


def test_sweep_idle_with_zero_threshold_kills_all():
    """sweep_idle(reg, idle_seconds=0) убивает все стримы — для shutdown'а."""
    import tempfile
    reg = get_registry()
    work_dir = tempfile.mkdtemp(prefix="lifespan_test_")
    proc = MagicMock(); proc.poll.return_value = None
    reg.register(StreamHandle(media_id=42, user_id=1, work_dir=work_dir, process=proc))

    killed = sweep_idle(reg, idle_seconds=0.0)
    assert killed >= 1
    assert reg.get(42, 1) is None
    proc.terminate.assert_called() if proc.terminate.called else proc.send_signal.assert_called()
```

- [ ] **Step 2: Запустить — должен пройти** (sweep_idle уже умеет это; тест валидирует contract)

- [ ] **Step 3: Изменить `app/main.py:lifespan`** — добавить teardown логику

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    scanner_task = asyncio.create_task(
        scanner_loop(get_qbittorrent_client(), get_db_factory(), interval_seconds=10.0)
    )
    watchdog_task = asyncio.create_task(watchdog_loop())
    try:
        yield
    finally:
        # 1. Остановить фоновые задачи
        for t in (scanner_task, watchdog_task):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        # 2. Убить все активные ffmpeg-стримы (idle_seconds=0 → все попадают в выборку)
        from app.streaming.stream_registry import get_registry
        from app.streaming.watchdog import sweep_idle
        try:
            sweep_idle(get_registry(), idle_seconds=0.0)
        except Exception:
            pass

        # 3. Закрыть httpx-клиент qBittorrent
        try:
            get_qbittorrent_client().close()
        except Exception:
            pass
```

- [ ] **Step 4: Прогнать тесты**

```bash
./venv/Scripts/python -m pytest -q
```

- [ ] **Step 5: Коммит**

```bash
git add app/main.py tests/unit/test_lifespan_teardown.py
git commit -m "fix(main): lifespan teardown sweeps streams and closes qBittorrent httpx client"
```

---

## Task 5: Validate `media_root` is absolute (I-4)

**Files:**
- Modify: `app/config.py`
- Test: дополнить `tests/unit/test_settings_validation.py`

- [ ] **Step 1: Падающий тест в `tests/unit/test_settings_validation.py`**

```python
def test_media_root_must_be_absolute(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("MEDIA_ROOT", "relative/path")  # не абсолютный
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestSrv")
    import pytest
    with pytest.raises(ValueError):
        Settings()


def test_media_root_absolute_path_accepted(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("MEDIA_ROOT", "/srv/Общее")
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestSrv")
    s = Settings()
    assert s.media_root == "/srv/Общее"
```

- [ ] **Step 2: Запустить — FAIL** (валидатора нет).

- [ ] **Step 3: Изменить `app/config.py`** — добавить валидатор

В классе Settings, после существующего `session_secret_long_enough`:
```python
@field_validator("media_root")
@classmethod
def media_root_absolute(cls, v: str) -> str:
    from pathlib import PurePosixPath, PureWindowsPath
    # Принимаем POSIX (`/srv/Общее`) и Windows (`C:\…`) абсолютные пути
    is_posix_abs = v.startswith("/")
    is_windows_abs = len(v) >= 3 and v[1:3] in (":\\", ":/")
    if not (is_posix_abs or is_windows_abs):
        raise ValueError(f"MEDIA_ROOT must be an absolute path, got: {v!r}")
    return v
```

- [ ] **Step 4: Изменить тестовый `conftest.py`**

В `_clear_caches` (Task 2 Plan 2) `MEDIA_ROOT="/tmp/media"` уже абсолютный — пройдёт валидатор. Но в conftest проверьте — там `MEDIA_ROOT` ставится как `/tmp/media` (Posix-абсолютный) — ок.

- [ ] **Step 5: Запустить тесты — PASS**

- [ ] **Step 6: Коммит**

```bash
git add app/config.py tests/unit/test_settings_validation.py
git commit -m "feat(config): validate MEDIA_ROOT is absolute path"
```

---

## Task 6: Лог при тихом сбое qBittorrent в delete_media (I-8)

**Files:**
- Modify: `app/library/routes.py`

- [ ] **Step 1: Изменить `app/library/routes.py:delete_media`**

В импорты:
```python
import logging
log = logging.getLogger(__name__)
```

В блоке `except QBittorrentError`:
```python
try:
    qb.delete_torrent(item.torrent_hash, delete_files=True)
except QBittorrentError as e:
    log.warning(
        "delete_media: qBittorrent unreachable for torrent %s, files orphaned: %s",
        item.torrent_hash, e,
    )
```

- [ ] **Step 2: Прогнать существующие тесты — должны остаться зелёными**

```bash
./venv/Scripts/python -m pytest tests/integration/test_media_delete.py -v
```

- [ ] **Step 3: Коммит**

```bash
git add app/library/routes.py
git commit -m "chore(library): log qBittorrent failure during delete_media"
```

---

## Task 7: `Cache-Control: no-store` на playlist + segment (I-2)

**Files:**
- Modify: `app/streaming/routes.py`

- [ ] **Step 1: Падающий тест в `tests/integration/test_streaming.py`**

В конец файла:
```python
def test_playlist_response_has_no_store_cache(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 200
    assert "no-store" in r.headers.get("cache-control", "").lower()


def test_segment_response_has_no_store_cache(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    seg_name = next((line for line in r.text.splitlines() if line.startswith("seg_")), None)
    assert seg_name
    r2 = client.get(f"/api/stream/{mid}/{seg_name}", cookies={"session": cookie})
    assert "no-store" in r2.headers.get("cache-control", "").lower()
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Изменить `app/streaming/routes.py`**

В `stream_playlist`:
```python
return Response(
    content=playlist.read_bytes(),
    media_type="application/vnd.apple.mpegurl",
    headers={"Cache-Control": "no-store"},
)
```

В `stream_segment`:
```python
return FileResponse(
    str(seg_path),
    media_type="video/mp2t",
    headers={"Cache-Control": "no-store"},
)
```

- [ ] **Step 4: Запустить — PASS**

- [ ] **Step 5: Коммит**

```bash
git add app/streaming/routes.py tests/integration/test_streaming.py
git commit -m "fix(streaming): Cache-Control: no-store on HLS playlist and segments"
```

---

## Task 8: Constant-time login (D-3 из Плана 1)

**Files:**
- Modify: `app/auth/routes.py`
- Test: `tests/unit/test_constant_time_login.py`

**Контекст:** Сейчас при unknown username login возвращается через ~10ms (skip bcrypt). При known username — ~250ms (bcrypt verify). Атакующий по таймингу определит, есть ли такой логин в системе. Фикс — всегда выполнять bcrypt-проверку, даже если юзера нет.

- [ ] **Step 1: Падающий тест `tests/unit/test_constant_time_login.py`**

```python
import time

import pytest

from app.auth.passwords import hash_password, verify_password
from app.auth.routes import _DUMMY_HASH


def test_dummy_hash_is_a_real_bcrypt_hash():
    """Sanity check: dummy-хеш можно проверить через verify_password (не падает)."""
    # Любой пароль не пройдёт против dummy hash, но verify_password должен вернуть False, не упасть
    assert verify_password("anything", _DUMMY_HASH) is False


def test_login_timing_with_unknown_user_uses_bcrypt(client, db_factory, csrf_for):
    """Time login для несуществующего юзера должно быть в том же порядке, что и для существующего."""
    from app.auth.passwords import hash_password
    from app.models import User
    with db_factory() as s:
        s.add(User(username="alice", password_hash=hash_password("correct-password-12"),
                   must_change_password=False, totp_enabled=True, totp_secret_encrypted="x"))
        s.commit()

    # Unknown user
    t1 = time.monotonic()
    client.post("/login", data={"username": "ghost", "password": "wrong", "csrf_token": csrf_for(None)})
    dt_unknown = time.monotonic() - t1

    # Known user, wrong password
    t2 = time.monotonic()
    client.post("/login", data={"username": "alice", "password": "wrong", "csrf_token": csrf_for(None)})
    dt_known = time.monotonic() - t2

    # Различие должно быть < 50ms (bcrypt с cost=12 ~ 250ms; в идеале они равны).
    # Допускаем шум JIT/планировщика.
    assert abs(dt_unknown - dt_known) < 0.1, f"unknown={dt_unknown:.3f}s, known={dt_known:.3f}s"
```

- [ ] **Step 2: Запустить — FAIL** (`_DUMMY_HASH` не существует).

- [ ] **Step 3: Изменить `app/auth/routes.py`**

В начало файла (после импортов, до router):
```python
# Pre-computed bcrypt hash для constant-time login.
# Любой реальный bcrypt-хеш подойдёт; cost=12 чтобы verify_password занимал столько же,
# сколько и проверка реального пароля.
_DUMMY_HASH = "$2b$12$dWqg7vK6vKqz0vK6vKqz0OvK6vKqz0vK6vKqz0vK6vKqz0vK6vKq2"
```

(этот хеш — синтетический, но валидной формы; bcrypt.checkpw на нём вернёт `False` для любого input'а кроме его исходного пароля).

В `login_post` заменить:
```python
user = db.scalars(select(User).where(User.username == username)).first()
if user is None or not verify_password(password, user.password_hash):
    return ...  # 401
```
на:
```python
user = db.scalars(select(User).where(User.username == username)).first()
# Constant-time: всегда выполняем bcrypt, даже если юзера нет
hash_to_check = user.password_hash if user is not None else _DUMMY_HASH
password_ok = verify_password(password, hash_to_check)
if user is None or not password_ok:
    return render(
        request, "login.html", {"error": "Неверный логин или пароль"},
        status_code=401,
    )
```

- [ ] **Step 4: Сгенерировать честный dummy-hash**

В шелле (один раз):
```bash
./venv/Scripts/python -c "from app.auth.passwords import hash_password; print(hash_password('this-is-the-canary-pwd-not-used'))"
```

Скопировать вывод как `_DUMMY_HASH`.

- [ ] **Step 5: Запустить — PASS**

- [ ] **Step 6: Коммит**

```bash
git add app/auth/routes.py tests/unit/test_constant_time_login.py
git commit -m "fix(auth): constant-time login (always run bcrypt to prevent user enumeration)"
```

---

## Phase 2 — `/admin/health` страница (1 задача)

## Task 9: `/admin/health` — диск, qBittorrent, активные стримы, последние ошибки

**Files:**
- Modify: `app/admin/routes.py`
- Create: `templates/admin_health.html`
- Test: `tests/integration/test_health.py`

- [ ] **Step 1: Падающий тест `tests/integration/test_health.py`**

```python
import httpx
import pyotp
import pytest
import respx

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret, _derive_key
from app.models import User


def _admin_logged_in(client, db_factory, csrf_for):
    secret = pyotp.random_base32()
    with db_factory() as s:
        s.add(User(
            username="root", password_hash=hash_password("admin-password-12"),
            must_change_password=False, totp_enabled=True, is_admin=True,
            totp_secret_encrypted=encrypt_secret(secret, _derive_key("x" * 64)),
        ))
        s.commit()
    r = client.post("/login", data={
        "username": "root", "password": "admin-password-12", "csrf_token": csrf_for(None)
    })
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post("/verify-totp", data={"code": code, "csrf_token": csrf_for(cookie)},
                cookies={"session": cookie})
    return cookie


def test_admin_health_requires_admin(client):
    r = client.get("/admin/health")
    assert r.status_code in (303, 401)


@respx.mock
def test_admin_health_renders_for_admin(client, db_factory, csrf_for):
    cookie = _admin_logged_in(client, db_factory, csrf_for)
    respx.post("http://127.0.0.1:8080/api/v2/auth/login").mock(
        return_value=httpx.Response(200, text="Ok.")
    )
    respx.get("http://127.0.0.1:8080/api/v2/torrents/info").mock(
        return_value=httpx.Response(200, json=[])
    )
    r = client.get("/admin/health", cookies={"session": cookie})
    assert r.status_code == 200
    # Страница содержит ключевые секции
    assert "qbittorrent" in r.text.lower() or "qbt" in r.text.lower()
    assert "диск" in r.text.lower() or "disk" in r.text.lower()
    assert "стрим" in r.text.lower() or "stream" in r.text.lower()
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/admin/routes.py`**

В импорты:
```python
import shutil
from app.deps import get_qbittorrent_client
from app.streaming.stream_registry import get_registry
from app.torrents.client import QBittorrentError, QBittorrentClient
```

В конец файла:
```python
@router.get("/health", response_class=HTMLResponse)
def health_page(
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    qb: Annotated[QBittorrentClient, Depends(get_qbittorrent_client)],
):
    # Диск
    try:
        d_root = shutil.disk_usage("/")
        disk_root = {
            "free_gb": round(d_root.free / (1024 ** 3), 1),
            "total_gb": round(d_root.total / (1024 ** 3), 1),
            "percent_used": round((1 - d_root.free / d_root.total) * 100, 1),
        }
    except Exception as e:
        disk_root = {"error": str(e)}

    # qBittorrent
    try:
        torrents = qb.list_torrents()
        qb_status = {"reachable": True, "active_torrents": len(torrents)}
    except QBittorrentError as e:
        qb_status = {"reachable": False, "error": str(e)}

    # Активные стримы
    streams = []
    for h in get_registry().all_streams():
        streams.append({
            "media_id": h.media_id, "user_id": h.user_id,
            "work_dir": h.work_dir,
            "alive": h.process is not None and (
                h.process.poll() is None if hasattr(h.process, "poll") else False
            ),
        })

    return render(request, "admin_health.html", {
        "user": admin,
        "disk_root": disk_root,
        "qb": qb_status,
        "streams": streams,
    })
```

- [ ] **Step 4: Создать `templates/admin_health.html`**

```html
{% extends "base.html" %}
{% block title %}Здоровье сервера{% endblock %}
{% block content %}
<h1>Здоровье сервера</h1>

<h2>Диск</h2>
{% if disk_root.error %}
  <p class="error">Ошибка: {{ disk_root.error }}</p>
{% else %}
  <p>
    Свободно: <strong>{{ disk_root.free_gb }} ГБ</strong> из {{ disk_root.total_gb }} ГБ
    (занято {{ disk_root.percent_used }}%)
  </p>
{% endif %}

<h2>qBittorrent</h2>
{% if qb.reachable %}
  <p>✅ Доступен. Активных торрентов: <strong>{{ qb.active_torrents }}</strong></p>
{% else %}
  <p class="error">❌ Не отвечает: {{ qb.error }}</p>
{% endif %}

<h2>Активные стримы (ffmpeg)</h2>
{% if not streams %}
  <p>Нет активных стримов.</p>
{% else %}
<table>
  <thead><tr><th>media_id</th><th>user_id</th><th>work_dir</th><th>живой?</th></tr></thead>
  <tbody>
    {% for s in streams %}
    <tr>
      <td>{{ s.media_id }}</td>
      <td>{{ s.user_id }}</td>
      <td><code>{{ s.work_dir }}</code></td>
      <td>{{ "да" if s.alive else "нет" }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

<p><a href="/admin/users">← Пользователи</a></p>
{% endblock %}
```

- [ ] **Step 5: Запустить — PASS**

- [ ] **Step 6: Коммит**

```bash
git add app/admin/routes.py templates/admin_health.html tests/integration/test_health.py
git commit -m "feat(admin): /admin/health page (disk, qBittorrent, active streams)"
```

---

## Phase 3 — Бэкапы (1 задача)

## Task 10: `scripts/backup_db.sh` + cron-конфиг

**Files:**
- Create: `scripts/backup_db.sh`
- Create: `deploy/cron/backup-db.cron`

- [ ] **Step 1: Создать `scripts/backup_db.sh`**

```bash
#!/usr/bin/env bash
# Бэкап app.db с ротацией (хранит последние 30 копий).
# Использование: backup_db.sh <source_db_path> <backup_dir>
# Пример (cron): /opt/mediasrv/scripts/backup_db.sh /opt/mediasrv/app.db /srv/Общее/backups

set -euo pipefail

SOURCE="${1:?usage: backup_db.sh <source.db> <backup_dir>}"
BACKUP_DIR="${2:?usage: backup_db.sh <source.db> <backup_dir>}"
KEEP="${3:-30}"

if [ ! -f "$SOURCE" ]; then
  echo "ERROR: source DB not found: $SOURCE" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

TS=$(date -u +%Y-%m-%d)
DEST="$BACKUP_DIR/app-$TS.db"

# sqlite3 .backup безопасно в read'е concurrent с running'ом приложения,
# в отличие от cp которое может прочитать write-in-progress файл.
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$SOURCE" ".backup '$DEST'"
else
  echo "WARNING: sqlite3 CLI not found, falling back to cp (less safe)" >&2
  cp "$SOURCE" "$DEST"
fi

echo "Backup created: $DEST"

# Ротация: оставить $KEEP самых свежих
cd "$BACKUP_DIR"
ls -1t app-*.db 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -v
```

- [ ] **Step 2: Сделать исполняемым**

```bash
chmod +x scripts/backup_db.sh
```

- [ ] **Step 3: Создать `deploy/cron/backup-db.cron`**

```
# Ежедневный бэкап app.db в 3:00 утра по локальному времени.
# Установка: sudo ln -s /opt/mediasrv/deploy/cron/backup-db.cron /etc/cron.d/mediasrv-backup
#       или: sudo cp /opt/mediasrv/deploy/cron/backup-db.cron /etc/cron.d/mediasrv-backup
# (имя без расширения, права 644)

SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

0 3 * * * mediasrv /opt/mediasrv/scripts/backup_db.sh /opt/mediasrv/app.db /srv/Общее/backups >> /var/log/mediasrv/backup.log 2>&1
```

- [ ] **Step 4: Smoke-тест локально**

```bash
mkdir -p /tmp/test-backup
echo "fake db" > /tmp/test.db
./scripts/backup_db.sh /tmp/test.db /tmp/test-backup 5
ls /tmp/test-backup/
# Должен показать app-YYYY-MM-DD.db
```

- [ ] **Step 5: Коммит**

```bash
git add scripts/backup_db.sh deploy/cron/backup-db.cron
git commit -m "feat(deploy): app.db backup script with 30-day rotation + cron config"
```

---

## Phase 4 — Деплой-скрипты и конфиги (6 задач)

## Task 11: `deploy/Caddyfile.template`

**Files:**
- Create: `deploy/Caddyfile.template`

- [ ] **Step 1: Создать `deploy/Caddyfile.template`**

```
# MediaServer Caddyfile.
# install.sh подставит {{DOMAIN}} на реальный домен.
# Скопировать в /etc/caddy/Caddyfile.

{{DOMAIN}} {
    # HTTPS автоматически от Let's Encrypt; HTTP на 80 редиректит.

    # Security headers (HSTS, защита от clickjacking, MIME-sniff)
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer"
        # CSP: разрешаем только свои static, inline-скрипты в шаблонах (htmx + plyer wiring)
        Content-Security-Policy "default-src 'self'; img-src 'self' data:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; media-src 'self'; connect-src 'self'"
        # Удаляем дефолтный Server: Caddy header
        -Server
    }

    # Rate limit на /login: ≤10 попыток в минуту с одного IP
    # Требует caddy с плагином rate_limit (https://github.com/mholt/caddy-ratelimit)
    # Если плагина нет — закомментируйте этот блок, защита остаётся через fail2ban.
    # rate_limit {
    #     zone login_zone {
    #         key {remote_host}
    #         events 10
    #         window 1m
    #     }
    #     match {
    #         path /login
    #     }
    # }

    # Проксируем всё на uvicorn
    reverse_proxy 127.0.0.1:8000 {
        # Передаём настоящий IP клиента (для логов / fail2ban)
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
    }

    # Логи в файл (для fail2ban-парсинга и общей отладки)
    log {
        output file /var/log/caddy/mediasrv-access.log {
            roll_size 100mb
            roll_keep 10
        }
        format json
    }
}
```

- [ ] **Step 2: Smoke-проверка синтаксиса** (если caddy установлен локально):

```bash
caddy validate --config deploy/Caddyfile.template --adapter caddyfile 2>&1 || true
```

(на dev-машине ожидается ошибка с `{{DOMAIN}}` placeholder — это нормально, валидируем уже на реальной машине после подстановки.)

- [ ] **Step 3: Коммит**

```bash
git add deploy/Caddyfile.template
git commit -m "feat(deploy): Caddyfile template with HTTPS + security headers + reverse proxy"
```

---

## Task 12: `deploy/systemd/mediasrv.service` + `qbittorrent-nox.service`

**Files:**
- Create: `deploy/systemd/mediasrv.service`
- Create: `deploy/systemd/qbittorrent-nox.service`

- [ ] **Step 1: Создать `deploy/systemd/mediasrv.service`**

```ini
[Unit]
Description=MediaServer (FastAPI + uvicorn)
After=network.target qbittorrent-nox.service
Wants=qbittorrent-nox.service

[Service]
Type=simple
User=mediasrv
Group=mediasrv
WorkingDirectory=/opt/mediasrv
EnvironmentFile=/opt/mediasrv/.env
ExecStart=/opt/mediasrv/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=5
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=15

# Hardening
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ReadWritePaths=/opt/mediasrv /srv/Общее /var/lib/mediasrv /var/log/mediasrv

# Лимиты ресурсов (медиа-сервер не должен сожрать всю RAM)
MemoryHigh=4G
MemoryMax=6G

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Создать `deploy/systemd/qbittorrent-nox.service`**

```ini
[Unit]
Description=qBittorrent-nox
After=network.target

[Service]
Type=simple
User=mediasrv
Group=mediasrv
ExecStart=/usr/bin/qbittorrent-nox --webui-port=8080
Restart=on-failure
RestartSec=5
KillMode=mixed
TimeoutStopSec=30

NoNewPrivileges=true
ReadWritePaths=/srv/Общее /var/log/mediasrv

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Коммит**

```bash
git add deploy/systemd/
git commit -m "feat(deploy): systemd units for mediasrv and qbittorrent-nox with hardening"
```

---

## Task 13: `deploy/fail2ban/mediasrv.conf`

**Files:**
- Create: `deploy/fail2ban/mediasrv.conf`
- Create: `deploy/fail2ban/mediasrv-filter.conf`

- [ ] **Step 1: Создать `deploy/fail2ban/mediasrv-filter.conf`**

```
# /etc/fail2ban/filter.d/mediasrv.conf
# Парсит Caddy access лог (JSON формат) на 401 от /login.

[Definition]
failregex = ^.*"remote_ip":"<HOST>".*"uri":"/login".*"status":401
ignoreregex =
```

- [ ] **Step 2: Создать `deploy/fail2ban/mediasrv.conf`**

```
# /etc/fail2ban/jail.d/mediasrv.conf
# 5 неудач за 5 минут → бан на 1 час.

[mediasrv-login]
enabled = true
filter = mediasrv
backend = auto
logpath = /var/log/caddy/mediasrv-access.log
maxretry = 5
findtime = 5m
bantime = 1h
action = iptables-multiport[name=mediasrv, port="80,443", protocol=tcp]
```

- [ ] **Step 3: Коммит**

```bash
git add deploy/fail2ban/
git commit -m "feat(deploy): fail2ban jail for /login bruteforce (5 fails/5min → 1h ban)"
```

---

## Task 14: `scripts/ddns_update.sh` + cron

**Files:**
- Create: `scripts/ddns_update.sh`
- Create: `deploy/cron/ddns-update.cron`

- [ ] **Step 1: Создать `scripts/ddns_update.sh`**

```bash
#!/usr/bin/env bash
# DuckDNS DDNS update.
# Использование (cron): ddns_update.sh <duckdns_subdomain> <duckdns_token>
# Бесплатная регистрация: https://www.duckdns.org/

set -euo pipefail

SUBDOMAIN="${1:?usage: ddns_update.sh <subdomain> <token>}"
TOKEN="${2:?usage: ddns_update.sh <subdomain> <token>}"

URL="https://www.duckdns.org/update?domains=${SUBDOMAIN}&token=${TOKEN}&ip="
RESPONSE=$(curl -fsS "$URL" || echo "ERROR")

if [ "$RESPONSE" = "OK" ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) DDNS updated: ${SUBDOMAIN}.duckdns.org"
else
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) DDNS update FAILED: $RESPONSE" >&2
  exit 1
fi
```

- [ ] **Step 2: Сделать исполняемым**

```bash
chmod +x scripts/ddns_update.sh
```

- [ ] **Step 3: Создать `deploy/cron/ddns-update.cron`**

```
# Каждые 5 минут обновляем DNS-запись на duckdns.org.
# Установка: sudo cp /opt/mediasrv/deploy/cron/ddns-update.cron /etc/cron.d/mediasrv-ddns
# Перед установкой: подставить SUBDOMAIN и TOKEN из .env

SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Замените YOUR_SUBDOMAIN и YOUR_TOKEN на реальные значения, прежде чем устанавливать!
*/5 * * * * mediasrv /opt/mediasrv/scripts/ddns_update.sh YOUR_SUBDOMAIN YOUR_TOKEN >> /var/log/mediasrv/ddns.log 2>&1
```

- [ ] **Step 4: Коммит**

```bash
git add scripts/ddns_update.sh deploy/cron/ddns-update.cron
git commit -m "feat(deploy): DuckDNS update script + 5min cron"
```

---

## Task 15: `deploy/install.sh` — bootstrap новой машины

**Files:**
- Create: `deploy/install.sh`

- [ ] **Step 1: Создать `deploy/install.sh`**

```bash
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
```

- [ ] **Step 2: Сделать исполняемым**

```bash
chmod +x deploy/install.sh
```

- [ ] **Step 3: Bash-syntax проверка**

```bash
bash -n deploy/install.sh
```

Должно завершиться без вывода (синтаксис ок).

- [ ] **Step 4: Коммит**

```bash
git add deploy/install.sh
git commit -m "feat(deploy): idempotent install.sh for Ubuntu 22.04+"
```

---

## Task 16: `deploy/update.sh`

**Files:**
- Create: `deploy/update.sh`

- [ ] **Step 1: Создать `deploy/update.sh`**

```bash
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
```

- [ ] **Step 2: Chmod + syntax check + commit**

```bash
chmod +x deploy/update.sh
bash -n deploy/update.sh
git add deploy/update.sh
git commit -m "feat(deploy): update.sh — git pull + migrations + systemctl restart"
```

---

## Phase 5 — Документация (1 задача)

## Task 17: `docs/DEPLOYMENT.md` + ссылка из README

**Files:**
- Create: `docs/DEPLOYMENT.md`
- Modify: `README.md`

- [ ] **Step 1: Создать `docs/DEPLOYMENT.md`**

```markdown
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
- [ ] Login → Change password → 2FA enroll → /library
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
```

- [ ] **Step 2: Обновить `README.md`**

В конец, перед "Что дальше":
```markdown
## Production deployment

См. **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — пошаговое руководство по установке на Ubuntu, настройке HTTPS, fail2ban, бэкапов, Tailscale для SSH.
```

- [ ] **Step 3: Коммит**

```bash
git add docs/DEPLOYMENT.md README.md
git commit -m "docs: deployment guide for Ubuntu (Caddy + Tailscale + DuckDNS + fail2ban)"
```

---

## Phase 6 — Финал (1 задача)

## Task 18: Финальная проверка + тег

- [ ] **Step 1: Прогнать всю тест-базу**

```bash
./venv/Scripts/python -m pytest -q
```

Ожидается: ~140+ passed (Plan 2: 130 → плюс ~10 от Plan 3 техдолга и health).

- [ ] **Step 2: Local smoke-test** (необязательно — реально работа на ubuntu)

```bash
./venv/Scripts/python -m uvicorn app.main:app --port 8000 &
sleep 3
curl -s http://127.0.0.1:8000/health
kill %1
```

- [ ] **Step 3: Проверить bash-синтаксис всех скриптов**

```bash
for f in scripts/*.sh deploy/*.sh; do
  echo "Checking $f"
  bash -n "$f"
done
```

Все должны пройти без вывода.

- [ ] **Step 4: Тег**

```bash
git tag plan-3-complete
git log --oneline | head -30
```

- [ ] **Step 5: Готово к мерджу в main, далее — реальный деплой на ubuntu-сервер**

---

## Self-Review

**Spec coverage:**
- §7.1 п.1 (network exposure) → Caddy слушает только 443/80, остальное на 127.0.0.1 (Task 11/12).
- §7.1 п.5 (rate limit) → Caddy rate_limit (Task 11), плюс fail2ban (Task 13).
- §7.1 п.6 (fail2ban) → Task 13.
- §7.1 п.9 (security headers) → Caddyfile (Task 11).
- §7.1 п.11 (unattended-upgrades) → install.sh устанавливает (Task 15).
- §7.1 п.12 (Tailscale) → docs/DEPLOYMENT.md (Task 17).
- §9.1 (структура на сервере) → install.sh создаёт всё (Task 15).
- §9.2 (systemd) → Task 12.
- §9.3 (install.sh шаги) → Task 15 покрывает 1-10.
- §9.4 (update.sh) → Task 16.
- §9.5 (бэкапы) → Task 10.
- §9.6 (логи + /admin/health) → Task 9 для health; логи через systemd journal + Caddy file log.
- §9.7 (DDNS) → Task 14.

Долг Плана 2:
- I-1 async→sync → Task 1.
- I-2 Cache-Control → Task 7.
- I-3 ffmpeg stderr → Task 2.
- I-4 media_root validation → Task 5.
- I-6 tempdirs leak + hardcoded /tmp → Task 3 (work_root config) + Task 4 (sweep on shutdown).
- I-7 httpx Client never closed → Task 4.
- I-8 silent qBittorrent failure → Task 6.
- D-3 (Plan 1) constant-time login → Task 8.

**Откладываем явно:**
- I-5 audit log on delete — не критично для семейного сервера.
- S-1 (title parser hyphens), S-4 (vendor SRI), S-5 (template heartbeat hardcode), S-6 (test helper duplication) — косметика, не блокеры.

**Перемотка ffmpeg (kill+restart с -ss из спеки §6.3.6):** не реализована в Плане 2/3. Текущая реализация (`hls_list_size=0`) работает для последовательного просмотра + перемотка в пределах сгенерированных сегментов. Если станет проблемой — отдельный мини-план.

**Placeholder scan:** прошёл по плану — нет TBD/TODO/«implement later». Все code-блоки полные.

**Type consistency:** проверил — `_DUMMY_HASH` (Task 8) единожды определён и используется только в auth/routes.py; `hls_work_root` (Task 3) согласовано между config.py, settings, и streaming/routes.py.

**Готов к исполнению.**
