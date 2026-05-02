import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.csrf import verify_csrf
from app.deps import get_db, get_qbittorrent_client, render
from app.models import MediaItem, User
from app.streaming.ffmpeg_runner import kill as kill_ffmpeg
from app.streaming.stream_registry import get_registry
from app.torrents.client import QBittorrentClient, QBittorrentError

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
    return render(request, "media.html", {"user": user, "item": item})


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
    except QBittorrentError:
        # qBittorrent упал — продолжаем; файлы можно потом вычистить вручную
        pass

    # 3. Удалить из БД (CASCADE снесёт watch_progress)
    db.delete(item)
    db.commit()

    return RedirectResponse("/library", status_code=303)
