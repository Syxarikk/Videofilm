"""Фоновый сканер: периодически опрашивает qBittorrent, для завершённых торрентов,
которых ещё нет в media_items, создаёт записи в БД.

Для сериальных торрентов (>=2 файла с SxxExx) создаёт MediaItem(kind='series') + Episode-записи.
"""
import asyncio
import logging
from datetime import date as _date_cls
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Episode, Genre, MediaItem
from app.torrents.series_grouper import EpisodeFile, group_as_series
from app.torrents.title_parser import ParsedTitle, parse_title
from app.torrents.types import TorrentInfo

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".m4v", ".ts"}


class _QbProto(Protocol):
    def list_torrents(self) -> list[TorrentInfo]: ...


def _all_videos(content_path: str):
    """Все видеофайлы в торренте (рекурсивно)."""
    p = Path(content_path)
    if p.is_file():
        if p.suffix.lower() in VIDEO_EXTENSIONS:
            yield p
        return
    if not p.is_dir():
        return
    for f in p.rglob("*"):
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
            yield f


def _find_largest_video(content_path: str) -> Path | None:
    candidates = list(_all_videos(content_path))
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_size)


def _resolve_genre(session: Session, name: str) -> Genre:
    g = session.scalars(select(Genre).where(Genre.name == name)).first()
    if g is None:
        g = Genre(name=name)
        session.add(g)
        session.flush()
    return g


def _apply_match_to_media(item: MediaItem, match, session: Session) -> None:
    """Применяет MetadataMatch к MediaItem (фильм или сериал)."""
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
        name = gname.strip()
        if not name:
            continue
        item.genres.append(_resolve_genre(session, name))


def scan_once(qb: _QbProto, session: Session, *, tmdb=None, kinopoisk=None) -> int:
    """Один проход. Возвращает число добавленных MediaItem-записей."""
    from app.metadata.ffprobe import get_duration_seconds, probe_audio_tracks
    from app.metadata.matcher import find_match

    try:
        torrents = qb.list_torrents()
    except Exception as e:
        log.warning("scan_once: qBittorrent error: %s", e)
        return 0

    existing_hashes = set(session.scalars(select(MediaItem.torrent_hash)).all())
    added = 0
    for t in torrents:
        if not t.is_complete:
            continue
        if t.hash in existing_hashes:
            continue

        video_files = list(_all_videos(t.content_path))
        if not video_files:
            log.info("scan_once: no video file in %s, skipping", t.content_path)
            continue

        files_parsed = [EpisodeFile(path=v, parsed=parse_title(v.name)) for v in video_files]
        dir_name = Path(t.content_path).name
        group = group_as_series(files_parsed, fallback_dir_name=dir_name)

        if group is not None:
            # === Сериал ===
            parsed_series = ParsedTitle(title=group.title, year=None, season=None,
                                         episode=None, kind_hint="tv")
            total_size = sum(f.path.stat().st_size for f in group.episodes)
            series_item = MediaItem(
                torrent_hash=t.hash,
                title=group.title,
                file_path=t.content_path,
                size_bytes=total_size,
                added_by=None,
                duration_seconds=None,
                audio_tracks=None,
                kind="series",
                match_status="pending",
            )

            match = find_match(parsed_series, tmdb=tmdb, kinopoisk=kinopoisk)
            season_meta: dict[int, dict] = {}
            if match is not None:
                _apply_match_to_media(series_item, match, session)
                # Принудительно kind="series", т.к. matcher мог вернуть другой
                series_item.kind = "series"
                if match.source == "tmdb" and tmdb is not None:
                    unique_seasons = {e.parsed.season for e in group.episodes}
                    for ssn in unique_seasons:
                        season_meta[ssn] = tmdb.get_tv_season(match.external_id, ssn)
            else:
                series_item.match_status = "failed"

            session.add(series_item)
            session.flush()

            for ef in group.episodes:
                meta = season_meta.get(ef.parsed.season, {}).get(ef.parsed.episode)
                ad = None
                if meta and meta.air_date:
                    try:
                        ad = _date_cls.fromisoformat(meta.air_date)
                    except (ValueError, TypeError):
                        pass
                ep = Episode(
                    series_id=series_item.id,
                    season=ef.parsed.season,
                    episode=ef.parsed.episode,
                    title=(meta.name if meta else None),
                    description=(meta.overview if meta else None),
                    file_path=str(ef.path),
                    size_bytes=ef.path.stat().st_size,
                    tmdb_episode_id=(meta.id if meta else None),
                    air_date=ad,
                )
                session.add(ep)
            added += 1
        else:
            # === Фильм или одиночный «эпизод» ===
            video = _find_largest_video(t.content_path)
            if video is None:
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
                _apply_match_to_media(item, match, session)
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
