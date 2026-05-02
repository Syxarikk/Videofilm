import pyotp
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.auth.totp import encrypt_secret, _derive_key
from app.models import MediaItem, User


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


def test_library_lists_media_items(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        s.add(MediaItem(torrent_hash="h1", title="Movie 1 (2024)", file_path="/x/1.mkv", size_bytes=1_000_000_000))
        s.add(MediaItem(torrent_hash="h2", title="Movie 2 (2023)", file_path="/x/2.mkv", size_bytes=2_000_000_000))
        s.commit()

    r = client.get("/library", cookies={"session": cookie})
    assert r.status_code == 200
    assert "Movie 1 (2024)" in r.text
    assert "Movie 2 (2023)" in r.text
    # Каждый item — ссылка на /media/{id}
    assert "/media/" in r.text
