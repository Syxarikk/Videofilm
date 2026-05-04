import shutil
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
    # Между тестами очищаем глобальный registry и убиваем процессы
    yield
    reg = get_registry()
    for h in list(reg.all_streams()):
        if h.process is not None:
            from app.streaming.ffmpeg_runner import kill
            kill(h.process)
        reg.unregister(h.media_id, h.user_id)


def _create_media(db_factory, sample: Path) -> int:
    with db_factory() as s:
        m = MediaItem(torrent_hash="h", torrent_name="Test", title="Test", file_path=str(sample), size_bytes=sample.stat().st_size)
        s.add(m); s.commit(); s.refresh(m)
        return m.id


def test_playlist_starts_ffmpeg_and_returns_m3u8(client, db_factory, csrf_for):
    assert SAMPLE.exists()
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    r = client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert "#EXTM3U" in r.text


def test_playlist_unauthenticated_returns_401(client, db_factory):
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/stream/{mid}/playlist.m3u8")
    # /api/* — middleware не редиректит, отдаёт 401
    assert r.status_code == 401


def test_playlist_404_for_unknown_media(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/api/stream/9999/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 404


def test_segment_returned_after_playlist(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    # Сначала запросим плейлист, чтобы стартовал ffmpeg
    r = client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 200

    # Достанем имя первого сегмента из плейлиста
    seg_name = None
    for line in r.text.splitlines():
        if line.startswith("seg_") and line.endswith(".ts"):
            seg_name = line
            break
    assert seg_name is not None, "плейлист не содержит ни одного сегмента"

    r2 = client.get(f"/api/stream/{mid}/{seg_name}", cookies={"session": cookie})
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "video/mp2t"
    assert len(r2.content) > 0


def test_segment_unknown_returns_404(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    # Сначала запустим стрим
    client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    r = client.get(f"/api/stream/{mid}/seg_99999.ts", cookies={"session": cookie})
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

    # Повторно — обновляет
    r2 = client.post(
        "/api/progress",
        json={"media_id": mid, "position_seconds": 100},
        cookies={"session": cookie},
    )
    assert r2.status_code == 204

    with db_factory() as s:
        wp = s.scalars(select(WatchProgress).where(WatchProgress.media_id == mid)).one()
        assert wp.position_seconds == 100


def test_progress_unauth_returns_401(client, db_factory):
    mid = _create_media(db_factory, SAMPLE)
    r = client.post("/api/progress", json={"media_id": mid, "position_seconds": 1})
    assert r.status_code == 401


def test_playlist_response_has_no_store_cache(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 200
    assert "no-store" in r.headers.get("cache-control", "").lower()


def test_segment_response_has_no_store_cache(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    r = client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    seg_name = next((line for line in r.text.splitlines() if line.startswith("seg_")), None)
    assert seg_name
    r2 = client.get(f"/api/stream/{mid}/{seg_name}", cookies={"session": cookie})
    assert "no-store" in r2.headers.get("cache-control", "").lower()
