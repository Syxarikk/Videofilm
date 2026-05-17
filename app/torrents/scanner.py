"""Фоновый сканер: периодически опрашивает qBittorrent, для завершённых торрентов,
которых ещё нет в media_items, создаёт записи в БД."""
import asyncio
import logging
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Genre, MediaItem
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


def scan_once(qb: _QbProto, session: Session, *, tmdb=None, kinopoisk=None) -> int:
    """Один проход. Возвращает число добавленных media_items.

    tmdb/kinopoisk — необязательные клиенты (None ⇒ авто-матч пропускается).
    """
    from app.metadata.ffprobe import get_duration_seconds, probe_audio_tracks
    from app.metadata.matcher import find_match

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

        parsed = parse_title(video.name)
        duration = get_duration_seconds(str(video))
        audio = probe_audio_tracks(str(video))
        audio_dicts = [
            {"index": a.index, "codec": a.codec, "language": a.language,
             "title": a.title, "channels": a.channels}
            for a in audio
        ]

        default_kind = "series" if parsed.kind_hint == "tv" else "movie"

        item = MediaItem(
            torrent_hash=t.hash,
            title=parsed.title,
            file_path=str(video),
            size_bytes=video.stat().st_size,
            added_by=None,
            duration_seconds=duration,
            audio_tracks=audio_dicts,
            kind=default_kind,
            match_status="pending",
        )

        match = find_match(parsed, tmdb=tmdb, kinopoisk=kinopoisk)
        if match is not None:
            item.title = match.title
            item.description = match.description
            item.poster_url = match.poster_url
            item.year = match.year
            item.kind = match.kind
            if match.source == "tmdb":
                item.tmdb_id = match.external_id
            else:
                item.kinopoisk_id = match.external_id
            item.match_source = match.source
            item.match_status = "matched"

            for gname in match.genres:
                normalized = gname.strip()
                if not normalized:
                    continue
                existing = session.scalars(
                    select(Genre).where(Genre.name == normalized)
                ).first()
                if existing is None:
                    existing = Genre(name=normalized)
                    session.add(existing)
                    session.flush()
                item.genres.append(existing)
        else:
            item.match_status = "failed"

        session.add(item)
        added += 1
    return added


async def scanner_loop(
    qb: _QbProto,
    factory: sessionmaker[Session],
    interval_seconds: float = 10.0,
    *,
    tmdb=None,
    kinopoisk=None,
) -> None:
    """Бесконечный цикл, вызывается из startup-event FastAPI."""
    while True:
        try:
            with factory() as s:
                added = scan_once(qb, s, tmdb=tmdb, kinopoisk=kinopoisk)
                s.commit()
            if added:
                log.info("scanner: added %d new media item(s)", added)
        except Exception as e:
            log.exception("scanner_loop iteration failed: %s", e)
        await asyncio.sleep(interval_seconds)
