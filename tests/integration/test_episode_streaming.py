import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.models import Episode, EpisodeWatchProgress, MediaItem, User
from app.streaming.stream_registry import get_registry


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _logged_in(client, db_factory, csrf_for):
    with db_factory() as s:
        s.add(User(username="alice",
                   password_hash=hash_password("correct-password-12"),
                   must_change_password=False))
        s.commit()
    r = client.post("/login", data={
        "username": "alice", "password": "correct-password-12",
        "csrf_token": csrf_for(None),
    })
    return r.cookies.get("session")


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    reg = get_registry()
    for h in list(reg.all_streams()):
        if h.process is not None:
            from app.streaming.ffmpeg_runner import kill
            kill(h.process)
        reg.unregister(h.target_id, h.user_id)


def _create_series_with_episode(db_factory) -> tuple[int, int]:
    with db_factory() as s:
        series = MediaItem(
            torrent_hash="ts", title="Show", file_path="/x",
            size_bytes=1, kind="series",
        )
        s.add(series); s.flush()
        ep = Episode(
            series_id=series.id, season=1, episode=1,
            file_path=str(SAMPLE), size_bytes=SAMPLE.stat().st_size,
        )
        s.add(ep); s.commit(); s.refresh(series); s.refresh(ep)
        return series.id, ep.id


def test_episode_master_starts_ffmpeg(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    _, eid = _create_series_with_episode(db_factory)

    r = client.get(f"/api/stream/episode/{eid}/master.m3u8",
                   cookies={"session": cookie})
    assert r.status_code == 200
    assert "#EXTM3U" in r.text


def test_episode_variant_playlist(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    _, eid = _create_series_with_episode(db_factory)
    client.get(f"/api/stream/episode/{eid}/master.m3u8",
               cookies={"session": cookie})
    r = client.get(f"/api/stream/episode/{eid}/v0/playlist.m3u8",
                   cookies={"session": cookie})
    assert r.status_code == 200
    assert "#EXTM3U" in r.text


def test_episode_segment(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    _, eid = _create_series_with_episode(db_factory)
    client.get(f"/api/stream/episode/{eid}/master.m3u8",
               cookies={"session": cookie})
    for _ in range(150):
        r = client.get(f"/api/stream/episode/{eid}/v0/playlist.m3u8",
                       cookies={"session": cookie})
        if "seg_" in r.text:
            break
        time.sleep(0.1)
    seg = next((l for l in r.text.splitlines() if l.startswith("seg_")), None)
    assert seg
    r2 = client.get(f"/api/stream/episode/{eid}/v0/{seg}",
                    cookies={"session": cookie})
    assert r2.status_code == 200


def test_episode_master_404_for_unknown_id(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/api/stream/episode/99999/master.m3u8",
                   cookies={"session": cookie})
    assert r.status_code == 404


def test_episode_progress_upserts(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    _, eid = _create_series_with_episode(db_factory)
    r = client.post("/api/progress/episode",
                    json={"episode_id": eid, "position_seconds": 42},
                    cookies={"session": cookie})
    assert r.status_code == 204
    with db_factory() as s:
        wp = s.scalars(select(EpisodeWatchProgress).where(
            EpisodeWatchProgress.episode_id == eid)).one()
        assert wp.position_seconds == 42

    r = client.post("/api/progress/episode",
                    json={"episode_id": eid, "position_seconds": 100,
                          "audio_track_index": 1},
                    cookies={"session": cookie})
    assert r.status_code == 204
    with db_factory() as s:
        wp = s.scalars(select(EpisodeWatchProgress).where(
            EpisodeWatchProgress.episode_id == eid)).one()
        assert wp.position_seconds == 100
        assert wp.audio_track_index == 1


def test_episode_progress_unauth(client, db_factory):
    _, eid = _create_series_with_episode(db_factory)
    r = client.post("/api/progress/episode",
                    json={"episode_id": eid, "position_seconds": 1})
    assert r.status_code == 401
