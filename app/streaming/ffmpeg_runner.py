"""On-the-fly HLS-транскодинг с мульти-вариантным master playlist.

Всегда генерируем master + v0 (видео) + v1.. (аудио, если есть).
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import logging

from app.metadata.types import AudioTrack

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HlsParams:
    source: str
    work_dir: str
    seek_seconds: float
    audio_tracks: list[AudioTrack] = field(default_factory=list)


def _sanitize_name(name: str) -> str:
    # var_stream_map name не может содержать пробелы и запятые
    return name.replace(",", " ").replace(" ", "_") or "Track"


def _build_var_stream_map(audio_tracks: list[AudioTrack]) -> str:
    if not audio_tracks:
        return "v:0"
    parts = ["v:0,agroup:audio"]
    for i, t in enumerate(audio_tracks):
        name = _sanitize_name(t.title or t.language or f"Track {i+1}")
        lang = t.language or "und"
        parts.append(f"a:{i},agroup:audio,language:{lang},name:{name}")
    return " ".join(parts)


def start_hls(params: HlsParams) -> subprocess.Popen:
    Path(params.work_dir).mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-loglevel", "warning", "-nostdin"]
    if params.seek_seconds > 0:
        cmd += ["-ss", f"{params.seek_seconds:.3f}"]
    cmd += ["-i", params.source, "-map", "0:v:0"]
    for t in params.audio_tracks:
        cmd += ["-map", f"0:a:{t.index}"]

    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    if params.audio_tracks:
        cmd += ["-c:a", "aac", "-b:a", "128k"]

    cmd += [
        "-f", "hls",
        "-hls_time", "6",
        "-hls_list_size", "0",
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", _build_var_stream_map(params.audio_tracks),
        "-hls_segment_filename", f"{params.work_dir}/v%v/seg_%05d.ts",
        f"{params.work_dir}/v%v/playlist.m3u8",
    ]

    log.debug("ffmpeg cmd: %s", shlex.join(cmd))
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )


def wait_for_first_segment(work_dir: str | Path, timeout: float = 15.0) -> bool:
    """Ждёт появления первого сегмента в v0/."""
    deadline = time.time() + timeout
    work = Path(work_dir) / "v0"
    while time.time() < deadline:
        if work.exists() and any(work.glob("seg_*.ts")):
            return True
        time.sleep(0.1)
    return False


def kill(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
    except ProcessLookupError:
        pass
