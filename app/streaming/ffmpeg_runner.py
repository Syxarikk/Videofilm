"""Запуск ffmpeg-подпроцесса для on-the-fly HLS-транскодинга.

Параметры подобраны под спецификацию §5.5:
- libx264 / preset veryfast / CRF 23 (баланс качества и нагрузки CPU)
- AAC 128k
- HLS-сегменты по 6 секунд
- VOD-плейлист (hls_list_size=0)

Перемотка реализуется внешним кодом: kill старый процесс, start новый с другим seek_seconds.
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import logging

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HlsParams:
    source: str
    work_dir: str
    seek_seconds: float


def start_hls(params: HlsParams) -> subprocess.Popen:
    """Запускает ffmpeg, который будет писать playlist.m3u8 + seg_*.ts в work_dir."""
    Path(params.work_dir).mkdir(parents=True, exist_ok=True)
    playlist = str(Path(params.work_dir) / "playlist.m3u8")
    segment_pattern = str(Path(params.work_dir) / "seg_%05d.ts")

    cmd = [
        "ffmpeg",
        "-loglevel", "warning",
        "-nostdin",
    ]
    if params.seek_seconds > 0:
        cmd += ["-ss", f"{params.seek_seconds:.3f}"]
    cmd += [
        "-i", params.source,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-f", "hls",
        "-hls_time", "6",
        "-hls_list_size", "0",
        "-hls_segment_filename", segment_pattern,
        playlist,
    ]
    log.debug("ffmpeg cmd: %s", shlex.join(cmd))
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        # На Windows нет os.setsid; используем CREATE_NEW_PROCESS_GROUP вместо для kill-tree.
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )


def wait_for_first_segment(work_dir: str | Path, timeout: float = 15.0) -> bool:
    """Ждёт появления первого сегмента, чтобы плейлист был «играбельным»."""
    deadline = time.time() + timeout
    work = Path(work_dir)
    while time.time() < deadline:
        if any(work.glob("seg_*.ts")):
            return True
        time.sleep(0.1)
    return False


def kill(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    """Завершить процесс. SIGTERM, потом SIGKILL если не вышел."""
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
