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
