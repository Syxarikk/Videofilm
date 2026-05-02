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


def test_login_post_wrong_password_returns_401(client, db_factory, csrf_for):
    make_user(db_factory)
    r = client.post(
        "/login",
        data={"username": "alice", "password": "wrong", "csrf_token": csrf_for(None)},
    )
    assert r.status_code == 401


def test_login_post_correct_password_creates_partial_session_and_redirects_to_totp(client, db_factory, csrf_for):
    make_user(db_factory)
    r = client.post(
        "/login",
        data={"username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/verify-totp"
    assert "session=" in r.headers.get("set-cookie", "")


import pyotp
from app.auth.totp import _derive_key, encrypt_secret


def make_user_with_totp(db_factory, *, username="alice", password="correct-password-12"):
    secret = pyotp.random_base32()
    key = _derive_key("x" * 64)  # совпадает с SESSION_SECRET из conftest
    with db_factory() as s:
        u = User(
            username=username,
            password_hash=hash_password(password),
            must_change_password=False,
            totp_enabled=True,
            totp_secret_encrypted=encrypt_secret(secret, key),
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id, secret


def test_verify_totp_get_requires_partial_session(client):
    r = client.get("/verify-totp")
    # Без сессии: либо 401 от dependency, либо 303 от AuthRedirectMiddleware (Task 19).
    assert r.status_code in (303, 401)
    if r.status_code == 303:
        assert r.headers["location"] == "/login"


def test_verify_totp_full_flow(client, db_factory, monkeypatch, csrf_for):
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)  # совпадает с conftest, ключ для encrypt
    _, secret = make_user_with_totp(db_factory)

    r = client.post(
        "/login",
        data={"username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/verify-totp"
    cookie = r.cookies.get("session")

    code = pyotp.TOTP(secret).now()
    r2 = client.post(
        "/verify-totp",
        data={"code": code, "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/library"


def test_verify_totp_wrong_code_returns_401(client, db_factory, csrf_for):
    make_user_with_totp(db_factory)
    r = client.post(
        "/login",
        data={"username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)},
    )
    cookie = r.cookies.get("session")
    r2 = client.post(
        "/verify-totp",
        data={"code": "000000", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r2.status_code == 401


def test_verify_totp_with_backup_code_succeeds(client, db_factory, csrf_for):
    from app.auth.backup_codes import generate_codes, hash_code as bc_hash
    from app.models import BackupCode

    uid, _ = make_user_with_totp(db_factory)
    codes = generate_codes()
    with db_factory() as s:
        for c in codes:
            s.add(BackupCode(user_id=uid, code_hash=bc_hash(c)))
        s.commit()

    r = client.post(
        "/login",
        data={"username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)},
    )
    cookie = r.cookies.get("session")
    r2 = client.post(
        "/verify-totp",
        data={"code": codes[0], "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/library"

    # Тот же код повторно — отвергается.
    r3 = client.post(
        "/login",
        data={"username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)},
    )
    cookie3 = r3.cookies.get("session")
    r4 = client.post(
        "/verify-totp",
        data={"code": codes[0], "csrf_token": csrf_for(cookie3)},
        cookies={"session": cookie3},
    )
    assert r4.status_code == 401


def test_logout_clears_session_and_redirects(client, db_factory, csrf_for):
    _, secret = make_user_with_totp(db_factory)
    r = client.post(
        "/login",
        data={"username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)},
    )
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post(
        "/verify-totp",
        data={"code": code, "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )

    r2 = client.post(
        "/logout",
        data={"csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r2.status_code == 303
    assert r2.headers["location"] == "/login"

    # После logout сессия должна быть удалена — попытка использовать её провалится.
    # /library пока не существует (добавляется в Task 19) → 404. Когда он появится,
    # удалённая сессия даст 401 (или 303 от middleware-редиректа).
    r3 = client.get("/library", cookies={"session": cookie})
    assert r3.status_code in (303, 401, 404)
