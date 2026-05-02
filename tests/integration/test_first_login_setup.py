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
    # Без сессии: либо 401 от dependency, либо 303 от AuthRedirectMiddleware (Task 19).
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


def test_change_password_success_redirects_to_enroll_2fa(client, db_factory, csrf_for):
    uid = make_fresh_user(db_factory)
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
    assert r2.headers["location"] == "/enroll-2fa"

    # Старый пароль больше не работает.
    r3 = client.post(
        "/login",
        data={"username": "newbie", "password": "temp-password-99", "csrf_token": csrf_for(None)},
    )
    assert r3.status_code == 401


import pyotp


def _full_setup_until_enroll(client, db_factory, csrf_for):
    make_fresh_user(db_factory)
    r1 = client.post(
        "/login",
        data={"username": "newbie", "password": "temp-password-99", "csrf_token": csrf_for(None)},
    )
    cookie = r1.cookies.get("session")
    client.post(
        "/change-password",
        data={"new_password": "new-strong-password-1", "confirm": "new-strong-password-1", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    return cookie


def test_enroll_2fa_get_returns_qr_and_secret(client, db_factory, csrf_for):
    cookie = _full_setup_until_enroll(client, db_factory, csrf_for)
    r = client.get("/enroll-2fa", cookies={"session": cookie})
    assert r.status_code == 200
    # На странице должны быть отрисован QR (img src=data:image/png;base64,...) и backup-коды.
    assert "data:image/png;base64," in r.text
    assert r.text.count("<li>") >= 10  # 10 backup-кодов как минимум


def test_enroll_2fa_post_wrong_code_keeps_user_unenrolled(client, db_factory, csrf_for):
    cookie = _full_setup_until_enroll(client, db_factory, csrf_for)
    client.get("/enroll-2fa", cookies={"session": cookie})
    r = client.post(
        "/enroll-2fa",
        data={"code": "000000", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 400


def test_enroll_2fa_post_correct_code_completes_setup(client, db_factory, csrf_for):
    from sqlalchemy import select
    from app.auth.totp import decrypt_secret, _derive_key
    from app.models import BackupCode, User

    cookie = _full_setup_until_enroll(client, db_factory, csrf_for)
    client.get("/enroll-2fa", cookies={"session": cookie})

    # Достаём секрет, который сервер сохранил во время GET.
    with db_factory() as s:
        user = s.execute(select(User).where(User.username == "newbie")).scalar_one()
        secret = decrypt_secret(user.totp_secret_encrypted, _derive_key("x" * 64))

    code = pyotp.TOTP(secret).now()
    r = client.post(
        "/enroll-2fa",
        data={"code": code, "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/library"

    with db_factory() as s:
        user = s.execute(select(User).where(User.username == "newbie")).scalar_one()
        assert user.totp_enabled is True
        codes = s.scalars(select(BackupCode).where(BackupCode.user_id == user.id)).all()
        assert len(codes) == 10
