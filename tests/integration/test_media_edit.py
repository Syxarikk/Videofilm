from pathlib import Path
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.models import MediaItem, User, Genre


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _login(client, db_factory, csrf_for):
    with db_factory() as s:
        s.add(User(username="alice",
                   password_hash=hash_password("correct-password-12"),
                   must_change_password=False))
        s.commit()
    r = client.post("/login", data={"username": "alice",
                                     "password": "correct-password-12",
                                     "csrf_token": csrf_for(None)})
    return r.cookies.get("session")


def _create_media(db_factory) -> int:
    with db_factory() as s:
        m = MediaItem(torrent_hash="t1", title="Old title", file_path=str(SAMPLE),
                      size_bytes=1, kind="movie")
        s.add(m); s.commit(); s.refresh(m)
        return m.id


def test_edit_form_returns_modal(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.get(f"/api/media/{mid}/edit-form", cookies={"session": cookie})
    assert r.status_code == 200
    assert 'name="title"' in r.text
    assert 'name="description"' in r.text


def test_edit_updates_fields(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.post(
        f"/api/media/{mid}/edit",
        data={"title": "New", "description": "X", "kind": "movie",
              "genres": "Драма,Боевик",
              "poster_url": "https://x/p.jpg",
              "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("HX-Redirect") == f"/media/{mid}"

    with db_factory() as s:
        m = s.get(MediaItem, mid)
        assert m.title == "New"
        assert m.description == "X"
        assert m.match_status == "manual"
        assert m.match_source == "manual"
        names = {g.name for g in m.genres}
        assert names == {"Драма", "Боевик"}
        assert m.poster_url == "https://x/p.jpg"


def test_edit_creates_new_genres(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.post(
        f"/api/media/{mid}/edit",
        data={"title": "T", "description": "", "kind": "movie",
              "genres": "Новый жанр",
              "poster_url": "",
              "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code in (200, 204), r.text
    with db_factory() as s:
        g = s.scalars(select(Genre).where(Genre.name == "Новый жанр")).one()
        assert g is not None


def test_edit_requires_csrf(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.post(
        f"/api/media/{mid}/edit",
        data={"title": "X", "description": "", "kind": "movie", "genres": "",
              "poster_url": "", "csrf_token": "bad-token"},
        cookies={"session": cookie},
    )
    assert r.status_code == 400  # CSRF returns 400 (см. app/csrf.py)
