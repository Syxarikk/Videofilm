"""Обёртки над ffprobe для извлечения метаданных файла."""
from __future__ import annotations

import json
import logging
import subprocess

from app.metadata.types import AudioTrack

log = logging.getLogger(__name__)


def get_duration_seconds(file_path: str) -> int | None:
    """Длительность файла в секундах (округлённо до int). None — если ffprobe упал."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                file_path,
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("ffprobe failed for %s: %s", file_path, e)
        return None
    if result.returncode != 0:
        log.warning("ffprobe returned %d for %s", result.returncode, file_path)
        return None
    raw = result.stdout.strip()
    try:
        return int(round(float(raw)))
    except (ValueError, TypeError):
        log.warning("ffprobe duration unparseable for %s: %r", file_path, raw)
        return None


def probe_audio_tracks(file_path: str) -> list[AudioTrack]:
    """Список аудиодорожек в файле. Пустой список при ошибке или отсутствии аудио."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index,codec_name,channels:stream_tags=language,title",
                "-of", "json",
                file_path,
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("ffprobe (audio) failed for %s: %s", file_path, e)
        return []
    if result.returncode != 0:
        log.warning("ffprobe (audio) returned %d for %s", result.returncode, file_path)
        return []
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as e:
        log.warning("ffprobe (audio) bad JSON for %s: %s", file_path, e)
        return []
    streams = data.get("streams") or []
    tracks: list[AudioTrack] = []
    for i, s in enumerate(streams):
        tags = s.get("tags") or {}
        tracks.append(AudioTrack(
            index=i,
            codec=s.get("codec_name") or "unknown",
            language=tags.get("language"),
            title=tags.get("title"),
            channels=int(s.get("channels") or 0),
        ))
    return tracks
