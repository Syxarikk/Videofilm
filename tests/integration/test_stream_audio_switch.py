from pathlib import Path

import pytest

from app.auth.passwords import hash_password
from app.models import MediaItem, User
from app.streaming.stream_registry import get_registry


MULTI_AUDIO = Path(__file__).parent.parent / "fixtures" / "multi_audio.mkv"


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    reg = get_registry()
    for h in list(reg.all_streams()):
        if h.process is not None:
            from app.streaming.ffmpeg_runner import kill
            kill(h.process)
        reg.unregister(h.media_id, h.user_id)


@pytest.mark.skipif(not MULTI_AUDIO.exists(),
                    reason="multi_audio.mkv fixture missing; run scripts/create_multi_audio_fixture.sh")
def test_master_playlist_lists_audio_renditions(client, db_factory, csrf_for):
    from app.metadata.ffprobe import probe_audio_tracks
    tracks = probe_audio_tracks(str(MULTI_AUDIO))
    assert len(tracks) == 2

    audio_dicts = [
        {"index": a.index, "codec": a.codec, "language": a.language,
         "title": a.title, "channels": a.channels}
        for a in tracks
    ]
    with db_factory() as s:
        s.add(User(username="alice",
                   password_hash=hash_password("correct-password-12"),
                   must_change_password=False))
        s.commit()
        m = MediaItem(
            torrent_hash="ma", title="Multi", file_path=str(MULTI_AUDIO),
            size_bytes=MULTI_AUDIO.stat().st_size, audio_tracks=audio_dicts,
        )
        s.add(m); s.commit(); s.refresh(m); mid = m.id

    r = client.post("/login", data={"username": "alice",
                                     "password": "correct-password-12",
                                     "csrf_token": csrf_for(None)})
    cookie = r.cookies.get("session")

    r = client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})
    assert r.status_code == 200
    master = r.text
    assert "#EXT-X-MEDIA:TYPE=AUDIO" in master
    assert master.count("#EXT-X-MEDIA:TYPE=AUDIO") == 2
    assert 'LANGUAGE="rus"' in master
    assert 'LANGUAGE="eng"' in master
