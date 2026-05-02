import pyotp

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret, _derive_key
from app.models import User


def setup_logged_in(client, db_factory):
    secret = pyotp.random_base32()
    with db_factory() as s:
        u = User(
            username="alice", password_hash=hash_password("correct-password-12"),
            must_change_password=False, totp_enabled=True,
            totp_secret_encrypted=encrypt_secret(secret, _derive_key("x" * 64)),
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
