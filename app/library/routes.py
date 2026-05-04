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


def _group_items_by_torrent(items: list[MediaItem]) -> list[dict]:
    """Сгруппировать MediaItem'ы по torrent_hash, сохраняя порядок (DESC added_at).

    Ключ `files` (а не `items`) намеренно — Jinja для dict'а резолвит `.items`
    как встроенный метод dict.items(), а не как значение ключа.

    Возвращает список dict'ов вида:
      {torrent_hash, torrent_name, files: [...], total_size_bytes, first_id}
    """
    groups: dict[str, dict] = {}
    for it in items:
        g = groups.get(it.torrent_hash)
        if g is None:
            groups[it.torrent_hash] = {
                "torrent_hash": it.torrent_hash,
                "torrent_name": it.torrent_name,
                "files": [it],
                "total_size_bytes": it.size_bytes,
                "first_id": it.id,
            }
        else:
            g["files"].append(it)
            g["total_size_bytes"] += it.size_bytes
    return list(groups.values())


@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    items = db.scalars(select(MediaItem).order_by(MediaItem.added_at.desc())).all()
    groups = _group_items_by_torrent(items)
    return render(request, "library.html", {"user": user, "groups": groups})


@router.get("/torrent/{torrent_hash}", response_class=HTMLResponse)
def torrent_page(
    torrent_hash: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    items = db.scalars(
        select(MediaItem)
        .where(MediaItem.torrent_hash == torrent_hash)
        .order_by(MediaItem.title)
    ).all()
    if not items:
        raise HTTPException(status_code=404)

    # Прогресс просмотра текущим пользователем (для значка «▶ просмотр» на каждой серии)
    progress_rows = db.execute(
        select(WatchProgress.media_id, WatchProgress.position_seconds)
        .where(WatchProgress.user_id == user.id)
        .where(WatchProgress.media_id.in_([i.id for i in items]))
    ).all()
    progress_map = {row[0]: row[1] for row in progress_rows}

    return render(
        request,
        "torrent.html",
        {
            "user": user,
            "torrent_hash": torrent_hash,
            "torrent_name": items[0].torrent_name,
            "items": items,
            "total_size_bytes": sum(i.size_bytes for i in items),
            "progress_map": progress_map,
        },
    )


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
    """Удалить торрент целиком — все его файлы из БД и с диска (через qBittorrent).

    qBittorrent умеет удалять только торрент целиком, не отдельные файлы. Поэтому
    при удалении любой серии (MediaItem) сносим весь торрент и все связанные
    MediaItem'ы из БД одним залпом.
    """
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    # Все «братья» — другие серии того же торрента
    siblings = db.scalars(
        select(MediaItem).where(MediaItem.torrent_hash == item.torrent_hash)
    ).all()
    sibling_ids = {s.id for s in siblings}

    # 1. Убить ffmpeg-стримы для всех затронутых media_id
    reg = get_registry()
    for handle in list(reg.all_streams()):
        if handle.media_id in sibling_ids and handle.process is not None:
            kill_ffmpeg(handle.process)
            reg.unregister(handle.media_id, handle.user_id)
            shutil.rmtree(handle.work_dir, ignore_errors=True)

    # 2. Сказать qBittorrent удалить торрент целиком + файлы с диска
    try:
        qb.delete_torrent(item.torrent_hash, delete_files=True)
    except QBittorrentError as e:
        # qBittorrent упал — продолжаем; файлы можно потом вычистить вручную
        log.warning(
            "delete_media: qBittorrent unreachable for torrent %s, files orphaned: %s",
            item.torrent_hash, e,
        )

    # 3. Удалить все «братские» MediaItem'ы из БД (CASCADE снесёт watch_progress)
    for s in siblings:
        db.delete(s)
    db.commit()

    return RedirectResponse("/library", status_code=303)
