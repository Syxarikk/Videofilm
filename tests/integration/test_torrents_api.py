import httpx
import pyotp
import pytest
import respx

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret, _derive_key
from app.deps import get_qbittorrent_client
from app.models import User


def _logged_in(client, db_factory, csrf_for):
    secret = pyotp.random_base32()
    with db_factory() as s:
        s.add(User(
            username="alice", password_hash=hash_password("correct-password-12"),
            must_change_password=False, totp_enabled=True,
            totp_secret_encrypted=encrypt_secret(secret, _derive_key("x" * 64)),
        ))
        s.commit()
    r = client.post("/login", data={
        "username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)
    })
    cookie = r.cookies.get("session")
    code = pyotp.TOTP(secret).now()
    client.post("/verify-totp", data={"code": code, "csrf_token": csrf_for(cookie)},
                cookies={"session": cookie})
    return cookie


@respx.mock
def test_add_torrent_calls_qbittorrent_and_redirects(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    respx.post("http://127.0.0.1:8080/api/v2/auth/login").mock(
        return_value=httpx.Response(200, text="Ok.")
    )
    add_route = respx.post("http://127.0.0.1:8080/api/v2/torrents/add").mock(
        return_value=httpx.Response(200)
    )

    r = client.post(
        "/api/torrents",
        data={"magnet": "magnet:?xt=urn:btih:abc1234567890123456789012345678901234567", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/downloads"
    assert add_route.called


def test_invalid_magnet_returns_400(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.post(
        "/api/torrents",
        data={"magnet": "not-a-magnet", "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 400


def test_unauthenticated_redirect(client, csrf_for):
    r = client.post(
        "/api/torrents",
        data={"magnet": "magnet:?xt=urn:btih:abc", "csrf_token": csrf_for(None)},
    )
    # /api/* префикс — middleware не редиректит, отдаёт 401
    assert r.status_code == 401


def test_add_torrent_page_renders_for_logged_in_user(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/add-torrent", cookies={"session": cookie})
    assert r.status_code == 200
    assert "magnet" in r.text.lower()


def test_add_torrent_page_unauth_redirects(client):
    r = client.get("/add-torrent")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


@respx.mock
def test_status_returns_active_torrents(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    respx.post("http://127.0.0.1:8080/api/v2/auth/login").mock(
        return_value=httpx.Response(200, text="Ok.")
    )
    respx.get("http://127.0.0.1:8080/api/v2/torrents/info").mock(return_value=httpx.Response(200, json=[
        {"hash": "abc", "name": "Movie.mkv", "progress": 0.42, "dlspeed": 1500000,
         "state": "downloading", "size": 4_000_000_000, "save_path": "/x", "content_path": "/x/Movie.mkv", "eta": 3600}
    ]))
    r = client.get("/api/torrents/status", cookies={"session": cookie})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["hash"] == "abc"
    assert data[0]["progress_percent"] == 42
    assert data[0]["speed_human"].endswith("/s")


@respx.mock
def test_status_handles_qbittorrent_down(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    respx.post("http://127.0.0.1:8080/api/v2/auth/login").mock(
        return_value=httpx.Response(500)
    )
    r = client.get("/api/torrents/status", cookies={"session": cookie})
    assert r.status_code == 503
