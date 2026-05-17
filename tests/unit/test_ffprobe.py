import json as _json
from unittest.mock import patch, MagicMock

from app.metadata.ffprobe import get_duration_seconds, probe_audio_tracks


def _mock_run(stdout: str, returncode: int = 0):
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    return m


@patch("app.metadata.ffprobe.subprocess.run")
def test_get_duration_seconds_parses_float(mock_run):
    mock_run.return_value = _mock_run("5400.123\n")
    assert get_duration_seconds("/some/file.mkv") == 5400


@patch("app.metadata.ffprobe.subprocess.run")
def test_get_duration_seconds_returns_none_on_error(mock_run):
    mock_run.return_value = _mock_run("", returncode=1)
    assert get_duration_seconds("/bad/file.mkv") is None


@patch("app.metadata.ffprobe.subprocess.run")
def test_get_duration_seconds_returns_none_on_unparseable(mock_run):
    mock_run.return_value = _mock_run("N/A\n")
    assert get_duration_seconds("/file.mkv") is None


_FFPROBE_OUTPUT_2_TRACKS = _json.dumps({
    "streams": [
        {
            "index": 1,
            "codec_name": "ac3",
            "channels": 6,
            "tags": {"language": "rus", "title": "Дубляж"},
        },
        {
            "index": 2,
            "codec_name": "aac",
            "channels": 2,
            "tags": {"language": "eng"},
        },
    ]
})


@patch("app.metadata.ffprobe.subprocess.run")
def test_probe_audio_tracks_parses_two_streams(mock_run):
    mock_run.return_value = _mock_run(_FFPROBE_OUTPUT_2_TRACKS)
    tracks = probe_audio_tracks("/file.mkv")
    assert len(tracks) == 2
    assert tracks[0].index == 0
    assert tracks[0].codec == "ac3"
    assert tracks[0].language == "rus"
    assert tracks[0].title == "Дубляж"
    assert tracks[0].channels == 6
    assert tracks[1].index == 1
    assert tracks[1].codec == "aac"
    assert tracks[1].language == "eng"
    assert tracks[1].title is None


@patch("app.metadata.ffprobe.subprocess.run")
def test_probe_audio_tracks_no_audio(mock_run):
    mock_run.return_value = _mock_run(_json.dumps({"streams": []}))
    assert probe_audio_tracks("/file.mkv") == []


@patch("app.metadata.ffprobe.subprocess.run")
def test_probe_audio_tracks_returns_empty_on_error(mock_run):
    mock_run.return_value = _mock_run("", returncode=1)
    assert probe_audio_tracks("/file.mkv") == []


@patch("app.metadata.ffprobe.subprocess.run")
def test_probe_audio_tracks_handles_missing_tags(mock_run):
    out = _json.dumps({"streams": [{"index": 1, "codec_name": "aac", "channels": 2}]})
    mock_run.return_value = _mock_run(out)
    tracks = probe_audio_tracks("/file.mkv")
    assert len(tracks) == 1
    assert tracks[0].language is None
    assert tracks[0].title is None
