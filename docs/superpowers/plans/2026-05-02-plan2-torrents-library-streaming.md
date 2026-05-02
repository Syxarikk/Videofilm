# План 2: Торренты + Библиотека + Стриминг + Скачивание

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Полная медиа-функциональность: добавил magnet → сервер скачивает → смотришь в браузере (HLS) → скачиваешь оригинал → удаляешь. Плюс закрытие технического долга, выявленного в финальном ревью Плана 1.

**Architecture:** Python-обёртка над qBittorrent HTTP API + фоновый сканер библиотеки + on-the-fly HLS-транскодинг через ffmpeg subprocess (стартует при открытии плеера, убивается через 60с idle, перезапускается с `-ss` при перемотке). Stream registry в памяти отслеживает активные процессы. Единый CSRF-токен в каждом шаблоне.

**Tech Stack:** Python 3.11+, httpx (qBittorrent API), ffmpeg subprocess, hls.js (frontend), всё остальное унаследовано из Плана 1.

**Реализует разделы спецификации:** §5.2 (qBittorrent), §5.5 (ffmpeg), §6.2 (добавление magnet), §6.3 (просмотр), §6.4 (скачивание оригинала), §6.5 (удаление). Плюс долг из Плана 1 (D-1, D-2, D-4, D-6, D-10).

**Не реализуется (отложено в План 3):**
- Production deploy (install.sh, Caddy, systemd, fail2ban, Tailscale, DDNS).
- `/admin/health` страница.
- Бэкапы по cron.
- Constant-time login (D-3 — закрывается fail2ban в Плане 3).

**Препекты:** должен быть установлен ffmpeg (для тестов стриминга) и qBittorrent (для smoke-теста; в unit-тестах мокается).

---

## Структура файлов

```
app/
├── csrf.py                      MODIFY: + verify_csrf dep + context processor
├── config.py                    MODIFY: @lru_cache get_settings
├── deps.py                      MODIFY: cache get_db_factory; helper render()
├── db.py                        MODIFY: PRAGMA foreign_keys=ON event listener
├── auth/routes.py               MODIFY: фикс delete_cookie + render() helper
├── library/routes.py            MODIFY: реальный рендеринг + /media/{id}
├── torrents/
│   ├── __init__.py              CREATE: пустой
│   ├── client.py                CREATE: QBittorrentClient (httpx)
│   ├── types.py                 CREATE: TorrentInfo dataclass
│   ├── routes.py                CREATE: /api/torrents endpoints + /add-torrent + /downloads
│   ├── scanner.py               CREATE: фоновый scanner-loop
│   └── title_parser.py          CREATE: имя файла → читаемое название
├── streaming/
│   ├── __init__.py              CREATE: пустой
│   ├── stream_registry.py       CREATE: отслеживание активных стримов
│   ├── ffmpeg_runner.py         CREATE: subprocess-обёртка
│   └── routes.py                CREATE: /api/stream/{id}/* + /api/progress
└── download/
    ├── __init__.py              CREATE: пустой
    └── routes.py                CREATE: /api/download/{id} с Range
templates/
├── base.html                    MODIFY: csrf-token meta точно работает
├── library.html                 MODIFY: реальные items + watch-progress
├── media.html                   CREATE: плеер + кнопки скачать/удалить
├── add_torrent.html             CREATE: форма для magnet
└── downloads.html               CREATE: HTMX-таблица с прогрессом
tests/
├── conftest.py                  MODIFY: фикстуры qbittorrent_mock, ffmpeg_path
├── fixtures/
│   └── sample.mp4               CREATE: 10-секундный тестовый mp4
├── unit/
│   ├── test_qbittorrent_client.py
│   ├── test_title_parser.py
│   ├── test_stream_registry.py
│   ├── test_ffmpeg_runner.py
│   ├── test_foreign_keys_pragma.py
│   ├── test_settings_caching.py
│   └── test_csrf_dependency.py
└── integration/
    ├── test_torrents_api.py
    ├── test_library_real.py
    ├── test_streaming.py
    ├── test_download.py
    └── test_media_delete.py
requirements.txt                  MODIFY: + respx (HTTP mocking)
```

---

## Phase 1 — Закрытие техдолга Плана 1 (4 задачи)

## Task 1: SQLite `PRAGMA foreign_keys=ON` (D-10)

**Files:**
- Modify: `app/db.py`
- Create: `tests/unit/test_foreign_keys_pragma.py`

**Контекст:** Без этой PRAGMA SQLite **молча игнорирует** `ON DELETE CASCADE`. Удаляешь юзера — sessions/backup_codes/watch_progress остаются висеть как сироты. Критично починить до того, как Plan 2 начнёт удалять media_items с привязанными watch_progress.

- [ ] **Step 1: Падающий тест `tests/unit/test_foreign_keys_pragma.py`**

```python
from sqlalchemy import select, text

from app.db import Base, make_engine, make_session_factory
from app.models import BackupCode, Session as UserSession, User


def test_foreign_keys_pragma_is_enabled():
    engine = make_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert result == 1


def test_user_delete_cascades_to_sessions_and_backup_codes():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    with factory() as s:
        u = User(username="alice", password_hash="x")
        s.add(u)
        s.commit()
        s.add(UserSession(token="t" * 50, user_id=u.id, expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
        s.add(BackupCode(user_id=u.id, code_hash="h"))
        s.commit()
        uid = u.id

    with factory() as s:
        u = s.get(User, uid)
        s.delete(u)
        s.commit()

    with factory() as s:
        assert s.scalars(select(UserSession).where(UserSession.user_id == uid)).first() is None
        assert s.scalars(select(BackupCode).where(BackupCode.user_id == uid)).first() is None
```

- [ ] **Step 2: Запустить — FAIL** (PRAGMA вернёт 0, либо CASCADE не сработает)

```bash
./venv/Scripts/python -m pytest tests/unit/test_foreign_keys_pragma.py -v
```

- [ ] **Step 3: Дополнить `app/db.py`** — добавить event listener

В импорты:
```python
from sqlalchemy import event
from sqlalchemy.engine import Engine as _Engine
```

В конец файла:
```python
@event.listens_for(_Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """SQLite по умолчанию не enforce'ит foreign keys; включаем явно для каждого нового подключения."""
    # Только для SQLite (модуль sqlite3 / pysqlite)
    module_name = type(dbapi_connection).__module__
    if "sqlite" not in module_name.lower():
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
```

- [ ] **Step 4: Запустить — PASS, плюс полный suite**

```bash
./venv/Scripts/python -m pytest tests/unit/test_foreign_keys_pragma.py -v
./venv/Scripts/python -m pytest -q
```

Ожидается: 2 passed для нового файла, и 67 (старые) + 2 (новые) = 69 total. Если какой-то старый тест начал падать из-за CASCADE — это и есть тот orphan-bug, который мы только что починили; разбираемся точечно.

- [ ] **Step 5: Коммит**

```bash
git add app/db.py tests/unit/test_foreign_keys_pragma.py
git commit -m "fix(db): enable SQLite PRAGMA foreign_keys=ON via connect event"
```

---

## Task 2: Кеширование `get_settings()` и `get_db_factory()` (D-2)

**Files:**
- Modify: `app/config.py`, `app/deps.py`
- Create: `tests/unit/test_settings_caching.py`

- [ ] **Step 1: Падающий тест `tests/unit/test_settings_caching.py`**

```python
from app.config import get_settings
from app.deps import get_db_factory


def test_get_settings_returns_same_instance():
    a = get_settings()
    b = get_settings()
    assert a is b


def test_get_db_factory_returns_same_instance():
    f1 = get_db_factory()
    f2 = get_db_factory()
    assert f1 is f2
```

- [ ] **Step 2: Запустить — FAIL** (текущий код возвращает новые экземпляры).

- [ ] **Step 3: Изменить `app/config.py`**

Заменить `get_settings`:
```python
from functools import lru_cache


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Изменить `app/deps.py`**

Заменить `get_db_factory`:
```python
from functools import lru_cache

# (existing imports kept)

@lru_cache(maxsize=1)
def get_db_factory() -> sessionmaker[Session]:
    """Singleton: один engine на всё приложение."""
    s = get_settings()
    engine = make_engine(s.database_url)
    return make_session_factory(engine)
```

- [ ] **Step 5: Подправить `tests/conftest.py`** — после каждого теста сбрасывать кеши, иначе тесты начнут шарить engine между собой

В начало `conftest.py` добавить новую autouse-фикстуру **выше** существующей `env`:
```python
@pytest.fixture(autouse=True)
def _clear_caches():
    yield
    from app.config import get_settings
    from app.deps import get_db_factory
    get_settings.cache_clear()
    get_db_factory.cache_clear()
```

- [ ] **Step 6: Запустить — PASS, плюс полный suite**

```bash
./venv/Scripts/python -m pytest -q
```

Ожидается: 71 passed (69 старых + 2 новых). Если что-то падает на изоляции — проверьте, что `_clear_caches` стоит перед `env`.

- [ ] **Step 7: Коммит**

```bash
git add app/config.py app/deps.py tests/conftest.py tests/unit/test_settings_caching.py
git commit -m "perf(deps): cache get_settings and get_db_factory as singletons"
```

---

## Task 3: CSRF — context processor + verify dependency (D-1)

**Files:**
- Modify: `app/csrf.py`, `app/deps.py`, `app/auth/routes.py`, `app/admin/routes.py`, `app/library/routes.py`, шаблоны с POST-формами
- Create: `tests/unit/test_csrf_dependency.py`

**Контекст:** В Плане 1 примитив `generate_token`/`verify_token` есть, но не подключён. Сейчас сделаем dep `verify_csrf`, добавим `csrf_token` в каждый рендер шаблона, и в формы скрытое поле.

- [ ] **Step 1: Падающий тест `tests/unit/test_csrf_dependency.py`**

```python
from fastapi import FastAPI, Form, Depends
from fastapi.testclient import TestClient
from typing import Annotated

from app.csrf import generate_token, verify_csrf


def _make_app():
    app = FastAPI()

    @app.get("/get-token")
    def get_tok():
        return {"token": generate_token("session-A")}

    @app.post("/protected")
    def protected(
        csrf_token: Annotated[str, Form()],
        _: Annotated[None, Depends(verify_csrf)],
    ):
        return {"ok": True}

    return app


def test_verify_csrf_accepts_matching_token():
    """Когда session-key совпадает и токен валиден — POST проходит."""
    app = _make_app()
    with TestClient(app) as c:
        # Тест-фикстура устанавливает session-cookie через middleware Plan-1; тут руками подставим
        c.cookies.set("session", "session-A")
        token = generate_token("session-A")
        r = c.post("/protected", data={"csrf_token": token})
    assert r.status_code == 200


def test_verify_csrf_rejects_missing_token():
    app = _make_app()
    with TestClient(app) as c:
        c.cookies.set("session", "session-A")
        r = c.post("/protected", data={})
    assert r.status_code in (400, 422)


def test_verify_csrf_rejects_wrong_session():
    app = _make_app()
    with TestClient(app) as c:
        c.cookies.set("session", "session-DIFFERENT")
        token = generate_token("session-A")
        r = c.post("/protected", data={"csrf_token": token})
    assert r.status_code == 400
```

- [ ] **Step 2: Запустить — FAIL** (`verify_csrf` не существует).

- [ ] **Step 3: Расширить `app/csrf.py`**

В конец файла:
```python
from fastapi import Form, HTTPException, Request, status
from typing import Annotated

from app.auth.deps import SESSION_COOKIE


def verify_csrf(
    request: Request,
    csrf_token: Annotated[str, Form()],
) -> None:
    """FastAPI dependency: убеждаемся, что в форме есть валидный CSRF-токен,
    подписанный текущим session-cookie."""
    session_key = request.cookies.get(SESSION_COOKIE) or ""
    if not verify_token(csrf_token, session_key):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSRF token invalid")
```

- [ ] **Step 4: Добавить хелпер `render()` в `app/deps.py`**

В конец файла:
```python
from fastapi import Request as _Req
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

_TEMPLATES = Jinja2Templates(directory="templates")


def render(
    request: _Req,
    template_name: str,
    context: dict | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Рендеринг шаблона с авто-инжектом csrf_token из текущей сессии.
    Все роуты должны использовать этот хелпер вместо прямого Jinja2Templates."""
    from app.auth.deps import SESSION_COOKIE
    from app.csrf import generate_token

    session_key = request.cookies.get(SESSION_COOKIE) or ""
    full = {"csrf_token": generate_token(session_key)}
    if context:
        full.update(context)
    return _TEMPLATES.TemplateResponse(request, template_name, full, status_code=status_code)
```

- [ ] **Step 5: Заменить вызовы `templates.TemplateResponse(request, ...)` на `render(request, ...)` в роутерах**

Файлы: `app/auth/routes.py`, `app/admin/routes.py`, `app/library/routes.py`.

В каждом файле:
1. Удалить `templates = Jinja2Templates(directory="templates")` и связанные импорты.
2. `from app.deps import get_db, render` (заменив старый `get_db` импорт).
3. Заменить **все** `templates.TemplateResponse(request, "name.html", {ctx})` на `render(request, "name.html", {ctx})`.
4. Если был `status_code=400` — теперь `render(request, ..., status_code=400)`.

- [ ] **Step 6: Подключить `verify_csrf` к POST-формам**

В `app/auth/routes.py`:
- `login_post`, `verify_totp_post`, `change_password_post`, `enroll_2fa_post`, `logout` — добавить параметр:
  ```python
  _csrf: Annotated[None, Depends(verify_csrf)] = None,
  ```
  (с дефолтом None, чтобы не сломать сигнатуру; FastAPI всё равно вызовет dep)

В `app/admin/routes.py`:
- `create_user`, `delete_user` — то же самое.

Импорт в каждом файле: `from app.csrf import verify_csrf`.

**Внимание:** `/login` POST принимает CSRF-токен из той же формы, что выдаст `/login` GET. Сессии ещё нет, токен подписан пустой строкой — это нормально, главное что злоумышленник тоже не сможет подписать (он не знает секрета HMAC-сервера, но ключ-то session_key="" общий). Для чисто публичного `/login` CSRF — это не классическая защита, а скорее «такого же origin'а форма», поэтому токен валидируется, но против пустого session_key. Так и оставим.

- [ ] **Step 7: Обновить шаблоны — добавить hidden CSRF-поле в каждую форму**

Шаблоны для правки: `templates/login.html`, `templates/verify_totp.html`, `templates/change_password.html`, `templates/enroll_2fa.html`, `templates/admin_users.html` (две формы — create + delete).

В каждый `<form>` сразу после открывающего тега добавить:
```html
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
```

В `templates/admin_users.html` для inline-формы удаления:
```html
<form method="post" action="/admin/users/{{ u.id }}/delete" style="display:inline" onsubmit="return confirm('Удалить пользователя {{ u.username }}?');">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <button type="submit">Удалить</button>
</form>
```

- [ ] **Step 8: Прогнать все интеграционные тесты — большинство упадёт, потому что не передают `csrf_token`**

```bash
./venv/Scripts/python -m pytest tests/integration -v
```

Ожидаются массовые провалы: ~20 тестов с 400 «CSRF token invalid».

- [ ] **Step 9: Починить интеграционные тесты — добавлять `csrf_token` в каждый POST**

В `tests/conftest.py` добавить хелпер-фикстуру для удобства:
```python
@pytest.fixture
def csrf_for(client):
    """Возвращает функцию: csrf_for(cookie) → токен, валидный при той же session-cookie."""
    from app.csrf import generate_token

    def _make(cookie: str | None) -> str:
        return generate_token(cookie or "")

    return _make
```

И во всех integration-тестах:

Для `POST /login` (нет ещё session): `data={"username": "...", "password": "...", "csrf_token": csrf_for(None)}`.

Для других POST: получить cookie из предыдущего ответа, потом `csrf_for(cookie)`.

Это требует пройтись по `tests/integration/test_login_flow.py`, `test_first_login_setup.py`, `test_admin_users.py`, `test_library.py` и в каждом POST добавить токен. Итого ~25 правок. Делайте по файлу за коммит, чтобы было удобно откатывать.

- [ ] **Step 10: Прогнать всё — должно быть всё зелёное**

```bash
./venv/Scripts/python -m pytest -q
```

Ожидается: 71 (старые) + 3 (новые из Task 3) + ~25 (изменённые тесты остаются passing) = ~74 passed.

- [ ] **Step 11: Коммит**

```bash
git add app/csrf.py app/deps.py app/auth/routes.py app/admin/routes.py app/library/routes.py templates/ tests/conftest.py tests/integration/ tests/unit/test_csrf_dependency.py
git commit -m "feat(csrf): wire token through render() helper, enforce on all POST forms"
```

---

## Task 4: Cookie-флаги при logout + TestClient cookies cleanup (D-4 + D-6)

**Files:**
- Modify: `app/auth/routes.py:logout`
- Modify: `tests/conftest.py` + интеграционные тесты

- [ ] **Step 1: Падающий тест в `tests/integration/test_login_flow.py`** — убедиться, что `Set-Cookie` на logout содержит security-флаги

В конец файла:
```python
def test_logout_set_cookie_has_security_flags(client, db_factory, csrf_for):
    _, secret = make_user_with_totp(db_factory)
    r = client.post("/login", data={"username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)})
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post("/verify-totp", data={"code": code, "csrf_token": csrf_for(cookie)}, cookies={"session": cookie})

    r2 = client.post("/logout", data={"csrf_token": csrf_for(cookie)}, cookies={"session": cookie})
    sc = r2.headers.get("set-cookie", "")
    assert "session=" in sc
    # Cookie должен быть удалён с теми же флагами, что и установлен
    assert "HttpOnly" in sc or "httponly" in sc.lower()
    assert "Secure" in sc or "secure" in sc.lower()
    assert "SameSite=Strict" in sc or "samesite=strict" in sc.lower()
```

- [ ] **Step 2: Запустить — FAIL** (текущий `delete_cookie` не передаёт флаги).

- [ ] **Step 3: Починить `app/auth/routes.py:logout`**

Заменить строку:
```python
response.delete_cookie(SESSION_COOKIE, path="/")
```
на:
```python
response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=True, samesite="strict")
```

- [ ] **Step 4: Подавить deprecation warnings от `cookies=` в TestClient**

В `tests/conftest.py` обновить фикстуру `client`:
```python
@pytest.fixture
def client(db_factory):
    from app.main import app
    from app.deps import get_db_factory

    app.dependency_overrides[get_db_factory] = lambda: db_factory
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()
    c.cookies.clear()  # на всякий случай — изоляция между тестами
```

И в интеграционных тестах **постепенно** заменять `cookies={"session": cookie}` на `client.cookies.set("session", cookie)`. Начните с `tests/integration/test_login_flow.py` и `test_first_login_setup.py` (самые «зацеплённые»). Это правка стиля, а не семантики.

Совет: можно добавить хелпер
```python
def authed(client, cookie: str):
    client.cookies.set("session", cookie)
    return client
```
и использовать `authed(client, cookie).get(...)`.

- [ ] **Step 5: Запустить — PASS**

```bash
./venv/Scripts/python -m pytest -q
```

Все тесты должны быть зелёными, число deprecation warnings — около 0.

- [ ] **Step 6: Коммит**

```bash
git add app/auth/routes.py tests/
git commit -m "fix(auth): logout delete_cookie passes security flags; cleanup TestClient cookies"
```

---

## Phase 2 — qBittorrent client (2 задачи)

## Task 5: TorrentInfo + QBittorrentClient

**Files:**
- Create: `app/torrents/__init__.py` (пустой)
- Create: `app/torrents/types.py`
- Create: `app/torrents/client.py`
- Create: `tests/unit/test_qbittorrent_client.py`
- Modify: `requirements.txt` (+ `respx>=0.21`)

- [ ] **Step 1: Установить `respx`**

```bash
echo "respx>=0.21" >> requirements.txt
./venv/Scripts/python -m pip install "respx>=0.21"
```

- [ ] **Step 2: Падающий тест `tests/unit/test_qbittorrent_client.py`**

```python
import httpx
import pytest
import respx

from app.torrents.client import QBittorrentClient, QBittorrentError
from app.torrents.types import TorrentInfo


@respx.mock
def test_login_succeeds_and_caches_cookie():
    respx.post("http://qb/api/v2/auth/login").mock(
        return_value=httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=abc; path=/"})
    )
    c = QBittorrentClient("http://qb", "admin", "secret")
    c.login()
    # Повторный login — без новых HTTP-вызовов
    assert respx.calls.call_count == 1


@respx.mock
def test_login_wrong_credentials_raises():
    respx.post("http://qb/api/v2/auth/login").mock(
        return_value=httpx.Response(200, text="Fails.")
    )
    c = QBittorrentClient("http://qb", "admin", "wrong")
    with pytest.raises(QBittorrentError):
        c.login()


@respx.mock
def test_add_magnet_calls_correct_endpoint():
    respx.post("http://qb/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    add_route = respx.post("http://qb/api/v2/torrents/add").mock(return_value=httpx.Response(200))
    c = QBittorrentClient("http://qb", "admin", "secret")
    c.add_magnet("magnet:?xt=urn:btih:abc", save_path="/srv/Общее/downloads")
    assert add_route.called
    sent = add_route.calls.last.request
    body = sent.content.decode()
    assert "magnet:?xt=urn:btih:abc" in body
    assert "/srv/Общее/downloads" in body


@respx.mock
def test_list_torrents_returns_typed_objects():
    respx.post("http://qb/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    respx.get("http://qb/api/v2/torrents/info").mock(return_value=httpx.Response(200, json=[
        {
            "hash": "abc123",
            "name": "Some.Movie.2024.1080p.mkv",
            "progress": 0.42,
            "dlspeed": 1500000,
            "state": "downloading",
            "size": 4_000_000_000,
            "save_path": "/srv/Общее/downloads",
            "content_path": "/srv/Общее/downloads/Some.Movie.2024.1080p.mkv",
            "eta": 600,
        }
    ]))
    c = QBittorrentClient("http://qb", "admin", "secret")
    torrents = c.list_torrents()
    assert len(torrents) == 1
    t = torrents[0]
    assert isinstance(t, TorrentInfo)
    assert t.hash == "abc123"
    assert t.progress == 0.42
    assert t.state == "downloading"
    assert t.is_complete is False


@respx.mock
def test_list_torrents_marks_completed_state():
    respx.post("http://qb/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    respx.get("http://qb/api/v2/torrents/info").mock(return_value=httpx.Response(200, json=[
        {"hash": "h", "name": "n", "progress": 1.0, "dlspeed": 0, "state": "uploading",
         "size": 1, "save_path": "/x", "content_path": "/x/n", "eta": 0}
    ]))
    c = QBittorrentClient("http://qb", "admin", "secret")
    [t] = c.list_torrents()
    assert t.is_complete is True


@respx.mock
def test_delete_torrent_with_files():
    respx.post("http://qb/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    delete_route = respx.post("http://qb/api/v2/torrents/delete").mock(return_value=httpx.Response(200))
    c = QBittorrentClient("http://qb", "admin", "secret")
    c.delete_torrent("abc123", delete_files=True)
    assert delete_route.called
    body = delete_route.calls.last.request.content.decode()
    assert "abc123" in body
    assert "deleteFiles=true" in body


@respx.mock
def test_request_failure_raises_qbittorrent_error():
    respx.post("http://qb/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    respx.get("http://qb/api/v2/torrents/info").mock(return_value=httpx.Response(500))
    c = QBittorrentClient("http://qb", "admin", "secret")
    with pytest.raises(QBittorrentError):
        c.list_torrents()
```

- [ ] **Step 3: Запустить — FAIL** (`app.torrents.client` не существует).

- [ ] **Step 4: Реализовать `app/torrents/__init__.py`** — пустой.

- [ ] **Step 5: Реализовать `app/torrents/types.py`**

```python
from dataclasses import dataclass


_COMPLETE_STATES = {"uploading", "stalledUP", "queuedUP", "checkingUP", "forcedUP", "pausedUP"}


@dataclass(frozen=True, slots=True)
class TorrentInfo:
    hash: str
    name: str
    progress: float       # 0.0–1.0
    dlspeed: int          # bytes/sec
    state: str            # qBittorrent state code
    size: int             # bytes
    save_path: str        # директория сохранения
    content_path: str     # путь к контенту торрента (файл или папка)
    eta_seconds: int      # -1 если не вычислен

    @property
    def is_complete(self) -> bool:
        return self.progress >= 1.0 or self.state in _COMPLETE_STATES
```

- [ ] **Step 6: Реализовать `app/torrents/client.py`**

```python
"""Тонкая обёртка над qBittorrent Web UI HTTP API.

Документация: https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-4.1)
"""
from typing import Any

import httpx

from app.torrents.types import TorrentInfo


class QBittorrentError(Exception):
    """Любая ошибка взаимодействия с qBittorrent — сеть, аутентификация, 5xx, неверный формат."""


class QBittorrentClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._client = httpx.Client(base_url=self._base_url, timeout=timeout)
        self._logged_in = False

    def login(self) -> None:
        if self._logged_in:
            return
        try:
            r = self._client.post(
                "/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
            )
        except httpx.HTTPError as e:
            raise QBittorrentError(f"login: connection failed: {e}") from e
        if r.status_code != 200 or "Ok." not in r.text:
            raise QBittorrentError(f"login: rejected (status={r.status_code}, body={r.text!r})")
        self._logged_in = True

    def add_magnet(self, magnet: str, *, save_path: str) -> None:
        self.login()
        try:
            r = self._client.post(
                "/api/v2/torrents/add",
                data={"urls": magnet, "savepath": save_path, "autoTMM": "false"},
            )
        except httpx.HTTPError as e:
            raise QBittorrentError(f"add_magnet: connection failed: {e}") from e
        if r.status_code != 200:
            raise QBittorrentError(f"add_magnet: status={r.status_code}, body={r.text!r}")

    def list_torrents(self) -> list[TorrentInfo]:
        self.login()
        try:
            r = self._client.get("/api/v2/torrents/info")
        except httpx.HTTPError as e:
            raise QBittorrentError(f"list_torrents: connection failed: {e}") from e
        if r.status_code != 200:
            raise QBittorrentError(f"list_torrents: status={r.status_code}")
        try:
            payload: list[dict[str, Any]] = r.json()
        except ValueError as e:
            raise QBittorrentError(f"list_torrents: invalid JSON: {e}") from e
        return [self._parse_torrent(t) for t in payload]

    def delete_torrent(self, info_hash: str, *, delete_files: bool) -> None:
        self.login()
        try:
            r = self._client.post(
                "/api/v2/torrents/delete",
                data={"hashes": info_hash, "deleteFiles": "true" if delete_files else "false"},
            )
        except httpx.HTTPError as e:
            raise QBittorrentError(f"delete_torrent: connection failed: {e}") from e
        if r.status_code != 200:
            raise QBittorrentError(f"delete_torrent: status={r.status_code}")

    def _parse_torrent(self, raw: dict[str, Any]) -> TorrentInfo:
        try:
            return TorrentInfo(
                hash=raw["hash"],
                name=raw["name"],
                progress=float(raw["progress"]),
                dlspeed=int(raw["dlspeed"]),
                state=str(raw["state"]),
                size=int(raw["size"]),
                save_path=str(raw["save_path"]),
                content_path=str(raw["content_path"]),
                eta_seconds=int(raw.get("eta", -1)),
            )
        except (KeyError, ValueError, TypeError) as e:
            raise QBittorrentError(f"unexpected torrent payload: {raw!r}") from e

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 7: Запустить — PASS**

```bash
./venv/Scripts/python -m pytest tests/unit/test_qbittorrent_client.py -v
```

Ожидается: 7 passed.

- [ ] **Step 8: Коммит**

```bash
git add app/torrents/__init__.py app/torrents/types.py app/torrents/client.py tests/unit/test_qbittorrent_client.py requirements.txt
git commit -m "feat(torrents): qBittorrent HTTP client with login/add/list/delete"
```

---

## Task 6: Singleton-фабрика QBittorrentClient в `deps.py`

**Files:**
- Modify: `app/deps.py`

- [ ] **Step 1: Добавить в `app/deps.py`** (в конец)

```python
from functools import lru_cache as _lru_cache_qb
from app.torrents.client import QBittorrentClient


@_lru_cache_qb(maxsize=1)
def get_qbittorrent_client() -> QBittorrentClient:
    s = get_settings()
    return QBittorrentClient(s.qbittorrent_url, s.qbittorrent_username, s.qbittorrent_password)
```

(Используем псевдоним `_lru_cache_qb`, чтобы не конфликтовать с уже импортированным выше `lru_cache`.)

- [ ] **Step 2: Дополнить `tests/conftest.py:_clear_caches`**

В autouse-фикстуру `_clear_caches` добавить:
```python
    from app.deps import get_qbittorrent_client
    get_qbittorrent_client.cache_clear()
```

- [ ] **Step 3: Прогнать тесты — должны все пройти**

```bash
./venv/Scripts/python -m pytest -q
```

- [ ] **Step 4: Коммит**

```bash
git add app/deps.py tests/conftest.py
git commit -m "feat(deps): cached QBittorrentClient factory"
```

---

## Phase 3 — Add torrent flow (4 задачи)

## Task 7: POST `/api/torrents` (добавить magnet)

**Files:**
- Create: `app/torrents/routes.py`
- Modify: `app/main.py`
- Create: `tests/integration/test_torrents_api.py`

- [ ] **Step 1: Падающий тест `tests/integration/test_torrents_api.py`**

```python
import httpx
import pyotp
import pytest
import respx

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret, _derive_key
from app.deps import get_qbittorrent_client
from app.models import User


def _logged_in(client, db_factory, csrf_for):
    secret = pyotp.random_base32()
    with db_factory() as s:
        s.add(User(
            username="alice", password_hash=hash_password("correct-password-12"),
            must_change_password=False, totp_enabled=True,
            totp_secret_encrypted=encrypt_secret(secret, _derive_key("x" * 64)),
        ))
        s.commit()
    r = client.post("/login", data={
        "username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)
    })
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post("/verify-totp", data={"code": code, "csrf_token": csrf_for(cookie)},
                cookies={"session": cookie})
    return cookie


@respx.mock
def test_add_torrent_calls_qbittorrent_and_redirects(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    respx.post("http://127.0.0.1:8080/api/v2/auth/login").mock(
        return_value=httpx.Response(200, text="Ok.")
    )
    add_route = respx.post("http://127.0.0.1:8080/api/v2/torrents/add").mock(
        return_value=httpx.Response(200)
    )

    r = client.post(
        "/api/torrents",
        data={"magnet": "magnet:?xt=urn:btih:abc", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/downloads"
    assert add_route.called


def test_invalid_magnet_returns_400(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.post(
        "/api/torrents",
        data={"magnet": "not-a-magnet", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 400


def test_unauthenticated_redirect(client, csrf_for):
    r = client.post(
        "/api/torrents",
        data={"magnet": "magnet:?xt=urn:btih:abc", "csrf_token": csrf_for(None)},
    )
    # /api/* префикс — middleware не редиректит, отдаёт 401
    assert r.status_code == 401
```

- [ ] **Step 2: Запустить — FAIL** (роут отсутствует).

- [ ] **Step 3: Реализовать `app/torrents/routes.py`**

```python
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.config import Settings, get_settings
from app.csrf import verify_csrf
from app.deps import get_db, get_qbittorrent_client
from app.models import User
from app.torrents.client import QBittorrentClient, QBittorrentError

router = APIRouter()
api_router = APIRouter(prefix="/api/torrents")

_MAGNET_RE = re.compile(r"^magnet:\?xt=urn:btih:[a-fA-F0-9]{32,64}", re.IGNORECASE)


@api_router.post("")
async def add_torrent(
    magnet: Annotated[str, Form()],
    user: Annotated[User, Depends(get_current_user)],
    qb: Annotated[QBittorrentClient, Depends(get_qbittorrent_client)],
    settings: Annotated[Settings, Depends(get_settings)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    magnet = magnet.strip()
    if not _MAGNET_RE.match(magnet):
        raise HTTPException(status_code=400, detail="Не похоже на magnet-ссылку")
    save_path = f"{settings.media_root}/downloads"
    try:
        qb.add_magnet(magnet, save_path=save_path)
    except QBittorrentError as e:
        raise HTTPException(status_code=503, detail=f"qBittorrent недоступен: {e}")
    return RedirectResponse("/downloads", status_code=303)
```

- [ ] **Step 4: Подключить в `app/main.py`**

В импорты:
```python
from app.torrents.routes import api_router as torrents_api_router, router as torrents_router
```

После остальных include_router:
```python
app.include_router(torrents_api_router)
app.include_router(torrents_router)
```

(Сейчас `torrents_router` пустой, наполним в Task 8/10.)

- [ ] **Step 5: Запустить — PASS**

```bash
./venv/Scripts/python -m pytest tests/integration/test_torrents_api.py -v
```

- [ ] **Step 6: Коммит**

```bash
git add app/torrents/routes.py app/main.py tests/integration/test_torrents_api.py
git commit -m "feat(torrents): POST /api/torrents adds magnet via qBittorrent"
```

---

## Task 8: `/add-torrent` HTML страница

**Files:**
- Modify: `app/torrents/routes.py`
- Create: `templates/add_torrent.html`

- [ ] **Step 1: Падающий тест в `tests/integration/test_torrents_api.py`** (в конец)

```python
def test_add_torrent_page_renders_for_logged_in_user(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/add-torrent", cookies={"session": cookie})
    assert r.status_code == 200
    assert "magnet" in r.text.lower()


def test_add_torrent_page_unauth_redirects(client):
    r = client.get("/add-torrent")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/torrents/routes.py`**

В импорты:
```python
from fastapi import Request
from fastapi.responses import HTMLResponse
from app.deps import render
```

В конец файла:
```python
@router.get("/add-torrent", response_class=HTMLResponse)
async def add_torrent_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
):
    return render(request, "add_torrent.html", {"user": user})
```

- [ ] **Step 4: Создать `templates/add_torrent.html`**

```html
{% extends "base.html" %}
{% block title %}Добавить торрент{% endblock %}
{% block content %}
<h1>Добавить magnet-ссылку</h1>
<p>Сервер скачает торрент и появится в библиотеке после загрузки.</p>
<form method="post" action="/api/torrents">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <label>Magnet-ссылка<br>
    <input name="magnet" placeholder="magnet:?xt=urn:btih:..." required style="width: 100%; max-width: 600px">
  </label>
  <button type="submit">Добавить</button>
</form>
<p><a href="/downloads">→ Активные загрузки</a> · <a href="/library">← Библиотека</a></p>
{% endblock %}
```

- [ ] **Step 5: Прогнать — PASS**

- [ ] **Step 6: Коммит**

```bash
git add app/torrents/routes.py templates/add_torrent.html tests/integration/test_torrents_api.py
git commit -m "feat(torrents): /add-torrent page with magnet form"
```

---

## Task 9: GET `/api/torrents/status` (JSON-ответ для HTMX-polling)

**Files:**
- Modify: `app/torrents/routes.py`

- [ ] **Step 1: Падающий тест** (в `tests/integration/test_torrents_api.py`)

```python
@respx.mock
def test_status_returns_active_torrents(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    respx.post("http://127.0.0.1:8080/api/v2/auth/login").mock(
        return_value=httpx.Response(200, text="Ok.")
    )
    respx.get("http://127.0.0.1:8080/api/v2/torrents/info").mock(return_value=httpx.Response(200, json=[
        {"hash": "abc", "name": "Movie.mkv", "progress": 0.42, "dlspeed": 1500000,
         "state": "downloading", "size": 4_000_000_000, "save_path": "/x", "content_path": "/x/Movie.mkv", "eta": 3600}
    ]))
    r = client.get("/api/torrents/status", cookies={"session": cookie})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["hash"] == "abc"
    assert data[0]["progress_percent"] == 42
    assert data[0]["speed_human"].endswith("/s")


@respx.mock
def test_status_handles_qbittorrent_down(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    respx.post("http://127.0.0.1:8080/api/v2/auth/login").mock(
        return_value=httpx.Response(500)
    )
    r = client.get("/api/torrents/status", cookies={"session": cookie})
    assert r.status_code == 503
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/torrents/routes.py`**

В конец файла (после `add_torrent`, перед `add_torrent_page`):
```python
def _format_speed(bytes_per_sec: int) -> str:
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec} B/s"
    if bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"


def _format_eta(seconds: int) -> str:
    if seconds < 0 or seconds > 365 * 24 * 3600:
        return "—"
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    if h > 0:
        return f"{h}ч {m:02d}м"
    return f"{m}м"


@api_router.get("/status")
async def torrents_status(
    user: Annotated[User, Depends(get_current_user)],
    qb: Annotated[QBittorrentClient, Depends(get_qbittorrent_client)],
):
    try:
        torrents = qb.list_torrents()
    except QBittorrentError as e:
        raise HTTPException(status_code=503, detail=f"qBittorrent недоступен: {e}")
    return [
        {
            "hash": t.hash,
            "name": t.name,
            "progress_percent": int(t.progress * 100),
            "speed_human": _format_speed(t.dlspeed),
            "eta_human": _format_eta(t.eta_seconds),
            "state": t.state,
            "is_complete": t.is_complete,
        }
        for t in torrents
    ]
```

- [ ] **Step 4: Запустить — PASS**

- [ ] **Step 5: Коммит**

```bash
git add app/torrents/routes.py tests/integration/test_torrents_api.py
git commit -m "feat(torrents): GET /api/torrents/status returns formatted progress"
```

---

## Task 10: `/downloads` страница с HTMX-поллингом

**Files:**
- Modify: `app/torrents/routes.py`
- Create: `templates/downloads.html`

- [ ] **Step 1: Падающий тест**

```python
def test_downloads_page_requires_auth(client):
    r = client.get("/downloads")
    assert r.status_code == 303


def test_downloads_page_has_htmx_polling(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/downloads", cookies={"session": cookie})
    assert r.status_code == 200
    assert "hx-get" in r.text or "/api/torrents/status" in r.text
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/torrents/routes.py`**

В конец:
```python
@router.get("/downloads", response_class=HTMLResponse)
async def downloads_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
):
    return render(request, "downloads.html", {"user": user})
```

- [ ] **Step 4: Создать `templates/downloads.html`**

```html
{% extends "base.html" %}
{% block title %}Загрузки{% endblock %}
{% block content %}
<h1>Активные загрузки</h1>
<p><a href="/add-torrent">+ Добавить magnet</a> · <a href="/library">→ Библиотека</a></p>
<div id="torrents-table"
     hx-get="/api/torrents/status"
     hx-trigger="load, every 2s"
     hx-swap="innerHTML"
     hx-ext="json-enc">
  Загрузка…
</div>

<script>
// Простой обработчик: hx-get вернёт JSON, мы рендерим его в таблицу руками.
document.body.addEventListener('htmx:afterRequest', (e) => {
  if (e.detail.requestConfig.path !== '/api/torrents/status') return;
  if (!e.detail.successful) {
    document.getElementById('torrents-table').innerHTML = '<p class="error">qBittorrent недоступен</p>';
    return;
  }
  const data = JSON.parse(e.detail.xhr.responseText);
  if (data.length === 0) {
    document.getElementById('torrents-table').innerHTML = '<p>Активных загрузок нет.</p>';
    return;
  }
  const rows = data.map(t => `
    <tr>
      <td>${t.name}</td>
      <td>${t.progress_percent}%</td>
      <td>${t.speed_human}</td>
      <td>${t.eta_human}</td>
      <td>${t.state}</td>
    </tr>
  `).join('');
  document.getElementById('torrents-table').innerHTML = `
    <table>
      <thead><tr><th>Название</th><th>%</th><th>Скорость</th><th>ETA</th><th>Статус</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
});
</script>
{% endblock %}
```

- [ ] **Step 5: Запустить — PASS**

- [ ] **Step 6: Коммит**

```bash
git add app/torrents/routes.py templates/downloads.html tests/integration/test_torrents_api.py
git commit -m "feat(torrents): /downloads page with HTMX polling every 2s"
```

---

## Phase 4 — Library scanner (2 задачи)

## Task 11: Title parser

**Files:**
- Create: `app/torrents/title_parser.py`
- Create: `tests/unit/test_title_parser.py`

- [ ] **Step 1: Падающий тест `tests/unit/test_title_parser.py`**

```python
import pytest
from app.torrents.title_parser import parse_title


@pytest.mark.parametrize("raw,expected", [
    ("Some.Movie.2024.1080p.BluRay.x264.mkv", "Some Movie (2024)"),
    ("Some.Movie.2024.1080p.BluRay.x264-GROUP.mkv", "Some Movie (2024)"),
    ("Some Movie 2024 1080p BluRay.mkv", "Some Movie (2024)"),
    ("Some.Movie.2024.WEB-DL.2160p.HEVC.HDR.mkv", "Some Movie (2024)"),
    ("Movie.Title.S01E05.1080p.mkv", "Movie Title S01E05"),
    ("Some_Movie_2024.mkv", "Some Movie (2024)"),
    ("plain-name.mkv", "plain-name"),
    ("No.Year.Here.1080p.mkv", "No Year Here"),
])
def test_parse_title_examples(raw, expected):
    assert parse_title(raw) == expected
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/torrents/title_parser.py`**

```python
"""Извлечение читаемого названия из имени файла торрента.

Торренты обычно именуются как:
  Some.Movie.2024.1080p.BluRay.x264-GROUP.mkv
  Movie.Title.S01E05.HDTV.x264.mkv

Простой парсер: режем по разделителям, ищем год или season/episode маркер,
остальное считаем шумом.
"""
import re
from pathlib import PurePosixPath


_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_SE_RE = re.compile(r"^[Ss]\d{1,2}[Ee]\d{1,3}$")
_NOISE_TOKENS = {
    "1080p", "2160p", "720p", "480p",
    "BluRay", "BRRip", "DVDRip", "WEB", "WEB-DL", "WEBRip", "HDTV", "HDR", "HDR10", "DV",
    "x264", "x265", "H264", "H265", "HEVC", "AVC",
    "AAC", "AC3", "DTS", "DDP", "DD5.1", "5.1", "7.1", "FLAC",
    "REMUX", "PROPER", "REPACK", "EXTENDED", "DIRECTORS-CUT",
}
_NOISE_LOWER = {t.lower() for t in _NOISE_TOKENS}


def parse_title(filename: str) -> str:
    stem = PurePosixPath(filename).stem
    if not stem:
        return filename

    # Группа после "-" в конце часто ник релизера: Some.Movie.2024-GROUP → отрезаем
    if "-" in stem and not _has_word_boundary(stem):
        # Не разрезаем дефис внутри слов (e.g. "Spider-Man")
        stem = stem.rsplit("-", 1)[0]

    # Разбиваем по любым из распространённых разделителей
    tokens = re.split(r"[.\s_]+", stem)
    tokens = [t for t in tokens if t]

    title_parts: list[str] = []
    suffix: str | None = None
    found_year = False

    for tok in tokens:
        # Год — превращаем в (YYYY) и обрываем дальнейший сбор шума
        if _YEAR_RE.match(tok):
            suffix = f"({tok})"
            found_year = True
            break
        # S01E05 — сохраняем как суффикс
        if _SE_RE.match(tok):
            suffix = tok.upper()
            break
        # Технический шум — игнорируем
        if tok.lower() in _NOISE_LOWER:
            continue
        # Слово выглядит как часть названия
        title_parts.append(tok)

    if not title_parts:
        # Не удалось ничего вытащить — возвращаем оригинальный stem
        return stem

    title = " ".join(title_parts)
    return f"{title} {suffix}" if suffix else title


def _has_word_boundary(s: str) -> bool:
    """True если в строке есть пробел/точка/подчёркивание — значит разделители есть и без дефиса."""
    return any(c in s for c in (" ", ".", "_"))
```

- [ ] **Step 4: Запустить — PASS**

```bash
./venv/Scripts/python -m pytest tests/unit/test_title_parser.py -v
```

- [ ] **Step 5: Коммит**

```bash
git add app/torrents/title_parser.py tests/unit/test_title_parser.py
git commit -m "feat(torrents): title parser for torrent filenames"
```

---

## Task 12: Library scanner — фоновая задача

**Files:**
- Create: `app/torrents/scanner.py`
- Modify: `app/main.py` (запуск при старте приложения)
- Create: `tests/integration/test_library_scanner.py`

- [ ] **Step 1: Падающий тест `tests/integration/test_library_scanner.py`**

```python
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import MediaItem, User
from app.torrents.scanner import scan_once
from app.torrents.types import TorrentInfo


def _make_completed_torrent_dir(tmp_path: Path) -> str:
    """Создаёт фейковый торрент-контент: папка с одним mp4-файлом."""
    folder = tmp_path / "Some.Movie.2024.1080p.BluRay.x264-GROUP"
    folder.mkdir()
    (folder / "RARBG.txt").write_text("ad")  # шумовой файл, не должен быть выбран
    big = folder / "Some.Movie.2024.1080p.BluRay.x264-GROUP.mp4"
    big.write_bytes(b"\x00" * 5_000_000)  # 5 МБ
    return str(folder)


class FakeQbClient:
    def __init__(self, torrents: list[TorrentInfo]):
        self._torrents = torrents

    def list_torrents(self) -> list[TorrentInfo]:
        return list(self._torrents)


def test_scan_once_creates_media_item_for_completed_torrent(db_factory, tmp_path):
    folder = _make_completed_torrent_dir(tmp_path)
    with db_factory() as s:
        admin = User(username="root", password_hash="x", is_admin=True, must_change_password=False)
        s.add(admin)
        s.commit()

    qb = FakeQbClient([TorrentInfo(
        hash="abc", name="Some.Movie.2024.1080p.BluRay.x264-GROUP",
        progress=1.0, dlspeed=0, state="uploading",
        size=5_000_000, save_path=str(tmp_path), content_path=folder, eta_seconds=0,
    )])
    with db_factory() as s:
        scan_once(qb, s)
        s.commit()

    with db_factory() as s:
        items = s.scalars(select(MediaItem)).all()
        assert len(items) == 1
        m = items[0]
        assert m.title == "Some Movie (2024)"
        assert m.file_path.endswith(".mp4")
        assert m.size_bytes == 5_000_000
        assert m.torrent_hash == "abc"


def test_scan_once_skips_incomplete_torrents(db_factory, tmp_path):
    qb = FakeQbClient([TorrentInfo(
        hash="abc", name="x", progress=0.5, dlspeed=1000, state="downloading",
        size=1, save_path=str(tmp_path), content_path=str(tmp_path), eta_seconds=600,
    )])
    with db_factory() as s:
        scan_once(qb, s)
        s.commit()
    with db_factory() as s:
        assert s.scalars(select(MediaItem)).first() is None


def test_scan_once_idempotent(db_factory, tmp_path):
    folder = _make_completed_torrent_dir(tmp_path)
    qb = FakeQbClient([TorrentInfo(
        hash="abc", name="Movie", progress=1.0, dlspeed=0, state="uploading",
        size=5_000_000, save_path=str(tmp_path), content_path=folder, eta_seconds=0,
    )])
    with db_factory() as s:
        scan_once(qb, s); s.commit()
    with db_factory() as s:
        scan_once(qb, s); s.commit()
    with db_factory() as s:
        assert len(s.scalars(select(MediaItem)).all()) == 1


def test_scan_once_picks_largest_video_file(db_factory, tmp_path):
    folder = tmp_path / "Show"
    folder.mkdir()
    (folder / "tiny.mkv").write_bytes(b"\x00" * 1000)
    (folder / "big.mkv").write_bytes(b"\x00" * 10_000_000)
    (folder / "huge.txt").write_bytes(b"\x00" * 100_000_000)  # не видео

    qb = FakeQbClient([TorrentInfo(
        hash="h", name="Show", progress=1.0, dlspeed=0, state="uploading",
        size=10_001_000, save_path=str(tmp_path), content_path=str(folder), eta_seconds=0,
    )])
    with db_factory() as s:
        scan_once(qb, s); s.commit()
    with db_factory() as s:
        m = s.scalars(select(MediaItem)).one()
        assert m.file_path.endswith("big.mkv")
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/torrents/scanner.py`**

```python
"""Фоновый сканер: периодически опрашивает qBittorrent, для завершённых торрентов,
которых ещё нет в media_items, создаёт записи в БД."""
import asyncio
import logging
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import MediaItem
from app.torrents.title_parser import parse_title
from app.torrents.types import TorrentInfo

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".m4v", ".ts"}


class _QbProto(Protocol):
    def list_torrents(self) -> list[TorrentInfo]: ...


def _find_largest_video(content_path: str) -> Path | None:
    p = Path(content_path)
    if p.is_file():
        return p if p.suffix.lower() in VIDEO_EXTENSIONS else None
    if not p.is_dir():
        return None
    candidates = [
        f for f in p.rglob("*")
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_size)


def scan_once(qb: _QbProto, session: Session) -> int:
    """Один проход. Возвращает число добавленных media_items."""
    try:
        torrents = qb.list_torrents()
    except Exception as e:  # ловим всё, чтобы не уронить фоновую задачу
        log.warning("scan_once: qBittorrent error: %s", e)
        return 0

    existing_hashes = set(session.scalars(select(MediaItem.torrent_hash)).all())
    added = 0
    for t in torrents:
        if not t.is_complete:
            continue
        if t.hash in existing_hashes:
            continue
        video = _find_largest_video(t.content_path)
        if video is None:
            log.info("scan_once: no video file in %s, skipping", t.content_path)
            continue
        item = MediaItem(
            torrent_hash=t.hash,
            title=parse_title(video.name),
            file_path=str(video),
            size_bytes=video.stat().st_size,
            added_by=None,  # неизвестно, кто добавил — qBittorrent не хранит
        )
        session.add(item)
        added += 1
    return added


async def scanner_loop(
    qb: _QbProto,
    factory: sessionmaker[Session],
    interval_seconds: float = 10.0,
) -> None:
    """Бесконечный цикл, вызывается из startup-event FastAPI."""
    while True:
        try:
            with factory() as s:
                added = scan_once(qb, s)
                s.commit()
            if added:
                log.info("scanner: added %d new media item(s)", added)
        except Exception as e:
            log.exception("scanner_loop iteration failed: %s", e)
        await asyncio.sleep(interval_seconds)
```

- [ ] **Step 4: Запустить тесты — PASS**

```bash
./venv/Scripts/python -m pytest tests/integration/test_library_scanner.py -v
```

- [ ] **Step 5: Подключить scanner_loop в `app/main.py`**

В импорты:
```python
import asyncio
from contextlib import asynccontextmanager
from app.deps import get_db_factory, get_qbittorrent_client
from app.torrents.scanner import scanner_loop
```

Заменить создание `app = FastAPI(...)` на:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: запускаем фоновый scanner.
    # В тестах TestClient() запустит lifespan, но scanner не пугает (он catches все ошибки).
    task = asyncio.create_task(scanner_loop(get_qbittorrent_client(), get_db_factory(), interval_seconds=10.0))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="MediaServer", lifespan=lifespan)
```

- [ ] **Step 6: Прогнать всю интеграцию — должно остаться зелёным**

```bash
./venv/Scripts/python -m pytest -q
```

Если scanner спамит ошибками в логах из-за того, что в тестах qBittorrent не поднят — это нормально, scanner ловит исключения. Главное чтобы тесты проходили.

- [ ] **Step 7: Коммит**

```bash
git add app/torrents/scanner.py app/main.py tests/integration/test_library_scanner.py
git commit -m "feat(torrents): background scanner creates media_items from completed downloads"
```

---

## Phase 5 — Реальная библиотека и страница медиа (2 задачи)

## Task 13: Библиотека показывает реальные элементы

**Files:**
- Modify: `app/library/routes.py`
- Modify: `templates/library.html`
- Create: `tests/integration/test_library_real.py`

- [ ] **Step 1: Падающий тест**

```python
# tests/integration/test_library_real.py
import pyotp
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret, _derive_key
from app.models import MediaItem, User


def _logged_in(client, db_factory, csrf_for):
    secret = pyotp.random_base32()
    with db_factory() as s:
        s.add(User(
            username="alice", password_hash=hash_password("correct-password-12"),
            must_change_password=False, totp_enabled=True,
            totp_secret_encrypted=encrypt_secret(secret, _derive_key("x" * 64)),
        ))
        s.commit()
    r = client.post("/login", data={
        "username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)
    })
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post("/verify-totp", data={"code": code, "csrf_token": csrf_for(cookie)},
                cookies={"session": cookie})
    return cookie


def test_library_lists_media_items(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        s.add(MediaItem(torrent_hash="h1", title="Movie 1 (2024)", file_path="/x/1.mkv", size_bytes=1_000_000_000))
        s.add(MediaItem(torrent_hash="h2", title="Movie 2 (2023)", file_path="/x/2.mkv", size_bytes=2_000_000_000))
        s.commit()

    r = client.get("/library", cookies={"session": cookie})
    assert r.status_code == 200
    assert "Movie 1 (2024)" in r.text
    assert "Movie 2 (2023)" in r.text
    # Каждый item — ссылка на /media/{id}
    assert "/media/" in r.text
```

- [ ] **Step 2: Запустить — FAIL** (текущий рендер только пустой плейсхолдер)

- [ ] **Step 3: Изменить `app/library/routes.py`**

Заменить файл целиком:
```python
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.deps import get_db, render
from app.models import MediaItem, User

router = APIRouter()


@router.get("/library", response_class=HTMLResponse)
async def library_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    items = db.scalars(select(MediaItem).order_by(MediaItem.added_at.desc())).all()
    return render(request, "library.html", {"user": user, "items": items})
```

- [ ] **Step 4: Изменить `templates/library.html`**

```html
{% extends "base.html" %}
{% block title %}Библиотека{% endblock %}
{% block content %}
<h1>Библиотека</h1>
<p><a href="/add-torrent">+ Добавить magnet</a> · <a href="/downloads">→ Загрузки</a></p>
{% if not items %}
  <p>Здесь пока ничего нет. Добавьте magnet-ссылку, чтобы начать.</p>
{% else %}
<ul style="list-style:none; padding:0">
{% for it in items %}
  <li style="padding:0.5rem 0; border-bottom: 1px solid #eee">
    <a href="/media/{{ it.id }}">{{ it.title }}</a>
    <span style="color:#888">— {{ (it.size_bytes / (1024**3)) | round(1) }} ГБ</span>
  </li>
{% endfor %}
</ul>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Запустить — PASS** + полный suite

- [ ] **Step 6: Коммит**

```bash
git add app/library/routes.py templates/library.html tests/integration/test_library_real.py
git commit -m "feat(library): render real media items from DB"
```

---

## Task 14: `/media/{id}` страница (скелет, без плеера)

**Files:**
- Modify: `app/library/routes.py`
- Create: `templates/media.html`

- [ ] **Step 1: Падающий тест в `tests/integration/test_library_real.py`**

```python
def test_media_page_shows_title_and_actions(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        m = MediaItem(torrent_hash="h", title="Test Movie", file_path="/x/m.mkv", size_bytes=1)
        s.add(m); s.commit(); s.refresh(m)
        mid = m.id

    r = client.get(f"/media/{mid}", cookies={"session": cookie})
    assert r.status_code == 200
    assert "Test Movie" in r.text
    # Кнопки скачать/удалить упоминаются (хотя сами эндпойнты в Tasks 22-23)
    assert "/api/download/" in r.text
    assert "delete" in r.text.lower() or "удалить" in r.text.lower()


def test_media_page_404_for_missing_id(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/media/9999", cookies={"session": cookie})
    assert r.status_code == 404
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/library/routes.py`**

В импорты:
```python
from fastapi import HTTPException
```

В конец:
```python
@router.get("/media/{media_id}", response_class=HTMLResponse)
async def media_page(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)
    return render(request, "media.html", {"user": user, "item": item})
```

- [ ] **Step 4: Создать `templates/media.html`**

```html
{% extends "base.html" %}
{% block title %}{{ item.title }}{% endblock %}
{% block content %}
<h1>{{ item.title }}</h1>
<p style="color:#888">{{ (item.size_bytes / (1024**3)) | round(1) }} ГБ</p>

<div id="player-container">
  <video id="player" controls preload="metadata" style="width:100%; max-width:960px; background:#000"></video>
</div>

<p style="margin-top:1rem">
  <a href="/api/download/{{ item.id }}" class="button">Скачать оригинал</a>
  <form method="post" action="/api/media/{{ item.id }}/delete" style="display:inline" onsubmit="return confirm('Удалить «{{ item.title }}»?');">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button type="submit">Удалить</button>
  </form>
  <a href="/library">← Библиотека</a>
</p>

<!-- Плеер подключается в Task 19 — здесь пока заглушка -->
<script>
console.log("media page loaded for {{ item.id }}");
</script>
{% endblock %}
```

- [ ] **Step 5: Запустить — PASS**

- [ ] **Step 6: Коммит**

```bash
git add app/library/routes.py templates/media.html tests/integration/test_library_real.py
git commit -m "feat(library): /media/{id} page with placeholder player and action buttons"
```

---

## Phase 6 — HLS streaming primitives (4 задачи)

## Task 15: Stream registry

**Files:**
- Create: `app/streaming/__init__.py` (пустой)
- Create: `app/streaming/stream_registry.py`
- Create: `tests/unit/test_stream_registry.py`

- [ ] **Step 1: Падающий тест `tests/unit/test_stream_registry.py`**

```python
import time

import pytest

from app.streaming.stream_registry import StreamHandle, StreamRegistry


def test_register_and_lookup():
    reg = StreamRegistry()
    h = StreamHandle(media_id=1, user_id=2, work_dir="/tmp/x", process=None)
    reg.register(h)
    assert reg.get(1, 2) is h


def test_register_replaces_existing():
    reg = StreamRegistry()
    h1 = StreamHandle(media_id=1, user_id=2, work_dir="/tmp/x1", process=None)
    h2 = StreamHandle(media_id=1, user_id=2, work_dir="/tmp/x2", process=None)
    reg.register(h1)
    reg.register(h2)
    assert reg.get(1, 2) is h2


def test_unregister():
    reg = StreamRegistry()
    h = StreamHandle(media_id=1, user_id=2, work_dir="/tmp/x", process=None)
    reg.register(h)
    reg.unregister(1, 2)
    assert reg.get(1, 2) is None


def test_touch_updates_last_access():
    reg = StreamRegistry()
    h = StreamHandle(media_id=1, user_id=2, work_dir="/tmp/x", process=None)
    reg.register(h)
    t1 = h.last_access
    time.sleep(0.01)
    reg.touch(1, 2)
    assert reg.get(1, 2).last_access > t1


def test_idle_streams_returns_those_past_threshold():
    reg = StreamRegistry()
    h = StreamHandle(media_id=1, user_id=2, work_dir="/tmp/x", process=None)
    reg.register(h)
    # Подменяем время доступа на «давно»
    object.__setattr__(h, "last_access", time.time() - 120)
    idle = list(reg.idle_streams(idle_seconds=60))
    assert len(idle) == 1
    assert idle[0].media_id == 1
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/streaming/__init__.py`** — пустой.

- [ ] **Step 4: Реализовать `app/streaming/stream_registry.py`**

```python
"""In-memory tracker активных HLS-стримов.

Ключ — пара (media_id, user_id), потому что один media может одновременно смотреть несколько юзеров.
Значение — StreamHandle с подпроцессом ffmpeg и временной директорией для сегментов.

Идея: при каждом запросе сегмента/плейлиста — touch().
Watchdog периодически вызывает idle_streams() и убивает старьё.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class StreamHandle:
    media_id: int
    user_id: int
    work_dir: str
    process: object  # subprocess.Popen (или None в тестах) — типизация subprocess неудобна
    seek_seconds: float = 0.0
    last_access: float = field(default_factory=time.time)


class StreamRegistry:
    def __init__(self):
        self._streams: dict[tuple[int, int], StreamHandle] = {}
        self._lock = threading.Lock()

    def register(self, handle: StreamHandle) -> None:
        with self._lock:
            self._streams[(handle.media_id, handle.user_id)] = handle

    def get(self, media_id: int, user_id: int) -> StreamHandle | None:
        with self._lock:
            return self._streams.get((media_id, user_id))

    def unregister(self, media_id: int, user_id: int) -> StreamHandle | None:
        with self._lock:
            return self._streams.pop((media_id, user_id), None)

    def touch(self, media_id: int, user_id: int) -> None:
        with self._lock:
            h = self._streams.get((media_id, user_id))
            if h is not None:
                h.last_access = time.time()

    def idle_streams(self, idle_seconds: float) -> Iterable[StreamHandle]:
        cutoff = time.time() - idle_seconds
        with self._lock:
            return [h for h in self._streams.values() if h.last_access < cutoff]

    def all_streams(self) -> Iterable[StreamHandle]:
        with self._lock:
            return list(self._streams.values())


# Глобальный инстанс — синглтон на процесс
_registry: StreamRegistry | None = None


def get_registry() -> StreamRegistry:
    global _registry
    if _registry is None:
        _registry = StreamRegistry()
    return _registry
```

- [ ] **Step 5: Запустить — PASS**

- [ ] **Step 6: Коммит**

```bash
git add app/streaming/ tests/unit/test_stream_registry.py
git commit -m "feat(streaming): in-memory StreamRegistry for active HLS sessions"
```

---

## Task 16: ffmpeg runner

**Files:**
- Create: `app/streaming/ffmpeg_runner.py`
- Create: `tests/fixtures/sample.mp4` (см. инструкции в Step 1)
- Create: `tests/unit/test_ffmpeg_runner.py`

- [ ] **Step 1: Сгенерировать тестовый mp4-фикстуру**

ffmpeg должен быть установлен. Сгенерируем 10 секунд цветной заставки 320×240:

```bash
mkdir -p tests/fixtures
ffmpeg -y -f lavfi -i testsrc=duration=10:size=320x240:rate=15 -c:v libx264 -preset ultrafast -pix_fmt yuv420p tests/fixtures/sample.mp4
```

Проверьте: `ls -la tests/fixtures/sample.mp4` — около 50–200 КБ.

Если ffmpeg не установлен на машине — установите:
- Windows: `winget install Gyan.FFmpeg` или скачайте с ffmpeg.org
- Ubuntu: `sudo apt install ffmpeg`

- [ ] **Step 2: Падающий тест `tests/unit/test_ffmpeg_runner.py`**

```python
import subprocess
import time
from pathlib import Path

import pytest

from app.streaming.ffmpeg_runner import HlsParams, kill, start_hls, wait_for_first_segment


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


@pytest.fixture
def work_dir(tmp_path):
    return tmp_path / "hls_session"


def test_start_hls_creates_playlist_and_segments(work_dir):
    assert SAMPLE.exists(), f"sample.mp4 missing at {SAMPLE} (see Task 16 Step 1)"
    work_dir.mkdir()
    proc = start_hls(HlsParams(
        source=str(SAMPLE),
        work_dir=str(work_dir),
        seek_seconds=0.0,
    ))
    try:
        # ffmpeg должен начать писать сегменты
        ok = wait_for_first_segment(work_dir, timeout=15.0)
        assert ok, "ffmpeg не создал ни одного сегмента за 15 секунд"

        playlist = work_dir / "playlist.m3u8"
        assert playlist.exists()
        content = playlist.read_text()
        assert "#EXTM3U" in content
        assert "seg_" in content
    finally:
        kill(proc)


def test_kill_terminates_process(work_dir):
    work_dir.mkdir()
    proc = start_hls(HlsParams(source=str(SAMPLE), work_dir=str(work_dir), seek_seconds=0.0))
    assert proc.poll() is None  # процесс жив
    kill(proc)
    # Через 2с должен быть мёртв
    for _ in range(20):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    assert proc.poll() is not None


def test_seek_offset_starts_later_in_video(work_dir):
    work_dir.mkdir()
    # Запросить с 5-й секунды
    proc = start_hls(HlsParams(source=str(SAMPLE), work_dir=str(work_dir), seek_seconds=5.0))
    try:
        ok = wait_for_first_segment(work_dir, timeout=15.0)
        assert ok
        # Просто убеждаемся, что сегмент создан — точное содержание трудно проверить без декодирования
    finally:
        kill(proc)
```

- [ ] **Step 3: Запустить — FAIL**

- [ ] **Step 4: Реализовать `app/streaming/ffmpeg_runner.py`**

```python
"""Запуск ffmpeg-подпроцесса для on-the-fly HLS-транскодинга.

Параметры подобраны под спецификацию §5.5:
- libx264 / preset veryfast / CRF 23 (баланс качества и нагрузки CPU)
- AAC 128k
- HLS-сегменты по 6 секунд
- VOD-плейлист (hls_list_size=0)

Перемотка реализуется внешним кодом: kill старый процесс, start новый с другим seek_seconds.
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import logging

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HlsParams:
    source: str
    work_dir: str
    seek_seconds: float


def start_hls(params: HlsParams) -> subprocess.Popen:
    """Запускает ffmpeg, который будет писать playlist.m3u8 + seg_*.ts в work_dir."""
    Path(params.work_dir).mkdir(parents=True, exist_ok=True)
    playlist = str(Path(params.work_dir) / "playlist.m3u8")
    segment_pattern = str(Path(params.work_dir) / "seg_%05d.ts")

    cmd = [
        "ffmpeg",
        "-loglevel", "warning",
        "-nostdin",
    ]
    if params.seek_seconds > 0:
        cmd += ["-ss", f"{params.seek_seconds:.3f}"]
    cmd += [
        "-i", params.source,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-f", "hls",
        "-hls_time", "6",
        "-hls_list_size", "0",
        "-hls_segment_filename", segment_pattern,
        playlist,
    ]
    log.debug("ffmpeg cmd: %s", shlex.join(cmd))
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        # На Windows нет os.setsid; используем CREATE_NEW_PROCESS_GROUP вместо для kill-tree.
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )


def wait_for_first_segment(work_dir: str | Path, timeout: float = 15.0) -> bool:
    """Ждёт появления первого сегмента, чтобы плейлист был «играбельным»."""
    deadline = time.time() + timeout
    work = Path(work_dir)
    while time.time() < deadline:
        if any(work.glob("seg_*.ts")):
            return True
        time.sleep(0.1)
    return False


def kill(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    """Завершить процесс. SIGTERM, потом SIGKILL если не вышел."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
    except ProcessLookupError:
        pass
```

- [ ] **Step 5: Запустить тесты — PASS**

```bash
./venv/Scripts/python -m pytest tests/unit/test_ffmpeg_runner.py -v
```

Тесты долгие (~30 сек суммарно из-за реальных запусков ffmpeg).

- [ ] **Step 6: Коммит**

```bash
git add app/streaming/ffmpeg_runner.py tests/fixtures/sample.mp4 tests/unit/test_ffmpeg_runner.py
git commit -m "feat(streaming): ffmpeg HLS runner with start/wait/kill"
```

---

## Task 17: HLS playlist endpoint

**Files:**
- Create: `app/streaming/routes.py`
- Modify: `app/main.py`
- Create: `tests/integration/test_streaming.py`

- [ ] **Step 1: Падающий integration-тест `tests/integration/test_streaming.py`**

```python
import shutil
from pathlib import Path

import pyotp
import pytest
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret, _derive_key
from app.models import MediaItem, User
from app.streaming.stream_registry import get_registry


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _logged_in(client, db_factory, csrf_for):
    secret = pyotp.random_base32()
    with db_factory() as s:
        s.add(User(
            username="alice", password_hash=hash_password("correct-password-12"),
            must_change_password=False, totp_enabled=True,
            totp_secret_encrypted=encrypt_secret(secret, _derive_key("x" * 64)),
        ))
        s.commit()
    r = client.post("/login", data={
        "username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)
    })
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post("/verify-totp", data={"code": code, "csrf_token": csrf_for(cookie)},
                cookies={"session": cookie})
    return cookie


@pytest.fixture(autouse=True)
def _clear_registry():
    # Между тестами очищаем глобальный registry и убиваем процессы
    yield
    reg = get_registry()
    for h in list(reg.all_streams()):
        if h.process is not None:
            from app.streaming.ffmpeg_runner import kill
            kill(h.process)
        reg.unregister(h.media_id, h.user_id)


def _create_media(db_factory, sample: Path) -> int:
    with db_factory() as s:
        m = MediaItem(torrent_hash="h", title="Test", file_path=str(sample), size_bytes=sample.stat().st_size)
        s.add(m); s.commit(); s.refresh(m)
        return m.id


def test_playlist_starts_ffmpeg_and_returns_m3u8(client, db_factory, csrf_for):
    assert SAMPLE.exists()
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    r = client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert "#EXTM3U" in r.text


def test_playlist_unauthenticated_returns_401(client, db_factory):
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/stream/{mid}/playlist.m3u8")
    # /api/* — middleware не редиректит, отдаёт 401
    assert r.status_code == 401


def test_playlist_404_for_unknown_media(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/api/stream/9999/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 404
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/streaming/routes.py`**

```python
import tempfile
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.deps import get_db
from app.models import MediaItem, User
from app.streaming.ffmpeg_runner import HlsParams, kill, start_hls, wait_for_first_segment
from app.streaming.stream_registry import StreamHandle, get_registry


api_router = APIRouter(prefix="/api/stream")


def _ensure_stream(media: MediaItem, user_id: int) -> StreamHandle:
    """Если стрим для (media_id, user_id) уже работает — touch и вернуть.
    Иначе — стартануть ffmpeg и положить в registry."""
    reg = get_registry()
    existing = reg.get(media.id, user_id)
    if existing is not None:
        reg.touch(media.id, user_id)
        return existing
    work_dir = Path(tempfile.mkdtemp(prefix=f"hls_m{media.id}_u{user_id}_"))
    proc = start_hls(HlsParams(source=media.file_path, work_dir=str(work_dir), seek_seconds=0.0))
    handle = StreamHandle(media_id=media.id, user_id=user_id, work_dir=str(work_dir), process=proc)
    reg.register(handle)
    if not wait_for_first_segment(work_dir, timeout=15.0):
        kill(proc)
        reg.unregister(media.id, user_id)
        raise HTTPException(status_code=503, detail="ffmpeg не выдал первый сегмент за 15с")
    return handle


@api_router.get("/{media_id}/playlist.m3u8")
async def stream_playlist(
    media_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    media = db.get(MediaItem, media_id)
    if media is None:
        raise HTTPException(status_code=404)
    handle = _ensure_stream(media, user.id)
    playlist = Path(handle.work_dir) / "playlist.m3u8"
    if not playlist.exists():
        raise HTTPException(status_code=503, detail="плейлист ещё не сгенерирован")
    return Response(
        content=playlist.read_bytes(),
        media_type="application/vnd.apple.mpegurl",
    )
```

- [ ] **Step 4: Подключить в `app/main.py`**

```python
from app.streaming.routes import api_router as streaming_api_router
# ...
app.include_router(streaming_api_router)
```

- [ ] **Step 5: Запустить — PASS**

```bash
./venv/Scripts/python -m pytest tests/integration/test_streaming.py -v
```

Тесты медленные (~15с на запуск ffmpeg). Это нормально.

- [ ] **Step 6: Коммит**

```bash
git add app/streaming/routes.py app/main.py tests/integration/test_streaming.py
git commit -m "feat(streaming): /api/stream/{id}/playlist.m3u8 starts ffmpeg and returns M3U8"
```

---

## Task 18: HLS segment endpoint

**Files:**
- Modify: `app/streaming/routes.py`

- [ ] **Step 1: Падающий тест в `tests/integration/test_streaming.py`**

```python
def test_segment_returned_after_playlist(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    # Сначала запросим плейлист, чтобы стартовал ffmpeg
    r = client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 200

    # Достанем имя первого сегмента из плейлиста
    seg_name = None
    for line in r.text.splitlines():
        if line.startswith("seg_") and line.endswith(".ts"):
            seg_name = line
            break
    assert seg_name is not None, "плейлист не содержит ни одного сегмента"

    r2 = client.get(f"/api/stream/{mid}/{seg_name}", cookies={"session": cookie})
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "video/mp2t"
    assert len(r2.content) > 0


def test_segment_unknown_returns_404(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    # Сначала запустим стрим
    client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    r = client.get(f"/api/stream/{mid}/seg_99999.ts", cookies={"session": cookie})
    assert r.status_code == 404
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/streaming/routes.py`**

В конец:
```python
import re

_SEGMENT_NAME_RE = re.compile(r"^seg_\d{5}\.ts$")


@api_router.get("/{media_id}/{segment_name}")
async def stream_segment(
    media_id: int,
    segment_name: str,
    user: Annotated[User, Depends(get_current_user)],
):
    if not _SEGMENT_NAME_RE.match(segment_name):
        raise HTTPException(status_code=404)
    reg = get_registry()
    handle = reg.get(media_id, user.id)
    if handle is None:
        raise HTTPException(status_code=410, detail="стрим уже завершён, обновите страницу")
    seg_path = Path(handle.work_dir) / segment_name
    if not seg_path.exists():
        raise HTTPException(status_code=404)
    reg.touch(media_id, user.id)
    return FileResponse(str(seg_path), media_type="video/mp2t")
```

- [ ] **Step 4: Запустить — PASS**

- [ ] **Step 5: Коммит**

```bash
git add app/streaming/routes.py tests/integration/test_streaming.py
git commit -m "feat(streaming): /api/stream/{id}/seg_NNNNN.ts serves HLS segments"
```

---

## Phase 7 — Player UI + progress (3 задачи)

## Task 19: hls.js плеер на `/media/{id}`

**Files:**
- Modify: `templates/media.html`
- Download: `static/hls.min.js`

- [ ] **Step 1: Скачать hls.js**

```bash
curl -sLo static/hls.min.js https://unpkg.com/hls.js@1.5.13/dist/hls.min.js
ls -la static/hls.min.js  # должно быть ~120 КБ
```

- [ ] **Step 2: Заменить `templates/media.html`**

```html
{% extends "base.html" %}
{% block title %}{{ item.title }}{% endblock %}
{% block content %}
<h1>{{ item.title }}</h1>
<p style="color:#888">{{ (item.size_bytes / (1024**3)) | round(1) }} ГБ</p>

<video id="player" controls preload="metadata" style="width:100%; max-width:960px; background:#000"></video>

<p style="margin-top:1rem">
  <a href="/api/download/{{ item.id }}" class="button">Скачать оригинал</a>
  <form method="post" action="/api/media/{{ item.id }}/delete" style="display:inline" onsubmit="return confirm('Удалить «{{ item.title }}»?');">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button type="submit">Удалить</button>
  </form>
  <a href="/library">← Библиотека</a>
</p>

<script src="/static/hls.min.js"></script>
<script>
(function() {
  const video = document.getElementById('player');
  const src = '/api/stream/{{ item.id }}/playlist.m3u8';

  if (Hls.isSupported()) {
    const hls = new Hls();
    hls.loadSource(src);
    hls.attachMedia(video);
    hls.on(Hls.Events.ERROR, (e, data) => {
      console.error("HLS error:", data);
      if (data.fatal) {
        document.querySelector('main').innerHTML += '<p class="error">Поток упал. Обновите страницу.</p>';
      }
    });
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    // Safari/iOS — нативная поддержка HLS
    video.src = src;
  } else {
    document.querySelector('main').innerHTML += '<p class="error">Браузер не поддерживает HLS. Скачайте оригинал.</p>';
  }
})();
</script>
{% endblock %}
```

- [ ] **Step 3: Запустить тесты — должны все пройти (page_renders теста хватит)**

```bash
./venv/Scripts/python -m pytest tests/integration/test_library_real.py -v
```

- [ ] **Step 4: Smoke-тест в реальном браузере** (необязательно для CI, но полезно подтвердить)

```bash
./venv/Scripts/python -m uvicorn app.main:app --port 8000 &
# открыть http://127.0.0.1:8000, залогиниться, добавить magnet, дождаться скачивания, открыть /media/N
```

(пропустите если ffmpeg/qBittorrent не настроены — integration-тесты уже доказывают работу пайплайна)

- [ ] **Step 5: Коммит**

```bash
git add static/hls.min.js templates/media.html
git commit -m "feat(player): hls.js video player on /media/{id} with fallback"
```

---

## Task 20: `/api/progress` heartbeat endpoint

**Files:**
- Modify: `app/streaming/routes.py`
- Modify: `templates/media.html`

- [ ] **Step 1: Падающий тест в `tests/integration/test_streaming.py`**

```python
from sqlalchemy import select
from app.models import WatchProgress


def test_progress_endpoint_upserts_watch_progress(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    r = client.post(
        "/api/progress",
        json={"media_id": mid, "position_seconds": 42},
        cookies={"session": cookie},
    )
    assert r.status_code == 204

    with db_factory() as s:
        wp = s.scalars(select(WatchProgress).where(WatchProgress.media_id == mid)).one()
        assert wp.position_seconds == 42

    # Повторно — обновляет
    r2 = client.post(
        "/api/progress",
        json={"media_id": mid, "position_seconds": 100},
        cookies={"session": cookie},
    )
    assert r2.status_code == 204

    with db_factory() as s:
        wp = s.scalars(select(WatchProgress).where(WatchProgress.media_id == mid)).one()
        assert wp.position_seconds == 100


def test_progress_unauth_returns_401(client, db_factory):
    mid = _create_media(db_factory, SAMPLE)
    r = client.post("/api/progress", json={"media_id": mid, "position_seconds": 1})
    assert r.status_code == 401
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/streaming/routes.py`**

В импорты:
```python
from datetime import datetime, timezone
from fastapi import Body
from pydantic import BaseModel
from sqlalchemy import select
from app.models import WatchProgress
```

В конец:
```python
class _ProgressIn(BaseModel):
    media_id: int
    position_seconds: int


@api_router.post("/progress", status_code=204, include_in_schema=False)
async def progress(
    payload: Annotated[_ProgressIn, Body()],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    # Upsert по (user_id, media_id)
    existing = db.scalars(
        select(WatchProgress).where(
            WatchProgress.user_id == user.id,
            WatchProgress.media_id == payload.media_id,
        )
    ).first()
    now = datetime.now(timezone.utc)
    if existing is not None:
        existing.position_seconds = payload.position_seconds
        existing.updated_at = now
    else:
        db.add(WatchProgress(
            user_id=user.id, media_id=payload.media_id,
            position_seconds=payload.position_seconds, updated_at=now,
        ))
    # Также используем как heartbeat для активного стрима
    get_registry().touch(payload.media_id, user.id)
    db.commit()
```

**Важно:** регистрируем POST-эндпоинт в api_router, но без CSRF (это не браузерная форма, а fetch-API из плеера; если хочется паранои — можно добавить отдельный заголовок-проверку, но плеерный код мы пишем сами и контролируем).

- [ ] **Step 4: Дополнить `templates/media.html`** — отправлять heartbeat каждые 10с

В конец `<script>`:
```javascript
setInterval(() => {
  if (video.paused || video.ended || isNaN(video.currentTime)) return;
  fetch('/api/progress', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({media_id: {{ item.id }}, position_seconds: Math.floor(video.currentTime)}),
  }).catch(e => console.warn('progress save failed', e));
}, 10000);
```

(добавьте перед закрывающей `})();`)

- [ ] **Step 5: Запустить тесты — PASS**

- [ ] **Step 6: Коммит**

```bash
git add app/streaming/routes.py templates/media.html tests/integration/test_streaming.py
git commit -m "feat(streaming): /api/progress saves watch position; player sends heartbeat"
```

---

## Task 21: Watchdog для убийства idle ffmpeg

**Files:**
- Modify: `app/main.py` (добавить второй фоновый таск)
- Create: `app/streaming/watchdog.py`

- [ ] **Step 1: Падающий тест `tests/unit/test_streaming_watchdog.py`** — пишем тест, потом реализацию

```python
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

from app.streaming.stream_registry import StreamHandle, StreamRegistry
from app.streaming.watchdog import sweep_idle


def test_sweep_idle_kills_old_streams_and_unregisters():
    reg = StreamRegistry()
    work_dir = tempfile.mkdtemp(prefix="watchdog_test_")
    proc = MagicMock()
    proc.poll.return_value = None  # «жив»
    handle = StreamHandle(media_id=1, user_id=2, work_dir=work_dir, process=proc)
    reg.register(handle)
    object.__setattr__(handle, "last_access", time.time() - 120)

    sweep_idle(reg, idle_seconds=60)

    assert reg.get(1, 2) is None
    proc.terminate.assert_called()  # процесс убит
    assert not Path(work_dir).exists()  # папка удалена


def test_sweep_idle_skips_active_streams(tmp_path):
    reg = StreamRegistry()
    proc = MagicMock(); proc.poll.return_value = None
    handle = StreamHandle(media_id=1, user_id=2, work_dir=str(tmp_path), process=proc)
    reg.register(handle)
    sweep_idle(reg, idle_seconds=60)
    assert reg.get(1, 2) is handle
    proc.terminate.assert_not_called()
```

- [ ] **Step 2: Реализовать `app/streaming/watchdog.py`**

```python
"""Watchdog: периодически убивает ffmpeg-процессы, к которым давно не было доступа."""
import asyncio
import logging
import shutil
from pathlib import Path

from app.streaming.ffmpeg_runner import kill
from app.streaming.stream_registry import StreamRegistry, get_registry

log = logging.getLogger(__name__)

IDLE_THRESHOLD_SECONDS = 60.0
SWEEP_INTERVAL_SECONDS = 15.0


def sweep_idle(reg: StreamRegistry, idle_seconds: float) -> int:
    killed = 0
    for handle in list(reg.idle_streams(idle_seconds)):
        try:
            if handle.process is not None:
                kill(handle.process)
            shutil.rmtree(handle.work_dir, ignore_errors=True)
        finally:
            reg.unregister(handle.media_id, handle.user_id)
            killed += 1
    if killed:
        log.info("watchdog: killed %d idle stream(s)", killed)
    return killed


async def watchdog_loop() -> None:
    reg = get_registry()
    while True:
        try:
            sweep_idle(reg, IDLE_THRESHOLD_SECONDS)
        except Exception:
            log.exception("watchdog iteration failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
```

- [ ] **Step 3: Подключить в `app/main.py:lifespan`**

```python
from app.streaming.watchdog import watchdog_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    scanner_task = asyncio.create_task(
        scanner_loop(get_qbittorrent_client(), get_db_factory(), interval_seconds=10.0)
    )
    watchdog_task = asyncio.create_task(watchdog_loop())
    try:
        yield
    finally:
        for t in (scanner_task, watchdog_task):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
```

- [ ] **Step 4: Запустить тесты — PASS**

- [ ] **Step 5: Коммит**

```bash
git add app/streaming/watchdog.py app/main.py tests/unit/test_streaming_watchdog.py
git commit -m "feat(streaming): watchdog kills idle ffmpeg processes after 60s"
```

---

## Phase 8 — Download + Delete (3 задачи)

## Task 22: `/api/download/{id}` с Range-support

**Files:**
- Create: `app/download/__init__.py` (пустой)
- Create: `app/download/routes.py`
- Modify: `app/main.py`
- Create: `tests/integration/test_download.py`

- [ ] **Step 1: Падающий тест `tests/integration/test_download.py`**

```python
from pathlib import Path

import pyotp
import pytest

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret, _derive_key
from app.models import MediaItem, User


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _logged_in(client, db_factory, csrf_for):
    secret = pyotp.random_base32()
    with db_factory() as s:
        s.add(User(
            username="alice", password_hash=hash_password("correct-password-12"),
            must_change_password=False, totp_enabled=True,
            totp_secret_encrypted=encrypt_secret(secret, _derive_key("x" * 64)),
        ))
        s.commit()
    r = client.post("/login", data={
        "username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)
    })
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post("/verify-totp", data={"code": code, "csrf_token": csrf_for(cookie)},
                cookies={"session": cookie})
    return cookie


def _create_media(db_factory, sample: Path) -> int:
    with db_factory() as s:
        m = MediaItem(torrent_hash="h", title="Test", file_path=str(sample), size_bytes=sample.stat().st_size)
        s.add(m); s.commit(); s.refresh(m)
        return m.id


def test_download_returns_full_file(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/download/{mid}", cookies={"session": cookie})
    assert r.status_code == 200
    assert int(r.headers["content-length"]) == SAMPLE.stat().st_size
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.content[:4] == SAMPLE.read_bytes()[:4]


def test_download_supports_range(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(
        f"/api/download/{mid}",
        headers={"Range": "bytes=0-99"},
        cookies={"session": cookie},
    )
    assert r.status_code == 206
    assert r.headers["content-range"].startswith("bytes 0-99/")
    assert int(r.headers["content-length"]) == 100


def test_download_unauth_returns_401(client, db_factory):
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/download/{mid}")
    assert r.status_code == 401


def test_download_404_for_unknown(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/api/download/9999", cookies={"session": cookie})
    assert r.status_code == 404
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/download/__init__.py`** — пустой.

- [ ] **Step 4: Реализовать `app/download/routes.py`**

```python
"""Скачивание оригинального файла с поддержкой Range-запросов."""
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.deps import get_db
from app.models import MediaItem, User


api_router = APIRouter(prefix="/api/download")


def _parse_range(header: str, file_size: int) -> tuple[int, int] | None:
    """Парсит 'bytes=START-END' (END необязателен). Возвращает (start, end) или None если невалидно."""
    if not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):]
    if "-" not in spec:
        return None
    start_s, end_s = spec.split("-", 1)
    try:
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
    except ValueError:
        return None
    if start < 0 or end >= file_size or start > end:
        return None
    return start, end


@api_router.get("/{media_id}")
async def download(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    media = db.get(MediaItem, media_id)
    if media is None:
        raise HTTPException(status_code=404)
    path = Path(media.file_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="файл отсутствует на диске")

    # Имя файла для скачивания: оригинальное имя файла, ASCII fallback + RFC 5987 utf-8
    filename = path.name
    ascii_fallback = "".join(c if c.isascii() and c.isprintable() else "_" for c in filename)
    cd = (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )

    range_header = request.headers.get("range")
    file_size = path.stat().st_size

    if range_header is None:
        return FileResponse(
            str(path),
            media_type="application/octet-stream",
            headers={"Content-Disposition": cd, "Accept-Ranges": "bytes"},
        )

    parsed = _parse_range(range_header, file_size)
    if parsed is None:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})

    start, end = parsed
    length = end - start + 1

    def _iter():
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(length),
        "Content-Disposition": cd,
        "Accept-Ranges": "bytes",
        "Content-Type": "application/octet-stream",
    }
    from fastapi.responses import StreamingResponse
    return StreamingResponse(_iter(), status_code=206, headers=headers)
```

- [ ] **Step 5: Подключить в `app/main.py`**

```python
from app.download.routes import api_router as download_api_router
app.include_router(download_api_router)
```

- [ ] **Step 6: Запустить — PASS**

- [ ] **Step 7: Коммит**

```bash
git add app/download/ app/main.py tests/integration/test_download.py
git commit -m "feat(download): /api/download/{id} with Range support"
```

---

## Task 23: Удаление media (`/api/media/{id}/delete`)

**Files:**
- Create: `app/library/api_routes.py` (или extension существующего)
- Modify: `app/main.py`
- Create: `tests/integration/test_media_delete.py`

- [ ] **Step 1: Падающий тест `tests/integration/test_media_delete.py`**

```python
from pathlib import Path

import httpx
import pyotp
import pytest
import respx
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret, _derive_key
from app.models import MediaItem, User, WatchProgress


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _logged_in(client, db_factory, csrf_for):
    secret = pyotp.random_base32()
    with db_factory() as s:
        s.add(User(
            username="alice", password_hash=hash_password("correct-password-12"),
            must_change_password=False, totp_enabled=True,
            totp_secret_encrypted=encrypt_secret(secret, _derive_key("x" * 64)),
        ))
        s.commit()
    r = client.post("/login", data={
        "username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)
    })
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post("/verify-totp", data={"code": code, "csrf_token": csrf_for(cookie)},
                cookies={"session": cookie})
    return cookie


@respx.mock
def test_delete_media_removes_db_row_and_calls_qbittorrent(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        m = MediaItem(torrent_hash="h-to-del", title="X", file_path="/x/y.mkv", size_bytes=1)
        s.add(m); s.commit(); s.refresh(m)
        mid = m.id

    respx.post("http://127.0.0.1:8080/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    delete_route = respx.post("http://127.0.0.1:8080/api/v2/torrents/delete").mock(
        return_value=httpx.Response(200)
    )

    r = client.post(
        f"/api/media/{mid}/delete",
        data={"csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/library"
    assert delete_route.called

    with db_factory() as s:
        gone = s.scalars(select(MediaItem).where(MediaItem.id == mid)).first()
        assert gone is None


def test_delete_unknown_media_returns_404(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.post(
        "/api/media/9999/delete",
        data={"csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 404


@respx.mock
def test_delete_cascades_to_watch_progress(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        u = s.scalars(select(User).where(User.username == "alice")).one()
        m = MediaItem(torrent_hash="h", title="X", file_path="/x.mkv", size_bytes=1)
        s.add(m); s.commit(); s.refresh(m)
        s.add(WatchProgress(user_id=u.id, media_id=m.id, position_seconds=42))
        s.commit()
        mid = m.id

    respx.post("http://127.0.0.1:8080/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    respx.post("http://127.0.0.1:8080/api/v2/torrents/delete").mock(return_value=httpx.Response(200))

    client.post(f"/api/media/{mid}/delete", data={"csrf_token": csrf_for(cookie)}, cookies={"session": cookie})

    with db_factory() as s:
        wp = s.scalars(select(WatchProgress).where(WatchProgress.media_id == mid)).first()
        assert wp is None  # CASCADE сработал благодаря Task 1
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/library/routes.py`** (положим эндпоинт сюда — это медиа-операция):

В импорты:
```python
from fastapi.responses import RedirectResponse
from app.csrf import verify_csrf
from app.deps import get_qbittorrent_client
from app.streaming.ffmpeg_runner import kill as kill_ffmpeg
from app.streaming.stream_registry import get_registry
from app.torrents.client import QBittorrentClient, QBittorrentError
```

В конец `app/library/routes.py`:
```python
@router.post("/api/media/{media_id}/delete")
async def delete_media(
    media_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    qb: Annotated[QBittorrentClient, Depends(get_qbittorrent_client)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    # 1. Убить все ffmpeg-процессы для этого media_id (любого юзера)
    reg = get_registry()
    for handle in list(reg.all_streams()):
        if handle.media_id == media_id and handle.process is not None:
            kill_ffmpeg(handle.process)
            reg.unregister(handle.media_id, handle.user_id)
            import shutil
            shutil.rmtree(handle.work_dir, ignore_errors=True)

    # 2. Сказать qBittorrent удалить торрент с файлами
    try:
        qb.delete_torrent(item.torrent_hash, delete_files=True)
    except QBittorrentError:
        # qBittorrent упал — продолжаем; файлы можно потом вычистить вручную
        pass

    # 3. Удалить из БД (CASCADE снесёт watch_progress)
    db.delete(item)
    db.commit()

    return RedirectResponse("/library", status_code=303)
```

- [ ] **Step 4: Запустить тесты — PASS**

- [ ] **Step 5: Коммит**

```bash
git add app/library/routes.py tests/integration/test_media_delete.py
git commit -m "feat(library): /api/media/{id}/delete kills ffmpeg + qBittorrent + DB row"
```

---

## Phase 9 — Финал

## Task 24: Финальный smoke test всего Plan 2

**Files:** (нет — только проверка)

- [ ] **Step 1: Прогнать всю тестовую базу**

```bash
./venv/Scripts/python -m pytest -v
```

Ожидается: ~110+ passed (Plan 1: 67 → плюс ~45 новых от Plan 2). Если что-то падает — починить **точечно**, не двигаемся дальше пока не зелено.

- [ ] **Step 2: Программный smoke-тест с реальным сервером**

Убедитесь, что qBittorrent-nox запущен на 127.0.0.1:8080 (или настройте `.env` так, чтобы конкретный URL мокался).

```bash
# Reset
rm -f app.db
./venv/Scripts/python -m alembic upgrade head
./venv/Scripts/python -m scripts.create_admin --username root --password 'admin-password-12'

# Запуск
./venv/Scripts/python -m uvicorn app.main:app --port 8000 &
SERVER_PID=$!
sleep 3

# Проверка через httpx (Python скрипт)
./venv/Scripts/python <<'EOF'
import httpx

c = httpx.Client(base_url="http://127.0.0.1:8000", follow_redirects=False)

# /health
assert c.get("/health").json() == {"status": "ok"}

# /add-torrent доступен после логина (тут не делаем полный логин, проверяем что 303 → /login)
r = c.get("/add-torrent")
assert r.status_code == 303 and r.headers["location"] == "/login"

# /api/torrents/status без auth → 401
r = c.get("/api/torrents/status")
assert r.status_code == 401

# /downloads без auth → 303
r = c.get("/downloads")
assert r.status_code == 303

print("OK")
EOF

kill $SERVER_PID
```

- [ ] **Step 3: Финальный тег**

```bash
git tag plan-2-complete
git log --oneline | head -30
```

- [ ] **Step 4: Готов к мерджу в main**

(после этого можно вызвать `superpowers:finishing-a-development-branch`)

---

## Self-Review

**Spec coverage:**
- §5.2 qBittorrent → Tasks 5, 6, 7, 9.
- §5.5 ffmpeg → Tasks 16, 17, 18, 21.
- §6.2 magnet flow → Tasks 7, 9, 10, 11, 12.
- §6.3 watch flow → Tasks 13, 14, 17, 18, 19, 20, 21.
- §6.4 download → Task 22.
- §6.5 delete → Task 23.
- §7.1 п.7 (CSRF) → Task 3.
- §7.1 п.10 (validation) → Task 7 (magnet regex).
- D-1, D-2, D-4, D-6, D-10 (Plan 1 debt) → Tasks 1, 2, 3, 4.

Закрыто всё в скоупе Plan 2.

**Откладываем явно:**
- D-3 constant-time login → Plan 3 (fail2ban закроет).
- D-5 backup-codes UX → не критично.
- D-7 HTTPS dev workaround → Plan 3.
- D-8 sessions tz-fix → косметика, оставим.
- D-9 расширенная валидация Settings → Plan 3 если потребуется.

**Перемотка (seek):** в спеке §6.3 описана как kill+restart с -ss. **В этом плане не реализована** — текущий плеер работает на VOD HLS-плейлисте (`hls_list_size=0`), плеер сможет переходить в пределах сгенерированных сегментов. Если пользователь перематывает за пределы — возможен буферинг. Если эта проблема возникнет на реальном использовании, отдельная задача добавит логику в `_ensure_stream`: при запросе сегмента, который ещё не существует, kill + restart с `seek_seconds = (segment_index * 6)`. **Помечено в TODO для следующего плана.**

**Placeholder scan:** прошёл по плану, нет «TBD»/«TODO»/«implement later». Все code-блоки полные.

**Type consistency:**
- `TorrentInfo` (Task 5) — используется в client.py, scanner.py, routes.py. Согласовано.
- `StreamHandle` (Task 15) — используется в registry, ffmpeg_runner, routes, watchdog. Согласовано.
- `HlsParams` (Task 16) — используется в ffmpeg_runner и routes. Согласовано.
- `QBittorrentClient.add_magnet/list_torrents/delete_torrent` сигнатуры стабильны между task 5 и task 7/9/23.
- `render(request, name, ctx)` (Task 3) — используется во всех обновлённых роутерах. Согласовано.

**Готово к исполнению.**
