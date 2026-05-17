import logging
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.csrf import verify_csrf
from app.deps import get_db, get_qbittorrent_client, render
from app.models import MediaItem, User, WatchProgress
from app.streaming.ffmpeg_runner import kill as kill_ffmpeg
from app.streaming.stream_registry import get_registry
from app.torrents.client import QBittorrentClient, QBittorrentError

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    items = db.scalars(select(MediaItem).order_by(MediaItem.added_at.desc())).all()
    return render(request, "library.html", {"user": user, "items": items})


@router.get("/media/{media_id}", response_class=HTMLResponse)
def media_page(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    # Лениво дозаполняем duration_seconds и audio_tracks для старых записей.
    needs_commit = False
    if item.duration_seconds is None:
        from app.metadata.ffprobe import get_duration_seconds
        dur = get_duration_seconds(item.file_path)
        if dur is not None:
            item.duration_seconds = dur
            needs_commit = True
    if item.audio_tracks is None:
        from app.metadata.ffprobe import probe_audio_tracks
        tracks = probe_audio_tracks(item.file_path)
        item.audio_tracks = [
            {"index": a.index, "codec": a.codec, "language": a.language,
             "title": a.title, "channels": a.channels}
            for a in tracks
        ]
        needs_commit = True
    if needs_commit:
        db.commit()

    progress = db.scalars(
        select(WatchProgress).where(
            WatchProgress.user_id == user.id,
            WatchProgress.media_id == media_id,
        )
    ).first()
    saved_position = progress.position_seconds if progress else 0
    saved_audio_track = progress.audio_track_index if progress else None

    return render(request, "media.html", {
        "user": user,
        "item": item,
        "saved_position_seconds": saved_position,
        "saved_audio_track_index": saved_audio_track,
    })


@router.post("/api/media/{media_id}/delete")
def delete_media(
    media_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    qb: Annotated[QBittorrentClient, Depends(get_qbittorrent_client)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    # 1. Убить все ffmpeg-процессы для этого media_id (любого юзера)
    reg = get_registry()
    for handle in list(reg.all_streams()):
        if handle.media_id == media_id and handle.process is not None:
            kill_ffmpeg(handle.process)
            reg.unregister(handle.media_id, handle.user_id)
            shutil.rmtree(handle.work_dir, ignore_errors=True)

    # 2. Сказать qBittorrent удалить торрент с файлами
    try:
        qb.delete_torrent(item.torrent_hash, delete_files=True)
    except QBittorrentError as e:
        # qBittorrent упал — продолжаем; файлы можно потом вычистить вручную
        log.warning(
            "delete_media: qBittorrent unreachable for torrent %s, files orphaned: %s",
            item.torrent_hash, e,
        )

    # 3. Удалить из БД (CASCADE снесёт watch_progress)
    db.delete(item)
    db.commit()

    return RedirectResponse("/library", status_code=303)
