"""Группировка видеофайлов торрента в серию (если они эпизодические)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.torrents.title_parser import ParsedTitle


@dataclass(frozen=True, slots=True)
class EpisodeFile:
    """Один файл-эпизод торрента: путь + распарсенное название."""
    path: Path
    parsed: ParsedTitle


@dataclass(frozen=True, slots=True)
class SeriesGroup:
    """Результат группировки: имя серии + список эпизодов."""
    title: str
    episodes: list[EpisodeFile]


def group_as_series(files: list[EpisodeFile],
                     fallback_dir_name: str) -> SeriesGroup | None:
    """Возвращает SeriesGroup если в files >=2 эпизода (season+episode заполнены).

    Имя серии: общий title всех эпизодов, иначе fallback_dir_name.
    """
    episodic = [f for f in files
                if f.parsed.season is not None and f.parsed.episode is not None]
    if len(episodic) < 2:
        return None

    titles = {_normalize(f.parsed.title) for f in episodic}
    if len(titles) == 1:
        title = episodic[0].parsed.title
    else:
        title = fallback_dir_name

    return SeriesGroup(title=title, episodes=episodic)


def _normalize(s: str) -> str:
    return s.lower().strip()
