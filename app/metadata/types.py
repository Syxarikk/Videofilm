from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


KindLiteral = Literal["movie", "series", "cartoon", "anime", "documentary", "show", "other"]


@dataclass(frozen=True, slots=True)
class MetadataMatch:
    """Унифицированный формат для одного матча из любого источника."""
    source: Literal["tmdb", "kinopoisk"]
    external_id: int
    title: str
    year: int | None
    kind: KindLiteral
    description: str | None
    poster_url: str | None
    genres: list[str]
    score: float


@dataclass(frozen=True, slots=True)
class AudioTrack:
    """Описание одной аудиодорожки из файла (через ffprobe)."""
    index: int
    codec: str
    language: str | None
    title: str | None
    channels: int


@dataclass(frozen=True, slots=True)
class TmdbEpisodeMeta:
    """Метаданные одного эпизода из TMDB /tv/{id}/season/{n}."""
    id: int
    episode_number: int
    name: str | None
    overview: str | None
    air_date: str | None  # ISO "YYYY-MM-DD"
