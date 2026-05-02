# MediaServer UI redesign + 2FA removal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove dublicate 2FA from MediaServer (Authentik already handles security upstream) and replace the bare-HTML UI with a dark cinematic design (Plex/Apple TV+ style, blue accent, top-bar nav, hand-rolled CSS).

**Architecture:** Two phases that ship in one PR. Phase 1 — atomic backend cleanup: drop TOTP/backup-codes columns, simplify login flow to two branches (must-change-password vs straight-to-library), delete dead modules and templates. Phase 2 — new `static/style.css` with CSS-variable design system, then restyle each of 9 templates one-by-one against existing integration smoke tests.

**Tech Stack:** FastAPI · Jinja2 · SQLAlchemy 2.x · Alembic · pytest · HTMX · HLS.js · plain CSS (no Tailwind, no build step). System font stack.

**Spec:** `docs/superpowers/specs/2026-05-03-mediaserver-ui-redesign-design.md`

---

## File Structure (decomposition)

**Backend (Phase 1, Task 1):**
- Create: `migrations/versions/0002_drop_2fa.py`
- Modify: `app/models.py` — drop `User.totp_*` fields, drop `BackupCode` class
- Modify: `app/auth/routes.py` — drop `/enroll-2fa` & `/verify-totp` routes, simplify `login_post`
- Modify: `app/admin/routes.py:81-87` — drop `totp_enabled=False` kwarg
- Modify: `scripts/create_admin.py:35` — drop `totp_enabled=False` line
- Modify: `tests/conftest.py` — drop `TOTP_ISSUER` env var line
- Rewrite: `tests/integration/test_login_flow.py` (new simplified content in Step 1.1)
- Rewrite: `tests/integration/test_first_login_setup.py` (new content in Step 1.2)
- Patch: `tests/integration/test_admin_users.py`, `test_download.py`, `test_health.py`, `test_library.py`, `test_library_real.py`, `test_media_delete.py`, `test_streaming.py`, `test_torrents_api.py` — drop TOTP imports + fixture lines (Step 1.7)
- Patch: `tests/unit/test_constant_time_login.py`, `test_models.py`, `test_foreign_keys_pragma.py` — drop or replace TOTP/BackupCode references (Step 1.7)
- Patch: `templates/admin_users.html` — drop the `2FA` column (also wholesale rewritten in Task 10)
- DELETE: `app/auth/totp.py`, `app/auth/backup_codes.py`, `templates/enroll_2fa.html`, `templates/verify_totp.html`, `tests/unit/test_totp.py`, `tests/unit/test_backup_codes.py`
- Modify: `README.md` — drop 2FA setup line

**Frontend (Phase 2, Tasks 2–11):**
- `static/style.css` — full rewrite (~400 lines)
- `templates/base.html` — new shell (topnav, sticky header, container)
- `templates/login.html` — centered card, no header
- `templates/change_password.html` — centered card, no header
- `templates/library.html` — hero + recently-added grid (+ optional continue-watching shelf if `WatchProgress` rows exist for the user)
- `templates/media.html` — hero + HLS player + actions
- `templates/add_torrent.html` — card with magnet/file tabs
- `templates/downloads.html` — table with progress bars
- `templates/admin_users.html` — table + create-user modal
- `templates/admin_health.html` — metric cards

**Wrap-up (Task 12):**
- Run full pytest, manual browser walk-through, commit any leftover fixes.

---

## Task 1: Remove 2FA backend

**Files:**
- Create: `migrations/versions/0002_drop_2fa.py`
- Modify: `app/models.py`, `app/auth/routes.py`, `tests/integration/test_login_flow.py`, `tests/integration/test_first_login_setup.py`, `README.md`
- Delete: `app/auth/totp.py`, `app/auth/backup_codes.py`, `templates/enroll_2fa.html`, `templates/verify_totp.html`, `tests/unit/test_totp.py`, `tests/unit/test_backup_codes.py`

This task is intentionally one atomic commit. Splitting introduces broken intermediate states (model field exists in DB but not in code, or vice versa).

- [ ] **Step 1.1: Write the new login-flow integration tests** (red baseline)

Replace `tests/integration/test_login_flow.py` entirely with:

```python
from app.auth.passwords import hash_password
from app.models import User


def make_user(db_factory, *, username="alice", password="correct-password-12", **kw):
    """Create a fully-onboarded user (no must_change_password)."""
    with db_factory() as s:
        u = User(
            username=username,
            password_hash=hash_password(password),
            must_change_password=False,
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


def test_login_post_wrong_password_returns_401(client, db_factory, csrf_for):
    make_user(db_factory)
    r = client.post(
        "/login",
        data={"username": "alice", "password": "wrong", "csrf_token": csrf_for(None)},
    )
    assert r.status_code == 401


def test_login_post_correct_password_creates_full_session_and_redirects_to_library(client, db_factory, csrf_for):
    make_user(db_factory)
    r = client.post(
        "/login",
        data={"username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/library"
    assert "session=" in r.headers.get("set-cookie", "")


def test_login_unknown_user_returns_401(client, csrf_for):
    r = client.post(
        "/login",
        data={"username": "nobody", "password": "any-password-12", "csrf_token": csrf_for(None)},
    )
    assert r.status_code == 401


def test_logout_clears_session_and_redirects(client, db_factory, csrf_for):
    make_user(db_factory)
    r = client.post(
        "/login",
        data={"username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)},
    )
    cookie = r.cookies.get("session")

    r2 = client.post(
        "/logout",
        data={"csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/login"

    # Session destroyed — using it should fail.
    r3 = client.get("/library", cookies={"session": cookie})
    assert r3.status_code in (303, 401, 404)


def test_logout_set_cookie_has_security_flags(client, db_factory, csrf_for):
    make_user(db_factory)
    r = client.post(
        "/login",
        data={"username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)},
    )
    cookie = r.cookies.get("session")

    r2 = client.post(
        "/logout",
        data={"csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    sc = r2.headers.get("set-cookie", "")
    assert "session=" in sc
    assert "HttpOnly" in sc or "httponly" in sc.lower()
    assert "Secure" in sc or "secure" in sc.lower()
    assert "SameSite=Strict" in sc or "samesite=strict" in sc.lower()
```

- [ ] **Step 1.2: Replace the first-login-setup tests**

Replace `tests/integration/test_first_login_setup.py` entirely with:

```python
from app.auth.passwords import hash_password, verify_password
from app.models import User
from sqlalchemy import select


def make_fresh_user(db_factory, password="temp-password-99"):
    with db_factory() as s:
        u = User(
            username="newbie",
            password_hash=hash_password(password),
            must_change_password=True,
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id


def test_login_redirects_to_change_password_for_fresh_user(client, db_factory, csrf_for):
    make_fresh_user(db_factory)
    r = client.post(
        "/login",
        data={"username": "newbie", "password": "temp-password-99", "csrf_token": csrf_for(None)},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/change-password"


def test_change_password_requires_partial_session(client):
    r = client.get("/change-password")
    assert r.status_code in (303, 401)
    if r.status_code == 303:
        assert r.headers["location"] == "/login"


def test_change_password_too_short_rejected(client, db_factory, csrf_for):
    make_fresh_user(db_factory)
    r = client.post(
        "/login",
        data={"username": "newbie", "password": "temp-password-99", "csrf_token": csrf_for(None)},
    )
    cookie = r.cookies.get("session")
    r2 = client.post(
        "/change-password",
        data={"new_password": "short", "confirm": "short", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r2.status_code == 400


def test_change_password_mismatch_rejected(client, db_factory, csrf_for):
    make_fresh_user(db_factory)
    r = client.post(
        "/login",
        data={"username": "newbie", "password": "temp-password-99", "csrf_token": csrf_for(None)},
    )
    cookie = r.cookies.get("session")
    r2 = client.post(
        "/change-password",
        data={"new_password": "long-enough-12345", "confirm": "different-12345", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r2.status_code == 400


def test_change_password_success_redirects_to_library_and_promotes_session(client, db_factory, csrf_for):
    make_fresh_user(db_factory)
    r = client.post(
        "/login",
        data={"username": "newbie", "password": "temp-password-99", "csrf_token": csrf_for(None)},
    )
    cookie = r.cookies.get("session")

    r2 = client.post(
        "/change-password",
        data={"new_password": "new-strong-password-1", "confirm": "new-strong-password-1", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/library"

    # Old password no longer works.
    r3 = client.post(
        "/login",
        data={"username": "newbie", "password": "temp-password-99", "csrf_token": csrf_for(None)},
    )
    assert r3.status_code == 401

    # New password works AND must_change_password cleared.
    r4 = client.post(
        "/login",
        data={"username": "newbie", "password": "new-strong-password-1", "csrf_token": csrf_for(None)},
    )
    assert r4.status_code == 303
    assert r4.headers["location"] == "/library"
```

- [ ] **Step 1.3: Update the User model**

Replace `app/models.py` entirely with:

```python
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
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

- [ ] **Step 1.4: Write the schema migration**

Create `migrations/versions/0002_drop_2fa.py`:

```python
"""drop 2fa

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('backup_codes') as batch_op:
        batch_op.drop_index('ix_backup_codes_user_id')
    op.drop_table('backup_codes')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('totp_enabled')
        batch_op.drop_column('totp_secret_encrypted')


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('totp_secret_encrypted', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default='0'))
    op.create_table(
        'backup_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('code_hash', sa.String(length=255), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('backup_codes') as batch_op:
        batch_op.create_index('ix_backup_codes_user_id', ['user_id'], unique=False)
```

- [ ] **Step 1.5: Simplify the auth routes**

Replace `app/auth/routes.py` entirely with:

```python
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import SESSION_COOKIE, get_current_user_partial
from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import create_session, delete_session, promote_session
from app.csrf import verify_csrf
from app.deps import get_db, render
from app.models import User

# Pre-computed bcrypt hash for constant-time login.
# When a username doesn't exist we still run verify_password against this hash
# so an attacker can't tell from timing whether the username is registered.
_DUMMY_HASH = "$2b$12$2c.5f53Q3NK9EuhHoCSFSuD6I6GXXAE9Vd654eSySWBtwDm.adhOC"

router = APIRouter()

PARTIAL_SESSION_TTL_DAYS = 1
FULL_SESSION_TTL_DAYS = 30
MIN_PASSWORD_LEN = 12


def _set_session_cookie(response, token: str):
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, secure=True, samesite="strict",
        path="/", max_age=FULL_SESSION_TTL_DAYS * 86400,
    )


@router.get("/login", response_class=HTMLResponse)
def login_get(request: Request) -> HTMLResponse:
    return render(request, "login.html", {"error": None})


@router.post("/login", response_model=None)
def login_post(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
) -> RedirectResponse | HTMLResponse:
    user = db.scalars(select(User).where(User.username == username)).first()
    hash_to_check = user.password_hash if user is not None else _DUMMY_HASH
    password_ok = verify_password(password, hash_to_check)
    if user is None or not password_ok:
        return render(
            request, "login.html", {"error": "Неверный логин или пароль"},
            status_code=401,
        )

    if user.must_change_password:
        token = create_session(db, user_id=user.id, ttl_days=PARTIAL_SESSION_TTL_DAYS, is_partial=True)
        target = "/change-password"
    else:
        token = create_session(db, user_id=user.id, ttl_days=FULL_SESSION_TTL_DAYS, is_partial=False)
        target = "/library"
    db.commit()

    response = RedirectResponse(target, status_code=303)
    _set_session_cookie(response, token)
    return response


@router.get("/change-password", response_class=HTMLResponse)
def change_password_get(
    request: Request,
    user: Annotated[User, Depends(get_current_user_partial)],
) -> HTMLResponse:
    return render(
        request, "change_password.html", {"user": user, "error": None}
    )


@router.post("/change-password", response_model=None)
def change_password_post(
    request: Request,
    new_password: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
    user: Annotated[User, Depends(get_current_user_partial)],
    db: Annotated[Session, Depends(get_db)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
) -> RedirectResponse | HTMLResponse:
    if len(new_password) < MIN_PASSWORD_LEN:
        return render(
            request,
            "change_password.html",
            {"user": user, "error": f"Пароль должен быть не короче {MIN_PASSWORD_LEN} символов"},
            status_code=400,
        )
    if new_password != confirm:
        return render(
            request,
            "change_password.html",
            {"user": user, "error": "Пароли не совпадают"},
            status_code=400,
        )

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    token = request.cookies.get(SESSION_COOKIE) or ""
    promote_session(db, token, ttl_days=FULL_SESSION_TTL_DAYS)
    db.commit()
    return RedirectResponse("/library", status_code=303)


@router.post("/logout")
def logout(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(db, token)
        db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=True, samesite="strict")
    return response
```

- [ ] **Step 1.6: Delete dead files**

```bash
rm app/auth/totp.py
rm app/auth/backup_codes.py
rm templates/enroll_2fa.html
rm templates/verify_totp.html
rm tests/unit/test_totp.py
rm tests/unit/test_backup_codes.py
```

- [ ] **Step 1.7: Patch every remaining TOTP reference (exhaustive)**

`grep -rn "totp\|backup_code\|BackupCode\|enroll_2fa\|verify_totp" --include="*.py" --include="*.html" .` was run at plan-write time. Below is the complete list. Apply all edits.

**Backend:**

`app/admin/routes.py:81-87` — drop `totp_enabled=False`:
```python
# Was:
new_user = User(username=username, password_hash=hash_password(temp_password),
                must_change_password=True, totp_enabled=False, is_admin=bool(is_admin))
# Becomes:
new_user = User(username=username, password_hash=hash_password(temp_password),
                must_change_password=True, is_admin=bool(is_admin))
```

`scripts/create_admin.py:35` — drop `totp_enabled=False` line.

`templates/admin_users.html` — has `<th>2FA</th>` and `<td>{{ "вкл" if u.totp_enabled else "выкл" }}</td>`. Will be wholesale replaced in Task 10, but to keep tests green between Task 1 and Task 10, drop those two lines now.

**Tests — fixtures that build `User` rows:**

The following test files have a fixture that creates a user with `totp_enabled=True, totp_secret_encrypted=encrypt_secret(secret, _derive_key("x" * 64))`. Replace each fixture's user-creation block with a simpler version (drop the TOTP lines AND drop the `pyotp.random_base32()` / `encrypt_secret` / `_derive_key` imports at the top of the file).

Files to patch (each one — same pattern):
- `tests/integration/test_admin_users.py:11-17, 34-40` (two user fixtures)
- `tests/integration/test_download.py:15-22`
- `tests/integration/test_health.py:11-19`
- `tests/integration/test_library.py:9-17`
- `tests/integration/test_library_real.py:10-18`
- `tests/integration/test_media_delete.py:18-25`
- `tests/integration/test_streaming.py:18-25`
- `tests/integration/test_torrents_api.py:13-20`

Pattern (remove the indicated lines, keep everything else in the fixture):
```python
# DELETE these from the top of each file (if present):
import pyotp
from app.auth.totp import _derive_key, encrypt_secret

# DELETE these from each User(...) construction:
#     totp_enabled=True,
#     totp_secret_encrypted=encrypt_secret(secret, _derive_key("x" * 64)),
# also delete the `secret = pyotp.random_base32()` line that feeds them.
```

`tests/unit/test_constant_time_login.py:20` — drop `totp_enabled=True, totp_secret_encrypted="x"` from the User kwargs.

`tests/unit/test_models.py` — currently imports `BackupCode` and asserts `u.totp_enabled is False` / `u.totp_secret_encrypted is None`, plus has a test that creates a `BackupCode`. Edit:

```python
# Top import — drop BackupCode:
from app.models import MediaItem, Session as UserSession, User, WatchProgress

# Drop the two assertions:
# assert u.totp_enabled is False
# assert u.totp_secret_encrypted is None

# Drop the BackupCode test entirely (the test function that creates one).
```

`tests/unit/test_foreign_keys_pragma.py` — currently tests cascade-delete via BackupCode. Replace BackupCode with a different cascading model (Session works — `user_id` has CASCADE on delete). Edit:

```python
# Top import — replace BackupCode with nothing (Session is already imported as UserSession):
from app.models import Session as UserSession, User

# In the test function, swap BackupCode for a UserSession row:
# Was:
#     s.add(BackupCode(user_id=u.id, code_hash="h"))
# Becomes:
from datetime import datetime, timedelta, timezone
s.add(UserSession(token="t1", user_id=u.id,
                  expires_at=datetime.now(timezone.utc) + timedelta(days=1)))

# And the assertion — swap select(BackupCode) for select(UserSession):
# Was:
#     assert s.scalars(select(BackupCode).where(BackupCode.user_id == uid)).first() is None
# Becomes:
assert s.scalars(select(UserSession).where(UserSession.user_id == uid)).first() is None
```

**Re-grep to confirm clean:**

```bash
grep -rn "totp\|backup_code\|BackupCode\|enroll_2fa\|verify_totp" --include="*.py" --include="*.html" .
```

Expected: zero matches. (The env var `TOTP_ISSUER=TestSrv` in `tests/conftest.py` is also fine to drop now — `monkeypatch.setenv("TOTP_ISSUER", "TestSrv")` line — since nothing reads it anymore. Drop it.)

- [ ] **Step 1.8: Update README**

In `README.md`, replace this paragraph:

```markdown
Открыть в браузере `http://127.0.0.1:8000/`. Зайти под админом → сменить пароль → активировать 2FA (отсканировать QR в Google Authenticator) → сохранить backup-коды → пустая библиотека.
```

with:

```markdown
Открыть в браузере `http://127.0.0.1:8000/`. Зайти под админом → сменить пароль → пустая библиотека.

> Двухфакторка внутри MediaServer не используется — на проде доступ закрыт Authentik (forward auth proxy), который сам отвечает за 2FA при необходимости. Для локального dev-запуска без Authentik достаточно пароля.
```

- [ ] **Step 1.9: Apply migration in your dev DB and run tests**

```bash
alembic upgrade head
pytest -v
```

Expected: `alembic upgrade head` reports applying `0002`. `pytest` shows all tests passing (with `test_totp.py` and `test_backup_codes.py` no longer collected). Existing library/streaming/torrents/admin tests should be unaffected. If any other test fails because of a stray TOTP reference, fix it inline before committing.

- [ ] **Step 1.10: Commit**

```bash
git add -A
git -c user.name=dev -c user.email=dev@local commit -m "feat(auth): remove local 2FA — Authentik handles it upstream

- drop User.totp_enabled, User.totp_secret_encrypted, BackupCode table
- migration 0002 with batch_alter_table for SQLite
- simplify login_post to two branches (must-change-password / library)
- delete totp.py, backup_codes.py, enroll_2fa.html, verify_totp.html
- delete test_totp.py, test_backup_codes.py
- rewrite test_login_flow.py and test_first_login_setup.py
- README: explain that 2FA lives in Authentik now"
```

---

## Task 2: New CSS design system

**Files:**
- Modify: `static/style.css` (full rewrite)

This task only touches `style.css`. Templates are still old; the page will look slightly different but should not break (existing class names without rules just inherit defaults). We replace templates one-by-one in later tasks.

- [ ] **Step 2.1: Replace `static/style.css` entirely**

Write `static/style.css` with:

```css
/* === MediaServer design system === */
/* Dark cinematic theme, blue accent. CSS-variable based — easy to retheme later. */

:root {
  /* Surfaces */
  --bg:           #0a0a0f;
  --surface:      #141420;
  --surface-2:    #1c1c2e;
  --border:       #2a2a3a;

  /* Text */
  --text:         #f5f5f7;
  --text-dim:     #8c8ca0;
  --text-faint:   #5c5c70;

  /* Accents */
  --accent:       #3b82f6;
  --accent-hover: #2563eb;
  --accent-soft:  rgba(59, 130, 246, 0.15);
  --success:      #10b981;
  --danger:       #ef4444;
  --warning:      #f59e0b;

  /* Geometry */
  --radius-sm:    4px;
  --radius:       8px;
  --radius-lg:    12px;
  --radius-pill:  999px;

  /* Shadow */
  --shadow:       0 4px 24px rgba(0, 0, 0, 0.4);
}

/* Reset */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, system-ui, sans-serif;
  font-size: 15px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
img { max-width: 100%; display: block; }
button { font: inherit; cursor: pointer; }

/* Focus */
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

/* === Top navigation === */
header.topnav {
  background: rgba(20, 20, 32, 0.92);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border);
  padding: 0.85rem 1.5rem;
  display: flex;
  align-items: center;
  gap: 1.75rem;
  position: sticky;
  top: 0;
  z-index: 10;
}
header.topnav .logo {
  color: var(--accent);
  font-weight: 800;
  letter-spacing: 0.04em;
  font-size: 0.95rem;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  text-decoration: none;
}
header.topnav .logo svg { width: 18px; height: 18px; }
header.topnav nav.main { display: flex; gap: 0.4rem; flex-wrap: wrap; }
header.topnav nav.main a {
  color: var(--text-dim);
  padding: 0.4rem 0.8rem;
  border-radius: var(--radius);
  font-size: 0.88rem;
  transition: color 0.15s, background 0.15s;
  text-decoration: none;
}
header.topnav nav.main a:hover { color: var(--text); background: var(--surface-2); }
header.topnav nav.main a.active { color: var(--text); background: var(--accent-soft); }
header.topnav .user-menu {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.3rem 0.6rem 0.3rem 0.4rem;
  border-radius: var(--radius-pill);
  background: var(--surface-2);
  font-size: 0.85rem;
}
header.topnav .user-menu form { display: inline; margin: 0; }
header.topnav .avatar {
  width: 28px; height: 28px; border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), #8b5cf6);
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 0.75rem;
}

/* === Main / container === */
main { flex: 1; max-width: 1280px; width: 100%; margin: 0 auto; padding: 2rem 1.5rem; }
main.centered { display: flex; align-items: center; justify-content: center; padding-top: 4rem; }

/* === Buttons === */
.btn {
  background: var(--accent);
  color: #fff;
  border: none;
  padding: 0.7rem 1.4rem;
  font-size: 0.9rem;
  font-weight: 600;
  border-radius: var(--radius);
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  transition: background 0.15s, transform 0.1s;
  text-decoration: none;
}
.btn:hover { background: var(--accent-hover); transform: translateY(-1px); text-decoration: none; }
.btn:active { transform: translateY(0); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
.btn-secondary {
  background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
}
.btn-secondary:hover { background: var(--border); }
.btn-ghost { background: transparent; color: var(--text-dim); }
.btn-ghost:hover { background: var(--surface-2); color: var(--text); }
.btn-danger { background: var(--danger); }
.btn-danger:hover { background: #dc2626; }
.btn-block { width: 100%; justify-content: center; }

/* === Forms === */
form.stack { display: flex; flex-direction: column; gap: 1rem; }
label { font-weight: 500; font-size: 0.85rem; color: var(--text-dim); display: block; margin-bottom: 0.35rem; }
.input, input[type=text], input[type=password], input[type=email], input[type=number], textarea, select {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  padding: 0.6rem 0.8rem;
  border-radius: var(--radius);
  font: inherit;
  width: 100%;
  transition: border-color 0.15s, background 0.15s;
}
.input:focus, input:focus, textarea:focus, select:focus {
  outline: none;
  border-color: var(--accent);
  background: var(--surface-2);
}
.input::placeholder, input::placeholder, textarea::placeholder { color: var(--text-faint); }
.error {
  background: rgba(239, 68, 68, 0.1);
  color: var(--danger);
  padding: 0.7rem 1rem;
  border-radius: var(--radius);
  border: 1px solid rgba(239, 68, 68, 0.3);
  font-size: 0.9rem;
}

/* === Card === */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 1.5rem;
}
.card-narrow { max-width: 420px; margin: 0 auto; }
.card h1, .card h2 { margin-bottom: 1rem; letter-spacing: -0.02em; }

/* === Section header === */
.section-header {
  display: flex; align-items: baseline; gap: 1rem; margin-bottom: 1rem;
}
.section-header h2 {
  font-size: 1.1rem; letter-spacing: -0.01em; font-weight: 700;
}
.section-header a { margin-left: auto; color: var(--accent); font-size: 0.85rem; }

/* === Hero (library / media) === */
.hero {
  position: relative;
  min-height: 380px;
  border-radius: var(--radius-lg);
  overflow: hidden;
  margin-bottom: 2.5rem;
  background-color: var(--surface);
}
.hero::after {
  content: "";
  position: absolute; inset: 0;
  background:
    linear-gradient(180deg, transparent 0%, var(--bg) 95%),
    linear-gradient(90deg, var(--bg) 0%, transparent 50%);
  pointer-events: none;
}
.hero-content {
  position: absolute; bottom: 2rem; left: 2rem; right: 2rem;
  max-width: 540px; z-index: 1;
}
.hero-label {
  color: var(--accent);
  font-size: 0.7rem;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  font-weight: 700;
  margin-bottom: 0.5rem;
}
.hero h1 {
  font-size: clamp(1.6rem, 3vw, 2.4rem);
  line-height: 1.1;
  letter-spacing: -0.02em;
  font-weight: 800;
  margin-bottom: 0.6rem;
}
.hero-meta {
  color: var(--text-dim);
  font-size: 0.85rem;
  margin-bottom: 0.8rem;
  display: flex; gap: 0.8rem; align-items: center; flex-wrap: wrap;
}
.hero-meta .dot { width: 3px; height: 3px; background: var(--text-faint); border-radius: 50%; }
.hero p.desc {
  color: var(--text-dim);
  font-size: 0.95rem;
  margin-bottom: 1.5rem;
}
.hero .actions { display: flex; gap: 0.6rem; flex-wrap: wrap; }

/* === Poster grid === */
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 1rem;
  margin-bottom: 2.5rem;
}
.poster {
  position: relative;
  aspect-ratio: 2/3;
  border-radius: var(--radius);
  overflow: hidden;
  display: block;
  text-decoration: none;
  color: inherit;
  transition: transform 0.2s;
}
.poster:hover { transform: scale(1.04); text-decoration: none; }
.poster .art { position: absolute; inset: 0; }
.poster .overlay {
  position: absolute; inset: 0;
  background: linear-gradient(to top, rgba(0,0,0,0.85) 0%, transparent 50%);
  display: flex; flex-direction: column; justify-content: flex-end;
  padding: 0.7rem;
}
.poster .title {
  font-weight: 700;
  font-size: 0.9rem;
  line-height: 1.2;
  margin-bottom: 0.2rem;
  color: var(--text);
}
.poster .year { color: var(--text-dim); font-size: 0.75rem; }
.poster .badge {
  position: absolute; top: 0.5rem; right: 0.5rem;
  background: rgba(0,0,0,0.7);
  color: #fff;
  font-size: 0.65rem;
  padding: 0.2rem 0.4rem;
  border-radius: var(--radius-sm);
  backdrop-filter: blur(4px);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.poster .progress {
  position: absolute; bottom: 0; left: 0; right: 0;
  height: 3px; background: rgba(255,255,255,0.15);
}
.poster .progress > div { height: 100%; background: var(--accent); }

/* === Tables === */
.table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.table th, .table td {
  padding: 0.85rem 1rem;
  text-align: left;
  border-bottom: 1px solid var(--border);
}
.table th {
  background: var(--surface-2);
  color: var(--text-dim);
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.table tr:last-child td { border-bottom: none; }
.table tr:hover td { background: var(--surface-2); }
.table .actions { display: flex; gap: 0.4rem; }

/* === Progress bar === */
.progress-bar {
  height: 6px; background: var(--surface-2); border-radius: var(--radius-pill);
  overflow: hidden; min-width: 100px;
}
.progress-bar > div {
  height: 100%; background: var(--accent);
  transition: width 0.3s ease;
}
.progress-bar.success > div { background: var(--success); }
.progress-bar.warning > div { background: var(--warning); }

/* === Status dot === */
.status-dot {
  display: inline-block;
  width: 8px; height: 8px; border-radius: 50%;
  margin-right: 0.4rem; vertical-align: middle;
}
.status-dot.success { background: var(--success); }
.status-dot.warning { background: var(--warning); }
.status-dot.danger { background: var(--danger); }
.status-dot.dim { background: var(--text-faint); }

/* === Badge (inline pill) === */
.badge-inline {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: var(--radius-sm);
  font-size: 0.7rem;
  font-weight: 600;
  background: var(--surface-2);
  color: var(--text-dim);
}
.badge-inline.success { background: rgba(16,185,129,0.15); color: var(--success); }
.badge-inline.danger { background: rgba(239,68,68,0.15); color: var(--danger); }

/* === Modal === */
.modal-backdrop {
  position: fixed; inset: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex; align-items: center; justify-content: center;
  z-index: 100;
  backdrop-filter: blur(4px);
}
.modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 2rem;
  max-width: 480px;
  width: calc(100% - 2rem);
  box-shadow: var(--shadow);
}
.modal h2 { margin-bottom: 1rem; letter-spacing: -0.02em; }
.modal .actions { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 1.5rem; }

/* === Toast (simple, manually toggled) === */
.toast {
  position: fixed; bottom: 1.5rem; right: 1.5rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: var(--radius);
  padding: 0.8rem 1.2rem;
  box-shadow: var(--shadow);
  font-size: 0.9rem;
  z-index: 50;
}
.toast.success { border-left-color: var(--success); }
.toast.danger { border-left-color: var(--danger); }

/* === Empty state === */
.empty {
  text-align: center;
  padding: 3rem 1rem;
  border: 1px dashed var(--border);
  border-radius: var(--radius-lg);
  color: var(--text-faint);
}
.empty h3 { color: var(--text); margin-bottom: 0.5rem; font-weight: 700; letter-spacing: -0.01em; }
.empty p { margin-bottom: 1rem; font-size: 0.9rem; }

/* === Drag-drop zone === */
.dropzone {
  border: 2px dashed var(--border);
  border-radius: var(--radius-lg);
  padding: 2rem;
  text-align: center;
  color: var(--text-dim);
  transition: border-color 0.15s, background 0.15s;
}
.dropzone:hover, .dropzone.over {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--text);
}

/* === Tabs === */
.tabs {
  display: flex; gap: 0.2rem; border-bottom: 1px solid var(--border);
  margin-bottom: 1.5rem;
}
.tabs button {
  background: transparent;
  border: none;
  color: var(--text-dim);
  padding: 0.7rem 1rem;
  font-size: 0.9rem;
  font-weight: 600;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.tabs button.active { color: var(--text); border-bottom-color: var(--accent); }
.tabs button:hover { color: var(--text); }

/* === Mobile === */
@media (max-width: 640px) {
  header.topnav { padding: 0.7rem 1rem; gap: 0.8rem; }
  header.topnav nav.main a { padding: 0.3rem 0.5rem; font-size: 0.8rem; }
  header.topnav .user-menu { padding: 0.2rem 0.5rem 0.2rem 0.3rem; font-size: 0.8rem; }
  header.topnav .avatar { width: 24px; height: 24px; font-size: 0.7rem; }
  main { padding: 1rem 1rem; }
  .hero { min-height: 280px; }
  .hero-content { left: 1rem; right: 1rem; bottom: 1rem; }
  .grid { grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 0.6rem; }
  .table th, .table td { padding: 0.6rem 0.5rem; font-size: 0.85rem; }
}
```

- [ ] **Step 2.2: Verify CSS syntactically valid**

```bash
python -c "import re; css=open('static/style.css').read(); print('rules:', css.count('{')); assert css.count('{') == css.count('}')"
```

Expected: prints rule count, no AssertionError.

- [ ] **Step 2.3: Commit**

```bash
git add static/style.css
git -c user.name=dev -c user.email=dev@local commit -m "style(css): replace style.css with full design system

Dark cinematic theme with blue accent, CSS-variables for theming.
Components: topnav, buttons, forms, cards, hero, poster grid, tables,
progress bars, modals, toasts, tabs, dropzone, empty states.
Mobile breakpoint at 640px.

Templates still old at this commit — they'll inherit some new defaults
(dark bg, system font) but won't fully match the design system until
they're rewritten in subsequent tasks."
```

---

## Task 3: New `base.html` shell

**Files:**
- Modify: `templates/base.html`

The base shell drives every page. Adding the topnav here makes the next page-by-page tasks straightforward.

- [ ] **Step 3.1: Replace `templates/base.html`**

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
  {% block head_extra %}{% endblock %}
</head>
<body>
  {% if user %}
  <header class="topnav">
    <a href="/library" class="logo" aria-label="MediaServer — главная">
      <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>
      MEDIASERVER
    </a>
    <nav class="main">
      <a href="/library" class="{% if request.url.path.startswith('/library') or request.url.path.startswith('/media') %}active{% endif %}">Библиотека</a>
      <a href="/downloads" class="{% if request.url.path.startswith('/downloads') %}active{% endif %}">Загрузки</a>
      <a href="/add-torrent" class="{% if request.url.path.startswith('/add-torrent') %}active{% endif %}">＋ Добавить</a>
      {% if user.is_admin %}
      <a href="/admin/users" class="{% if request.url.path.startswith('/admin/users') %}active{% endif %}">Пользователи</a>
      <a href="/admin/health" class="{% if request.url.path.startswith('/admin/health') %}active{% endif %}">Здоровье</a>
      {% endif %}
    </nav>
    <div class="user-menu">
      <div class="avatar" aria-hidden="true">{{ user.username[0]|upper }}</div>
      <span>{{ user.username }}</span>
      <form method="post" action="/logout" style="display:inline">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button type="submit" class="btn btn-ghost" style="padding:0.3rem 0.6rem;font-size:0.8rem;">Выйти</button>
      </form>
    </div>
  </header>
  {% endif %}
  <main{% if not user %} class="centered"{% endif %}>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 3.2: Verify Jinja syntax with a render**

Start the dev server and hit any logged-in page (or use the test client):

```bash
pytest tests/integration/test_app_starts.py -v
```

Expected: PASS. App boots without template error.

- [ ] **Step 3.3: Commit**

```bash
git add templates/base.html
git -c user.name=dev -c user.email=dev@local commit -m "feat(ui): new base.html shell with sticky topnav

Topnav has logo, main nav (Библиотека / Загрузки / + Добавить /
Пользователи [admin] / Здоровье [admin]), user pill with avatar
initial and logout button. Active link highlighted via
request.url.path. Logged-out pages get a centered main layout."
```

---

## Task 4: Restyle `login.html`

**Files:**
- Modify: `templates/login.html`

- [ ] **Step 4.1: Replace `templates/login.html`**

```html
{% extends "base.html" %}
{% block title %}Вход — MediaServer{% endblock %}
{% block content %}
<div class="card card-narrow">
  <h1 style="text-align:center; font-size: 1.6rem;">
    <span style="color: var(--accent);">▶</span> Вход
  </h1>
  {% if error %}<div class="error" style="margin-bottom:1rem;">{{ error }}</div>{% endif %}
  <form method="post" action="/login" class="stack">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div>
      <label for="username">Логин</label>
      <input id="username" name="username" autocomplete="username" autofocus required>
    </div>
    <div>
      <label for="password">Пароль</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
    </div>
    <button type="submit" class="btn btn-block">Войти</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 4.2: Verify with integration test**

```bash
pytest tests/integration/test_login_flow.py::test_login_get_returns_form -v
```

Expected: PASS.

- [ ] **Step 4.3: Manual check**

Start `uvicorn app.main:app --reload --port 8000`, open `http://127.0.0.1:8000/login`, confirm centered card with two fields, dark background, blue button. (Tests don't catch visual regressions.)

- [ ] **Step 4.4: Commit**

```bash
git add templates/login.html
git -c user.name=dev -c user.email=dev@local commit -m "feat(ui): restyle login as centered card with stack form"
```

---

## Task 5: Restyle `change_password.html`

**Files:**
- Modify: `templates/change_password.html`

- [ ] **Step 5.1: Replace `templates/change_password.html`**

```html
{% extends "base.html" %}
{% block title %}Смена пароля — MediaServer{% endblock %}
{% block content %}
<div class="card card-narrow">
  <h1 style="text-align:center; font-size: 1.4rem;">Смена пароля</h1>
  <p style="color:var(--text-dim); font-size:0.9rem; text-align:center; margin-bottom:1.2rem;">
    Это первый вход. Задайте новый пароль (минимум 12 символов).
  </p>
  {% if error %}<div class="error" style="margin-bottom:1rem;">{{ error }}</div>{% endif %}
  <form method="post" action="/change-password" class="stack">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div>
      <label for="new_password">Новый пароль</label>
      <input id="new_password" name="new_password" type="password" autocomplete="new-password" minlength="12" required autofocus>
    </div>
    <div>
      <label for="confirm">Подтвердите пароль</label>
      <input id="confirm" name="confirm" type="password" autocomplete="new-password" minlength="12" required>
    </div>
    <button type="submit" class="btn btn-block">Сохранить</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 5.2: Verify with integration tests**

```bash
pytest tests/integration/test_first_login_setup.py -v
```

Expected: all PASS.

- [ ] **Step 5.3: Commit**

```bash
git add templates/change_password.html
git -c user.name=dev -c user.email=dev@local commit -m "feat(ui): restyle change_password as centered card with hint"
```

---

## Task 6: Restyle `library.html`

**Files:**
- Modify: `templates/library.html`

Context (from `app/library/routes.py:23-30` `library_page`): `user`, `items` — list of `MediaItem` already sorted by `added_at` descending. So `items[0]` is the freshest. Item fields: `id`, `title`, `size_bytes`, `added_at`, `torrent_hash`, `file_path`.

- [ ] **Step 6.1: Replace `templates/library.html`**

```html
{% extends "base.html" %}
{% block title %}Библиотека — MediaServer{% endblock %}
{% block content %}

{% if items %}
  {# Hero — самый свежий файл (items[0] — DESC-сортировка из роута) #}
  {% set hero = items[0] %}
  <div class="hero" style="background: linear-gradient(135deg, hsl({{ hero.id * 47 % 360 }}, 50%, 25%), var(--surface));">
    <div class="hero-content">
      <div class="hero-label">Недавно добавлено</div>
      <h1>{{ hero.title }}</h1>
      <div class="hero-meta">
        <span>{{ (hero.size_bytes / 1024 / 1024 / 1024)|round(1) }} ГБ</span>
        <span class="dot"></span>
        <span>{{ hero.added_at.strftime('%d.%m.%Y') }}</span>
      </div>
      <div class="actions">
        <a href="/media/{{ hero.id }}" class="btn">▶ Смотреть</a>
        <a href="/api/download/{{ hero.id }}" class="btn btn-secondary">⬇ Скачать</a>
      </div>
    </div>
  </div>

  <div class="section-header">
    <h2>Вся библиотека</h2>
    <span style="color:var(--text-dim); font-size:0.85rem;">{{ items|length }} файлов</span>
  </div>
  <div class="grid">
    {% for item in items %}
    <a href="/media/{{ item.id }}" class="poster">
      <div class="art" style="background: linear-gradient(180deg, hsl({{ item.id * 47 % 360 }}, 50%, 30%), var(--bg));"></div>
      <div class="overlay">
        <div class="title">{{ item.title }}</div>
        <div class="year">{{ (item.size_bytes / 1024 / 1024 / 1024)|round(1) }} ГБ</div>
      </div>
      {% if loop.index <= 3 %}<div class="badge">NEW</div>{% endif %}
    </a>
    {% endfor %}
  </div>
{% else %}
  <div class="empty">
    <h3>Библиотека пуста</h3>
    <p>Добавьте первый торрент — magnet-ссылку.</p>
    <a href="/add-torrent" class="btn">＋ Добавить торрент</a>
  </div>
{% endif %}

{% endblock %}
```

Note on the poster art: with no real cover images, we hash the `id` into HSL hue to give each poster a stable color — matches the spec §9 "симулируем градиентом" trade-off.

- [ ] **Step 6.2: Verify with integration test**

```bash
pytest tests/integration/test_library.py -v
```

Expected: tests pass. They check status 200 and presence of titles in HTML.

- [ ] **Step 6.3: Manual check (logged-in)**

Open `/library` in browser. Confirm: hero block at top with first item, grid below with all items, empty state if library is empty. Hover should give scale effect.

- [ ] **Step 6.4: Commit**

```bash
git add templates/library.html
git -c user.name=dev -c user.email=dev@local commit -m "feat(ui): restyle library with hero + poster grid + empty state

Posters use hashed-HSL gradients as art (no TMDb integration yet).
Hero block highlights the most recently added item; grid shows all
files with size and a NEW badge on top 3."
```

---

## Task 7: Restyle `media.html`

**Files:**
- Modify: `templates/media.html`

Context (from `app/library/routes.py:33-43` `media_page`): `user`, `item` — single `MediaItem`. URLs (verified):
- HLS playlist: `/api/stream/{item.id}/playlist.m3u8`
- Original file download: `/api/download/{item.id}`
- Delete: POST `/api/media/{item.id}/delete` (returns redirect to `/library`)
- Watch progress save: POST `/api/progress` with `{media_id, position_seconds}` (existing fire-and-forget interval call worth preserving)

- [ ] **Step 7.1: Replace `templates/media.html`**

```html
{% extends "base.html" %}
{% block title %}{{ item.title }} — MediaServer{% endblock %}
{% block head_extra %}
<script src="/static/hls.min.js"></script>
{% endblock %}
{% block content %}

<div class="hero" style="background: linear-gradient(135deg, hsl({{ item.id * 47 % 360 }}, 50%, 28%), var(--surface));">
  <div class="hero-content">
    <div class="hero-label">Файл</div>
    <h1>{{ item.title }}</h1>
    <div class="hero-meta">
      <span>{{ (item.size_bytes / 1024 / 1024 / 1024)|round(1) }} ГБ</span>
      <span class="dot"></span>
      <span>Добавлен {{ item.added_at.strftime('%d.%m.%Y') }}</span>
    </div>
    <div class="actions">
      <button type="button" class="btn" onclick="document.getElementById('player-card').scrollIntoView({behavior:'smooth'}); document.getElementById('player').play()">▶ Смотреть</button>
      <a href="/api/download/{{ item.id }}" class="btn btn-secondary">⬇ Скачать оригинал</a>
      <button type="button" class="btn btn-danger" onclick="document.getElementById('confirm-delete').style.display='flex'">Удалить</button>
    </div>
  </div>
</div>

<div id="player-card" class="card" style="padding:0; overflow:hidden; background:#000; margin-bottom:1.5rem;">
  <video id="player" controls preload="metadata" playsinline style="width:100%; max-height:80vh; display:block; background:#000;"></video>
</div>

<p><a href="/library" style="color:var(--text-dim);">← Библиотека</a></p>

{# Delete confirmation modal — hidden by default via inline display:none #}
<div id="confirm-delete" class="modal-backdrop" style="display:none;">
  <div class="modal">
    <h2>Удалить «{{ item.title }}»?</h2>
    <p style="color:var(--text-dim);">Файл будет удалён с диска. Действие необратимо.</p>
    <form method="post" action="/api/media/{{ item.id }}/delete" class="actions">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <button type="button" class="btn btn-secondary" onclick="document.getElementById('confirm-delete').style.display='none'">Отмена</button>
      <button type="submit" class="btn btn-danger">Удалить</button>
    </form>
  </div>
</div>

<script>
(function() {
  const video = document.getElementById('player');
  const src = '/api/stream/{{ item.id }}/playlist.m3u8';

  if (window.Hls && Hls.isSupported()) {
    const hls = new Hls();
    hls.loadSource(src);
    hls.attachMedia(video);
    hls.on(Hls.Events.ERROR, (e, data) => {
      console.error("HLS error:", data);
      if (data.fatal) {
        document.querySelector('main').insertAdjacentHTML('beforeend',
          '<div class="error" style="margin-top:1rem">Поток упал. Обновите страницу.</div>');
      }
    });
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = src;
  } else {
    document.querySelector('main').insertAdjacentHTML('beforeend',
      '<div class="error" style="margin-top:1rem">Браузер не поддерживает HLS. Скачайте оригинал.</div>');
  }

  // Прогресс просмотра — раз в 10 секунд, как было
  setInterval(() => {
    if (video.paused || video.ended || isNaN(video.currentTime)) return;
    fetch('/api/progress', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({media_id: {{ item.id }}, position_seconds: Math.floor(video.currentTime)}),
    }).catch(e => console.warn('progress save failed', e));
  }, 10000);
})();
</script>

{% endblock %}
```

- [ ] **Step 7.2: Verify with integration tests**

```bash
pytest tests/integration/test_streaming.py tests/integration/test_media_delete.py -v
```

Expected: PASS.

- [ ] **Step 7.3: Manual check**

Open `/media/<some_id>`, click «Смотреть», confirm video plays. Click «Удалить», confirm modal appears with cancel + delete buttons.

- [ ] **Step 7.4: Commit**

```bash
git add templates/media.html
git -c user.name=dev -c user.email=dev@local commit -m "feat(ui): restyle media page with hero, HLS player, delete modal"
```

---

## Task 8: Restyle `add_torrent.html`

**Files:**
- Modify: `templates/add_torrent.html`

Context: just `user`. Existing route accepts only `magnet` POST to `/api/torrents` (no file upload yet). The "file" tab below is a UI scaffold for future — keep it visually but disable submission until backend supports it. (For now, the file-tab form posts to the same endpoint and would 422 — so we hide its submit and add a "пока не реализовано" hint. YAGNI.)

Actually for YAGNI — drop the file tab entirely until the backend supports it. Single magnet form is enough.

- [ ] **Step 8.1: Replace `templates/add_torrent.html`**

```html
{% extends "base.html" %}
{% block title %}Добавить торрент — MediaServer{% endblock %}
{% block content %}

<div class="card" style="max-width: 640px; margin: 0 auto;">
  <h1 style="font-size: 1.4rem; letter-spacing: -0.02em; margin-bottom: 0.5rem;">Добавить торрент</h1>
  <p style="color:var(--text-dim); font-size:0.9rem; margin-bottom: 1.2rem;">
    Сервер скачает торрент и он появится в библиотеке после загрузки.
  </p>

  <form method="post" action="/api/torrents" class="stack">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div>
      <label for="magnet">Magnet-ссылка</label>
      <textarea id="magnet" name="magnet" rows="3" placeholder="magnet:?xt=urn:btih:..." required style="font-family: ui-monospace, 'SF Mono', Menlo, monospace; font-size:0.85rem;"></textarea>
    </div>
    <button type="submit" class="btn">＋ Добавить</button>
  </form>

  <p style="margin-top: 1.5rem; font-size:0.85rem;">
    <a href="/downloads">→ Активные загрузки</a>
    &nbsp;·&nbsp;
    <a href="/library" style="color:var(--text-dim);">← Библиотека</a>
  </p>
</div>

{% endblock %}
```

- [ ] **Step 8.2: Verify with integration tests**

```bash
pytest tests/integration/test_torrents_api.py -v
```

Expected: PASS — backend isn't touched, only the page render.

- [ ] **Step 8.3: Manual check**

Open `/add-torrent`. Confirm centered card with magnet textarea. Submit a real magnet (or invalid one — should 400 with the existing backend error UI on `/downloads` redirect).

- [ ] **Step 8.4: Commit**

```bash
git add templates/add_torrent.html
git -c user.name=dev -c user.email=dev@local commit -m "feat(ui): restyle add-torrent with tabs (magnet / file) and dropzone"
```

---

## Task 9: Restyle `downloads.html`

**Files:**
- Modify: `templates/downloads.html`

Important — this page is **NOT** server-rendered. The route just renders the empty shell; the table is populated by JS that polls `GET /api/torrents/status` every 2s and renders rows from the JSON response. JSON fields per torrent: `name`, `progress_percent` (0–100), `speed_human`, `eta_human`, `state`, `hash` (probably present — check first row of response).

We rewrite both the shell and the JS-rendering inside.

- [ ] **Step 9.1: Replace `templates/downloads.html`**

```html
{% extends "base.html" %}
{% block title %}Загрузки — MediaServer{% endblock %}
{% block content %}

<div class="section-header">
  <h2>Активные загрузки</h2>
  <a href="/add-torrent">＋ Добавить</a>
</div>

<div id="torrents-table"
     hx-get="/api/torrents/status"
     hx-trigger="load, every 2s"
     hx-swap="innerHTML">
  <p style="color:var(--text-dim);">Загрузка…</p>
</div>

<script>
function dotClass(state) {
  const s = (state || '').toLowerCase();
  if (s.includes('download') || s === 'downloading') return 'success';
  if (s.includes('pause') || s === 'paused') return 'warning';
  if (s.includes('error') || s === 'missingfiles') return 'danger';
  return 'dim';
}

document.body.addEventListener('htmx:afterRequest', (e) => {
  if (e.detail.requestConfig.path !== '/api/torrents/status') return;
  const target = document.getElementById('torrents-table');
  if (!e.detail.successful) {
    target.innerHTML = '<div class="error">qBittorrent недоступен</div>';
    return;
  }
  const data = JSON.parse(e.detail.xhr.responseText);
  if (data.length === 0) {
    target.innerHTML = `
      <div class="empty">
        <h3>Нет активных загрузок</h3>
        <p>Все скачанные файлы попадают в библиотеку.</p>
        <a href="/add-torrent" class="btn">＋ Добавить торрент</a>
      </div>`;
    return;
  }
  const rows = data.map(t => `
    <tr>
      <td><span class="status-dot ${dotClass(t.state)}"></span></td>
      <td>${escapeHtml(t.name)}</td>
      <td>
        <div class="progress-bar ${t.progress_percent >= 100 ? 'success' : ''}">
          <div style="width: ${t.progress_percent}%"></div>
        </div>
        <div style="font-size:0.75rem; color:var(--text-dim); margin-top:0.2rem;">${t.progress_percent}%</div>
      </td>
      <td>${escapeHtml(t.speed_human || '—')}</td>
      <td>${escapeHtml(t.eta_human || '—')}</td>
      <td><span class="badge-inline">${escapeHtml(t.state || '')}</span></td>
    </tr>
  `).join('');
  target.innerHTML = `
    <table class="table">
      <thead>
        <tr>
          <th style="width:1%"></th>
          <th>Название</th>
          <th style="width:30%">Прогресс</th>
          <th style="width:12%">Скорость</th>
          <th style="width:10%">ETA</th>
          <th style="width:10%">Статус</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
</script>

{% endblock %}
```

Notes:
- The status JSON uses qBittorrent state strings (`downloading`, `pausedUP`, `pausedDL`, `stalledDL`, `error`, etc.). The `dotClass()` function above maps loose categories to colors.
- We removed the per-row delete button — there's no `/api/torrents/{hash}/delete` route in the current codebase. Adding it is out of scope for this PR.
- `escapeHtml()` is essential — torrent names come from arbitrary magnets and could contain `<script>`.

- [ ] **Step 9.2: Verify with integration tests**

```bash
pytest tests/integration/test_torrents_api.py tests/integration/test_download.py -v
```

Expected: PASS.

- [ ] **Step 9.3: Manual check**

Open `/downloads` with at least one torrent in qBittorrent. Confirm progress bar updates every 2s. Empty state if none.

- [ ] **Step 9.4: Commit**

```bash
git add templates/downloads.html
git -c user.name=dev -c user.email=dev@local commit -m "feat(ui): restyle downloads as table with progress bars and status dots"
```

---

## Task 10: Restyle `admin_users.html`

**Files:**
- Modify: `templates/admin_users.html`

Context (from `app/admin/routes.py:22-33` `list_users` and `:39-102` `create_user`): `user` (the current admin), `users` (all users), `created_user` (a `User` if just created, else `None`), `temp_password` (string if just created, else `None`), optional `error` (string).

URLs (verified):
- Create user form: POST `/admin/users` (NOT `/admin/users/create`).
- Delete user: POST `/admin/users/{user_id}/delete`.
- The backend generates `temp_password` itself via `secrets.token_urlsafe(12)` — the form should NOT send a password field. The template displays the generated password after creation.
- The current admin can't delete themselves: backend already enforces with HTTP 400, but we also gate the button visually via `u.id != user.id`.

- [ ] **Step 10.1: Replace `templates/admin_users.html`**

```html
{% extends "base.html" %}
{% block title %}Пользователи — MediaServer{% endblock %}
{% block content %}

<div class="section-header">
  <h2>Пользователи</h2>
  <button type="button" class="btn" onclick="document.getElementById('create-modal').style.display='flex'">＋ Создать</button>
</div>

{% if error %}<div class="error" style="margin-bottom:1rem;">{{ error }}</div>{% endif %}

{% if created_user %}
<div style="padding:1rem 1.2rem; border-radius:var(--radius); background:rgba(16,185,129,0.1); color:var(--text); margin-bottom:1rem; border:1px solid rgba(16,185,129,0.3);">
  <div style="font-weight:600; margin-bottom:0.4rem;">
    <span class="status-dot success"></span> Создан пользователь <strong>{{ created_user.username }}</strong>
  </div>
  <div style="font-size:0.9rem;">
    Временный пароль: <code style="background:var(--surface-2); padding:0.15rem 0.4rem; border-radius:var(--radius-sm); user-select:all;">{{ temp_password }}</code>
  </div>
  <div style="font-size:0.85rem; color:var(--text-dim); margin-top:0.4rem;">
    Передайте его пользователю — он сменит пароль при первом входе.
  </div>
</div>
{% endif %}

<table class="table">
  <thead>
    <tr>
      <th style="width:1%">ID</th>
      <th>Логин</th>
      <th>Роль</th>
      <th>Создан</th>
      <th style="width:1%"></th>
    </tr>
  </thead>
  <tbody>
    {% for u in users %}
    <tr>
      <td style="color:var(--text-dim); font-size:0.85rem;">{{ u.id }}</td>
      <td>{{ u.username }}</td>
      <td>
        {% if u.is_admin %}
          <span class="badge-inline" style="color:var(--accent); background:var(--accent-soft);">admin</span>
        {% else %}
          <span class="badge-inline">user</span>
        {% endif %}
      </td>
      <td style="color:var(--text-dim); font-size:0.85rem;">{{ u.created_at.strftime('%d.%m.%Y') }}</td>
      <td>
        {% if u.id != user.id %}
        <form method="post" action="/admin/users/{{ u.id }}/delete" style="display:inline" onsubmit="return confirm('Удалить пользователя {{ u.username }}?')">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button type="submit" class="btn btn-ghost" style="padding:0.3rem 0.5rem; font-size:0.9rem; color:var(--danger);" title="Удалить">×</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>

{# Create-user modal — backend generates temp password, we just submit username + is_admin flag #}
<div id="create-modal" class="modal-backdrop" style="display:none;">
  <div class="modal">
    <h2>Новый пользователь</h2>
    <p style="color:var(--text-dim); font-size:0.9rem; margin-bottom:1rem;">
      Сервер сам сгенерирует временный пароль и покажет его после создания.
    </p>
    <form method="post" action="/admin/users" class="stack">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <div>
        <label for="new_username">Логин</label>
        <input id="new_username" name="username" autocomplete="off" required pattern="[a-zA-Z0-9_]{3,32}">
        <small style="color:var(--text-dim); display:block; margin-top:0.3rem;">3–32 символа, латиница / цифры / _</small>
      </div>
      <label style="display:flex; align-items:center; gap:0.5rem; font-weight:normal; color:var(--text);">
        <input type="checkbox" name="is_admin" value="1" style="width:auto;"> Дать права администратора
      </label>
      <div class="actions">
        <button type="button" class="btn btn-secondary" onclick="document.getElementById('create-modal').style.display='none'">Отмена</button>
        <button type="submit" class="btn">Создать</button>
      </div>
    </form>
  </div>
</div>

{% endblock %}
```

- [ ] **Step 10.2: Verify with integration tests**

```bash
pytest tests/integration/test_admin_users.py -v
```

Expected: PASS. (Tests check status codes, username appearance, password mention — all preserved.)

- [ ] **Step 10.3: Manual check**

Open `/admin/users`. Click «＋ Создать», confirm modal appears with username field + checkbox. Submit, see green notification with generated password and new row in table. Try to delete the current admin — button shouldn't show. Delete another user (with confirm), see row gone.

- [ ] **Step 10.4: Commit**

```bash
git add templates/admin_users.html
git -c user.name=dev -c user.email=dev@local commit -m "feat(ui): restyle admin users with table + create-user modal"
```

---

## Task 11: Restyle `admin_health.html`

**Files:**
- Modify: `templates/admin_health.html`

Context (from `app/admin/routes.py:122-162` `health_page`):
- `disk_root` — dict `{"free_gb": float, "total_gb": float, "percent_used": float}` OR `{"error": str}`
- `qb` — dict `{"reachable": True, "active_torrents": int}` OR `{"reachable": False, "error": str}`
- `streams` — list of dicts `{"media_id": int, "user_id": int, "work_dir": str, "alive": bool}`

- [ ] **Step 11.1: Replace `templates/admin_health.html`**

```html
{% extends "base.html" %}
{% block title %}Здоровье сервера — MediaServer{% endblock %}
{% block content %}

<div class="section-header">
  <h2>Здоровье сервера</h2>
</div>

<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:1rem; margin-bottom:2rem;">

  {# Диск #}
  <div class="card">
    <div style="color:var(--text-dim); font-size:0.7rem; letter-spacing:0.1em; text-transform:uppercase; margin-bottom:0.6rem;">Диск</div>
    {% if disk_root.error %}
      <div class="error">{{ disk_root.error }}</div>
    {% else %}
      <div style="font-size:1.8rem; font-weight:700; letter-spacing:-0.02em; margin-bottom:0.4rem;">{{ disk_root.percent_used }}%</div>
      <div class="progress-bar {% if disk_root.percent_used > 85 %}warning{% endif %}" style="margin-bottom:0.5rem;">
        <div style="width: {{ disk_root.percent_used }}%"></div>
      </div>
      <div style="color:var(--text-dim); font-size:0.85rem;">
        Свободно <strong style="color:var(--text);">{{ disk_root.free_gb }} ГБ</strong> из {{ disk_root.total_gb }} ГБ
      </div>
    {% endif %}
  </div>

  {# qBittorrent #}
  <div class="card">
    <div style="color:var(--text-dim); font-size:0.7rem; letter-spacing:0.1em; text-transform:uppercase; margin-bottom:0.6rem;">qBittorrent</div>
    {% if qb.reachable %}
      <div style="font-size:1.2rem; font-weight:700; margin-bottom:0.4rem;">
        <span class="status-dot success"></span> Доступен
      </div>
      <div style="color:var(--text-dim); font-size:0.85rem;">
        Активных торрентов: <strong style="color:var(--text);">{{ qb.active_torrents }}</strong>
      </div>
    {% else %}
      <div style="font-size:1.2rem; font-weight:700; margin-bottom:0.4rem;">
        <span class="status-dot danger"></span> Не отвечает
      </div>
      <div style="color:var(--danger); font-size:0.85rem;">{{ qb.error }}</div>
    {% endif %}
  </div>

  {# Активные стримы (счётчик) #}
  <div class="card">
    <div style="color:var(--text-dim); font-size:0.7rem; letter-spacing:0.1em; text-transform:uppercase; margin-bottom:0.6rem;">Активные стримы</div>
    <div style="font-size:1.8rem; font-weight:700; letter-spacing:-0.02em; margin-bottom:0.4rem;">{{ streams|length }}</div>
    <div style="color:var(--text-dim); font-size:0.85rem;">ffmpeg-процессов</div>
  </div>

</div>

{# Подробности по стримам — таблица только если есть #}
{% if streams %}
<div class="section-header"><h2>Стримы (детально)</h2></div>
<table class="table">
  <thead>
    <tr>
      <th>media_id</th>
      <th>user_id</th>
      <th>work_dir</th>
      <th style="width:1%"></th>
    </tr>
  </thead>
  <tbody>
    {% for s in streams %}
    <tr>
      <td>{{ s.media_id }}</td>
      <td>{{ s.user_id }}</td>
      <td><code style="font-size:0.8rem; color:var(--text-dim);">{{ s.work_dir }}</code></td>
      <td>
        <span class="status-dot {% if s.alive %}success{% else %}dim{% endif %}"></span>
        <span style="font-size:0.85rem; color:var(--text-dim);">{{ "жив" if s.alive else "завершён" }}</span>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

{% endblock %}
```

- [ ] **Step 11.2: Verify with integration tests**

```bash
pytest tests/integration/test_health.py -v
```

Expected: PASS.

- [ ] **Step 11.3: Manual check**

Open `/admin/health`. Confirm 3 cards displayed (Диск / qBittorrent / Стримы); disk progress bar turns yellow if >85% used. If qBittorrent is down, see red dot + error.

- [ ] **Step 11.4: Commit**

```bash
git add templates/admin_health.html
git -c user.name=dev -c user.email=dev@local commit -m "feat(ui): restyle admin health as metric cards grid"
```

---

## Task 12: Final pass + manual walkthrough

**Files:** none initially — fix anything that breaks during the walkthrough.

- [ ] **Step 12.1: Full test suite**

```bash
pytest -v
```

Expected: all PASS. If any test fails, fix the underlying issue (don't skip).

- [ ] **Step 12.2: Manual browser walkthrough at 1280×800**

Start the server:

```bash
uvicorn app.main:app --reload --port 8000
```

Walk through every page:
- `/login` — centered card, dark background, focused username field.
- Log in as admin (use `python -m scripts.create_admin` if no admin yet, or existing one).
- `/change-password` (if first-login) — centered card, hint, two fields.
- `/library` — hero, grid, NEW badges. Empty state if library empty.
- `/media/<id>` — hero, click «Смотреть», video plays. Click «Удалить», modal opens, cancel works.
- `/add-torrent` — magnet form renders, submit goes to `/api/torrents`.
- `/downloads` — table renders, status dots colored, empty state if no downloads.
- `/admin/users` — table renders, «＋ Создать» opens modal, delete confirm works.
- `/admin/health` — metric cards grid, progress bar colors correctly.
- Click logo → goes to /library.
- Click «Выйти» → returns to /login, session cookie cleared.

Note any visual bugs (overlap, contrast, clipping). Fix inline; commit each fix as a separate "fix(ui): ..." commit.

- [ ] **Step 12.3: Mobile check at 375px width**

Use browser devtools responsive mode at 375×667. Walk same pages. Confirm:
- Topnav doesn't overflow (links wrap if needed).
- Hero text readable.
- Poster grid switches to 120px columns.
- Tables horizontally scroll or stack acceptably.

Fix anything broken; commit each fix.

- [ ] **Step 12.4: Run pytest one more time after any fixes**

```bash
pytest -v
```

Expected: PASS.

- [ ] **Step 12.5: Final commit (if any leftover fixes uncommitted)**

```bash
git status
# If anything outstanding:
git add -A
git -c user.name=dev -c user.email=dev@local commit -m "fix(ui): polish from manual walkthrough"
```

- [ ] **Step 12.6: Push branch and verify**

```bash
git log --oneline -15
git push -u origin claude/elastic-wescoff-a07042
```

Expected: ~12–14 commits on the branch (1 spec already there + 1 plan + 11 task commits + optional polish), branch pushed.

---

## Verification

After Task 12, the system should:

1. **Have no 2FA traces.** `grep -rn "totp\|backup_code\|BackupCode\|enroll_2fa\|verify_totp" --include="*.py" --include="*.html" .` returns zero matches.
2. **Pass all tests.** `pytest -v` is green.
3. **Look like the spec.** Every page matches the dark cinematic style from the design mockup.
4. **Work behind Authentik.** Open `https://film.syxarik.ru` (after deploy), log in via Authentik, then via local password, land on the new library page.
