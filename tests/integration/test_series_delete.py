from pathlib import Path
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.models import Episode, EpisodeWatchProgress, MediaItem, User


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _login(client, db_factory, csrf_for):
    with db_factory() as s:
        s.add(User(username="alice",
                   password_hash=hash_password("correct-password-12"),
                   must_change_password=False, is_admin=True))
        s.commit()
    r = client.post("/login", data={"username": "alice",
                                     "password": "correct-password-12",
                                     "csrf_token": csrf_for(None)})
    return r.cookies.get("session")


def test_delete_series_cascades_episodes(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    with db_factory() as s:
        u = s.scalars(select(User)).one()
        sr = MediaItem(torrent_hash="t", title="Show",
                        file_path=str(SAMPLE), size_bytes=1, kind="series")
        s.add(sr); s.flush()
        e1 = Episode(series_id=sr.id, season=1, episode=1,
                      file_path=str(SAMPLE),
                      size_bytes=SAMPLE.stat().st_size)
        s.add(e1); s.flush()
        s.add(EpisodeWatchProgress(user_id=u.id, episode_id=e1.id,
                                     position_seconds=10))
        s.commit()
        sid = sr.id

    r = client.post(f"/api/media/{sid}/delete",
                    data={"csrf_token": csrf_for(cookie)},
                    cookies={"session": cookie})
    assert r.status_code in (303, 200)

    with db_factory() as s:
        assert s.get(MediaItem, sid) is None
        assert len(s.scalars(select(Episode)).all()) == 0
        assert len(s.scalars(select(EpisodeWatchProgress)).all()) == 0
