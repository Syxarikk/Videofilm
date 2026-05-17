import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import MediaItem, User
from app.torrents.scanner import scan_once
from app.torrents.types import TorrentInfo


def _make_completed_torrent_dir(tmp_path: Path) -> str:
    """Создаёт фейковый торрент-контент: папка с одним mp4-файлом."""
    folder = tmp_path / "Some.Movie.2024.1080p.BluRay.x264-GROUP"
    folder.mkdir()
    (folder / "RARBG.txt").write_text("ad")  # шумовой файл, не должен быть выбран
    big = folder / "Some.Movie.2024.1080p.BluRay.x264-GROUP.mp4"
    big.write_bytes(b"\x00" * 5_000_000)  # 5 МБ
    return str(folder)


class FakeQbClient:
    def __init__(self, torrents: list[TorrentInfo]):
        self._torrents = torrents

    def list_torrents(self) -> list[TorrentInfo]:
        return list(self._torrents)


def test_scan_once_creates_media_item_for_completed_torrent(db_factory, tmp_path):
    folder = _make_completed_torrent_dir(tmp_path)
    with db_factory() as s:
        admin = User(username="root", password_hash="x", is_admin=True, must_change_password=False)
        s.add(admin)
        s.commit()

    qb = FakeQbClient([TorrentInfo(
        hash="abc", name="Some.Movie.2024.1080p.BluRay.x264-GROUP",
        progress=1.0, dlspeed=0, state="uploading",
        size=5_000_000, save_path=str(tmp_path), content_path=folder, eta_seconds=0,
    )])
    with db_factory() as s:
        scan_once(qb, s)
        s.commit()

    with db_factory() as s:
        items = s.scalars(select(MediaItem)).all()
        assert len(items) == 1
        m = items[0]
        assert m.title == "Some Movie"  # ParsedTitle: title без года, год отдельным полем
        assert m.file_path.endswith(".mp4")
        assert m.size_bytes == 5_000_000
        assert m.torrent_hash == "abc"


def test_scan_once_skips_incomplete_torrents(db_factory, tmp_path):
    qb = FakeQbClient([TorrentInfo(
        hash="abc", name="x", progress=0.5, dlspeed=1000, state="downloading",
        size=1, save_path=str(tmp_path), content_path=str(tmp_path), eta_seconds=600,
    )])
    with db_factory() as s:
        scan_once(qb, s)
        s.commit()
    with db_factory() as s:
        assert s.scalars(select(MediaItem)).first() is None


def test_scan_once_idempotent(db_factory, tmp_path):
    folder = _make_completed_torrent_dir(tmp_path)
    qb = FakeQbClient([TorrentInfo(
        hash="abc", name="Movie", progress=1.0, dlspeed=0, state="uploading",
        size=5_000_000, save_path=str(tmp_path), content_path=folder, eta_seconds=0,
    )])
    with db_factory() as s:
        scan_once(qb, s); s.commit()
    with db_factory() as s:
        scan_once(qb, s); s.commit()
    with db_factory() as s:
        assert len(s.scalars(select(MediaItem)).all()) == 1


def test_scan_once_picks_largest_video_file(db_factory, tmp_path):
    folder = tmp_path / "Show"
    folder.mkdir()
    (folder / "tiny.mkv").write_bytes(b"\x00" * 1000)
    (folder / "big.mkv").write_bytes(b"\x00" * 10_000_000)
    (folder / "huge.txt").write_bytes(b"\x00" * 100_000_000)  # не видео

    qb = FakeQbClient([TorrentInfo(
        hash="h", name="Show", progress=1.0, dlspeed=0, state="uploading",
        size=10_001_000, save_path=str(tmp_path), content_path=str(folder), eta_seconds=0,
    )])
    with db_factory() as s:
        scan_once(qb, s); s.commit()
    with db_factory() as s:
        m = s.scalars(select(MediaItem)).one()
        assert m.file_path.endswith("big.mkv")
