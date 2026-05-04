from app.auth.passwords import hash_password
from app.models import User


def make_user(db_factory, *, username="alice", password="correct-password-12", **kw):
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
    cookie = r.cookies.get("session")
    assert cookie

    r2 = client.get("/library", cookies={"session": cookie})
    assert r2.status_code == 200


def test_2fa_routes_are_removed(client):
    assert client.get("/verify-totp").status_code == 404
    assert client.get("/enroll-2fa").status_code == 404


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

    r3 = client.get("/library", cookies={"session": cookie})
    assert r3.status_code in (303, 401)


def test_logout_set_cookie_has_security_flags(client, db_factory, csrf_for):
    make_user(db_factory)
    r = client.post("/login", data={"username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)})
    cookie = r.cookies.get("session")
    client.cookies.set("session", cookie)

    r2 = client.post("/logout", data={"csrf_token": csrf_for(cookie)})
    sc = r2.headers.get("set-cookie", "")
    assert "session=" in sc
    assert "HttpOnly" in sc or "httponly" in sc.lower()
    assert "Secure" in sc or "secure" in sc.lower()
    assert "SameSite=Strict" in sc or "samesite=strict" in sc.lower()
