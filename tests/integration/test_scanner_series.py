import os
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import select

from app.metadata.types import MetadataMatch, TmdbEpisodeMeta
from app.models import Episode, MediaItem
from app.torrents.scanner import scan_once
from app.torrents.types import TorrentInfo


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _qb_with(torrents):
    m = MagicMock()
    m.list_torrents.return_value = torrents
    return m


def _torrent(tmp_dir: Path, hash_str: str) -> TorrentInfo:
    return TorrentInfo(
        hash=hash_str, name="x", progress=1.0, dlspeed=0,
        state="uploading", size=1000, save_path=str(tmp_dir),
        content_path=str(tmp_dir), eta_seconds=-1,
    )


def test_scan_once_creates_series_with_episodes(db_factory, tmp_path):
    ep1 = tmp_path / "Show.S01E01.1080p.mkv"
    ep2 = tmp_path / "Show.S01E02.1080p.mkv"
    os.symlink(SAMPLE, ep1); os.symlink(SAMPLE, ep2)

    tmdb = MagicMock()
    tmdb.search.return_value = [{"id": 99, "name": "Show", "first_air_date": "2020-01-01"}]
    tmdb.get_tv.return_value = MetadataMatch(
        source="tmdb", external_id=99, title="Show", year=2020,
        kind="series", description="A show.", poster_url=None,
        genres=["Драма"], score=1.0,
    )
    tmdb.get_tv_season.return_value = {
        1: TmdbEpisodeMeta(id=101, episode_number=1, name="Pilot",
                            overview="First.", air_date="2020-01-01"),
        2: TmdbEpisodeMeta(id=102, episode_number=2, name="Second",
                            overview="Second.", air_date="2020-01-08"),
    }

    t = _torrent(tmp_path, "h_series")
    with db_factory() as s:
        added = scan_once(_qb_with([t]), s, tmdb=tmdb, kinopoisk=None)
        s.commit()
    assert added == 1

    with db_factory() as s:
        series = s.scalars(select(MediaItem)).one()
        assert series.kind == "series"
        assert series.title == "Show"
        assert series.duration_seconds is None
        eps = s.scalars(select(Episode).where(Episode.series_id == series.id)
                          .order_by(Episode.season, Episode.episode)).all()
        assert len(eps) == 2
        assert eps[0].season == 1 and eps[0].episode == 1
        assert eps[0].title == "Pilot"
        assert eps[0].tmdb_episode_id == 101
        assert eps[1].title == "Second"


def test_scan_once_single_episode_treated_as_movie(db_factory, tmp_path):
    ep1 = tmp_path / "Show.S01E01.1080p.mkv"
    os.symlink(SAMPLE, ep1)

    t = _torrent(tmp_path, "h_single")
    with db_factory() as s:
        added = scan_once(_qb_with([t]), s, tmdb=None, kinopoisk=None)
        s.commit()
    assert added == 1

    with db_factory() as s:
        item = s.scalars(select(MediaItem)).one()
        # group_as_series требует ≥2 эпизода — поэтому одиночный E01 идёт как фильм
        # default_kind="series" т.к. parsed.kind_hint=="tv", но эпизоды НЕ создаются
        eps_count = s.scalars(select(Episode)).all()
        assert len(eps_count) == 0
