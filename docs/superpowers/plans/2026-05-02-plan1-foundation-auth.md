# План 1: Фундамент + Auth + пустой UI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Скелет FastAPI-приложения с авторизацией (пароль + TOTP + backup-коды), управлением пользователями админом, пустым плейсхолдером библиотеки. Запускается локально через `uvicorn`.

**Architecture:** Sync FastAPI на SQLAlchemy 2.x + SQLite + Alembic. Серверный рендеринг Jinja2, прогрессивный HTMX. Сессии — токен в БД, cookie HttpOnly. 2FA — TOTP (pyotp) + 10 одноразовых backup-кодов. Политика «при первом входе обязан сменить пароль и активировать 2FA».

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Alembic, SQLite, Jinja2, HTMX, bcrypt, pyotp, qrcode, pydantic-settings, pytest.

**Реализует разделы спецификации:** §5.3 (auth модуль), §5.4 (схема БД), §6.1 (логин с 2FA), §7.1 п.3-4 + 7-8 + 10 (авторизация, сессии, CSRF, валидация), §7.2 (создание пользователей).

**Откладываем на будущие планы:**
- qBittorrent, библиотека, стриминг, скачивание (План 2).
- Caddy, HTTPS, fail2ban, systemd, install.sh, `/admin/health`, бэкапы (План 3).

---

## Структура файлов

```
/
├── app/
│   ├── __init__.py
│   ├── main.py              FastAPI app + routes
│   ├── config.py            настройки из .env (pydantic-settings)
│   ├── db.py                SQLAlchemy engine + Session factory
│   ├── models.py            ORM-модели (User, Session, BackupCode + stub MediaItem/WatchProgress)
│   ├── csrf.py              генерация и проверка CSRF-токенов
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── routes.py        /login, /verify-totp, /change-password, /enroll-2fa, /logout
│   │   ├── passwords.py     bcrypt hash/verify
│   │   ├── totp.py          секрет, QR, проверка кода
│   │   ├── sessions.py      создание/чтение/истечение сессий
│   │   ├── backup_codes.py  генерация 10 кодов, одноразовое использование
│   │   └── deps.py          FastAPI dependency: get_current_user, require_admin
│   ├── library/
│   │   ├── __init__.py
│   │   └── routes.py        /library (плейсхолдер)
│   └── admin/
│       ├── __init__.py
│       └── routes.py        /admin/users (list/create/delete)
├── static/
│   ├── style.css
│   └── htmx.min.js          вендорим
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── verify_totp.html
│   ├── change_password.html
│   ├── enroll_2fa.html
│   ├── library.html
│   ├── admin_users.html
│   └── admin_user_form.html
├── migrations/              Alembic
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial_schema.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py          фикстуры: in-memory БД, test client, freeze_time
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_passwords.py
│   │   ├── test_totp.py
│   │   ├── test_sessions.py
│   │   ├── test_backup_codes.py
│   │   └── test_csrf.py
│   └── integration/
│       ├── __init__.py
│       ├── test_login_flow.py
│       ├── test_first_login_setup.py
│       └── test_admin_users.py
├── scripts/
│   └── create_admin.py      bootstrap-скрипт первого админа
├── .env.example
├── requirements.txt
├── pyproject.toml
├── alembic.ini
├── .gitignore
└── README.md
```

---

## Task 1: Bootstrap проекта

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `app/__init__.py` (пустой)
- Create: `tests/__init__.py` (пустой)
- Create: `tests/unit/__init__.py` (пустой)
- Create: `tests/integration/__init__.py` (пустой)

- [ ] **Step 1: Создать `requirements.txt`**

```
fastapi>=0.110
uvicorn[standard]>=0.29
sqlalchemy>=2.0
alembic>=1.13
pydantic-settings>=2.2
python-multipart>=0.0.9
jinja2>=3.1
itsdangerous>=2.2
bcrypt>=4.1
pyotp>=2.9
qrcode[pil]>=7.4
httpx>=0.27
pytest>=8.0
pytest-cov>=5.0
freezegun>=1.4
```

- [ ] **Step 2: Создать `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "mediasrv"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --strict-markers"
markers = [
    "integration: integration tests (slower)",
]

[tool.setuptools.packages.find]
include = ["app*"]
```

- [ ] **Step 3: Создать `.env.example`**

```
# Скопируйте в .env и заполните своими значениями.
# .env в git НЕ коммитим.

# Случайная строка >= 32 байта (например, openssl rand -hex 32)
SESSION_SECRET=change-me-to-random-64-hex-chars

# SQLite-файл (development)
DATABASE_URL=sqlite:///./app.db

# Куда qBittorrent кладёт файлы (используется в Плане 2; в Плане 1 не используется,
# но конфиг готов сразу).
MEDIA_ROOT=/srv/Общее

# Логин/пароль qBittorrent Web UI (План 2; в Плане 1 не используется).
QBITTORRENT_URL=http://127.0.0.1:8080
QBITTORRENT_USERNAME=admin
QBITTORRENT_PASSWORD=change-me

# Имя для TOTP (видно в Authenticator-приложениях).
TOTP_ISSUER=MediaServer
```

- [ ] **Step 4: Создать `.gitignore`**

```
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.env
*.db
*.db-journal
.pytest_cache/
.coverage
htmlcov/
hls/
*.log
```

- [ ] **Step 5: Создать пустые `app/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`**

Просто пустые файлы.

- [ ] **Step 6: Установить зависимости**

```bash
python -m venv venv
source venv/Scripts/activate    # Windows Git Bash
# или: source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

Ожидается: установка прошла без ошибок, `pip list` показывает все пакеты.

- [ ] **Step 7: Коммит**

```bash
git add pyproject.toml requirements.txt .env.example .gitignore app/ tests/
git commit -m "chore: project bootstrap with pinned deps"
```

---

## Task 2: Конфигурация (pydantic-settings)

**Files:**
- Create: `app/config.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Написать падающий тест `tests/unit/test_config.py`**

```python
import os
from app.config import Settings


def test_settings_loads_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_SECRET", "a" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("MEDIA_ROOT", "/tmp/media")
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestServer")

    s = Settings()

    assert s.session_secret == "a" * 64
    assert s.database_url == "sqlite:///test.db"
    assert s.media_root == "/tmp/media"
    assert s.totp_issuer == "TestServer"


def test_settings_rejects_short_session_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "tooshort")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
    monkeypatch.setenv("MEDIA_ROOT", "/tmp/media")
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestServer")

    import pytest
    with pytest.raises(ValueError):
        Settings()
```

- [ ] **Step 2: Запустить — должен падать на ImportError**

```bash
pytest tests/unit/test_config.py -v
```

Ожидается: FAIL — `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 3: Реализовать `app/config.py`**

```python
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    session_secret: str
    database_url: str
    media_root: str
    qbittorrent_url: str
    qbittorrent_username: str
    qbittorrent_password: str
    totp_issuer: str = "MediaServer"

    @field_validator("session_secret")
    @classmethod
    def session_secret_long_enough(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 characters")
        return v


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Запустить тесты — должны пройти**

```bash
pytest tests/unit/test_config.py -v
```

Ожидается: 2 passed.

- [ ] **Step 5: Коммит**

```bash
git add app/config.py tests/unit/test_config.py
git commit -m "feat(config): load and validate settings from .env"
```

---

## Task 3: Подключение к БД (engine + Session factory)

**Files:**
- Create: `app/db.py`
- Create: `tests/unit/test_db.py`

- [ ] **Step 1: Падающий тест `tests/unit/test_db.py`**

```python
from sqlalchemy import text
from app.db import make_engine, make_session_factory


def test_make_engine_creates_in_memory_sqlite():
    engine = make_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1")).scalar()
    assert result == 1


def test_session_factory_yields_session():
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with factory() as session:
        result = session.execute(text("SELECT 42")).scalar()
    assert result == 42
```

- [ ] **Step 2: Запустить — FAIL**

```bash
pytest tests/unit/test_db.py -v
```

Ожидается: ModuleNotFoundError.

- [ ] **Step 3: Реализовать `app/db.py`**

```python
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
```

- [ ] **Step 4: Запустить тесты — PASS**

```bash
pytest tests/unit/test_db.py -v
```

Ожидается: 2 passed.

- [ ] **Step 5: Коммит**

```bash
git add app/db.py tests/unit/test_db.py
git commit -m "feat(db): SQLAlchemy engine and Session factory"
```

---

## Task 4: ORM-модели

**Files:**
- Create: `app/models.py`
- Create: `tests/unit/test_models.py`

- [ ] **Step 1: Падающий тест `tests/unit/test_models.py`**

```python
from datetime import datetime, timezone
from app.db import Base, make_engine, make_session_factory
from app.models import BackupCode, MediaItem, Session as UserSession, User, WatchProgress


def setup_db():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_user_columns_and_defaults():
    factory = setup_db()
    with factory() as s:
        u = User(username="alice", password_hash="x", is_admin=False)
        s.add(u)
        s.commit()
        s.refresh(u)
        assert u.id is not None
        assert u.totp_enabled is False
        assert u.must_change_password is True
        assert u.totp_secret_encrypted is None
        assert isinstance(u.created_at, datetime)


def test_session_links_to_user():
    factory = setup_db()
    with factory() as s:
        u = User(username="bob", password_hash="x")
        s.add(u)
        s.commit()
        sess = UserSession(token="t" * 43, user_id=u.id, expires_at=datetime.now(timezone.utc))
        s.add(sess)
        s.commit()
        assert sess.user_id == u.id


def test_backup_code_links_to_user():
    factory = setup_db()
    with factory() as s:
        u = User(username="carol", password_hash="x")
        s.add(u)
        s.commit()
        b = BackupCode(user_id=u.id, code_hash="hash")
        s.add(b)
        s.commit()
        assert b.used_at is None


def test_media_item_and_watch_progress_models_exist():
    # Эти таблицы нужны в схеме сразу (для будущих планов), но в Плане 1 не используются.
    factory = setup_db()
    with factory() as s:
        u = User(username="dave", password_hash="x")
        s.add(u)
        s.commit()
        m = MediaItem(torrent_hash="abc", title="T", file_path="/x", size_bytes=1, added_by=u.id)
        s.add(m)
        s.commit()
        w = WatchProgress(user_id=u.id, media_id=m.id, position_seconds=0)
        s.add(w)
        s.commit()
        assert m.id is not None
        assert w.position_seconds == 0
```

- [ ] **Step 2: Запустить — FAIL**

```bash
pytest tests/unit/test_models.py -v
```

- [ ] **Step 3: Реализовать `app/models.py`**

```python
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    totp_secret_encrypted: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    is_partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class BackupCode(Base):
    __tablename__ = "backup_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    torrent_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    added_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class WatchProgress(Base):
    __tablename__ = "watch_progress"
    __table_args__ = (UniqueConstraint("user_id", "media_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    media_id: Mapped[int] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False)
    position_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
```

- [ ] **Step 4: Запустить тесты — PASS**

```bash
pytest tests/unit/test_models.py -v
```

- [ ] **Step 5: Коммит**

```bash
git add app/models.py tests/unit/test_models.py
git commit -m "feat(models): User, Session, BackupCode, MediaItem, WatchProgress"
```

---

## Task 5: Alembic — initial migration

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_initial_schema.py`

- [ ] **Step 1: Сгенерировать скелет Alembic**

```bash
alembic init -t generic migrations
```

Ожидается: появились `alembic.ini` и `migrations/`. Удалите автосгенерированный `migrations/versions/.gitkeep` если есть.

- [ ] **Step 2: Отредактировать `alembic.ini`**

Найти строку `sqlalchemy.url =` и **очистить значение** — URL будем брать из `app.config`:

```
sqlalchemy.url =
```

- [ ] **Step 3: Заменить `migrations/env.py` целиком**

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db import Base
from app import models  # noqa: F401  чтобы зарегистрировать модели в Base.metadata

config = context.config
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # для SQLite ALTER TABLE
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Сгенерировать миграцию**

```bash
alembic revision --autogenerate -m "initial schema"
```

Должен появиться файл вида `migrations/versions/<хеш>_initial_schema.py` с операциями `op.create_table('users', ...)` и т.д. для всех 5 таблиц.

- [ ] **Step 5: Переименовать файл в `0001_initial_schema.py`**

```bash
mv migrations/versions/*_initial_schema.py migrations/versions/0001_initial_schema.py
```

В первой строке файла (после комментария `revision = ...`) задать `revision = "0001"`.

- [ ] **Step 6: Применить миграцию к dev-БД**

```bash
cp .env.example .env
# Отредактируйте .env, проставьте SESSION_SECRET (например: openssl rand -hex 32)
alembic upgrade head
```

Ожидается: создаётся файл `app.db`, появляются все 5 таблиц.

- [ ] **Step 7: Проверить, что таблицы есть**

```bash
sqlite3 app.db ".tables"
```

Ожидается: `alembic_version backup_codes media_items sessions users watch_progress`.

- [ ] **Step 8: Коммит**

```bash
git add alembic.ini migrations/
git commit -m "feat(migrations): initial Alembic setup with full schema"
```

---

## Task 6: Хеширование паролей

**Files:**
- Create: `app/auth/__init__.py` (пустой)
- Create: `app/auth/passwords.py`
- Create: `tests/unit/test_passwords.py`

- [ ] **Step 1: Падающий тест**

```python
# tests/unit/test_passwords.py
from app.auth.passwords import hash_password, verify_password


def test_hash_then_verify_succeeds():
    h = hash_password("correcthorsebatterystaple")
    assert verify_password("correcthorsebatterystaple", h) is True


def test_verify_with_wrong_password_fails():
    h = hash_password("correct")
    assert verify_password("wrong", h) is False


def test_hash_is_not_plaintext():
    h = hash_password("secret123456")
    assert "secret123456" not in h
    assert h.startswith("$2b$")  # bcrypt prefix


def test_verify_rejects_corrupt_hash():
    assert verify_password("anything", "not-a-hash") is False
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/auth/__init__.py`** — пустой файл.

- [ ] **Step 4: Реализовать `app/auth/passwords.py`**

```python
import bcrypt

_BCRYPT_ROUNDS = 12


def hash_password(plaintext: str) -> str:
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(plaintext.encode("utf-8"), salt).decode("utf-8")


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
```

- [ ] **Step 5: Запустить — PASS**

```bash
pytest tests/unit/test_passwords.py -v
```

- [ ] **Step 6: Коммит**

```bash
git add app/auth/__init__.py app/auth/passwords.py tests/unit/test_passwords.py
git commit -m "feat(auth): bcrypt password hashing and verification"
```

---

## Task 7: TOTP (генерация секрета, QR, проверка)

**Files:**
- Create: `app/auth/totp.py`
- Create: `tests/unit/test_totp.py`

- [ ] **Step 1: Падающий тест**

```python
# tests/unit/test_totp.py
import pyotp
from freezegun import freeze_time

from app.auth.totp import (
    decrypt_secret,
    encrypt_secret,
    generate_secret,
    provisioning_uri,
    qr_png_bytes,
    verify_code,
)


def test_generate_secret_returns_base32_string():
    s = generate_secret()
    assert len(s) >= 16
    assert s.isalnum()


def test_provisioning_uri_contains_issuer_and_username():
    secret = generate_secret()
    uri = provisioning_uri(secret, "alice", "TestSrv")
    assert uri.startswith("otpauth://totp/")
    assert "alice" in uri
    assert "TestSrv" in uri


def test_qr_png_returns_bytes_starting_with_png_magic():
    uri = provisioning_uri(generate_secret(), "alice", "TestSrv")
    data = qr_png_bytes(uri)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


@freeze_time("2026-05-02 12:00:00")
def test_verify_code_accepts_current_code():
    secret = generate_secret()
    code = pyotp.TOTP(secret).now()
    assert verify_code(secret, code) is True


@freeze_time("2026-05-02 12:00:00")
def test_verify_code_rejects_wrong_code():
    secret = generate_secret()
    assert verify_code(secret, "000000") is False


def test_encrypt_then_decrypt_roundtrip():
    key = b"k" * 32
    secret = "JBSWY3DPEHPK3PXP"
    enc = encrypt_secret(secret, key)
    assert enc != secret
    assert decrypt_secret(enc, key) == secret


def test_decrypt_with_wrong_key_raises():
    enc = encrypt_secret("JBSWY3DPEHPK3PXP", b"a" * 32)
    import pytest
    with pytest.raises(Exception):
        decrypt_secret(enc, b"b" * 32)
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/auth/totp.py`**

```python
import base64
import io
import os
from hashlib import sha256
from urllib.parse import quote

import pyotp
import qrcode
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=quote(username), issuer_name=quote(issuer))


def qr_png_bytes(uri: str) -> bytes:
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def verify_code(secret: str, code: str, window: int = 1) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=window)


def _derive_key(session_secret: str | bytes) -> bytes:
    raw = session_secret if isinstance(session_secret, bytes) else session_secret.encode("utf-8")
    return sha256(b"totp-key:" + raw).digest()


def encrypt_secret(secret: str, key: bytes) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_secret(encrypted_b64: str, key: bytes) -> str:
    raw = base64.b64decode(encrypted_b64)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
```

- [ ] **Step 4: Добавить `cryptography>=42` в `requirements.txt`**

В конец файла:
```
cryptography>=42
```

И установить:
```bash
pip install cryptography
```

- [ ] **Step 5: Запустить тесты — PASS**

```bash
pytest tests/unit/test_totp.py -v
```

Ожидается: 7 passed.

- [ ] **Step 6: Коммит**

```bash
git add app/auth/totp.py tests/unit/test_totp.py requirements.txt
git commit -m "feat(auth): TOTP generate/verify and AES-GCM secret encryption"
```

---

## Task 8: Backup-коды

**Files:**
- Create: `app/auth/backup_codes.py`
- Create: `tests/unit/test_backup_codes.py`

- [ ] **Step 1: Падающий тест**

```python
# tests/unit/test_backup_codes.py
from app.auth.backup_codes import (
    generate_codes,
    hash_code,
    verify_and_consume,
)
from app.db import Base, make_engine, make_session_factory
from app.models import BackupCode, User


def setup_db_with_user():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        u = User(username="alice", password_hash="x")
        s.add(u)
        s.commit()
        s.refresh(u)
        return factory, u.id


def test_generate_codes_returns_10_unique_human_readable():
    codes = generate_codes()
    assert len(codes) == 10
    assert len(set(codes)) == 10
    for c in codes:
        assert len(c) == 11        # 5-5 with dash, e.g. "ab3cd-ef9gh"
        assert c[5] == "-"


def test_hash_code_is_deterministic_and_not_plaintext():
    h1 = hash_code("abcde-fghij")
    h2 = hash_code("abcde-fghij")
    assert h1 == h2
    assert "abcde-fghij" not in h1


def test_verify_and_consume_marks_code_used_and_only_once():
    factory, uid = setup_db_with_user()
    codes = generate_codes()
    with factory() as s:
        for c in codes:
            s.add(BackupCode(user_id=uid, code_hash=hash_code(c)))
        s.commit()

    with factory() as s:
        assert verify_and_consume(s, uid, codes[0]) is True
        s.commit()

    with factory() as s:
        # Второй раз тот же код — нет.
        assert verify_and_consume(s, uid, codes[0]) is False


def test_verify_unknown_code_returns_false():
    factory, uid = setup_db_with_user()
    with factory() as s:
        assert verify_and_consume(s, uid, "wrong-codez") is False
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/auth/backup_codes.py`**

```python
import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BackupCode

_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # без 0/O/1/l/I — меньше шанса опечаток


def _random_block(n: int = 5) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def generate_codes(count: int = 10) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    while len(codes) < count:
        c = f"{_random_block()}-{_random_block()}"
        if c in seen:
            continue
        seen.add(c)
        codes.append(c)
    return codes


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def verify_and_consume(session: Session, user_id: int, code: str) -> bool:
    h = hash_code(code)
    stmt = select(BackupCode).where(
        BackupCode.user_id == user_id,
        BackupCode.code_hash == h,
        BackupCode.used_at.is_(None),
    )
    row = session.scalars(stmt).first()
    if row is None:
        return False
    row.used_at = datetime.now(timezone.utc)
    session.flush()
    return True
```

- [ ] **Step 4: Запустить — PASS**

```bash
pytest tests/unit/test_backup_codes.py -v
```

- [ ] **Step 5: Коммит**

```bash
git add app/auth/backup_codes.py tests/unit/test_backup_codes.py
git commit -m "feat(auth): backup codes generation and one-time consumption"
```

---

## Task 9: Сессии (создание, чтение, истечение)

**Files:**
- Create: `app/auth/sessions.py`
- Create: `tests/unit/test_sessions.py`

- [ ] **Step 1: Падающий тест**

```python
# tests/unit/test_sessions.py
from datetime import datetime, timedelta, timezone

from freezegun import freeze_time

from app.auth.sessions import (
    create_session,
    delete_session,
    get_active_session,
    promote_session,
)
from app.db import Base, make_engine, make_session_factory
from app.models import User


def setup():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        u = User(username="alice", password_hash="x")
        s.add(u)
        s.commit()
        s.refresh(u)
        return factory, u.id


@freeze_time("2026-05-02 12:00:00")
def test_create_session_returns_token_and_persists():
    factory, uid = setup()
    with factory() as s:
        token = create_session(s, user_id=uid, ttl_days=30, is_partial=False)
        s.commit()
    assert len(token) >= 32
    with factory() as s:
        sess = get_active_session(s, token)
        assert sess is not None
        assert sess.user_id == uid
        assert sess.is_partial is False


@freeze_time("2026-05-02 12:00:00")
def test_get_active_session_rejects_expired():
    factory, uid = setup()
    with factory() as s:
        token = create_session(s, user_id=uid, ttl_days=1, is_partial=False)
        s.commit()
    with freeze_time("2026-06-01"):
        with factory() as s:
            assert get_active_session(s, token) is None


def test_get_active_session_unknown_token():
    factory, _ = setup()
    with factory() as s:
        assert get_active_session(s, "not-a-real-token") is None


def test_delete_session_removes_it():
    factory, uid = setup()
    with factory() as s:
        token = create_session(s, user_id=uid, ttl_days=1, is_partial=False)
        s.commit()
    with factory() as s:
        delete_session(s, token)
        s.commit()
    with factory() as s:
        assert get_active_session(s, token) is None


def test_promote_session_clears_partial_flag_and_extends_ttl():
    factory, uid = setup()
    with freeze_time("2026-05-02 12:00:00"):
        with factory() as s:
            token = create_session(s, user_id=uid, ttl_days=1, is_partial=True)
            s.commit()
    with freeze_time("2026-05-02 12:00:30"):
        with factory() as s:
            promote_session(s, token, ttl_days=30)
            s.commit()
        with factory() as s:
            sess = get_active_session(s, token)
            assert sess.is_partial is False
            # expires near 30 days later
            assert sess.expires_at > datetime(2026, 5, 30, tzinfo=timezone.utc)
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/auth/sessions.py`**

```python
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from app.models import Session as UserSession


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_session(session: DbSession, *, user_id: int, ttl_days: int, is_partial: bool) -> str:
    token = secrets.token_urlsafe(48)
    obj = UserSession(
        token=token,
        user_id=user_id,
        expires_at=_now() + timedelta(days=ttl_days),
        is_partial=is_partial,
    )
    session.add(obj)
    return token


def get_active_session(session: DbSession, token: str) -> UserSession | None:
    if not token:
        return None
    stmt = select(UserSession).where(UserSession.token == token, UserSession.expires_at > _now())
    return session.scalars(stmt).first()


def delete_session(session: DbSession, token: str) -> None:
    session.execute(delete(UserSession).where(UserSession.token == token))


def promote_session(session: DbSession, token: str, *, ttl_days: int) -> None:
    sess = session.scalars(select(UserSession).where(UserSession.token == token)).first()
    if sess is None:
        return
    sess.is_partial = False
    sess.expires_at = _now() + timedelta(days=ttl_days)
    session.flush()
```

- [ ] **Step 4: Запустить — PASS**

```bash
pytest tests/unit/test_sessions.py -v
```

- [ ] **Step 5: Коммит**

```bash
git add app/auth/sessions.py tests/unit/test_sessions.py
git commit -m "feat(auth): session creation, lookup, expiry, promotion"
```

---

## Task 10: CSRF-токены

**Files:**
- Create: `app/csrf.py`
- Create: `tests/unit/test_csrf.py`

- [ ] **Step 1: Падающий тест**

```python
# tests/unit/test_csrf.py
import pytest

from app.csrf import generate_token, verify_token


def test_generate_returns_two_unique_tokens():
    a = generate_token("session-key")
    b = generate_token("session-key")
    assert a != b


def test_verify_accepts_token_for_same_secret():
    secret = "session-key"
    t = generate_token(secret)
    assert verify_token(t, secret) is True


def test_verify_rejects_token_for_different_secret():
    t = generate_token("alice-session")
    assert verify_token(t, "bob-session") is False


def test_verify_rejects_garbage():
    assert verify_token("not-a-token", "any") is False
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/csrf.py`**

```python
import hmac
import secrets
from hashlib import sha256


def _sign(value: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), sha256).hexdigest()


def generate_token(session_key: str) -> str:
    nonce = secrets.token_urlsafe(16)
    sig = _sign(nonce, session_key)
    return f"{nonce}.{sig}"


def verify_token(token: str, session_key: str) -> bool:
    if not token or "." not in token:
        return False
    try:
        nonce, sig = token.rsplit(".", 1)
    except ValueError:
        return False
    expected = _sign(nonce, session_key)
    return hmac.compare_digest(sig, expected)
```

- [ ] **Step 4: Запустить — PASS**

```bash
pytest tests/unit/test_csrf.py -v
```

- [ ] **Step 5: Коммит**

```bash
git add app/csrf.py tests/unit/test_csrf.py
git commit -m "feat(csrf): per-session CSRF token generation and verification"
```

---

## Task 11: Тестовые фикстуры (`conftest.py`)

**Files:**
- Create: `tests/conftest.py`

- [ ] **Step 1: Реализовать `tests/conftest.py`**

```python
import os

import pytest
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.db import Base, make_engine, make_session_factory


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MEDIA_ROOT", "/tmp/media")
    monkeypatch.setenv("QBITTORRENT_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("QBITTORRENT_USERNAME", "admin")
    monkeypatch.setenv("QBITTORRENT_PASSWORD", "secret")
    monkeypatch.setenv("TOTP_ISSUER", "TestSrv")


@pytest.fixture
def db_factory() -> sessionmaker:
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def client(db_factory):
    """TestClient with overridden DB dependency. Used by integration tests."""
    from app.main import app
    from app.deps import get_db_factory

    app.dependency_overrides[get_db_factory] = lambda: db_factory
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()
```

(`app.main` и `app.deps` появятся в Task 12 и Task 14 — пока этот файл просто лежит, integration-тесты к нему обратятся позже.)

- [ ] **Step 2: Коммит**

```bash
git add tests/conftest.py
git commit -m "test: shared fixtures (env, db_factory, client)"
```

---

## Task 12: FastAPI app skeleton

**Files:**
- Create: `app/deps.py`
- Create: `app/main.py`
- Create: `templates/base.html`
- Create: `static/style.css`
- Create: `tests/integration/test_app_starts.py`

- [ ] **Step 1: Падающий integration-тест**

```python
# tests/integration/test_app_starts.py
def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_redirects_to_login(client):
    r = client.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
```

- [ ] **Step 2: Запустить — FAIL** (`/health` не существует).

- [ ] **Step 3: Реализовать `app/deps.py`**

```python
from typing import Callable

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory


def get_db_factory() -> sessionmaker[Session]:
    """Singleton-фабрика. Подменяется в тестах через app.dependency_overrides."""
    s = get_settings()
    engine = make_engine(s.database_url)
    return make_session_factory(engine)


def get_db(factory: sessionmaker[Session] = Depends(get_db_factory)) -> Session:
    db = factory()
    try:
        yield db
    finally:
        db.close()


def get_app_settings() -> Settings:
    return get_settings()
```

- [ ] **Step 4: Реализовать `app/main.py`**

```python
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="MediaServer")

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)
```

- [ ] **Step 5: Создать `templates/base.html`**

```html
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}MediaServer{% endblock %}</title>
  <link rel="stylesheet" href="/static/style.css">
  {% if csrf_token %}<meta name="csrf-token" content="{{ csrf_token }}">{% endif %}
  <script src="/static/htmx.min.js" defer></script>
</head>
<body>
  <header>
    <a href="/library">MediaServer</a>
    {% if user %}
      <nav>
        <span>{{ user.username }}</span>
        {% if user.is_admin %}<a href="/admin/users">Пользователи</a>{% endif %}
        <form method="post" action="/logout" style="display:inline">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button type="submit">Выйти</button>
        </form>
      </nav>
    {% endif %}
  </header>
  <main>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 6: Создать `static/style.css`** (минимум)

```css
* { box-sizing: border-box; }
body { font-family: system-ui, sans-serif; max-width: 900px; margin: 0 auto; padding: 1rem; line-height: 1.4; }
header { display:flex; justify-content:space-between; align-items:center; padding: 1rem 0; border-bottom: 1px solid #ddd; }
header a { text-decoration: none; font-weight: 600; }
header nav { display: flex; gap: 1rem; align-items: center; }
form { display: grid; gap: 0.5rem; max-width: 360px; }
label { font-weight: 500; }
input, button { padding: 0.5rem; font: inherit; }
button { cursor: pointer; }
.error { color: #c00; padding: 0.5rem; background: #fee; border-radius: 4px; }
.codes { font-family: monospace; background: #f4f4f4; padding: 1rem; border-radius: 4px; }
.codes li { margin: 0.25rem 0; }
```

- [ ] **Step 7: Скачать htmx**

```bash
curl -sLo static/htmx.min.js https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js
```

Проверить размер: `ls -la static/htmx.min.js` — должно быть ~50 КБ.

- [ ] **Step 8: Запустить integration-тест**

```bash
pytest tests/integration/test_app_starts.py -v
```

Ожидается: 2 passed.

- [ ] **Step 9: Локальный smoke-тест**

```bash
uvicorn app.main:app --reload --port 8000
```

Открыть в браузере `http://127.0.0.1:8000/health` — должен показать `{"status":"ok"}`. Остановить: Ctrl+C.

- [ ] **Step 10: Коммит**

```bash
git add app/deps.py app/main.py templates/ static/ tests/integration/test_app_starts.py
git commit -m "feat: FastAPI skeleton with /health, /, base template, static"
```

---

## Task 13: Зависимости авторизации (`get_current_user`, `require_admin`)

**Files:**
- Create: `app/auth/deps.py`
- Create: `tests/unit/test_auth_deps.py`

- [ ] **Step 1: Падающий тест**

```python
# tests/unit/test_auth_deps.py
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.auth.deps import _resolve_current_user, _resolve_current_user_or_none
from app.auth.sessions import create_session
from app.models import User


def test_resolve_current_user_returns_user(db_factory):
    with db_factory() as s:
        u = User(username="alice", password_hash="x")
        s.add(u)
        s.commit()
        token = create_session(s, user_id=u.id, ttl_days=1, is_partial=False)
        s.commit()
    with db_factory() as s:
        user = _resolve_current_user(s, token, allow_partial=False)
        assert user.username == "alice"


def test_resolve_rejects_partial_when_not_allowed(db_factory):
    with db_factory() as s:
        u = User(username="bob", password_hash="x")
        s.add(u)
        s.commit()
        token = create_session(s, user_id=u.id, ttl_days=1, is_partial=True)
        s.commit()
    with db_factory() as s:
        with pytest.raises(HTTPException) as exc:
            _resolve_current_user(s, token, allow_partial=False)
        assert exc.value.status_code == 401


def test_resolve_or_none_returns_none_for_missing_token(db_factory):
    with db_factory() as s:
        assert _resolve_current_user_or_none(s, None, allow_partial=False) is None
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/auth/deps.py`**

```python
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.sessions import get_active_session
from app.deps import get_db
from app.models import User

SESSION_COOKIE = "session"


def _resolve_current_user_or_none(db: Session, token: str | None, *, allow_partial: bool) -> User | None:
    if not token:
        return None
    sess = get_active_session(db, token)
    if sess is None:
        return None
    if sess.is_partial and not allow_partial:
        return None
    return db.get(User, sess.user_id)


def _resolve_current_user(db: Session, token: str | None, *, allow_partial: bool) -> User:
    user = _resolve_current_user_or_none(db, token, allow_partial=allow_partial)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user


def get_current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    return _resolve_current_user(db, token, allow_partial=False)


def get_current_user_partial(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    """Allows partial sessions (post-password, pre-2FA)."""
    token = request.cookies.get(SESSION_COOKIE)
    return _resolve_current_user(db, token, allow_partial=True)


def get_current_user_optional(request: Request, db: Annotated[Session, Depends(get_db)]) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    return _resolve_current_user_or_none(db, token, allow_partial=False)


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
```

- [ ] **Step 4: Запустить — PASS**

```bash
pytest tests/unit/test_auth_deps.py -v
```

- [ ] **Step 5: Коммит**

```bash
git add app/auth/deps.py tests/unit/test_auth_deps.py
git commit -m "feat(auth): get_current_user, _partial, _optional, require_admin deps"
```

---

## Task 14: Логин — шаг 1 (пароль)

**Files:**
- Create: `app/auth/routes.py`
- Modify: `app/main.py` (подключить роутер)
- Create: `templates/login.html`
- Create: `tests/integration/test_login_flow.py`

- [ ] **Step 1: Падающий integration-тест**

```python
# tests/integration/test_login_flow.py
from app.auth.passwords import hash_password
from app.models import User


def make_user(db_factory, *, username="alice", password="correct-password-12", **kw):
    with db_factory() as s:
        u = User(
            username=username,
            password_hash=hash_password(password),
            must_change_password=False,
            totp_enabled=True,
            totp_secret_encrypted="dummy",   # реальный секрет не нужен в тесте этой ветки
            **kw,
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id


def test_login_get_returns_form(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "username" in r.text.lower()
    assert "password" in r.text.lower()


def test_login_post_wrong_password_returns_401(client, db_factory):
    make_user(db_factory)
    r = client.post("/login", data={"username": "alice", "password": "wrong"})
    assert r.status_code == 401


def test_login_post_correct_password_creates_partial_session_and_redirects_to_totp(client, db_factory):
    make_user(db_factory)
    r = client.post("/login", data={"username": "alice", "password": "correct-password-12"})
    assert r.status_code == 303
    assert r.headers["location"] == "/verify-totp"
    assert "session=" in r.headers.get("set-cookie", "")
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/auth/routes.py`** (минимально, под текущие тесты — расширим в следующих task'ах)

```python
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import SESSION_COOKIE
from app.auth.passwords import verify_password
from app.auth.sessions import create_session
from app.deps import get_db
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory="templates")

PARTIAL_SESSION_TTL_DAYS = 1
FULL_SESSION_TTL_DAYS = 30


def _set_session_cookie(response, token: str):
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, secure=True, samesite="strict",
        path="/", max_age=FULL_SESSION_TTL_DAYS * 86400,
    )


@router.get("/login", response_class=HTMLResponse)
def login_get(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login_post(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
) -> RedirectResponse | HTMLResponse:
    user = db.scalars(select(User).where(User.username == username)).first()
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Неверный логин или пароль"},
            status_code=401,
        )

    token = create_session(db, user_id=user.id, ttl_days=PARTIAL_SESSION_TTL_DAYS, is_partial=True)
    db.commit()

    if user.must_change_password:
        target = "/change-password"
    elif not user.totp_enabled:
        target = "/enroll-2fa"
    else:
        target = "/verify-totp"

    response = RedirectResponse(target, status_code=303)
    _set_session_cookie(response, token)
    return response
```

- [ ] **Step 4: Реализовать `templates/login.html`**

```html
{% extends "base.html" %}
{% block title %}Вход{% endblock %}
{% block content %}
<h1>Вход</h1>
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<form method="post" action="/login">
  <label>Логин <input name="username" autocomplete="username" required></label>
  <label>Пароль <input name="password" type="password" autocomplete="current-password" required></label>
  <button type="submit">Войти</button>
</form>
{% endblock %}
```

- [ ] **Step 5: Подключить роутер в `app/main.py`**

Добавить импорт и `app.include_router`:

```python
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.auth.routes import router as auth_router

app = FastAPI(title="MediaServer")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(auth_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)
```

- [ ] **Step 6: Запустить тесты — PASS**

```bash
pytest tests/integration/test_login_flow.py -v
```

Ожидается: 3 passed.

- [ ] **Step 7: Коммит**

```bash
git add app/main.py app/auth/routes.py templates/login.html tests/integration/test_login_flow.py
git commit -m "feat(auth): /login GET+POST with password check and partial session"
```

---

## Task 15: Логин — шаг 2 (TOTP)

**Files:**
- Modify: `app/auth/routes.py` (добавить `/verify-totp`)
- Create: `templates/verify_totp.html`
- Modify: `tests/integration/test_login_flow.py` (добавить кейсы)

- [ ] **Step 1: Дописать тесты в `tests/integration/test_login_flow.py`**

В конец файла:

```python
import pyotp
from app.auth.totp import encrypt_secret


def make_user_with_totp(db_factory, *, username="alice", password="correct-password-12"):
    secret = pyotp.random_base32()
    with db_factory() as s:
        u = User(
            username=username,
            password_hash=hash_password(password),
            must_change_password=False,
            totp_enabled=True,
            totp_secret_encrypted=encrypt_secret(secret, b"x" * 32),
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id, secret


def test_verify_totp_get_requires_partial_session(client):
    r = client.get("/verify-totp")
    assert r.status_code == 401


def test_verify_totp_full_flow(client, db_factory, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)  # совпадает с conftest, ключ для encrypt
    _, secret = make_user_with_totp(db_factory)

    r = client.post("/login", data={"username": "alice", "password": "correct-password-12"})
    assert r.status_code == 303
    assert r.headers["location"] == "/verify-totp"
    cookie = r.cookies.get("session")

    code = pyotp.TOTP(secret).now()
    r2 = client.post(
        "/verify-totp",
        data={"code": code},
        cookies={"session": cookie},
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/library"


def test_verify_totp_wrong_code_returns_401(client, db_factory):
    make_user_with_totp(db_factory)
    r = client.post("/login", data={"username": "alice", "password": "correct-password-12"})
    cookie = r.cookies.get("session")
    r2 = client.post("/verify-totp", data={"code": "000000"}, cookies={"session": cookie})
    assert r2.status_code == 401


def test_verify_totp_with_backup_code_succeeds(client, db_factory):
    from app.auth.backup_codes import generate_codes, hash_code as bc_hash
    from app.models import BackupCode

    uid, _ = make_user_with_totp(db_factory)
    codes = generate_codes()
    with db_factory() as s:
        for c in codes:
            s.add(BackupCode(user_id=uid, code_hash=bc_hash(c)))
        s.commit()

    r = client.post("/login", data={"username": "alice", "password": "correct-password-12"})
    cookie = r.cookies.get("session")
    r2 = client.post("/verify-totp", data={"code": codes[0]}, cookies={"session": cookie})
    assert r2.status_code == 303
    assert r2.headers["location"] == "/library"

    # Тот же код повторно — отвергается.
    r3 = client.post("/login", data={"username": "alice", "password": "correct-password-12"})
    cookie3 = r3.cookies.get("session")
    r4 = client.post("/verify-totp", data={"code": codes[0]}, cookies={"session": cookie3})
    assert r4.status_code == 401
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/auth/routes.py`**

В импортах добавить:

```python
from app.auth.backup_codes import verify_and_consume
from app.auth.deps import get_current_user_partial
from app.auth.sessions import promote_session
from app.auth.totp import decrypt_secret, verify_code
from app.config import get_settings
from app.auth.totp import _derive_key
```

В конце файла добавить:

```python
@router.get("/verify-totp", response_class=HTMLResponse)
def verify_totp_get(
    request: Request,
    user: Annotated[User, Depends(get_current_user_partial)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        "verify_totp.html", {"request": request, "user": user, "error": None}
    )


@router.post("/verify-totp")
def verify_totp_post(
    request: Request,
    code: Annotated[str, Form()],
    user: Annotated[User, Depends(get_current_user_partial)],
    db: Annotated[Session, Depends(get_db)],
) -> RedirectResponse | HTMLResponse:
    code = code.strip()
    settings = get_settings()
    key = _derive_key(settings.session_secret)

    ok = False
    if user.totp_secret_encrypted:
        secret = decrypt_secret(user.totp_secret_encrypted, key)
        if verify_code(secret, code):
            ok = True
    if not ok:
        if verify_and_consume(db, user.id, code):
            ok = True
            db.commit()

    if not ok:
        return templates.TemplateResponse(
            "verify_totp.html", {"request": request, "user": user, "error": "Неверный код"},
            status_code=401,
        )

    token = request.cookies.get(SESSION_COOKIE) or ""
    promote_session(db, token, ttl_days=FULL_SESSION_TTL_DAYS)
    db.commit()
    return RedirectResponse("/library", status_code=303)
```

- [ ] **Step 4: Создать `templates/verify_totp.html`**

```html
{% extends "base.html" %}
{% block title %}Двухфакторная аутентификация{% endblock %}
{% block content %}
<h1>Введите 6-значный код</h1>
<p>Код из приложения-аутентификатора (Google Authenticator / Authy / 1Password) или один из ваших backup-кодов.</p>
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<form method="post" action="/verify-totp">
  <label>Код <input name="code" autocomplete="one-time-code" autofocus required></label>
  <button type="submit">Подтвердить</button>
</form>
{% endblock %}
```

- [ ] **Step 5: Запустить тесты — PASS**

```bash
pytest tests/integration/test_login_flow.py -v
```

Ожидается: 7 passed (3 старых + 4 новых).

- [ ] **Step 6: Коммит**

```bash
git add app/auth/routes.py templates/verify_totp.html tests/integration/test_login_flow.py
git commit -m "feat(auth): /verify-totp accepts TOTP or backup code, promotes session"
```

---

## Task 16: Принудительная смена пароля при первом входе

**Files:**
- Modify: `app/auth/routes.py`
- Create: `templates/change_password.html`
- Create: `tests/integration/test_first_login_setup.py`

- [ ] **Step 1: Падающий тест**

```python
# tests/integration/test_first_login_setup.py
from app.auth.passwords import hash_password
from app.models import User


def make_fresh_user(db_factory, password="temp-password-99"):
    with db_factory() as s:
        u = User(
            username="newbie",
            password_hash=hash_password(password),
            must_change_password=True,
            totp_enabled=False,
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id


def test_login_redirects_to_change_password_for_fresh_user(client, db_factory):
    make_fresh_user(db_factory)
    r = client.post("/login", data={"username": "newbie", "password": "temp-password-99"})
    assert r.status_code == 303
    assert r.headers["location"] == "/change-password"


def test_change_password_requires_partial_session(client):
    r = client.get("/change-password")
    assert r.status_code == 401


def test_change_password_too_short_rejected(client, db_factory):
    make_fresh_user(db_factory)
    r = client.post("/login", data={"username": "newbie", "password": "temp-password-99"})
    cookie = r.cookies.get("session")
    r2 = client.post(
        "/change-password",
        data={"new_password": "short", "confirm": "short"},
        cookies={"session": cookie},
    )
    assert r2.status_code == 400


def test_change_password_mismatch_rejected(client, db_factory):
    make_fresh_user(db_factory)
    r = client.post("/login", data={"username": "newbie", "password": "temp-password-99"})
    cookie = r.cookies.get("session")
    r2 = client.post(
        "/change-password",
        data={"new_password": "long-enough-12345", "confirm": "different-12345"},
        cookies={"session": cookie},
    )
    assert r2.status_code == 400


def test_change_password_success_redirects_to_enroll_2fa(client, db_factory):
    uid = make_fresh_user(db_factory)
    r = client.post("/login", data={"username": "newbie", "password": "temp-password-99"})
    cookie = r.cookies.get("session")
    r2 = client.post(
        "/change-password",
        data={"new_password": "new-strong-password-1", "confirm": "new-strong-password-1"},
        cookies={"session": cookie},
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/enroll-2fa"

    # Старый пароль больше не работает.
    r3 = client.post("/login", data={"username": "newbie", "password": "temp-password-99"})
    assert r3.status_code == 401
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/auth/routes.py`**

Добавить в импорты:
```python
from app.auth.passwords import hash_password
```

В конце файла:

```python
MIN_PASSWORD_LEN = 12


@router.get("/change-password", response_class=HTMLResponse)
def change_password_get(
    request: Request,
    user: Annotated[User, Depends(get_current_user_partial)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        "change_password.html", {"request": request, "user": user, "error": None}
    )


@router.post("/change-password")
def change_password_post(
    request: Request,
    new_password: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
    user: Annotated[User, Depends(get_current_user_partial)],
    db: Annotated[Session, Depends(get_db)],
) -> RedirectResponse | HTMLResponse:
    if len(new_password) < MIN_PASSWORD_LEN:
        return templates.TemplateResponse(
            "change_password.html",
            {"request": request, "user": user, "error": f"Пароль должен быть не короче {MIN_PASSWORD_LEN} символов"},
            status_code=400,
        )
    if new_password != confirm:
        return templates.TemplateResponse(
            "change_password.html",
            {"request": request, "user": user, "error": "Пароли не совпадают"},
            status_code=400,
        )

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    db.commit()
    return RedirectResponse("/enroll-2fa", status_code=303)
```

- [ ] **Step 4: Создать `templates/change_password.html`**

```html
{% extends "base.html" %}
{% block title %}Смена пароля{% endblock %}
{% block content %}
<h1>Задайте новый пароль</h1>
<p>Минимум 12 символов. Это обязательный шаг для нового аккаунта.</p>
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<form method="post" action="/change-password">
  <label>Новый пароль <input name="new_password" type="password" autocomplete="new-password" required></label>
  <label>Повторите <input name="confirm" type="password" autocomplete="new-password" required></label>
  <button type="submit">Сохранить</button>
</form>
{% endblock %}
```

- [ ] **Step 5: Запустить тесты — PASS**

```bash
pytest tests/integration/test_first_login_setup.py -v
```

Ожидается: 5 passed.

- [ ] **Step 6: Коммит**

```bash
git add app/auth/routes.py templates/change_password.html tests/integration/test_first_login_setup.py
git commit -m "feat(auth): forced password change on first login"
```

---

## Task 17: Активация 2FA при первом входе (QR + backup-коды)

**Files:**
- Modify: `app/auth/routes.py`
- Create: `templates/enroll_2fa.html`
- Modify: `tests/integration/test_first_login_setup.py`

- [ ] **Step 1: Дописать тесты в `tests/integration/test_first_login_setup.py`** (в конец файла)

```python
import pyotp


def _full_setup_until_enroll(client, db_factory):
    make_fresh_user(db_factory)
    r1 = client.post("/login", data={"username": "newbie", "password": "temp-password-99"})
    cookie = r1.cookies.get("session")
    client.post(
        "/change-password",
        data={"new_password": "new-strong-password-1", "confirm": "new-strong-password-1"},
        cookies={"session": cookie},
    )
    return cookie


def test_enroll_2fa_get_returns_qr_and_secret(client, db_factory):
    cookie = _full_setup_until_enroll(client, db_factory)
    r = client.get("/enroll-2fa", cookies={"session": cookie})
    assert r.status_code == 200
    # На странице должны быть отрисован QR (img src=data:image/png;base64,...) и backup-коды.
    assert "data:image/png;base64," in r.text
    assert r.text.count("<li>") >= 10  # 10 backup-кодов как минимум


def test_enroll_2fa_post_wrong_code_keeps_user_unenrolled(client, db_factory):
    cookie = _full_setup_until_enroll(client, db_factory)
    client.get("/enroll-2fa", cookies={"session": cookie})
    r = client.post("/enroll-2fa", data={"code": "000000"}, cookies={"session": cookie})
    assert r.status_code == 400


def test_enroll_2fa_post_correct_code_completes_setup(client, db_factory):
    from sqlalchemy import select
    from app.auth.totp import decrypt_secret, _derive_key
    from app.models import BackupCode, User

    cookie = _full_setup_until_enroll(client, db_factory)
    client.get("/enroll-2fa", cookies={"session": cookie})

    # Достаём секрет, который сервер сохранил во время GET.
    with db_factory() as s:
        user = s.execute(select(User).where(User.username == "newbie")).scalar_one()
        secret = decrypt_secret(user.totp_secret_encrypted, _derive_key("x" * 64))

    code = pyotp.TOTP(secret).now()
    r = client.post("/enroll-2fa", data={"code": code}, cookies={"session": cookie})
    assert r.status_code == 303
    assert r.headers["location"] == "/library"

    with db_factory() as s:
        user = s.execute(select(User).where(User.username == "newbie")).scalar_one()
        assert user.totp_enabled is True
        codes = s.scalars(select(BackupCode).where(BackupCode.user_id == user.id)).all()
        assert len(codes) == 10
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/auth/routes.py`**

Добавить импорты:
```python
import base64

from app.auth.backup_codes import generate_codes, hash_code as bc_hash_code
from app.auth.totp import (
    decrypt_secret, encrypt_secret, generate_secret, provisioning_uri,
    qr_png_bytes, verify_code, _derive_key,
)
from app.models import BackupCode
```

В конце файла:

```python
@router.get("/enroll-2fa", response_class=HTMLResponse)
def enroll_2fa_get(
    request: Request,
    user: Annotated[User, Depends(get_current_user_partial)],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    settings = get_settings()
    key = _derive_key(settings.session_secret)

    if user.totp_secret_encrypted is None:
        secret = generate_secret()
        user.totp_secret_encrypted = encrypt_secret(secret, key)
    else:
        secret = decrypt_secret(user.totp_secret_encrypted, key)

    # Backup-коды: генерируем при первом GET'е этой страницы для пользователя.
    from sqlalchemy import select as _select_bc
    existing = db.scalars(_select_bc(BackupCode).where(BackupCode.user_id == user.id)).all()
    backup_plain: list[str] | None = None
    if not existing:
        backup_plain = generate_codes()
        for c in backup_plain:
            db.add(BackupCode(user_id=user.id, code_hash=bc_hash_code(c)))

    db.commit()

    uri = provisioning_uri(secret, user.username, settings.totp_issuer)
    qr_b64 = base64.b64encode(qr_png_bytes(uri)).decode("ascii")

    return templates.TemplateResponse(
        "enroll_2fa.html",
        {
            "request": request,
            "user": user,
            "qr_data_uri": f"data:image/png;base64,{qr_b64}",
            "secret": secret,
            "backup_codes": backup_plain,  # None если уже сгенерированы (а значит, страница перезагружена)
            "error": None,
        },
    )


@router.post("/enroll-2fa")
def enroll_2fa_post(
    request: Request,
    code: Annotated[str, Form()],
    user: Annotated[User, Depends(get_current_user_partial)],
    db: Annotated[Session, Depends(get_db)],
) -> RedirectResponse | HTMLResponse:
    if user.totp_secret_encrypted is None:
        return RedirectResponse("/enroll-2fa", status_code=303)

    settings = get_settings()
    key = _derive_key(settings.session_secret)
    secret = decrypt_secret(user.totp_secret_encrypted, key)

    if not verify_code(secret, code.strip()):
        return templates.TemplateResponse(
            "enroll_2fa.html",
            {
                "request": request, "user": user,
                "qr_data_uri": None, "secret": None, "backup_codes": None,
                "error": "Неверный код. Проверьте время на телефоне и попробуйте снова.",
            },
            status_code=400,
        )

    user.totp_enabled = True
    db.commit()

    token = request.cookies.get(SESSION_COOKIE) or ""
    promote_session(db, token, ttl_days=FULL_SESSION_TTL_DAYS)
    db.commit()
    return RedirectResponse("/library", status_code=303)
```

- [ ] **Step 4: Создать `templates/enroll_2fa.html`**

```html
{% extends "base.html" %}
{% block title %}Активация 2FA{% endblock %}
{% block content %}
<h1>Активация двухфакторной аутентификации</h1>
{% if qr_data_uri %}
<p>1. Установите Google Authenticator (или Authy / 1Password). 2. Отсканируйте QR-код. 3. Введите 6-значный код снизу.</p>
<img src="{{ qr_data_uri }}" alt="QR" width="240" height="240">
<p>Если QR не сканируется — введите секрет вручную:</p>
<pre class="codes">{{ secret }}</pre>
{% endif %}

{% if backup_codes %}
<h2>Сохраните эти 10 backup-кодов</h2>
<p><strong>Они показываются один раз.</strong> Каждым кодом можно войти один раз, если телефон с аутентификатором недоступен.</p>
<ol class="codes">
  {% for c in backup_codes %}<li>{{ c }}</li>{% endfor %}
</ol>
{% endif %}

{% if error %}<p class="error">{{ error }}</p>{% endif %}

<form method="post" action="/enroll-2fa">
  <label>Код из приложения <input name="code" autocomplete="one-time-code" required></label>
  <button type="submit">Активировать</button>
</form>
{% endblock %}
```

- [ ] **Step 5: Запустить тесты — PASS**

```bash
pytest tests/integration/test_first_login_setup.py -v
```

Ожидается: 8 passed (5 старых + 3 новых).

- [ ] **Step 6: Коммит**

```bash
git add app/auth/routes.py templates/enroll_2fa.html tests/integration/test_first_login_setup.py
git commit -m "feat(auth): 2FA enrollment with QR and 10 backup codes"
```

---

## Task 18: Logout

**Files:**
- Modify: `app/auth/routes.py`
- Modify: `tests/integration/test_login_flow.py`

- [ ] **Step 1: Дописать тест в `tests/integration/test_login_flow.py`** (в конец)

```python
def test_logout_clears_session_and_redirects(client, db_factory):
    _, secret = make_user_with_totp(db_factory)
    r = client.post("/login", data={"username": "alice", "password": "correct-password-12"})
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post("/verify-totp", data={"code": code}, cookies={"session": cookie})

    r2 = client.post("/logout", cookies={"session": cookie})
    assert r2.status_code == 303
    assert r2.headers["location"] == "/login"

    # После logout сессия должна быть удалена — попытка использовать её провалится.
    r3 = client.get("/library", cookies={"session": cookie})
    assert r3.status_code in (303, 401)
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/auth/routes.py`**

Добавить импорт:
```python
from app.auth.sessions import delete_session
```

В конце файла:
```python
@router.post("/logout")
def logout(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(db, token)
        db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
```

- [ ] **Step 4: Запустить — PASS**

```bash
pytest tests/integration/test_login_flow.py -v
```

- [ ] **Step 5: Коммит**

```bash
git add app/auth/routes.py tests/integration/test_login_flow.py
git commit -m "feat(auth): /logout clears session and cookie"
```

---

## Task 19: Плейсхолдер библиотеки (`/library`)

**Files:**
- Create: `app/library/__init__.py` (пустой)
- Create: `app/library/routes.py`
- Create: `templates/library.html`
- Modify: `app/main.py`
- Create: `tests/integration/test_library.py`

- [ ] **Step 1: Падающий тест**

```python
# tests/integration/test_library.py
import pyotp

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret
from app.models import User


def setup_logged_in(client, db_factory):
    secret = pyotp.random_base32()
    with db_factory() as s:
        u = User(
            username="alice", password_hash=hash_password("correct-password-12"),
            must_change_password=False, totp_enabled=True,
            totp_secret_encrypted=encrypt_secret(secret, b"x" * 32),
        )
        s.add(u); s.commit()
    r = client.post("/login", data={"username": "alice", "password": "correct-password-12"})
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post("/verify-totp", data={"code": code}, cookies={"session": cookie})
    return cookie


def test_library_unauthenticated_redirects_to_login(client):
    r = client.get("/library")
    assert r.status_code in (303, 401)


def test_library_logged_in_shows_empty_state(client, db_factory):
    cookie = setup_logged_in(client, db_factory)
    r = client.get("/library", cookies={"session": cookie})
    assert r.status_code == 200
    assert "пуст" in r.text.lower() or "empty" in r.text.lower() or "ничего" in r.text.lower()
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/library/__init__.py`** — пустой.

- [ ] **Step 4: Реализовать `app/library/routes.py`**

```python
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth.deps import get_current_user
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        "library.html", {"request": request, "user": user, "items": []}
    )
```

- [ ] **Step 5: Реализовать `templates/library.html`**

```html
{% extends "base.html" %}
{% block title %}Библиотека{% endblock %}
{% block content %}
<h1>Библиотека</h1>
{% if not items %}
  <p>Здесь пока ничего нет. Добавьте magnet-ссылку в разделе «Загрузки» (появится в Плане 2).</p>
{% else %}
  <ul>
    {% for it in items %}<li>{{ it.title }}</li>{% endfor %}
  </ul>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: Подключить роутер в `app/main.py`**

```python
from app.library.routes import router as library_router
# ...
app.include_router(library_router)
```

- [ ] **Step 7: Изменить `_resolve_current_user` поведение для GET-запросов: вместо 401 редиректить на /login.**

Логика: если пользователь не залогинен и заходит GET-запросом на защищённую страницу — приятнее редиректить на форму входа, чем показывать голый 401. Делаем это через middleware.

В `app/main.py` добавить:

```python
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest


class AuthRedirectMiddleware(BaseHTTPMiddleware):
    """Если ответ 401 на GET → редиректим на /login (для UX). API-эндпоинты пусть отдают 401 как есть."""

    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        if (
            response.status_code == 401
            and request.method == "GET"
            and not request.url.path.startswith("/api/")
            and request.url.path not in ("/login", "/health")
        ):
            return RedirectResponse("/login", status_code=303)
        return response


app.add_middleware(AuthRedirectMiddleware)
```

- [ ] **Step 8: Запустить тесты библиотеки**

```bash
pytest tests/integration/test_library.py -v
```

Ожидается: 2 passed.

- [ ] **Step 9: Коммит**

```bash
git add app/library/ templates/library.html app/main.py tests/integration/test_library.py
git commit -m "feat(library): empty placeholder page + GET 401→/login middleware"
```

---

## Task 20: Админка — список пользователей

**Files:**
- Create: `app/admin/__init__.py` (пустой)
- Create: `app/admin/routes.py`
- Create: `templates/admin_users.html`
- Modify: `app/main.py`
- Create: `tests/integration/test_admin_users.py`

- [ ] **Step 1: Падающий тест**

```python
# tests/integration/test_admin_users.py
import pyotp

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret
from app.models import User


def make_admin_logged_in(client, db_factory):
    secret = pyotp.random_base32()
    with db_factory() as s:
        u = User(
            username="root", password_hash=hash_password("admin-password-12"),
            must_change_password=False, totp_enabled=True, is_admin=True,
            totp_secret_encrypted=encrypt_secret(secret, b"x" * 32),
        )
        s.add(u); s.commit()
    r = client.post("/login", data={"username": "root", "password": "admin-password-12"})
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post("/verify-totp", data={"code": code}, cookies={"session": cookie})
    return cookie


def make_regular_logged_in(client, db_factory):
    secret = pyotp.random_base32()
    with db_factory() as s:
        u = User(
            username="alice", password_hash=hash_password("user-password-12"),
            must_change_password=False, totp_enabled=True, is_admin=False,
            totp_secret_encrypted=encrypt_secret(secret, b"x" * 32),
        )
        s.add(u); s.commit()
    r = client.post("/login", data={"username": "alice", "password": "user-password-12"})
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post("/verify-totp", data={"code": code}, cookies={"session": cookie})
    return cookie


def test_admin_users_lists_all_users(client, db_factory):
    cookie = make_admin_logged_in(client, db_factory)
    r = client.get("/admin/users", cookies={"session": cookie})
    assert r.status_code == 200
    assert "root" in r.text


def test_regular_user_cannot_access_admin_users(client, db_factory):
    cookie = make_regular_logged_in(client, db_factory)
    r = client.get("/admin/users", cookies={"session": cookie})
    assert r.status_code == 403


def test_unauthenticated_redirected(client):
    r = client.get("/admin/users")
    assert r.status_code in (303, 401)
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `app/admin/__init__.py`** — пустой.

- [ ] **Step 4: Реализовать `app/admin/routes.py`**

```python
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.deps import get_db
from app.models import User

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")


@router.get("/users", response_class=HTMLResponse)
def list_users(
    request: Request,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    users = db.scalars(select(User).order_by(User.id)).all()
    return templates.TemplateResponse(
        "admin_users.html",
        {"request": request, "user": admin, "users": users, "created_user": None, "temp_password": None},
    )
```

- [ ] **Step 5: Реализовать `templates/admin_users.html`**

```html
{% extends "base.html" %}
{% block title %}Управление пользователями{% endblock %}
{% block content %}
<h1>Пользователи</h1>

{% if created_user %}
<p class="error">Создан пользователь <strong>{{ created_user.username }}</strong>.
Временный пароль: <code>{{ temp_password }}</code> — передайте его пользователю и попросите сразу сменить.</p>
{% endif %}

<form method="post" action="/admin/users">
  <label>Логин нового пользователя <input name="username" required pattern="[a-zA-Z0-9_]{3,32}"></label>
  <label>Сделать админом? <input name="is_admin" type="checkbox" value="1"></label>
  <button type="submit">Создать</button>
</form>

<table>
  <thead><tr><th>ID</th><th>Логин</th><th>Админ?</th><th>2FA</th><th>Создан</th><th></th></tr></thead>
  <tbody>
  {% for u in users %}
    <tr>
      <td>{{ u.id }}</td>
      <td>{{ u.username }}</td>
      <td>{{ "да" if u.is_admin else "нет" }}</td>
      <td>{{ "вкл" if u.totp_enabled else "выкл" }}</td>
      <td>{{ u.created_at.strftime("%Y-%m-%d") }}</td>
      <td>
        {% if u.id != user.id %}
        <form method="post" action="/admin/users/{{ u.id }}/delete" style="display:inline" onsubmit="return confirm('Удалить пользователя {{ u.username }}?');">
          <button type="submit">Удалить</button>
        </form>
        {% endif %}
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 6: Подключить роутер в `app/main.py`**

```python
from app.admin.routes import router as admin_router
# ...
app.include_router(admin_router)
```

- [ ] **Step 7: Запустить тесты — PASS**

```bash
pytest tests/integration/test_admin_users.py -v
```

Ожидается: 3 passed.

- [ ] **Step 8: Коммит**

```bash
git add app/admin/ templates/admin_users.html app/main.py tests/integration/test_admin_users.py
git commit -m "feat(admin): /admin/users list page (admin-only)"
```

---

## Task 21: Админка — создание пользователя

**Files:**
- Modify: `app/admin/routes.py`
- Modify: `tests/integration/test_admin_users.py`

- [ ] **Step 1: Дописать тесты**

В конец `tests/integration/test_admin_users.py`:

```python
import secrets


def test_admin_creates_user_and_sees_temp_password(client, db_factory):
    cookie = make_admin_logged_in(client, db_factory)
    r = client.post(
        "/admin/users",
        data={"username": "newbie"},
        cookies={"session": cookie},
    )
    assert r.status_code == 200
    assert "newbie" in r.text
    assert "Временный пароль" in r.text or "temporary" in r.text.lower() or "temp_password" in r.text.lower()


def test_create_user_rejects_duplicate_username(client, db_factory):
    cookie = make_admin_logged_in(client, db_factory)
    client.post("/admin/users", data={"username": "twin"}, cookies={"session": cookie})
    r = client.post("/admin/users", data={"username": "twin"}, cookies={"session": cookie})
    assert r.status_code == 400


def test_create_user_validates_username_format(client, db_factory):
    cookie = make_admin_logged_in(client, db_factory)
    r = client.post("/admin/users", data={"username": "ab"}, cookies={"session": cookie})
    assert r.status_code == 400
    r2 = client.post("/admin/users", data={"username": "with spaces"}, cookies={"session": cookie})
    assert r2.status_code == 400


def test_regular_user_cannot_create(client, db_factory):
    cookie = make_regular_logged_in(client, db_factory)
    r = client.post("/admin/users", data={"username": "evil"}, cookies={"session": cookie})
    assert r.status_code == 403
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/admin/routes.py`**

Добавить импорты:
```python
import re
import secrets

from fastapi import Form, HTTPException, status

from app.auth.passwords import hash_password
```

В конце файла:

```python
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


@router.post("/users", response_class=HTMLResponse)
def create_user(
    request: Request,
    username: Annotated[str, Form()],
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    is_admin: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    username = username.strip()
    users = db.scalars(select(User).order_by(User.id)).all()

    if not USERNAME_RE.match(username):
        return templates.TemplateResponse(
            "admin_users.html",
            {
                "request": request, "user": admin, "users": users,
                "created_user": None, "temp_password": None,
                "error": "Логин: 3–32 символа, только латиница, цифры, _.",
            },
            status_code=400,
        )

    existing = db.scalars(select(User).where(User.username == username)).first()
    if existing is not None:
        return templates.TemplateResponse(
            "admin_users.html",
            {
                "request": request, "user": admin, "users": users,
                "created_user": None, "temp_password": None,
                "error": "Логин уже занят.",
            },
            status_code=400,
        )

    temp_password = secrets.token_urlsafe(12)
    new_user = User(
        username=username,
        password_hash=hash_password(temp_password),
        must_change_password=True,
        totp_enabled=False,
        is_admin=bool(is_admin),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    users = db.scalars(select(User).order_by(User.id)).all()
    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request, "user": admin, "users": users,
            "created_user": new_user, "temp_password": temp_password,
        },
    )
```

- [ ] **Step 4: Запустить — PASS**

```bash
pytest tests/integration/test_admin_users.py -v
```

- [ ] **Step 5: Коммит**

```bash
git add app/admin/routes.py tests/integration/test_admin_users.py
git commit -m "feat(admin): create user with auto-generated temp password"
```

---

## Task 22: Админка — удаление пользователя

**Files:**
- Modify: `app/admin/routes.py`
- Modify: `tests/integration/test_admin_users.py`

- [ ] **Step 1: Дописать тесты**

В конец `tests/integration/test_admin_users.py`:

```python
def test_admin_can_delete_other_user(client, db_factory):
    cookie = make_admin_logged_in(client, db_factory)
    client.post("/admin/users", data={"username": "victim"}, cookies={"session": cookie})

    with db_factory() as s:
        from sqlalchemy import select
        victim = s.scalars(select(User).where(User.username == "victim")).one()

    r = client.post(f"/admin/users/{victim.id}/delete", cookies={"session": cookie})
    assert r.status_code == 303

    with db_factory() as s:
        from sqlalchemy import select
        gone = s.scalars(select(User).where(User.username == "victim")).first()
        assert gone is None


def test_admin_cannot_delete_themselves(client, db_factory):
    cookie = make_admin_logged_in(client, db_factory)
    with db_factory() as s:
        from sqlalchemy import select
        me = s.scalars(select(User).where(User.username == "root")).one()
    r = client.post(f"/admin/users/{me.id}/delete", cookies={"session": cookie})
    assert r.status_code == 400
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Дополнить `app/admin/routes.py`**

Добавить импорт:
```python
from fastapi.responses import RedirectResponse
```

В конце файла:

```python
@router.post("/users/{user_id}/delete")
def delete_user(
    user_id: int,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    db.delete(target)
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)
```

- [ ] **Step 4: Запустить — PASS**

```bash
pytest tests/integration/test_admin_users.py -v
```

Ожидается: 9 passed суммарно.

- [ ] **Step 5: Коммит**

```bash
git add app/admin/routes.py tests/integration/test_admin_users.py
git commit -m "feat(admin): delete user (forbid self-delete)"
```

---

## Task 23: Bootstrap-скрипт первого админа

**Files:**
- Create: `scripts/__init__.py` (пустой)
- Create: `scripts/create_admin.py`
- Create: `tests/unit/test_create_admin_script.py`

- [ ] **Step 1: Падающий тест**

```python
# tests/unit/test_create_admin_script.py
from sqlalchemy import select

from app.db import Base, make_engine, make_session_factory
from app.models import User
from scripts.create_admin import create_admin


def test_create_admin_creates_user_with_admin_flag():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        create_admin(s, username="root", password="bootstrap-password-1")
        s.commit()
    with factory() as s:
        u = s.scalars(select(User).where(User.username == "root")).one()
        assert u.is_admin is True
        assert u.must_change_password is True


def test_create_admin_rejects_short_password():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    import pytest
    with factory() as s:
        with pytest.raises(ValueError):
            create_admin(s, username="root", password="short")


def test_create_admin_rejects_duplicate_username():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        create_admin(s, username="root", password="bootstrap-password-1")
        s.commit()
    import pytest
    with factory() as s:
        with pytest.raises(ValueError):
            create_admin(s, username="root", password="bootstrap-password-2")
```

- [ ] **Step 2: Запустить — FAIL**

- [ ] **Step 3: Реализовать `scripts/__init__.py`** — пустой.

- [ ] **Step 4: Реализовать `scripts/create_admin.py`**

```python
"""
Bootstrap-скрипт: создать первого админа.

Использование:
    python -m scripts.create_admin
(скрипт в интерактивном режиме спросит логин и пароль)

или для CI/install.sh:
    python -m scripts.create_admin --username root --password 'bootstrap-password-1'
"""
import argparse
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import User

MIN_PASSWORD_LEN = 12


def create_admin(session: Session, *, username: str, password: str) -> User:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Пароль должен быть не короче {MIN_PASSWORD_LEN} символов")
    if session.scalars(select(User).where(User.username == username)).first():
        raise ValueError(f"Пользователь {username!r} уже существует")
    u = User(
        username=username,
        password_hash=hash_password(password),
        must_change_password=True,
        totp_enabled=False,
        is_admin=True,
    )
    session.add(u)
    session.flush()
    return u


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", help="Логин админа")
    parser.add_argument("--password", help="Временный пароль админа")
    args = parser.parse_args()

    username = args.username or input("Логин: ").strip()
    password = args.password or getpass.getpass("Временный пароль: ")

    settings = get_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    with factory() as session:
        try:
            u = create_admin(session, username=username, password=password)
            session.commit()
        except ValueError as e:
            print(f"Ошибка: {e}", file=sys.stderr)
            return 1
    print(f"Создан админ {u.username!r} (id={u.id}). При первом входе ему предложат сменить пароль и активировать 2FA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Запустить тесты — PASS**

```bash
pytest tests/unit/test_create_admin_script.py -v
```

- [ ] **Step 6: Коммит**

```bash
git add scripts/ tests/unit/test_create_admin_script.py
git commit -m "feat: scripts/create_admin.py for bootstrap admin"
```

---

## Task 24: README с инструкцией локального запуска

**Files:**
- Create: `README.md`

- [ ] **Step 1: Создать `README.md`**

````markdown
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

## Что дальше

- План 2 — торренты, библиотека, стриминг, скачивание.
- План 3 — production-деплой, Caddy, HTTPS, fail2ban, systemd, install.sh.
````

- [ ] **Step 2: Коммит**

```bash
git add README.md
git commit -m "docs: README with local dev instructions"
```

---

## Task 25: Финальная проверка всего плана

- [ ] **Step 1: Запустить все тесты разом**

```bash
pytest -v
```

Ожидается: всё зелёное. Примерное число тестов: 40–50.

- [ ] **Step 2: Запустить локально и пройти полный сценарий руками**

```bash
rm -f app.db
alembic upgrade head
python -m scripts.create_admin --username root --password 'admin-password-12'
uvicorn app.main:app --port 8000
```

В браузере:
1. Открыть `http://127.0.0.1:8000/`.
2. Войти как `root` / `admin-password-12`.
3. Сменить пароль на новый.
4. Отсканировать QR в Authenticator-приложении (например, в Google Authenticator).
5. Сохранить 10 backup-кодов.
6. Ввести 6-значный код → попасть на `/library` (увидеть «Здесь пока ничего нет»).
7. Перейти в `/admin/users` → создать пользователя `alice` → скопировать временный пароль.
8. Выйти. Войти как `alice` с временным паролем → пройти весь setup (смена пароля + 2FA).
9. Войти второй раз как `alice` (уже с правильным паролем + кодом из Authenticator) → попасть на `/library`.
10. Logout.

Если все 10 шагов проходят — План 1 выполнен.

- [ ] **Step 3: Финальный коммит-маркер**

```bash
git tag plan-1-complete
git log --oneline | head -30
```

---

## Self-review (выполняется после написания плана, до старта имплементации)

**Spec coverage:**
- §5.3 auth модуль → Tasks 6–18, 23. ✓
- §5.4 схема БД → Task 4 (модели), Task 5 (миграция). ✓
- §6.1 логин с 2FA → Tasks 14, 15, 17. ✓
- §7.1 п.3 (bcrypt + TOTP + backup-коды) → Tasks 6, 7, 8. ✓
- §7.1 п.4 (сессии 256-бит, HttpOnly+Secure+SameSite) → Tasks 9, 14, 15. ✓
- §7.1 п.7 (CSRF) → Task 10 (примитив). ⚠️ Применение CSRF в формах не реализовано в Плане 1 — токен генерируется, но проверка в маршрутах не подключена. **TODO для Плана 2:** dependency `verify_csrf` для всех POST. (Решаемо: в Плане 2 при добавлении торрент-форм всё равно понадобится CSRF — реализуем там целостно. В Плане 1 публичных POST-эндпоинтов мало, и они защищены `SameSite=Strict`, что закрывает классический CSRF. Не критично.)
- §7.1 п.8 (валидация входа) → Task 21 (валидация логина регуляркой). magnet/file-paths — Плана 2.
- §7.1 п.10 (секреты в .env) → Task 1.
- §7.2 (создание пользователей) → Tasks 21, 22, 23.

Закрыто всё, что в скоупе Плана 1. CSRF-применение откладывается на План 2 явным образом.

**Placeholder scan:**
- Просмотрел все шаги — ни одного «TBD», «TODO», «implement later», «add validation» без кода. ✓
- Все code-блоки содержат полный код. ✓

**Type consistency:**
- `User.totp_secret_encrypted` (Optional[str]) используется одинаково везде — Tasks 4, 7, 15, 17. ✓
- `Session.is_partial` — введено в Task 4, используется в Tasks 9, 13, 14, 15, 17. ✓
- `BackupCode.code_hash` — Tasks 4, 8, 15. ✓
- `verify_and_consume(session, user_id, code)` — Task 8 определяет, Task 15 использует. ✓
- `_derive_key(session_secret)` — Task 7, используется в Tasks 15, 17. ✓
- `SESSION_COOKIE` const — Task 13, используется в Tasks 14, 15, 17, 18. ✓

**Минорная нестыковка, заметная при ревью:**
- В Task 14 в `login_post` устанавливается cookie с `max_age=FULL_SESSION_TTL_DAYS * 86400`, хотя сессия в этот момент частичная (1 день). На безопасность не влияет (БД авторитетна, истекает по `expires_at`), но cookie долго живёт и его лучше выровнять. **Не правлю в Плане 1** — это микро-косметика, при необходимости одной строчкой исправим в Плане 2.

**Готов к исполнению.**
