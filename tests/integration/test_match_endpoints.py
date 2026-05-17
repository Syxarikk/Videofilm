from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from app.auth.passwords import hash_password
from app.models import MediaItem, User
from app.metadata.types import MetadataMatch


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
        m = MediaItem(torrent_hash="t", title="Inception",
                      file_path=str(SAMPLE), size_bytes=1, kind="movie", year=2010)
        s.add(m); s.commit(); s.refresh(m)
        return m.id


def test_search_form_returns_dialog(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.get(f"/api/media/{mid}/match/search-form", cookies={"session": cookie})
    assert r.status_code == 200
    assert 'name="query"' in r.text


@patch("app.library.routes.get_tmdb_client")
@patch("app.library.routes.get_kinopoisk_client")
def test_search_returns_combined_results(mock_kp_factory, mock_tmdb_factory, client, db_factory, csrf_for):
    mock_tmdb = mock_tmdb_factory.return_value
    mock_tmdb.search.return_value = [
        {"id": 27205, "title": "Inception", "release_date": "2010-07-15",
         "poster_path": "/p.jpg"},
    ]
    mock_kp_factory.return_value = None

    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.post(
        f"/api/media/{mid}/match/search",
        data={"query": "Inception", "year": "2010", "kind": "movie",
              "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 200
    assert "Inception" in r.text
    assert "TMDB" in r.text


@patch("app.library.routes.get_tmdb_client")
@patch("app.library.routes.get_kinopoisk_client")
def test_apply_writes_metadata(mock_kp_factory, mock_tmdb_factory, client, db_factory, csrf_for):
    mock_tmdb = mock_tmdb_factory.return_value
    mock_tmdb.get_movie.return_value = MetadataMatch(
        source="tmdb", external_id=27205, title="Начало", year=2010,
        kind="movie", description="Описание.", poster_url="https://example/p.jpg",
        genres=["Боевик", "Драма"], score=1.0,
    )
    mock_kp_factory.return_value = None

    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.post(
        f"/api/media/{mid}/match/apply",
        data={"source": "tmdb", "external_id": "27205",
              "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("HX-Redirect") == f"/media/{mid}"
    with db_factory() as s:
        m = s.get(MediaItem, mid)
        assert m.title == "Начало"
        assert m.tmdb_id == 27205
        assert m.match_status == "matched"
        names = {g.name for g in m.genres}
        assert names == {"Боевик", "Драма"}
