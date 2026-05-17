import pytest
from app.torrents.title_parser import parse_title, ParsedTitle


@pytest.mark.parametrize("raw,expected_title,expected_year,expected_season,expected_episode,expected_hint", [
    ("Some.Movie.2024.1080p.BluRay.x264.mkv", "Some Movie", 2024, None, None, "movie"),
    ("Some.Movie.2024.1080p.BluRay.x264-GROUP.mkv", "Some Movie", 2024, None, None, "movie"),
    ("Some Movie 2024 1080p BluRay.mkv", "Some Movie", 2024, None, None, "movie"),
    ("Some.Movie.2024.WEB-DL.2160p.HEVC.HDR.mkv", "Some Movie", 2024, None, None, "movie"),
    ("Movie.Title.S01E05.1080p.mkv", "Movie Title", None, 1, 5, "tv"),
    ("Some_Movie_2024.mkv", "Some Movie", 2024, None, None, "movie"),
    ("plain-name.mkv", "plain-name", None, None, None, None),
    ("No.Year.Here.1080p.mkv", "No Year Here", None, None, None, "movie"),
])
def test_parse_title_returns_parsed_title(raw, expected_title, expected_year,
                                            expected_season, expected_episode, expected_hint):
    p = parse_title(raw)
    assert isinstance(p, ParsedTitle)
    assert p.title == expected_title
    assert p.year == expected_year
    assert p.season == expected_season
    assert p.episode == expected_episode
    assert p.kind_hint == expected_hint
