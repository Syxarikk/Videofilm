"""Фоновый сканер: периодически опрашивает qBittorrent, для завершённых торрентов,
для каждого видеофайла внутри создаёт запись в media_items."""
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


def _find_all_videos(content_path: str) -> list[Path]:
    """Все видеофайлы внутри торрента. Для одиночных торрентов — список из одного файла.

    Сортируется по имени файла, чтобы серии шли в естественном порядке (S01E01, S01E02 …).
    """
    p = Path(content_path)
    if p.is_file():
        return [p] if p.suffix.lower() in VIDEO_EXTENSIONS else []
    if not p.is_dir():
        return []
    return sorted(
        (f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS),
        key=lambda f: f.name.lower(),
    )


def scan_once(qb: _QbProto, session: Session) -> int:
    """Один проход. Возвращает число добавленных media_items.

    Для каждого завершённого торрента создаём по одной MediaItem на каждый видеофайл.
    Если запись с тем же `(torrent_hash, file_path)` уже есть — пропускаем.
    """
    try:
        torrents = qb.list_torrents()
    except Exception as e:  # ловим всё, чтобы не уронить фоновую задачу
        log.warning("scan_once: qBittorrent error: %s", e)
        return 0

    existing_pairs: set[tuple[str, str]] = set(
        session.execute(select(MediaItem.torrent_hash, MediaItem.file_path)).all()
    )
    added = 0
    for t in torrents:
        if not t.is_complete:
            continue
        videos = _find_all_videos(t.content_path)
        if not videos:
            log.info("scan_once: no video file in %s, skipping", t.content_path)
            continue
        torrent_name = parse_title(t.name)
        for video in videos:
            key = (t.hash, str(video))
            if key in existing_pairs:
                continue
            session.add(MediaItem(
                torrent_hash=t.hash,
                torrent_name=torrent_name,
                title=parse_title(video.name),
                file_path=str(video),
                size_bytes=video.stat().st_size,
                added_by=None,  # неизвестно, кто добавил — qBittorrent не хранит
            ))
            existing_pairs.add(key)
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
