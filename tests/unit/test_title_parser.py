import pytest
from app.torrents.title_parser import parse_title


@pytest.mark.parametrize("raw,expected", [
    ("Some.Movie.2024.1080p.BluRay.x264.mkv", "Some Movie (2024)"),
    ("Some.Movie.2024.1080p.BluRay.x264-GROUP.mkv", "Some Movie (2024)"),
    ("Some Movie 2024 1080p BluRay.mkv", "Some Movie (2024)"),
    ("Some.Movie.2024.WEB-DL.2160p.HEVC.HDR.mkv", "Some Movie (2024)"),
    ("Movie.Title.S01E05.1080p.mkv", "Movie Title S01E05"),
    ("Some_Movie_2024.mkv", "Some Movie (2024)"),
    ("plain-name.mkv", "plain-name"),
    ("No.Year.Here.1080p.mkv", "No Year Here"),
])
def test_parse_title_examples(raw, expected):
    assert parse_title(raw) == expected
