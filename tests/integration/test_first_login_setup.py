from app.auth.passwords import hash_password
from app.models import User


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


def test_change_password_success_redirects_to_library(client, db_factory, csrf_for):
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

    r3 = client.get("/library", cookies={"session": cookie})
    assert r3.status_code == 200

    r4 = client.post(
        "/login",
        data={"username": "newbie", "password": "temp-password-99", "csrf_token": csrf_for(None)},
    )
    assert r4.status_code == 401
