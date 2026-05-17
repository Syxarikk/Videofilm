"""Клиент к TMDB API v3 (Bearer auth, v4 Read Access Token)."""
from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

from app.metadata.types import KindLiteral, MetadataMatch

log = logging.getLogger(__name__)


TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG_BASE = "https://image.tmdb.org/t/p/w500"


def _map_kind_from_tmdb(media_type: Literal["movie", "tv"],
                         genres: list[str],
                         country_codes: list[str]) -> KindLiteral:
    if "Документальный" in genres:
        return "documentary"
    if "Анимация" in genres:
        if "JP" in country_codes:
            return "anime"
        if media_type == "movie":
            return "cartoon"
        return "series"
    return "movie" if media_type == "movie" else "series"


def _parse_year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        return int(date_str[:4])
    except (ValueError, TypeError):
        return None


def _poster_url(poster_path: str | None) -> str | None:
    if not poster_path:
        return None
    return f"{TMDB_IMG_BASE}{poster_path}"


class TmdbClient:
    def __init__(self, api_key: str, timeout: float = 5.0):
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=TMDB_BASE,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def search(self, title: str, year: int | None,
               kind_hint: Literal["movie", "tv"] | None) -> list[dict[str, Any]]:
        if kind_hint == "tv":
            endpoint, params = "/search/tv", {"query": title, "language": "ru-RU"}
            if year is not None:
                params["first_air_date_year"] = year
        elif kind_hint == "movie":
            endpoint, params = "/search/movie", {"query": title, "language": "ru-RU"}
            if year is not None:
                params["year"] = year
        else:
            endpoint, params = "/search/multi", {"query": title, "language": "ru-RU"}
        try:
            r = self._client.get(endpoint, params=params)
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("TMDB search failed for %r: %s", title, e)
            return []
        return r.json().get("results") or []

    def get_movie(self, tmdb_id: int) -> MetadataMatch | None:
        try:
            r = self._client.get(f"/movie/{tmdb_id}", params={"language": "ru-RU"})
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("TMDB get_movie %d failed: %s", tmdb_id, e)
            return None
        d = r.json()
        genres = [g["name"] for g in (d.get("genres") or [])]
        country_codes = [c["iso_3166_1"] for c in (d.get("production_countries") or [])]
        return MetadataMatch(
            source="tmdb",
            external_id=tmdb_id,
            title=d.get("title") or "",
            year=_parse_year(d.get("release_date")),
            kind=_map_kind_from_tmdb("movie", genres, country_codes),
            description=d.get("overview") or None,
            poster_url=_poster_url(d.get("poster_path")),
            genres=genres,
            score=1.0,
        )

    def get_tv(self, tmdb_id: int) -> MetadataMatch | None:
        try:
            r = self._client.get(f"/tv/{tmdb_id}", params={"language": "ru-RU"})
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("TMDB get_tv %d failed: %s", tmdb_id, e)
            return None
        d = r.json()
        genres = [g["name"] for g in (d.get("genres") or [])]
        country_codes = list(d.get("origin_country") or [])
        return MetadataMatch(
            source="tmdb",
            external_id=tmdb_id,
            title=d.get("name") or "",
            year=_parse_year(d.get("first_air_date")),
            kind=_map_kind_from_tmdb("tv", genres, country_codes),
            description=d.get("overview") or None,
            poster_url=_poster_url(d.get("poster_path")),
            genres=genres,
            score=1.0,
        )
