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
