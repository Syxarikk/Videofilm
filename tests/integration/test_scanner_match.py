import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import select

from app.metadata.types import MetadataMatch
from app.models import MediaItem
from app.torrents.scanner import scan_once
from app.torrents.types import TorrentInfo


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _qb_with(torrents: list[TorrentInfo]):
    m = MagicMock()
    m.list_torrents.return_value = torrents
    return m


def test_scan_once_populates_metadata_from_tmdb(db_factory):
    tmdb = MagicMock()
    tmdb.search.return_value = [
        {"id": 1, "title": "Sample Movie", "release_date": "2024-01-01"}
    ]
    tmdb.get_movie.return_value = MetadataMatch(
        source="tmdb", external_id=1, title="Sample Movie", year=2024,
        kind="movie", description="A test movie.",
        poster_url="https://example.com/p.jpg", genres=["Драма"], score=1.0,
    )

    with tempfile.TemporaryDirectory() as tmp:
        sym = Path(tmp) / "Sample.Movie.2024.1080p.BluRay.mkv"
        os.symlink(SAMPLE, sym)
        t = TorrentInfo(hash="aaa", name="x", progress=1.0, dlspeed=0,
                        state="uploading", size=1000, save_path=str(sym.parent),
                        content_path=str(sym), eta_seconds=-1)
        with db_factory() as s:
            added = scan_once(_qb_with([t]), s, tmdb=tmdb, kinopoisk=None)
            s.commit()

    assert added == 1
    with db_factory() as s:
        m = s.scalars(select(MediaItem)).one()
        assert m.title == "Sample Movie"
        assert m.year == 2024
        assert m.kind == "movie"
        assert m.match_status == "matched"
        assert m.match_source == "tmdb"
        assert m.tmdb_id == 1
        assert m.duration_seconds is not None
        assert m.audio_tracks is not None
        assert len(m.genres) == 1 and m.genres[0].name == "Драма"


def test_scan_once_failed_match_without_keys(db_factory, tmp_path):
    sym = tmp_path / "Unknown.Some.Title.1080p.mkv"
    os.symlink(SAMPLE, sym)
    t = TorrentInfo(hash="bbb", name="x", progress=1.0, dlspeed=0,
                    state="uploading", size=1000, save_path=str(sym.parent),
                    content_path=str(sym), eta_seconds=-1)

    with db_factory() as s:
        added = scan_once(_qb_with([t]), s, tmdb=None, kinopoisk=None)
        s.commit()

    assert added == 1
    with db_factory() as s:
        m = s.scalars(select(MediaItem)).one()
        assert m.match_status == "failed"
        assert m.match_source is None
        assert m.title == "Unknown Some Title"
