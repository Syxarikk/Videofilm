"""Клиент к Kinopoisk Unofficial API."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.metadata.types import MetadataMatch

log = logging.getLogger(__name__)


KP_BASE = "https://kinopoiskapiunofficial.tech/api/v2.2"
DAILY_LIMIT = 500


class KinopoiskClient:
    def __init__(self, api_key: str, timeout: float = 5.0):
        self._client = httpx.Client(
            base_url=KP_BASE,
            timeout=timeout,
            headers={"X-API-KEY": api_key, "Accept": "application/json"},
        )
        self._quota_count = 0
        self._quota_day_start = time.time()

    def close(self) -> None:
        self._client.close()

    def quota_used_today(self) -> int:
        self._maybe_reset_quota()
        return self._quota_count

    def quota_ok(self) -> bool:
        return self.quota_used_today() < DAILY_LIMIT

    def _maybe_reset_quota(self) -> None:
        if time.time() - self._quota_day_start > 86400:
            self._quota_count = 0
            self._quota_day_start = time.time()

    def _bump_quota(self) -> None:
        self._maybe_reset_quota()
        self._quota_count += 1

    def search(self, title: str, year: int | None) -> list[dict[str, Any]]:
        if not self.quota_ok():
            log.info("Kinopoisk daily quota exhausted, skipping search for %r", title)
            return []
        try:
            r = self._client.get("/films/search-by-keyword", params={"keyword": title})
            self._bump_quota()
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("Kinopoisk search failed for %r: %s", title, e)
            return []
        films = r.json().get("films") or []
        if year is not None:
            films = [f for f in films if str(f.get("year") or "") == str(year)]
        return films

    def get_film(self, kp_id: int) -> MetadataMatch | None:
        if not self.quota_ok():
            log.info("Kinopoisk daily quota exhausted, skipping get_film(%d)", kp_id)
            return None
        try:
            r = self._client.get(f"/films/{kp_id}")
            self._bump_quota()
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("Kinopoisk get_film(%d) failed: %s", kp_id, e)
            return None
        d = r.json()
        kp_type = (d.get("type") or "").upper()
        kind = "series" if kp_type in ("TV_SERIES", "MINI_SERIES", "TV_SHOW") else "movie"
        genres = [g.get("genre") for g in (d.get("genres") or []) if g.get("genre")]
        year = d.get("year")
        try:
            year = int(year) if year else None
        except (ValueError, TypeError):
            year = None
        return MetadataMatch(
            source="kinopoisk",
            external_id=kp_id,
            title=d.get("nameRu") or d.get("nameOriginal") or "",
            year=year,
            kind=kind,
            description=d.get("description") or None,
            poster_url=d.get("posterUrl") or None,
            genres=genres,
            score=1.0,
        )
