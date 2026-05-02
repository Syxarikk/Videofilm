import pyotp

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret, _derive_key
from app.models import User


def make_admin_logged_in(client, db_factory):
    secret = pyotp.random_base32()
    with db_factory() as s:
        u = User(
            username="root", password_hash=hash_password("admin-password-12"),
            must_change_password=False, totp_enabled=True, is_admin=True,
            totp_secret_encrypted=encrypt_secret(secret, _derive_key("x" * 64)),
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
            totp_secret_encrypted=encrypt_secret(secret, _derive_key("x" * 64)),
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
