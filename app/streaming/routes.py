import re
import tempfile
import time as _t
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.config import get_settings
from app.deps import get_db
from app.models import Episode, EpisodeWatchProgress, MediaItem, User, WatchProgress
from app.streaming.ffmpeg_runner import HlsParams, kill, start_hls, wait_for_first_segment
from app.streaming.stream_registry import StreamHandle, episode_key, get_registry, media_key


api_router = APIRouter(prefix="/api/stream")
progress_router = APIRouter(prefix="/api")


_VARIANT_RE = re.compile(r"^v\d+$")
_SEGMENT_NAME_RE = re.compile(r"^seg_\d{5}\.ts$")


def _audio_tracks_from_json(audio_tracks: list | None):
    from app.metadata.types import AudioTrack
    if not audio_tracks:
        return []
    return [
        AudioTrack(
            index=a["index"], codec=a["codec"], language=a.get("language"),
            title=a.get("title"), channels=a.get("channels", 0),
        )
        for a in audio_tracks
    ]


def _ensure_stream_for(target_id: str, source_path: str,
                        audio_tracks_json: list | None, user_id: int) -> StreamHandle:
    reg = get_registry()
    existing = reg.get(target_id, user_id)
    if existing is not None:
        reg.touch(target_id, user_id)
        return existing
    settings = get_settings()
    Path(settings.hls_work_root).mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(
        prefix=f"hls_{target_id.replace(':', '_')}_u{user_id}_",
        dir=settings.hls_work_root,
    ))
    audio_tracks = _audio_tracks_from_json(audio_tracks_json)
    proc = start_hls(HlsParams(
        source=source_path, work_dir=str(work_dir),
        seek_seconds=0.0, audio_tracks=audio_tracks,
    ))
    handle = StreamHandle(target_id=target_id, user_id=user_id,
                           work_dir=str(work_dir), process=proc)
    reg.register(handle)
    if not wait_for_first_segment(work_dir, timeout=15.0):
        kill(proc)
        reg.unregister(target_id, user_id)
        raise HTTPException(status_code=503,
                            detail="ffmpeg не выдал первый сегмент за 15с")
    return handle


def _serve_master(handle: StreamHandle) -> Response:
    master = Path(handle.work_dir) / "master.m3u8"
    v0_playlist = Path(handle.work_dir) / "v0" / "playlist.m3u8"
    deadline = _t.time() + 5.0
    while not master.exists() and not v0_playlist.exists() and _t.time() < deadline:
        _t.sleep(0.1)
    target = master if master.exists() else v0_playlist
    if not target.exists():
        raise HTTPException(status_code=503, detail="плейлист ещё не сгенерирован")
    return Response(
        content=target.read_bytes(),
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


# === Episode streaming routes — РЕГИСТРИРУЕМ ПЕРВЫМИ (иначе /{media_id} ловит "episode") ===

@api_router.get("/episode/{episode_id}/master.m3u8")
def episode_stream_master(
    episode_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    ep = db.get(Episode, episode_id)
    if ep is None:
        raise HTTPException(status_code=404)
    handle = _ensure_stream_for(
        target_id=episode_key(ep.id),
        source_path=ep.file_path,
        audio_tracks_json=ep.audio_tracks,
        user_id=user.id,
    )
    return _serve_master(handle)


@api_router.get("/episode/{episode_id}/{variant}/playlist.m3u8")
def episode_stream_variant_playlist(
    episode_id: int, variant: str,
    user: Annotated[User, Depends(get_current_user)],
):
    if not _VARIANT_RE.match(variant):
        raise HTTPException(status_code=404)
    reg = get_registry()
    target_id = episode_key(episode_id)
    handle = reg.get(target_id, user.id)
    if handle is None:
        raise HTTPException(status_code=410, detail="стрим уже завершён, обновите страницу")
    playlist = Path(handle.work_dir) / variant / "playlist.m3u8"
    if not playlist.exists():
        raise HTTPException(status_code=404)
    reg.touch(target_id, user.id)
    return Response(
        content=playlist.read_bytes(),
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@api_router.get("/episode/{episode_id}/{variant}/{segment_name}")
def episode_stream_segment(
    episode_id: int, variant: str, segment_name: str,
    user: Annotated[User, Depends(get_current_user)],
):
    if not _VARIANT_RE.match(variant) or not _SEGMENT_NAME_RE.match(segment_name):
        raise HTTPException(status_code=404)
    reg = get_registry()
    target_id = episode_key(episode_id)
    handle = reg.get(target_id, user.id)
    if handle is None:
        raise HTTPException(status_code=410, detail="стрим уже завершён, обновите страницу")
    seg_path = Path(handle.work_dir) / variant / segment_name
    if not seg_path.exists():
        raise HTTPException(status_code=404)
    reg.touch(target_id, user.id)
    return FileResponse(
        str(seg_path),
        media_type="video/mp2t",
        headers={"Cache-Control": "no-store"},
    )


# === Movie streaming routes ===

@api_router.get("/{media_id}/master.m3u8")
def stream_master(
    media_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    media = db.get(MediaItem, media_id)
    if media is None:
        raise HTTPException(status_code=404)
    handle = _ensure_stream_for(
        target_id=media_key(media.id),
        source_path=media.file_path,
        audio_tracks_json=media.audio_tracks,
        user_id=user.id,
    )
    return _serve_master(handle)


@api_router.get("/{media_id}/playlist.m3u8")
def legacy_playlist_redirect(media_id: int):
    return RedirectResponse(f"/api/stream/{media_id}/master.m3u8", status_code=301)


@api_router.get("/{media_id}/{variant}/playlist.m3u8")
def stream_variant_playlist(
    media_id: int,
    variant: str,
    user: Annotated[User, Depends(get_current_user)],
):
    if not _VARIANT_RE.match(variant):
        raise HTTPException(status_code=404)
    reg = get_registry()
    target_id = media_key(media_id)
    handle = reg.get(target_id, user.id)
    if handle is None:
        raise HTTPException(status_code=410, detail="стрим уже завершён, обновите страницу")
    playlist = Path(handle.work_dir) / variant / "playlist.m3u8"
    if not playlist.exists():
        raise HTTPException(status_code=404)
    reg.touch(target_id, user.id)
    return Response(
        content=playlist.read_bytes(),
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@api_router.get("/{media_id}/{variant}/{segment_name}")
def stream_segment(
    media_id: int,
    variant: str,
    segment_name: str,
    user: Annotated[User, Depends(get_current_user)],
):
    if not _VARIANT_RE.match(variant) or not _SEGMENT_NAME_RE.match(segment_name):
        raise HTTPException(status_code=404)
    reg = get_registry()
    target_id = media_key(media_id)
    handle = reg.get(target_id, user.id)
    if handle is None:
        raise HTTPException(status_code=410, detail="стрим уже завершён, обновите страницу")
    seg_path = Path(handle.work_dir) / variant / segment_name
    if not seg_path.exists():
        raise HTTPException(status_code=404)
    reg.touch(target_id, user.id)
    return FileResponse(
        str(seg_path),
        media_type="video/mp2t",
        headers={"Cache-Control": "no-store"},
    )


# === Progress ===

class _ProgressIn(BaseModel):
    media_id: int
    position_seconds: int
    audio_track_index: int | None = None


@progress_router.post("/progress", status_code=204, include_in_schema=False)
def progress(
    payload: Annotated[_ProgressIn, Body()],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
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
        if payload.audio_track_index is not None:
            existing.audio_track_index = payload.audio_track_index
    else:
        db.add(WatchProgress(
            user_id=user.id, media_id=payload.media_id,
            position_seconds=payload.position_seconds,
            audio_track_index=payload.audio_track_index,
            updated_at=now,
        ))
    get_registry().touch(media_key(payload.media_id), user.id)
    db.commit()


class _EpisodeProgressIn(BaseModel):
    episode_id: int
    position_seconds: int
    audio_track_index: int | None = None


@progress_router.post("/progress/episode", status_code=204, include_in_schema=False)
def episode_progress(
    payload: Annotated[_EpisodeProgressIn, Body()],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    existing = db.scalars(
        select(EpisodeWatchProgress).where(
            EpisodeWatchProgress.user_id == user.id,
            EpisodeWatchProgress.episode_id == payload.episode_id,
        )
    ).first()
    now = datetime.now(timezone.utc)
    if existing is not None:
        existing.position_seconds = payload.position_seconds
        existing.updated_at = now
        if payload.audio_track_index is not None:
            existing.audio_track_index = payload.audio_track_index
    else:
        db.add(EpisodeWatchProgress(
            user_id=user.id, episode_id=payload.episode_id,
            position_seconds=payload.position_seconds,
            audio_track_index=payload.audio_track_index,
            updated_at=now,
        ))
    get_registry().touch(episode_key(payload.episode_id), user.id)
    db.commit()
