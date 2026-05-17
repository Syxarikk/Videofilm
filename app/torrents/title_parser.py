"""Извлечение читаемого названия + года + season/episode из имени файла торрента."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal


_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_SE_RE = re.compile(r"^[Ss](\d{1,2})[Ee](\d{1,3})$")
_NOISE_TOKENS = {
    "1080p", "2160p", "720p", "480p",
    "BluRay", "BRRip", "DVDRip", "WEB", "WEB-DL", "WEBRip", "HDTV", "HDR", "HDR10", "DV",
    "x264", "x265", "H264", "H265", "HEVC", "AVC",
    "AAC", "AC3", "DTS", "DDP", "DD5.1", "5.1", "7.1", "FLAC",
    "REMUX", "PROPER", "REPACK", "EXTENDED", "DIRECTORS-CUT",
}
_NOISE_LOWER = {t.lower() for t in _NOISE_TOKENS}


@dataclass(frozen=True, slots=True)
class ParsedTitle:
    title: str
    year: int | None
    season: int | None
    episode: int | None
    kind_hint: Literal["movie", "tv"] | None


def parse_title(filename: str) -> ParsedTitle:
    original_stem = PurePosixPath(filename).stem
    if not original_stem:
        return ParsedTitle(title=filename, year=None, season=None, episode=None, kind_hint=None)

    stem = original_stem
    if "-" in stem and not _has_word_boundary(stem):
        stem = stem.rsplit("-", 1)[0]

    tokens = [t for t in re.split(r"[.\s_]+", stem) if t]

    title_parts: list[str] = []
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    saw_noise = False

    for tok in tokens:
        if _YEAR_RE.match(tok):
            year = int(tok)
            break
        m = _SE_RE.match(tok)
        if m:
            season = int(m.group(1))
            episode = int(m.group(2))
            break
        if tok.lower() in _NOISE_LOWER:
            saw_noise = True
            continue
        title_parts.append(tok)

    if not title_parts:
        return ParsedTitle(title=original_stem, year=None, season=None, episode=None, kind_hint=None)

    kind_hint: Literal["movie", "tv"] | None
    if season is not None:
        kind_hint = "tv"
    elif year is not None:
        kind_hint = "movie"
    elif saw_noise:
        kind_hint = "movie"
    else:
        return ParsedTitle(title=original_stem, year=None, season=None, episode=None, kind_hint=None)

    title = " ".join(title_parts)
    return ParsedTitle(title=title, year=year, season=season, episode=episode, kind_hint=kind_hint)


def _has_word_boundary(s: str) -> bool:
    return any(c in s for c in (" ", ".", "_"))
