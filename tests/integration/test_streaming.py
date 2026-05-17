import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.models import MediaItem, User, WatchProgress
from app.streaming.stream_registry import get_registry


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


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    reg = get_registry()
    for h in list(reg.all_streams()):
        if h.process is not None:
            from app.streaming.ffmpeg_runner import kill
            kill(h.process)
        reg.unregister(h.media_id, h.user_id)


def _create_media(db_factory, sample: Path) -> int:
    with db_factory() as s:
        m = MediaItem(torrent_hash="h", title="Test", file_path=str(sample),
                      size_bytes=sample.stat().st_size)
        s.add(m); s.commit(); s.refresh(m)
        return m.id


def test_master_starts_ffmpeg_and_returns_m3u8(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    r = client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert "#EXTM3U" in r.text


def test_legacy_playlist_redirects_to_master(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    r = client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 301
    assert r.headers["location"].endswith(f"/api/stream/{mid}/master.m3u8")


def test_master_unauthenticated_returns_401(client, db_factory):
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/stream/{mid}/master.m3u8")
    assert r.status_code == 401


def test_master_404_for_unknown_media(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/api/stream/9999/master.m3u8", cookies={"session": cookie})
    assert r.status_code == 404


def test_variant_playlist_returned(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})
    assert r.status_code == 200

    r2 = client.get(f"/api/stream/{mid}/v0/playlist.m3u8", cookies={"session": cookie})
    assert r2.status_code == 200
    assert "#EXTM3U" in r2.text


def test_segment_in_subdir(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})

    for _ in range(150):
        r = client.get(f"/api/stream/{mid}/v0/playlist.m3u8", cookies={"session": cookie})
        if "seg_" in r.text:
            break
        time.sleep(0.1)

    seg_name = next((line for line in r.text.splitlines() if line.startswith("seg_")), None)
    assert seg_name
    r2 = client.get(f"/api/stream/{mid}/v0/{seg_name}", cookies={"session": cookie})
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "video/mp2t"


def test_variant_unknown_index_404(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})
    r = client.get(f"/api/stream/{mid}/v99/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 404


def test_progress_endpoint_upserts_watch_progress(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    r = client.post(
        "/api/progress",
        json={"media_id": mid, "position_seconds": 42},
        cookies={"session": cookie},
    )
    assert r.status_code == 204

    with db_factory() as s:
        wp = s.scalars(select(WatchProgress).where(WatchProgress.media_id == mid)).one()
        assert wp.position_seconds == 42


def test_progress_endpoint_accepts_audio_track_index(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    r = client.post(
        "/api/progress",
        json={"media_id": mid, "position_seconds": 42, "audio_track_index": 1},
        cookies={"session": cookie},
    )
    assert r.status_code == 204
    with db_factory() as s:
        wp = s.scalars(select(WatchProgress).where(WatchProgress.media_id == mid)).one()
        assert wp.audio_track_index == 1


def test_progress_unauth_returns_401(client, db_factory):
    mid = _create_media(db_factory, SAMPLE)
    r = client.post("/api/progress", json={"media_id": mid, "position_seconds": 1})
    assert r.status_code == 401


def test_master_response_has_no_store_cache(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})
    assert "no-store" in r.headers.get("cache-control", "").lower()


def test_progress_endpoint_touches_stream_registry(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    r = client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})
    assert r.status_code == 200

    reg = get_registry()
    handle = next((h for h in reg.all_streams() if h.media_id == mid), None)
    assert handle is not None
    old_access = handle.last_access

    time.sleep(0.05)
    r = client.post(
        "/api/progress",
        json={"media_id": mid, "position_seconds": 100},
        cookies={"session": cookie},
    )
    assert r.status_code == 204

    handle2 = next((h for h in reg.all_streams() if h.media_id == mid), None)
    assert handle2.last_access > old_access
