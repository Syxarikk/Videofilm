"""Оркестратор: TMDB → fallback Kinopoisk → None."""
from __future__ import annotations

import difflib
import logging
import re
from typing import TYPE_CHECKING

from app.metadata.types import MetadataMatch

if TYPE_CHECKING:
    from app.metadata.tmdb import TmdbClient
    from app.metadata.kinopoisk import KinopoiskClient
    from app.torrents.title_parser import ParsedTitle

log = logging.getLogger(__name__)


_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _is_confident(match: MetadataMatch, parsed: "ParsedTitle") -> bool:
    sim = _similarity(match.title, parsed.title)
    if parsed.year is not None:
        if match.year is None:
            return sim >= 0.9
        if abs(match.year - parsed.year) > 1:
            return False
        return sim >= 0.7
    return sim >= 0.85


def _tmdb_top_to_match(client: "TmdbClient", result: dict, hint: str | None) -> MetadataMatch | None:
    media_type = result.get("media_type") or hint
    rid = result.get("id")
    if rid is None:
        return None
    if media_type == "tv":
        return client.get_tv(rid)
    return client.get_movie(rid)


def find_match(
    parsed: "ParsedTitle",
    tmdb: "TmdbClient | None",
    kinopoisk: "KinopoiskClient | None",
) -> MetadataMatch | None:
    # 1. TMDB
    if tmdb is not None:
        results = tmdb.search(parsed.title, year=parsed.year, kind_hint=parsed.kind_hint)
        if results:
            top = _tmdb_top_to_match(tmdb, results[0], parsed.kind_hint)
            if top is not None and _is_confident(top, parsed):
                return top

    # 2. Kinopoisk fallback
    if kinopoisk is not None and kinopoisk.quota_ok():
        results = kinopoisk.search(parsed.title, year=parsed.year)
        if results:
            kp_id = results[0].get("filmId") or results[0].get("kinopoiskId")
            if kp_id is None:
                return None
            top = kinopoisk.get_film(int(kp_id))
            if top is not None and _is_confident(top, parsed):
                return top

    return None
