from pathlib import Path

from app.torrents.series_grouper import group_as_series, EpisodeFile
from app.torrents.title_parser import ParsedTitle


def _ep(path, season, ep, title="Show"):
    return EpisodeFile(
        path=Path(path),
        parsed=ParsedTitle(title=title, year=None, season=season, episode=ep, kind_hint="tv"),
    )


def test_two_episodes_returns_series_group():
    files = [
        _ep("/x/Show.S01E01.mkv", 1, 1),
        _ep("/x/Show.S01E02.mkv", 1, 2),
    ]
    group = group_as_series(files, fallback_dir_name="Show")
    assert group is not None
    assert group.title == "Show"
    assert len(group.episodes) == 2
    assert (group.episodes[0].parsed.season, group.episodes[0].parsed.episode) == (1, 1)


def test_single_episode_returns_none():
    files = [_ep("/x/Show.S01E01.mkv", 1, 1)]
    assert group_as_series(files, fallback_dir_name="Show") is None


def test_empty_returns_none():
    assert group_as_series([], fallback_dir_name="X") is None


def test_multi_season_supported():
    files = [
        _ep("/x/Show.S01E01.mkv", 1, 1),
        _ep("/x/Show.S01E02.mkv", 1, 2),
        _ep("/x/Show.S02E01.mkv", 2, 1),
    ]
    group = group_as_series(files, fallback_dir_name="Show")
    assert group is not None
    assert len(group.episodes) == 3
    seasons = {e.parsed.season for e in group.episodes}
    assert seasons == {1, 2}


def test_mixed_titles_falls_back_to_dir_name():
    files = [
        _ep("/x/Show.S01E01.mkv", 1, 1, title="Show"),
        _ep("/x/Other.S01E02.mkv", 1, 2, title="Different"),
    ]
    group = group_as_series(files, fallback_dir_name="DirName")
    assert group is not None
    assert group.title == "DirName"
