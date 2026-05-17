import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.metadata.types import AudioTrack
from app.streaming.ffmpeg_runner import HlsParams, kill, start_hls, wait_for_first_segment


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


@pytest.fixture
def work_dir(tmp_path):
    return tmp_path / "hls_session"


def test_start_hls_creates_v0_segments(work_dir):
    """Для одно-вариантного случая (без аудио) ffmpeg пишет только v0/playlist.m3u8."""
    assert SAMPLE.exists()
    work_dir.mkdir()
    proc = start_hls(HlsParams(
        source=str(SAMPLE),
        work_dir=str(work_dir),
        seek_seconds=0.0,
        audio_tracks=[],
    ))
    try:
        ok = wait_for_first_segment(work_dir, timeout=15.0)
        assert ok, "ffmpeg не создал ни одного сегмента за 15 секунд"

        v0_playlist = work_dir / "v0" / "playlist.m3u8"
        assert v0_playlist.exists()
        assert "#EXTM3U" in v0_playlist.read_text()
        assert "seg_" in v0_playlist.read_text()
    finally:
        kill(proc)


def test_kill_terminates_process(work_dir):
    work_dir.mkdir()
    proc = start_hls(HlsParams(source=str(SAMPLE), work_dir=str(work_dir),
                                seek_seconds=0.0, audio_tracks=[]))
    assert proc.poll() is None
    kill(proc)
    for _ in range(20):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    assert proc.poll() is not None


def test_seek_offset_starts_later_in_video(work_dir):
    work_dir.mkdir()
    proc = start_hls(HlsParams(source=str(SAMPLE), work_dir=str(work_dir),
                                seek_seconds=5.0, audio_tracks=[]))
    try:
        ok = wait_for_first_segment(work_dir, timeout=15.0)
        assert ok
    finally:
        kill(proc)


@patch("app.streaming.ffmpeg_runner.subprocess.Popen")
def test_start_hls_cmd_for_no_audio(mock_popen):
    start_hls(HlsParams(source="/v.mkv", work_dir="/tmp/w", seek_seconds=0.0,
                         audio_tracks=[]))
    cmd = mock_popen.call_args[0][0]
    assert "-master_pl_name" in cmd
    i = cmd.index("-var_stream_map")
    assert cmd[i+1] == "v:0"


@patch("app.streaming.ffmpeg_runner.subprocess.Popen")
def test_start_hls_cmd_for_one_audio(mock_popen):
    audio = [AudioTrack(index=0, codec="aac", language="rus", title="Дубляж", channels=6)]
    start_hls(HlsParams(source="/v.mkv", work_dir="/tmp/w", seek_seconds=0.0,
                         audio_tracks=audio))
    cmd = mock_popen.call_args[0][0]
    i = cmd.index("-var_stream_map")
    var_map = cmd[i+1]
    assert "v:0,agroup:audio" in var_map
    assert "a:0,agroup:audio" in var_map
    assert "language:rus" in var_map
    assert "Дубляж" in var_map.replace("_", " ")


@patch("app.streaming.ffmpeg_runner.subprocess.Popen")
def test_start_hls_cmd_for_three_audio(mock_popen):
    audio = [
        AudioTrack(index=0, codec="ac3", language="rus", title="Дубляж", channels=6),
        AudioTrack(index=1, codec="aac", language="eng", title=None, channels=2),
        AudioTrack(index=2, codec="aac", language="rus", title="Комментарии", channels=2),
    ]
    start_hls(HlsParams(source="/v.mkv", work_dir="/tmp/w", seek_seconds=0.0,
                         audio_tracks=audio))
    cmd = mock_popen.call_args[0][0]
    assert cmd.count("-map") == 4
    map_args = [cmd[j+1] for j, x in enumerate(cmd) if x == "-map"]
    assert "0:v:0" in map_args
    assert "0:a:0" in map_args
    assert "0:a:1" in map_args
    assert "0:a:2" in map_args


@patch("app.streaming.ffmpeg_runner.subprocess.Popen")
def test_start_hls_seek_preserved(mock_popen):
    start_hls(HlsParams(source="/v.mkv", work_dir="/tmp/w", seek_seconds=42.5,
                         audio_tracks=[]))
    cmd = mock_popen.call_args[0][0]
    i = cmd.index("-ss")
    assert cmd[i+1] == "42.500"
