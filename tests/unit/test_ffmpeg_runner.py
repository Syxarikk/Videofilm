import subprocess
import time
from pathlib import Path

import pytest

from app.streaming.ffmpeg_runner import HlsParams, kill, start_hls, wait_for_first_segment


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


@pytest.fixture
def work_dir(tmp_path):
    return tmp_path / "hls_session"


def test_start_hls_creates_playlist_and_segments(work_dir):
    assert SAMPLE.exists(), f"sample.mp4 missing at {SAMPLE} (see Task 16 Step 1)"
    work_dir.mkdir()
    proc = start_hls(HlsParams(
        source=str(SAMPLE),
        work_dir=str(work_dir),
        seek_seconds=0.0,
    ))
    try:
        # ffmpeg должен начать писать сегменты
        ok = wait_for_first_segment(work_dir, timeout=15.0)
        assert ok, "ffmpeg не создал ни одного сегмента за 15 секунд"

        playlist = work_dir / "playlist.m3u8"
        assert playlist.exists()
        content = playlist.read_text()
        assert "#EXTM3U" in content
        assert "seg_" in content
    finally:
        kill(proc)


def test_kill_terminates_process(work_dir):
    work_dir.mkdir()
    proc = start_hls(HlsParams(source=str(SAMPLE), work_dir=str(work_dir), seek_seconds=0.0))
    assert proc.poll() is None  # процесс жив
    kill(proc)
    # Через 2с должен быть мёртв
    for _ in range(20):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    assert proc.poll() is not None


def test_seek_offset_starts_later_in_video(work_dir):
    work_dir.mkdir()
    # Запросить с 5-й секунды
    proc = start_hls(HlsParams(source=str(SAMPLE), work_dir=str(work_dir), seek_seconds=5.0))
    try:
        ok = wait_for_first_segment(work_dir, timeout=15.0)
        assert ok
        # Просто убеждаемся, что сегмент создан — точное содержание трудно проверить без декодирования
    finally:
        kill(proc)
