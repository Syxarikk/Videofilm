from sqlalchemy import select

from app.auth.passwords import hash_password
from app.models import MediaItem, User


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


def test_media_page_shows_title_and_actions(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        m = MediaItem(torrent_hash="h", title="Test Movie", file_path="/x/m.mkv", size_bytes=1)
        s.add(m); s.commit(); s.refresh(m)
        mid = m.id

    r = client.get(f"/media/{mid}", cookies={"session": cookie})
    assert r.status_code == 200
    assert "Test Movie" in r.text
    # Кнопки скачать/удалить упоминаются (хотя сами эндпойнты в Tasks 22-23)
    assert "/api/download/" in r.text
    assert "delete" in r.text.lower() or "удалить" in r.text.lower()


def test_media_page_404_for_missing_id(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/media/9999", cookies={"session": cookie})
    assert r.status_code == 404
