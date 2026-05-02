import tempfile
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.deps import get_db
from app.models import MediaItem, User
from app.streaming.ffmpeg_runner import HlsParams, kill, start_hls, wait_for_first_segment
from app.streaming.stream_registry import StreamHandle, get_registry


api_router = APIRouter(prefix="/api/stream")


def _ensure_stream(media: MediaItem, user_id: int) -> StreamHandle:
    """Если стрим для (media_id, user_id) уже работает — touch и вернуть.
    Иначе — стартануть ffmpeg и положить в registry."""
    reg = get_registry()
    existing = reg.get(media.id, user_id)
    if existing is not None:
        reg.touch(media.id, user_id)
        return existing
    work_dir = Path(tempfile.mkdtemp(prefix=f"hls_m{media.id}_u{user_id}_"))
    proc = start_hls(HlsParams(source=media.file_path, work_dir=str(work_dir), seek_seconds=0.0))
    handle = StreamHandle(media_id=media.id, user_id=user_id, work_dir=str(work_dir), process=proc)
    reg.register(handle)
    if not wait_for_first_segment(work_dir, timeout=15.0):
        kill(proc)
        reg.unregister(media.id, user_id)
        raise HTTPException(status_code=503, detail="ffmpeg не выдал первый сегмент за 15с")
    return handle


@api_router.get("/{media_id}/playlist.m3u8")
async def stream_playlist(
    media_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    media = db.get(MediaItem, media_id)
    if media is None:
        raise HTTPException(status_code=404)
    handle = _ensure_stream(media, user.id)
    playlist = Path(handle.work_dir) / "playlist.m3u8"
    if not playlist.exists():
        raise HTTPException(status_code=503, detail="плейлист ещё не сгенерирован")
    return Response(
        content=playlist.read_bytes(),
        media_type="application/vnd.apple.mpegurl",
    )
