import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.deps import get_db
from app.models import MediaItem, User, WatchProgress
from app.streaming.ffmpeg_runner import HlsParams, kill, start_hls, wait_for_first_segment
from app.streaming.stream_registry import StreamHandle, get_registry


api_router = APIRouter(prefix="/api/stream")
progress_router = APIRouter(prefix="/api")


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
def stream_playlist(
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


_SEGMENT_NAME_RE = re.compile(r"^seg_\d{5}\.ts$")


@api_router.get("/{media_id}/{segment_name}")
def stream_segment(
    media_id: int,
    segment_name: str,
    user: Annotated[User, Depends(get_current_user)],
):
    if not _SEGMENT_NAME_RE.match(segment_name):
        raise HTTPException(status_code=404)
    reg = get_registry()
    handle = reg.get(media_id, user.id)
    if handle is None:
        raise HTTPException(status_code=410, detail="стрим уже завершён, обновите страницу")
    seg_path = Path(handle.work_dir) / segment_name
    if not seg_path.exists():
        raise HTTPException(status_code=404)
    reg.touch(media_id, user.id)
    return FileResponse(str(seg_path), media_type="video/mp2t")


class _ProgressIn(BaseModel):
    media_id: int
    position_seconds: int


@progress_router.post("/progress", status_code=204, include_in_schema=False)
def progress(
    payload: Annotated[_ProgressIn, Body()],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    # Upsert по (user_id, media_id)
    existing = db.scalars(
        select(WatchProgress).where(
            WatchProgress.user_id == user.id,
            WatchProgress.media_id == payload.media_id,
        )
    ).first()
    now = datetime.now(timezone.utc)
    if existing is not None:
        existing.position_seconds = payload.position_seconds
        existing.updated_at = now
    else:
        db.add(WatchProgress(
            user_id=user.id, media_id=payload.media_id,
            position_seconds=payload.position_seconds, updated_at=now,
        ))
    # Также используем как heartbeat для активного стрима
    get_registry().touch(payload.media_id, user.id)
    db.commit()
