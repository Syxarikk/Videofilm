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


def _create_series_three_eps(db_factory) -> int:
    with db_factory() as s:
        sr = MediaItem(torrent_hash="t", title="Show",
                        file_path="/x", size_bytes=1, kind="series")
        s.add(sr); s.flush()
        for season, ep_n, title in [(1,1,"Pilot"), (1,2,"Second"), (2,1,"S2E1")]:
            e = Episode(series_id=sr.id, season=season, episode=ep_n,
                         title=title, file_path=str(SAMPLE),
                         size_bytes=SAMPLE.stat().st_size, duration_seconds=600)
            s.add(e); s.flush()
        s.commit()
        return sr.id


def test_episode_page_renders(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid = _create_series_three_eps(db_factory)
    r = client.get(f"/media/{sid}/s1/e1", cookies={"session": cookie})
    assert r.status_code == 200
    assert "Pilot" in r.text


def test_episode_page_404_for_unknown(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid = _create_series_three_eps(db_factory)
    r = client.get(f"/media/{sid}/s1/e99", cookies={"session": cookie})
    assert r.status_code == 404


def test_episode_page_next_within_season(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid = _create_series_three_eps(db_factory)
    r = client.get(f"/media/{sid}/s1/e1", cookies={"session": cookie})
    assert f"/media/{sid}/s1/e2" in r.text


def test_episode_page_next_across_seasons(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid = _create_series_three_eps(db_factory)
    r = client.get(f"/media/{sid}/s1/e2", cookies={"session": cookie})
    assert f"/media/{sid}/s2/e1" in r.text


def test_episode_page_no_next_at_last(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid = _create_series_three_eps(db_factory)
    r = client.get(f"/media/{sid}/s2/e1", cookies={"session": cookie})
    # Кнопка "Следующий" должна быть disabled (нет ссылки на следующий эпизод)
    assert "disabled" in r.text
