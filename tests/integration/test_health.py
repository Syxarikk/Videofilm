import httpx
import pytest
import respx

from app.auth.passwords import hash_password
from app.models import User


def _admin_logged_in(client, db_factory, csrf_for):
    with db_factory() as s:
        s.add(User(
            username="root", password_hash=hash_password("admin-password-12"),
            must_change_password=False, is_admin=True,
        ))
        s.commit()
    r = client.post("/login", data={
        "username": "root", "password": "admin-password-12", "csrf_token": csrf_for(None)
    })
    cookie = r.cookies.get("session")
    return cookie


def test_admin_health_requires_admin(client):
    r = client.get("/admin/health")
    assert r.status_code in (303, 401)


@respx.mock
def test_admin_health_renders_for_admin(client, db_factory, csrf_for):
    cookie = _admin_logged_in(client, db_factory, csrf_for)
    respx.post("http://127.0.0.1:8080/api/v2/auth/login").mock(
        return_value=httpx.Response(200, text="Ok.")
    )
    respx.get("http://127.0.0.1:8080/api/v2/torrents/info").mock(
        return_value=httpx.Response(200, json=[])
    )
    r = client.get("/admin/health", cookies={"session": cookie})
    assert r.status_code == 200
    # Страница содержит ключевые секции
    assert "qbittorrent" in r.text.lower() or "qbt" in r.text.lower()
    assert "диск" in r.text.lower() or "disk" in r.text.lower()
    assert "стрим" in r.text.lower() or "stream" in r.text.lower()
