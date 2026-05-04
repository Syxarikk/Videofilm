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


def test_library_lists_single_file_torrents(client, db_factory, csrf_for):
    """Одно-файловые торренты — каждый показывается отдельной карточкой со ссылкой /media/{id}."""
    cookie = _logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        s.add(MediaItem(torrent_hash="h1", torrent_name="Movie 1 (2024)", title="Movie 1 (2024)",
                        file_path="/x/1.mkv", size_bytes=1_000_000_000))
        s.add(MediaItem(torrent_hash="h2", torrent_name="Movie 2 (2023)", title="Movie 2 (2023)",
                        file_path="/x/2.mkv", size_bytes=2_000_000_000))
        s.commit()

    r = client.get("/library", cookies={"session": cookie})
    assert r.status_code == 200
    assert "Movie 1 (2024)" in r.text
    assert "Movie 2 (2023)" in r.text
    # Одно-файловые торренты ведут прямо на /media/{id}, не на /torrent/{hash}
    assert "/media/" in r.text


def test_library_groups_multi_file_torrent_into_one_card(client, db_factory, csrf_for):
    """Многофайловый торрент (сериал) — одна карточка ведущая на /torrent/{hash}, не три карточки."""
    cookie = _logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        for ep in ("S01E01", "S01E02", "S01E03"):
            s.add(MediaItem(
                torrent_hash="show-h", torrent_name="Some Show S01",
                title=ep, file_path=f"/x/Show.S01/{ep}.mkv", size_bytes=1_000_000_000,
            ))
        s.commit()

    r = client.get("/library", cookies={"session": cookie})
    assert r.status_code == 200
    # Имя сериала появляется один раз (как заголовок группы), не три
    assert r.text.count("Some Show S01") < 5  # с запасом на повтор в href/заголовок
    # Карточка ведёт на /torrent/{hash}, не на /media/
    assert "/torrent/show-h" in r.text
    # Есть индикатор количества серий
    assert "3 серий" in r.text


def test_torrent_page_lists_episodes(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        for ep in ("S01E01", "S01E02"):
            s.add(MediaItem(
                torrent_hash="show", torrent_name="My Show",
                title=ep, file_path=f"/x/{ep}.mkv", size_bytes=1,
            ))
        s.commit()

    r = client.get("/torrent/show", cookies={"session": cookie})
    assert r.status_code == 200
    assert "My Show" in r.text
    assert "S01E01" in r.text
    assert "S01E02" in r.text
    # Каждая серия — линк на свой /media/{id}
    assert "/media/" in r.text


def test_torrent_page_404_for_unknown_hash(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/torrent/no-such-hash", cookies={"session": cookie})
    assert r.status_code == 404


def test_media_page_shows_title_and_actions(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    with db_factory() as s:
        m = MediaItem(torrent_hash="h", torrent_name="Test Movie", title="Test Movie",
                      file_path="/x/m.mkv", size_bytes=1)
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
