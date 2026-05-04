from pathlib import Path

import pytest

from app.auth.passwords import hash_password
from app.models import MediaItem, User


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _logged_in(client, db_factory, csrf_for):
    with db_factory() as s:
        s.add(User(
            username="alice", password_hash=hash_password("correct-password-12"),
            must_change_password=False,
        ))
        s.commit()
    r = client.post("/login", data={
        "username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)
    })
    return r.cookies.get("session")


def _create_media(db_factory, sample: Path) -> int:
    with db_factory() as s:
        m = MediaItem(torrent_hash="h", title="Test", file_path=str(sample), size_bytes=sample.stat().st_size)
        s.add(m); s.commit(); s.refresh(m)
        return m.id


def test_download_returns_full_file(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/download/{mid}", cookies={"session": cookie})
    assert r.status_code == 200
    assert int(r.headers["content-length"]) == SAMPLE.stat().st_size
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.content[:4] == SAMPLE.read_bytes()[:4]


def test_download_supports_range(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(
        f"/api/download/{mid}",
        headers={"Range": "bytes=0-99"},
        cookies={"session": cookie},
    )
    assert r.status_code == 206
    assert r.headers["content-range"].startswith("bytes 0-99/")
    assert int(r.headers["content-length"]) == 100


def test_download_unauth_returns_401(client, db_factory):
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/download/{mid}")
    assert r.status_code == 401


def test_download_404_for_unknown(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/api/download/9999", cookies={"session": cookie})
    assert r.status_code == 404
