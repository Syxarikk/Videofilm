from pathlib import Path

from app.auth.passwords import hash_password
from app.models import Episode, MediaItem, User


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


def _create_series(db_factory) -> tuple[int, list[int]]:
    with db_factory() as s:
        sr = MediaItem(torrent_hash="t", title="Show",
                        file_path="/x", size_bytes=1, kind="series",
                        year=2020, description="A show.")
        s.add(sr); s.flush()
        eps = []
        for season, ep_n, title in [(1,1,"Pilot"), (1,2,"Second"), (2,1,"S2E1")]:
            e = Episode(series_id=sr.id, season=season, episode=ep_n,
                         title=title, file_path=str(SAMPLE),
                         size_bytes=SAMPLE.stat().st_size, duration_seconds=600)
            s.add(e); s.flush(); eps.append(e.id)
        s.commit()
        return sr.id, eps


def test_series_page_shows_seasons_and_episodes(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid, _ = _create_series(db_factory)
    r = client.get(f"/media/{sid}", cookies={"session": cookie})
    assert r.status_code == 200
    assert "Show" in r.text
    assert "Сезон 1" in r.text
    assert "Pilot" in r.text


def test_series_page_season_selector(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid, _ = _create_series(db_factory)
    r = client.get(f"/media/{sid}?season=2", cookies={"session": cookie})
    assert r.status_code == 200
    assert "S2E1" in r.text


def test_series_page_movie_uses_movie_template(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    with db_factory() as s:
        m = MediaItem(torrent_hash="m", title="Movie", file_path=str(SAMPLE),
                       size_bytes=SAMPLE.stat().st_size, kind="movie")
        s.add(m); s.commit(); s.refresh(m); mid = m.id
    r = client.get(f"/media/{mid}", cookies={"session": cookie})
    assert r.status_code == 200
    assert "Скачать оригинал" in r.text  # movie-only button
