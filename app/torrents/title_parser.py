"""Извлечение читаемого названия из имени файла торрента.

Торренты обычно именуются как:
  Some.Movie.2024.1080p.BluRay.x264-GROUP.mkv
  Movie.Title.S01E05.HDTV.x264.mkv

Простой парсер: режем по разделителям, ищем год или season/episode маркер,
остальное считаем шумом.
"""
import re
from pathlib import PurePosixPath


_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_SE_RE = re.compile(r"^[Ss]\d{1,2}[Ee]\d{1,3}$")
_NOISE_TOKENS = {
    "1080p", "2160p", "720p", "480p",
    "BluRay", "BRRip", "DVDRip", "WEB", "WEB-DL", "WEBRip", "HDTV", "HDR", "HDR10", "DV",
    "x264", "x265", "H264", "H265", "HEVC", "AVC",
    "AAC", "AC3", "DTS", "DDP", "DD5.1", "5.1", "7.1", "FLAC",
    "REMUX", "PROPER", "REPACK", "EXTENDED", "DIRECTORS-CUT",
}
_NOISE_LOWER = {t.lower() for t in _NOISE_TOKENS}


def parse_title(filename: str) -> str:
    original_stem = PurePosixPath(filename).stem
    if not original_stem:
        return filename

    stem = original_stem
    # Группа после "-" в конце часто ник релизера: Some.Movie.2024-GROUP → отрезаем
    if "-" in stem and not _has_word_boundary(stem):
        # Не разрезаем дефис внутри слов (e.g. "Spider-Man")
        stem = stem.rsplit("-", 1)[0]

    # Разбиваем по любым из распространённых разделителей
    tokens = re.split(r"[.\s_]+", stem)
    tokens = [t for t in tokens if t]

    title_parts: list[str] = []
    suffix: str | None = None
    saw_noise = False

    for tok in tokens:
        # Год — превращаем в (YYYY) и обрываем дальнейший сбор шума
        if _YEAR_RE.match(tok):
            suffix = f"({tok})"
            break
        # S01E05 — сохраняем как суффикс
        if _SE_RE.match(tok):
            suffix = tok.upper()
            break
        # Технический шум — игнорируем
        if tok.lower() in _NOISE_LOWER:
            saw_noise = True
            continue
        # Слово выглядит как часть названия
        title_parts.append(tok)

    if not title_parts:
        # Не удалось ничего вытащить — возвращаем оригинальный stem
        return original_stem

    # Если не нашли ни года, ни эпизода, ни шумовых токенов — это не похоже
    # на торрент-имя, отдаём оригинальный stem без изменений.
    if suffix is None and not saw_noise:
        return original_stem

    title = " ".join(title_parts)
    return f"{title} {suffix}" if suffix else title


def _has_word_boundary(s: str) -> bool:
    """True если в строке есть пробел/точка/подчёркивание — значит разделители есть и без дефиса."""
    return any(c in s for c in (" ", ".", "_"))
