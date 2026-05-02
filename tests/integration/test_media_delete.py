from pathlib import Path

import httpx
import pyotp
import pytest
import respx
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret, _derive_key
from app.models import MediaItem, User, WatchProgress


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


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
def test_delete_media_removes_db_row_and_calls_qbittorrent(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        m = MediaItem(torrent_hash="h-to-del", title="X", file_path="/x/y.mkv", size_bytes=1)
        s.add(m); s.commit(); s.refresh(m)
        mid = m.id

    respx.post("http://127.0.0.1:8080/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    delete_route = respx.post("http://127.0.0.1:8080/api/v2/torrents/delete").mock(
        return_value=httpx.Response(200)
    )

    r = client.post(
        f"/api/media/{mid}/delete",
        data={"csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/library"
    assert delete_route.called

    with db_factory() as s:
        gone = s.scalars(select(MediaItem).where(MediaItem.id == mid)).first()
        assert gone is None


def test_delete_unknown_media_returns_404(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.post(
        "/api/media/9999/delete",
        data={"csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 404


@respx.mock
def test_delete_cascades_to_watch_progress(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        u = s.scalars(select(User).where(User.username == "alice")).one()
        m = MediaItem(torrent_hash="h", title="X", file_path="/x.mkv", size_bytes=1)
        s.add(m); s.commit(); s.refresh(m)
        s.add(WatchProgress(user_id=u.id, media_id=m.id, position_seconds=42))
        s.commit()
        mid = m.id

    respx.post("http://127.0.0.1:8080/api/v2/auth/login").mock(return_value=httpx.Response(200, text="Ok."))
    respx.post("http://127.0.0.1:8080/api/v2/torrents/delete").mock(return_value=httpx.Response(200))

    client.post(f"/api/media/{mid}/delete", data={"csrf_token": csrf_for(cookie)}, cookies={"session": cookie})

    with db_factory() as s:
        wp = s.scalars(select(WatchProgress).where(WatchProgress.media_id == mid)).first()
        assert wp is None  # CASCADE сработал благодаря Task 1
