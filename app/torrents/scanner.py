"""Фоновый сканер: периодически опрашивает qBittorrent, для завершённых торрентов,
которых ещё нет в media_items, создаёт записи в БД."""
import asyncio
import logging
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import MediaItem
from app.torrents.title_parser import parse_title
from app.torrents.types import TorrentInfo

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".m4v", ".ts"}


class _QbProto(Protocol):
    def list_torrents(self) -> list[TorrentInfo]: ...


def _find_largest_video(content_path: str) -> Path | None:
    p = Path(content_path)
    if p.is_file():
        return p if p.suffix.lower() in VIDEO_EXTENSIONS else None
    if not p.is_dir():
        return None
    candidates = [
        f for f in p.rglob("*")
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_size)


def scan_once(qb: _QbProto, session: Session) -> int:
    """Один проход. Возвращает число добавленных media_items."""
    try:
        torrents = qb.list_torrents()
    except Exception as e:  # ловим всё, чтобы не уронить фоновую задачу
        log.warning("scan_once: qBittorrent error: %s", e)
        return 0

    existing_hashes = set(session.scalars(select(MediaItem.torrent_hash)).all())
    added = 0
    for t in torrents:
        if not t.is_complete:
            continue
        if t.hash in existing_hashes:
            continue
        video = _find_largest_video(t.content_path)
        if video is None:
            log.info("scan_once: no video file in %s, skipping", t.content_path)
            continue
        item = MediaItem(
            torrent_hash=t.hash,
            title=parse_title(video.name).title,
            file_path=str(video),
            size_bytes=video.stat().st_size,
            added_by=None,  # неизвестно, кто добавил — qBittorrent не хранит
        )
        session.add(item)
        added += 1
    return added


async def scanner_loop(
    qb: _QbProto,
    factory: sessionmaker[Session],
    interval_seconds: float = 10.0,
) -> None:
    """Бесконечный цикл, вызывается из startup-event FastAPI."""
    while True:
        try:
            with factory() as s:
                added = scan_once(qb, s)
                s.commit()
            if added:
                log.info("scanner: added %d new media item(s)", added)
        except Exception as e:
            log.exception("scanner_loop iteration failed: %s", e)
        await asyncio.sleep(interval_seconds)
