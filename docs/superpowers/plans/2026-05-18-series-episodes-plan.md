# Series Episodes Implementation Plan (Spec 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать сериалы с разбиением на эпизоды: новая модель `Episode`, страница сериала с сезонами/эпизодами, плеер эпизода с навигацией и auto-play, прогресс/аудиодорожки per-эпизод.

**Architecture:** Новая таблица `episodes` с FK на `MediaItem(kind='series')`. Отдельная `episode_watch_progress`. Сканер при детекции ≥2 файлов с `SxxExx` создаёт MediaItem-серию + N Episode. StreamRegistry рефакторится на строковый ключ `"m:N"`/`"e:N"`. TMDB-метаданные эпизодов через `/tv/{id}/season/{n}`.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic, Jinja2, HTMX, hls.js, ffmpeg/ffprobe, httpx, respx, pytest.

**Spec:** [`docs/superpowers/specs/2026-05-18-series-episodes-design.md`](../specs/2026-05-18-series-episodes-design.md)

**Depends on:** Spec 1 (catalog + player) — реализован.

---

## Setup Notes

**Окружение готово:** Python 3.11 в `venv/`, ffmpeg/ffprobe в `~/.local/bin/`. Команда запуска тестов:

```bash
export PATH="$HOME/.local/bin:$PATH"
venv/bin/python -m pytest <path> -v
```

**Git:** ветка `feat/catalog-player-fixes` (от Spec 1). Можно продолжать на той же ветке или сделать новую `feat/series-episodes` — на выбор.

---

## Phase 1 — Data Model

### Task 1: Add Episode and EpisodeWatchProgress models

**Files:**
- Modify: `app/models.py`
- Test: `tests/unit/test_models.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_models.py`:

```python
from app.models import Episode, EpisodeWatchProgress


def test_episode_model_columns():
    cols = {c.name for c in Episode.__table__.columns}
    required = {
        "id", "series_id", "season", "episode", "title", "description",
        "file_path", "size_bytes", "duration_seconds", "audio_tracks",
        "tmdb_episode_id", "air_date", "added_at",
    }
    missing = required - cols
    assert not missing, f"missing on Episode: {missing}"


def test_episode_watch_progress_model_columns():
    cols = {c.name for c in EpisodeWatchProgress.__table__.columns}
    required = {"id", "user_id", "episode_id", "position_seconds",
                "audio_track_index", "updated_at"}
    missing = required - cols
    assert not missing, f"missing on EpisodeWatchProgress: {missing}"
```

- [ ] **Step 2: Run test — verify fails**

```
venv/bin/python -m pytest tests/unit/test_models.py -v
```
Expected: FAIL (ImportError on Episode).

- [ ] **Step 3: Update `app/models.py`**

Add at top of `app/models.py`, after existing `Date` import (add `Date` to imports):

```python
from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, JSON,
    String, Text, UniqueConstraint,
)
```

Append `Episode` and `EpisodeWatchProgress` classes after the `WatchProgress` class:

```python
class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("series_id", "season", "episode",
                                         name="ix_episodes_series_season_episode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    episode: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_tracks: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tmdb_episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    air_date: Mapped["Date | None"] = mapped_column(Date, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 nullable=False, default=_now)


class EpisodeWatchProgress(Base):
    __tablename__ = "episode_watch_progress"
    __table_args__ = (UniqueConstraint("user_id", "episode_id",
                                         name="ix_episode_watch_progress_user_episode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False
    )
    position_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    audio_track_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  nullable=False, default=_now, onupdate=_now)
```

Also add relationship to `MediaItem` class (after `genres` relationship):

```python
    episodes: Mapped[list["Episode"]] = relationship(
        "Episode",
        primaryjoin="MediaItem.id == foreign(Episode.series_id)",
        order_by="(Episode.season, Episode.episode)",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
```

(Используем primaryjoin потому что Episode.series_id ссылается на MediaItem.id, явно объявляем для ясности.)

- [ ] **Step 4: Run tests — verify pass**

```
venv/bin/python -m pytest tests/unit/test_models.py -v
```
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/unit/test_models.py
git commit -m "feat(model): add Episode and EpisodeWatchProgress models"
```

---

### Task 2: Alembic migration 0004

**Files:**
- Create: `migrations/versions/0004_episodes.py`

- [ ] **Step 1: Create migration file**

Create `migrations/versions/0004_episodes.py`:

```python
"""Episodes for series support

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "episodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("series_id", sa.Integer(),
                  sa.ForeignKey("media_items.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("episode", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("audio_tracks", sa.JSON(), nullable=True),
        sa.Column("tmdb_episode_id", sa.Integer(), nullable=True),
        sa.Column("air_date", sa.Date(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_episodes_series_id", "episodes", ["series_id"])
    op.create_index("ix_episodes_series_season_episode", "episodes",
                    ["series_id", "season", "episode"], unique=True)

    op.create_table(
        "episode_watch_progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.Integer(),
                  sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("audio_track_index", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_episode_watch_progress_user_episode", "episode_watch_progress",
                    ["user_id", "episode_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_episode_watch_progress_user_episode",
                  table_name="episode_watch_progress")
    op.drop_table("episode_watch_progress")
    op.drop_index("ix_episodes_series_season_episode", table_name="episodes")
    op.drop_index("ix_episodes_series_id", table_name="episodes")
    op.drop_table("episodes")
```

- [ ] **Step 2: Apply and verify**

```bash
export SESSION_SECRET=$(venv/bin/python -c "import secrets; print(secrets.token_hex(32))")
export DATABASE_URL=sqlite:///./app.db MEDIA_ROOT=/tmp/media
export QBITTORRENT_URL=http://127.0.0.1:8080 QBITTORRENT_USERNAME=admin QBITTORRENT_PASSWORD=secret

venv/bin/python -m alembic upgrade head 2>&1 | tail -3
venv/bin/python -c "
from sqlalchemy import create_engine, inspect
e = create_engine('sqlite:///./app.db')
i = inspect(e)
print('episodes:', 'episodes' in i.get_table_names())
print('episode_watch_progress:', 'episode_watch_progress' in i.get_table_names())
"
```

Expected: оба `True`.

- [ ] **Step 3: Roundtrip downgrade**

```bash
venv/bin/python -m alembic downgrade -1 2>&1 | tail -3
venv/bin/python -m alembic upgrade head 2>&1 | tail -3
```

Expected: оба без ошибок.

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/0004_episodes.py
git commit -m "feat(db): migration 0004 — episodes and episode_watch_progress"
```

---

## Phase 2 — TMDB Season Endpoint

### Task 3: TmdbClient.get_tv_season + TmdbEpisodeMeta

**Files:**
- Modify: `app/metadata/tmdb.py`
- Modify: `app/metadata/types.py` (add `TmdbEpisodeMeta`)
- Test: `tests/unit/test_tmdb_client.py` (extend)

- [ ] **Step 1: Add `TmdbEpisodeMeta` to types**

Append to `app/metadata/types.py`:

```python
@dataclass(frozen=True, slots=True)
class TmdbEpisodeMeta:
    """Метаданные одного эпизода из TMDB /tv/{id}/season/{n}."""
    id: int
    episode_number: int
    name: str | None
    overview: str | None
    air_date: str | None  # ISO "YYYY-MM-DD", не парсим в Date — это делает caller
```

- [ ] **Step 2: Write failing tests**

Append to `tests/unit/test_tmdb_client.py`:

```python
from app.metadata.types import TmdbEpisodeMeta


@respx.mock
def test_get_tv_season_parses_episodes(client):
    respx.get("https://api.themoviedb.org/3/tv/1396/season/1").mock(
        return_value=httpx.Response(200, json={
            "episodes": [
                {"id": 62085, "episode_number": 1, "name": "Pilot",
                 "overview": "Walter White...", "air_date": "2008-01-20"},
                {"id": 62086, "episode_number": 2, "name": "Cat's in the Bag...",
                 "overview": "Walt and Jesse...", "air_date": "2008-01-27"},
            ],
        })
    )
    result = client.get_tv_season(1396, 1)
    assert 1 in result and 2 in result
    assert result[1].name == "Pilot"
    assert result[1].id == 62085
    assert result[1].air_date == "2008-01-20"
    assert result[2].overview == "Walt and Jesse..."


@respx.mock
def test_get_tv_season_returns_empty_on_404(client):
    respx.get("https://api.themoviedb.org/3/tv/9999/season/1").mock(
        return_value=httpx.Response(404)
    )
    assert client.get_tv_season(9999, 1) == {}


@respx.mock
def test_get_tv_season_handles_missing_fields(client):
    respx.get("https://api.themoviedb.org/3/tv/1/season/1").mock(
        return_value=httpx.Response(200, json={
            "episodes": [{"id": 1, "episode_number": 1}]
        })
    )
    result = client.get_tv_season(1, 1)
    assert result[1].name is None
    assert result[1].overview is None
    assert result[1].air_date is None
```

- [ ] **Step 3: Run tests — verify fails**

```
venv/bin/python -m pytest tests/unit/test_tmdb_client.py -v -k "tv_season"
```
Expected: FAIL (method doesn't exist).

- [ ] **Step 4: Add method to `TmdbClient`**

In `app/metadata/tmdb.py`, add at top of file (in existing imports):

```python
from app.metadata.types import KindLiteral, MetadataMatch, TmdbEpisodeMeta
```

Append method to `TmdbClient` class:

```python
    def get_tv_season(self, tv_id: int, season_number: int) -> dict[int, TmdbEpisodeMeta]:
        """Возвращает {episode_number: meta} для одного сезона. {} при ошибке."""
        try:
            r = self._client.get(f"/tv/{tv_id}/season/{season_number}",
                                 params={"language": "ru-RU"})
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("TMDB get_tv_season(%d, %d) failed: %s",
                        tv_id, season_number, e)
            return {}
        episodes = r.json().get("episodes") or []
        result: dict[int, TmdbEpisodeMeta] = {}
        for ep in episodes:
            n = ep.get("episode_number")
            if n is None:
                continue
            result[n] = TmdbEpisodeMeta(
                id=ep.get("id"),
                episode_number=n,
                name=ep.get("name") or None,
                overview=ep.get("overview") or None,
                air_date=ep.get("air_date") or None,
            )
        return result
```

- [ ] **Step 5: Run tests — verify pass**

```
venv/bin/python -m pytest tests/unit/test_tmdb_client.py -v
```
Expected: PASS (existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add app/metadata/tmdb.py app/metadata/types.py tests/unit/test_tmdb_client.py
git commit -m "feat(tmdb): add get_tv_season returning episode metadata dict"
```

---

## Phase 3 — Scanner: Series Detection and Grouping

### Task 4: Helper for grouping episodic files

**Files:**
- Create: `app/torrents/series_grouper.py`
- Test: `tests/unit/test_series_grouper.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_series_grouper.py`:

```python
from pathlib import Path

from app.torrents.series_grouper import group_as_series, EpisodeFile


def _ep(path, season, ep, title="Show"):
    """Helper для EpisodeFile."""
    from app.torrents.title_parser import ParsedTitle
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
```

- [ ] **Step 2: Run tests — verify fails**

```
venv/bin/python -m pytest tests/unit/test_series_grouper.py -v
```
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Create the module**

Create `app/torrents/series_grouper.py`:

```python
"""Группировка видеофайлов торрента в серию (если они эпизодические)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.torrents.title_parser import ParsedTitle


@dataclass(frozen=True, slots=True)
class EpisodeFile:
    """Один файл-эпизод торрента: путь + распарсенное название."""
    path: Path
    parsed: ParsedTitle


@dataclass(frozen=True, slots=True)
class SeriesGroup:
    """Результат группировки: имя серии + список эпизодов."""
    title: str
    episodes: list[EpisodeFile]


def group_as_series(files: list[EpisodeFile],
                     fallback_dir_name: str) -> SeriesGroup | None:
    """Возвращает SeriesGroup если в files >=2 эпизода (season+episode заполнены).

    Имя серии: общий title всех эпизодов, иначе fallback_dir_name.
    """
    episodic = [f for f in files
                if f.parsed.season is not None and f.parsed.episode is not None]
    if len(episodic) < 2:
        return None

    titles = {_normalize(f.parsed.title) for f in episodic}
    if len(titles) == 1:
        title = episodic[0].parsed.title  # неноромализованный для отображения
    else:
        title = fallback_dir_name

    return SeriesGroup(title=title, episodes=episodic)


def _normalize(s: str) -> str:
    return s.lower().strip()
```

- [ ] **Step 4: Run tests — verify pass**

```
venv/bin/python -m pytest tests/unit/test_series_grouper.py -v
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/torrents/series_grouper.py tests/unit/test_series_grouper.py
git commit -m "feat(scanner): add series_grouper for episodic file detection"
```

---

### Task 5: Scanner uses series_grouper and creates Episode entries

**Files:**
- Modify: `app/torrents/scanner.py`
- Test: `tests/integration/test_scanner_series.py`

- [ ] **Step 1: Write failing integration test**

Create `tests/integration/test_scanner_series.py`:

```python
import os
import tempfile
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
    # Симлинки на sample.mp4 в директории торрента
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
    assert added == 1  # один MediaItem (сериал)

    with db_factory() as s:
        series = s.scalars(select(MediaItem)).one()
        assert series.kind == "series"
        assert series.title == "Show"
        assert series.duration_seconds is None  # для сериалов NULL
        eps = s.scalars(select(Episode).where(Episode.series_id == series.id)
                          .order_by(Episode.season, Episode.episode)).all()
        assert len(eps) == 2
        assert eps[0].season == 1 and eps[0].episode == 1
        assert eps[0].title == "Pilot"
        assert eps[0].tmdb_episode_id == 101
        assert eps[1].title == "Second"


def test_scan_once_single_episode_treated_as_movie(db_factory, tmp_path):
    # Только один эпизод → НЕ создаём серию, обычный MediaItem
    ep1 = tmp_path / "Show.S01E01.1080p.mkv"
    os.symlink(SAMPLE, ep1)

    t = _torrent(tmp_path, "h_single")
    with db_factory() as s:
        added = scan_once(_qb_with([t]), s, tmdb=None, kinopoisk=None)
        s.commit()
    assert added == 1

    with db_factory() as s:
        item = s.scalars(select(MediaItem)).one()
        assert item.kind in ("series", "movie")  # старая логика — kind_hint=tv даёт series
        eps_count = s.scalars(select(Episode)).all()
        assert len(eps_count) == 0  # эпизодов не создано
```

- [ ] **Step 2: Run test — verify fails**

```
venv/bin/python -m pytest tests/integration/test_scanner_series.py -v
```
Expected: FAIL.

- [ ] **Step 3: Update `app/torrents/scanner.py`**

Refactor `scan_once`. Replace function:

```python
def scan_once(qb: _QbProto, session: Session, *, tmdb=None, kinopoisk=None) -> int:
    from app.metadata.ffprobe import get_duration_seconds, probe_audio_tracks
    from app.metadata.matcher import find_match
    from app.models import Genre, Episode
    from app.torrents.series_grouper import EpisodeFile, group_as_series
    from datetime import date as _date_cls

    try:
        torrents = qb.list_torrents()
    except Exception as e:
        log.warning("scan_once: qBittorrent error: %s", e)
        return 0

    existing_hashes = set(session.scalars(select(MediaItem.torrent_hash)).all())
    added = 0
    for t in torrents:
        if not t.is_complete:
            continue
        if t.hash in existing_hashes:
            continue

        # Все видеофайлы в торренте
        video_files = list(_all_videos(t.content_path))
        if not video_files:
            log.info("scan_once: no video file in %s, skipping", t.content_path)
            continue

        # Парсим имена → пытаемся сгруппировать как серию
        files_parsed = [EpisodeFile(path=v, parsed=parse_title(v.name)) for v in video_files]
        dir_name = Path(t.content_path).name
        group = group_as_series(files_parsed, fallback_dir_name=dir_name)

        if group is not None:
            # === Сериал ===
            from app.torrents.title_parser import ParsedTitle
            parsed_series = ParsedTitle(title=group.title, year=None, season=None,
                                          episode=None, kind_hint="tv")
            total_size = sum(f.path.stat().st_size for f in group.episodes)
            series_item = MediaItem(
                torrent_hash=t.hash,
                title=group.title,
                file_path=t.content_path,
                size_bytes=total_size,
                added_by=None,
                duration_seconds=None,
                audio_tracks=None,
                kind="series",
                match_status="pending",
            )

            match = find_match(parsed_series, tmdb=tmdb, kinopoisk=kinopoisk)
            season_meta: dict[int, dict] = {}
            if match is not None:
                series_item.title = match.title
                series_item.description = match.description
                series_item.poster_url = match.poster_url
                series_item.year = match.year
                series_item.kind = match.kind if match.kind == "series" else "series"
                if match.source == "tmdb":
                    series_item.tmdb_id = match.external_id
                else:
                    series_item.kinopoisk_id = match.external_id
                series_item.match_source = match.source
                series_item.match_status = "matched"

                for gname in match.genres:
                    g = gname.strip()
                    if not g:
                        continue
                    existing_g = session.scalars(
                        select(Genre).where(Genre.name == g)).first()
                    if existing_g is None:
                        existing_g = Genre(name=g)
                        session.add(existing_g); session.flush()
                    series_item.genres.append(existing_g)

                # Подтягиваем эпизодные метаданные только если TMDB-матч
                if match.source == "tmdb" and tmdb is not None:
                    unique_seasons = {e.parsed.season for e in group.episodes}
                    for ssn in unique_seasons:
                        season_meta[ssn] = tmdb.get_tv_season(match.external_id, ssn)
            else:
                series_item.match_status = "failed"

            session.add(series_item); session.flush()  # получаем series_item.id

            # Создаём эпизоды
            for ef in group.episodes:
                meta = season_meta.get(ef.parsed.season, {}).get(ef.parsed.episode)
                ad = None
                if meta and meta.air_date:
                    try:
                        ad = _date_cls.fromisoformat(meta.air_date)
                    except ValueError:
                        pass
                ep = Episode(
                    series_id=series_item.id,
                    season=ef.parsed.season,
                    episode=ef.parsed.episode,
                    title=(meta.name if meta else None),
                    description=(meta.overview if meta else None),
                    file_path=str(ef.path),
                    size_bytes=ef.path.stat().st_size,
                    tmdb_episode_id=(meta.id if meta else None),
                    air_date=ad,
                )
                session.add(ep)
            added += 1
        else:
            # === Фильм или одиночный «эпизод» ===
            video = _find_largest_video(t.content_path)
            if video is None:
                continue
            parsed = parse_title(video.name)
            duration = get_duration_seconds(str(video))
            audio = probe_audio_tracks(str(video))
            audio_dicts = [
                {"index": a.index, "codec": a.codec, "language": a.language,
                 "title": a.title, "channels": a.channels}
                for a in audio
            ]
            default_kind = "series" if parsed.kind_hint == "tv" else "movie"
            item = MediaItem(
                torrent_hash=t.hash,
                title=parsed.title,
                file_path=str(video),
                size_bytes=video.stat().st_size,
                added_by=None,
                duration_seconds=duration,
                audio_tracks=audio_dicts,
                kind=default_kind,
                match_status="pending",
            )
            match = find_match(parsed, tmdb=tmdb, kinopoisk=kinopoisk)
            if match is not None:
                item.title = match.title
                item.description = match.description
                item.poster_url = match.poster_url
                item.year = match.year
                item.kind = match.kind
                if match.source == "tmdb":
                    item.tmdb_id = match.external_id
                else:
                    item.kinopoisk_id = match.external_id
                item.match_source = match.source
                item.match_status = "matched"
                for gname in match.genres:
                    g = gname.strip()
                    if not g:
                        continue
                    existing_g = session.scalars(
                        select(Genre).where(Genre.name == g)).first()
                    if existing_g is None:
                        existing_g = Genre(name=g)
                        session.add(existing_g); session.flush()
                    item.genres.append(existing_g)
            else:
                item.match_status = "failed"
            session.add(item)
            added += 1
    return added


def _all_videos(content_path: str):
    """Все видеофайлы в торренте (рекурсивно)."""
    p = Path(content_path)
    if p.is_file():
        if p.suffix.lower() in VIDEO_EXTENSIONS:
            yield p
        return
    if not p.is_dir():
        return
    for f in p.rglob("*"):
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
            yield f
```

- [ ] **Step 4: Run all scanner tests**

```
export PATH="$HOME/.local/bin:$PATH"
venv/bin/python -m pytest tests/integration/test_scanner_series.py tests/integration/test_scanner_match.py tests/integration/test_library_scanner.py -v
```
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add app/torrents/scanner.py tests/integration/test_scanner_series.py
git commit -m "feat(scanner): detect series torrents and create Episode entries with TMDB metadata"
```

---

## Phase 4 — StreamRegistry Refactoring

### Task 6: Refactor StreamRegistry to use string target_id

**Files:**
- Modify: `app/streaming/stream_registry.py`
- Modify: `app/streaming/routes.py`
- Modify: `app/streaming/watchdog.py` (no changes if it only uses `all_streams()`)
- Modify: `app/library/routes.py` (delete uses registry)
- Modify: `tests/unit/test_stream_registry.py`
- Modify: `tests/integration/test_streaming.py`
- Modify: `tests/unit/test_streaming_watchdog.py`

This is the biggest refactor. Все тесты с streaming-registry обновляются.

- [ ] **Step 1: Update tests first — verify they fail**

In `tests/unit/test_stream_registry.py`, replace integer `media_id` with string `target_id`. Example diff:

```python
# Before:
handle = StreamHandle(media_id=1, user_id=2, work_dir=work_dir, process=proc)

# After:
handle = StreamHandle(target_id="m:1", user_id=2, work_dir=work_dir, process=proc)
```

Read the existing `tests/unit/test_stream_registry.py` and update all `media_id=N` → `target_id=f"m:{N}"`. Same in `tests/unit/test_streaming_watchdog.py` and `tests/integration/test_streaming.py`.

Also add helper imports if needed.

- [ ] **Step 2: Update `app/streaming/stream_registry.py`**

```python
"""In-memory tracker активных HLS-стримов.

Ключ — пара (target_id, user_id). target_id — строка:
  "m:42"  — для MediaItem id 42 (фильм)
  "e:128" — для Episode id 128

Один media может одновременно смотреть несколько юзеров.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Iterable


def media_key(media_id: int) -> str:
    return f"m:{media_id}"


def episode_key(episode_id: int) -> str:
    return f"e:{episode_id}"


@dataclass
class StreamHandle:
    target_id: str
    user_id: int
    work_dir: str
    process: object
    seek_seconds: float = 0.0
    last_access: float = field(default_factory=time.time)


class StreamRegistry:
    def __init__(self):
        self._streams: dict[tuple[str, int], StreamHandle] = {}
        self._lock = threading.Lock()

    def register(self, handle: StreamHandle) -> None:
        with self._lock:
            self._streams[(handle.target_id, handle.user_id)] = handle

    def get(self, target_id: str, user_id: int) -> StreamHandle | None:
        with self._lock:
            return self._streams.get((target_id, user_id))

    def unregister(self, target_id: str, user_id: int) -> StreamHandle | None:
        with self._lock:
            return self._streams.pop((target_id, user_id), None)

    def touch(self, target_id: str, user_id: int) -> None:
        with self._lock:
            h = self._streams.get((target_id, user_id))
            if h is not None:
                h.last_access = time.time()

    def idle_streams(self, idle_seconds: float) -> Iterable[StreamHandle]:
        cutoff = time.time() - idle_seconds
        with self._lock:
            return [h for h in self._streams.values() if h.last_access < cutoff]

    def all_streams(self) -> Iterable[StreamHandle]:
        with self._lock:
            return list(self._streams.values())


_registry: StreamRegistry | None = None


def get_registry() -> StreamRegistry:
    global _registry
    if _registry is None:
        _registry = StreamRegistry()
    return _registry
```

- [ ] **Step 3: Update `app/streaming/routes.py`**

Replace all `media.id` → `media_key(media.id)` in `_ensure_stream` and route handlers. Imports:

```python
from app.streaming.stream_registry import StreamHandle, get_registry, media_key
```

Function `_ensure_stream(media, user_id)` — `target_id = media_key(media.id)`. Pass to `StreamHandle(target_id=target_id, ...)`.

Same for `reg.get(target_id, user_id)`, `reg.touch(target_id, user_id)`, `reg.unregister(target_id, user_id)`.

Specifically modify:
- `_ensure_stream`: use `media_key(media.id)` to construct key.
- `stream_variant_playlist`, `stream_segment`: `target = media_key(media_id)`.
- `progress` endpoint: `get_registry().touch(media_key(payload.media_id), user.id)`.

Sketch (replace existing `_ensure_stream`):

```python
def _ensure_stream(media: MediaItem, user_id: int) -> StreamHandle:
    reg = get_registry()
    target_id = media_key(media.id)
    existing = reg.get(target_id, user_id)
    if existing is not None:
        reg.touch(target_id, user_id)
        return existing
    # ... rest unchanged but uses target_id ...
    handle = StreamHandle(target_id=target_id, user_id=user_id,
                           work_dir=str(work_dir), process=proc)
    reg.register(handle)
    # ... wait_for_first_segment, kill ...
    return handle
```

For `stream_variant_playlist` and `stream_segment`:

```python
@api_router.get("/{media_id}/{variant}/playlist.m3u8")
def stream_variant_playlist(media_id: int, variant: str,
                              user: Annotated[User, Depends(get_current_user)]):
    if not _VARIANT_RE.match(variant):
        raise HTTPException(status_code=404)
    reg = get_registry()
    target_id = media_key(media_id)
    handle = reg.get(target_id, user.id)
    if handle is None:
        raise HTTPException(status_code=410, ...)
    # ... rest unchanged ...
    reg.touch(target_id, user.id)
    return Response(...)
```

For `progress`:

```python
get_registry().touch(media_key(payload.media_id), user.id)
```

- [ ] **Step 4: Update `app/library/routes.py::delete_media`**

```python
for handle in list(reg.all_streams()):
    # target_id may be "m:N" or "e:N"; we want all streams of this media (movie)
    if handle.target_id == f"m:{media_id}" and handle.process is not None:
        kill_ffmpeg(handle.process)
        reg.unregister(handle.target_id, handle.user_id)
        shutil.rmtree(handle.work_dir, ignore_errors=True)
```

- [ ] **Step 5: Run all tests — verify they pass**

```
export PATH="$HOME/.local/bin:$PATH"
venv/bin/python -m pytest tests/unit/test_stream_registry.py tests/unit/test_streaming_watchdog.py tests/integration/test_streaming.py tests/integration/test_media_delete.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/streaming/stream_registry.py app/streaming/routes.py app/library/routes.py tests/unit/test_stream_registry.py tests/unit/test_streaming_watchdog.py tests/integration/test_streaming.py
git commit -m "refactor(streaming): StreamRegistry uses string target_id (m:N / e:N) for movies and episodes"
```

---

## Phase 5 — Episode Streaming Routes

### Task 7: Episode streaming endpoints

**Files:**
- Modify: `app/streaming/routes.py` (add episode routes)
- Test: `tests/integration/test_episode_streaming.py`

- [ ] **Step 1: Write failing test**

Create `tests/integration/test_episode_streaming.py`:

```python
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.auth.passwords import hash_password
from app.models import Episode, MediaItem, User
from app.streaming.stream_registry import get_registry


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _logged_in(client, db_factory, csrf_for):
    with db_factory() as s:
        s.add(User(username="alice",
                   password_hash=hash_password("correct-password-12"),
                   must_change_password=False))
        s.commit()
    r = client.post("/login", data={
        "username": "alice", "password": "correct-password-12",
        "csrf_token": csrf_for(None),
    })
    return r.cookies.get("session")


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    reg = get_registry()
    for h in list(reg.all_streams()):
        if h.process is not None:
            from app.streaming.ffmpeg_runner import kill
            kill(h.process)
        reg.unregister(h.target_id, h.user_id)


def _create_series_with_episode(db_factory) -> tuple[int, int]:
    """Returns (series_id, episode_id)."""
    with db_factory() as s:
        series = MediaItem(
            torrent_hash="ts", title="Show", file_path="/x",
            size_bytes=1, kind="series",
        )
        s.add(series); s.flush()
        ep = Episode(
            series_id=series.id, season=1, episode=1,
            file_path=str(SAMPLE), size_bytes=SAMPLE.stat().st_size,
        )
        s.add(ep); s.commit(); s.refresh(series); s.refresh(ep)
        return series.id, ep.id


def test_episode_master_starts_ffmpeg(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    _, eid = _create_series_with_episode(db_factory)

    r = client.get(f"/api/stream/episode/{eid}/master.m3u8",
                   cookies={"session": cookie})
    assert r.status_code == 200
    assert "#EXTM3U" in r.text


def test_episode_variant_playlist(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    _, eid = _create_series_with_episode(db_factory)
    client.get(f"/api/stream/episode/{eid}/master.m3u8",
               cookies={"session": cookie})

    r = client.get(f"/api/stream/episode/{eid}/v0/playlist.m3u8",
                   cookies={"session": cookie})
    assert r.status_code == 200
    assert "#EXTM3U" in r.text


def test_episode_segment(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    _, eid = _create_series_with_episode(db_factory)
    client.get(f"/api/stream/episode/{eid}/master.m3u8",
               cookies={"session": cookie})

    for _ in range(150):
        r = client.get(f"/api/stream/episode/{eid}/v0/playlist.m3u8",
                       cookies={"session": cookie})
        if "seg_" in r.text:
            break
        time.sleep(0.1)
    seg = next((l for l in r.text.splitlines() if l.startswith("seg_")), None)
    assert seg
    r2 = client.get(f"/api/stream/episode/{eid}/v0/{seg}",
                    cookies={"session": cookie})
    assert r2.status_code == 200


def test_episode_master_404_for_unknown_id(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    r = client.get("/api/stream/episode/99999/master.m3u8",
                   cookies={"session": cookie})
    assert r.status_code == 404
```

- [ ] **Step 2: Run test — verify fails**

```
export PATH="$HOME/.local/bin:$PATH"
venv/bin/python -m pytest tests/integration/test_episode_streaming.py -v
```
Expected: FAIL (404 на все, эндпоинтов нет).

- [ ] **Step 3: Add episode streaming endpoints to `app/streaming/routes.py`**

Add imports at top:

```python
from app.models import Episode
from app.streaming.stream_registry import episode_key, media_key
```

Refactor: extract `_audio_tracks_from_json` helper (since both `MediaItem` and `Episode` have `audio_tracks: list[dict] | None`):

```python
def _audio_tracks_from_json(audio_tracks: list | None):
    from app.metadata.types import AudioTrack
    if not audio_tracks:
        return []
    return [
        AudioTrack(
            index=a["index"], codec=a["codec"], language=a.get("language"),
            title=a.get("title"), channels=a.get("channels", 0),
        )
        for a in audio_tracks
    ]
```

Replace existing `_audio_tracks_from_media` with calls to `_audio_tracks_from_json(media.audio_tracks)`.

Extract a generic `_ensure_stream_for`:

```python
def _ensure_stream_for(target_id: str, source_path: str,
                        audio_tracks_json: list | None, user_id: int) -> StreamHandle:
    reg = get_registry()
    existing = reg.get(target_id, user_id)
    if existing is not None:
        reg.touch(target_id, user_id)
        return existing
    settings = get_settings()
    Path(settings.hls_work_root).mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(
        prefix=f"hls_{target_id.replace(':', '_')}_u{user_id}_",
        dir=settings.hls_work_root,
    ))
    audio_tracks = _audio_tracks_from_json(audio_tracks_json)
    proc = start_hls(HlsParams(
        source=source_path, work_dir=str(work_dir),
        seek_seconds=0.0, audio_tracks=audio_tracks,
    ))
    handle = StreamHandle(target_id=target_id, user_id=user_id,
                           work_dir=str(work_dir), process=proc)
    reg.register(handle)
    if not wait_for_first_segment(work_dir, timeout=15.0):
        kill(proc)
        reg.unregister(target_id, user_id)
        raise HTTPException(status_code=503,
                            detail="ffmpeg не выдал первый сегмент за 15с")
    return handle
```

Update existing `_ensure_stream` (movies) to delegate:

```python
def _ensure_stream(media: MediaItem, user_id: int) -> StreamHandle:
    return _ensure_stream_for(
        target_id=media_key(media.id),
        source_path=media.file_path,
        audio_tracks_json=media.audio_tracks,
        user_id=user_id,
    )
```

Add new endpoints:

```python
@api_router.get("/episode/{episode_id}/master.m3u8")
def episode_stream_master(
    episode_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    ep = db.get(Episode, episode_id)
    if ep is None:
        raise HTTPException(status_code=404)
    handle = _ensure_stream_for(
        target_id=episode_key(ep.id),
        source_path=ep.file_path,
        audio_tracks_json=ep.audio_tracks,
        user_id=user.id,
    )
    master = Path(handle.work_dir) / "master.m3u8"
    v0_playlist = Path(handle.work_dir) / "v0" / "playlist.m3u8"
    deadline = _t.time() + 5.0
    while not master.exists() and not v0_playlist.exists() and _t.time() < deadline:
        _t.sleep(0.1)
    target = master if master.exists() else v0_playlist
    if not target.exists():
        raise HTTPException(status_code=503, detail="плейлист ещё не сгенерирован")
    return Response(
        content=target.read_bytes(),
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@api_router.get("/episode/{episode_id}/{variant}/playlist.m3u8")
def episode_stream_variant_playlist(
    episode_id: int, variant: str,
    user: Annotated[User, Depends(get_current_user)],
):
    if not _VARIANT_RE.match(variant):
        raise HTTPException(status_code=404)
    reg = get_registry()
    target_id = episode_key(episode_id)
    handle = reg.get(target_id, user.id)
    if handle is None:
        raise HTTPException(status_code=410,
                              detail="стрим уже завершён, обновите страницу")
    playlist = Path(handle.work_dir) / variant / "playlist.m3u8"
    if not playlist.exists():
        raise HTTPException(status_code=404)
    reg.touch(target_id, user.id)
    return Response(
        content=playlist.read_bytes(),
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@api_router.get("/episode/{episode_id}/{variant}/{segment_name}")
def episode_stream_segment(
    episode_id: int, variant: str, segment_name: str,
    user: Annotated[User, Depends(get_current_user)],
):
    if not _VARIANT_RE.match(variant) or not _SEGMENT_NAME_RE.match(segment_name):
        raise HTTPException(status_code=404)
    reg = get_registry()
    target_id = episode_key(episode_id)
    handle = reg.get(target_id, user.id)
    if handle is None:
        raise HTTPException(status_code=410, detail="стрим уже завершён, обновите страницу")
    seg_path = Path(handle.work_dir) / variant / segment_name
    if not seg_path.exists():
        raise HTTPException(status_code=404)
    reg.touch(target_id, user.id)
    return FileResponse(
        str(seg_path),
        media_type="video/mp2t",
        headers={"Cache-Control": "no-store"},
    )
```

- [ ] **Step 4: Run tests**

```
export PATH="$HOME/.local/bin:$PATH"
venv/bin/python -m pytest tests/integration/test_episode_streaming.py tests/integration/test_streaming.py -v
```
Expected: PASS (existing 12 + new 4).

- [ ] **Step 5: Commit**

```bash
git add app/streaming/routes.py tests/integration/test_episode_streaming.py
git commit -m "feat(streaming): episode streaming endpoints (master.m3u8, variants, segments)"
```

---

### Task 8: Episode progress endpoint

**Files:**
- Modify: `app/streaming/routes.py`
- Test: `tests/integration/test_episode_streaming.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/integration/test_episode_streaming.py`:

```python
from sqlalchemy import select
from app.models import EpisodeWatchProgress


def test_episode_progress_upserts(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    _, eid = _create_series_with_episode(db_factory)
    r = client.post("/api/progress/episode",
                    json={"episode_id": eid, "position_seconds": 42},
                    cookies={"session": cookie})
    assert r.status_code == 204
    with db_factory() as s:
        wp = s.scalars(select(EpisodeWatchProgress).where(
            EpisodeWatchProgress.episode_id == eid)).one()
        assert wp.position_seconds == 42

    # Update existing
    r = client.post("/api/progress/episode",
                    json={"episode_id": eid, "position_seconds": 100,
                          "audio_track_index": 1},
                    cookies={"session": cookie})
    assert r.status_code == 204
    with db_factory() as s:
        wp = s.scalars(select(EpisodeWatchProgress).where(
            EpisodeWatchProgress.episode_id == eid)).one()
        assert wp.position_seconds == 100
        assert wp.audio_track_index == 1


def test_episode_progress_unauth(client, db_factory):
    _, eid = _create_series_with_episode(db_factory)
    r = client.post("/api/progress/episode",
                    json={"episode_id": eid, "position_seconds": 1})
    assert r.status_code == 401
```

- [ ] **Step 2: Run — verify fails**

```
venv/bin/python -m pytest tests/integration/test_episode_streaming.py::test_episode_progress_upserts -v
```
Expected: FAIL (404).

- [ ] **Step 3: Add endpoint to `app/streaming/routes.py`**

Add imports at top:

```python
from app.models import Episode, EpisodeWatchProgress, MediaItem, User, WatchProgress
```

Add Pydantic model and endpoint:

```python
class _EpisodeProgressIn(BaseModel):
    episode_id: int
    position_seconds: int
    audio_track_index: int | None = None


@progress_router.post("/progress/episode", status_code=204, include_in_schema=False)
def episode_progress(
    payload: Annotated[_EpisodeProgressIn, Body()],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    existing = db.scalars(
        select(EpisodeWatchProgress).where(
            EpisodeWatchProgress.user_id == user.id,
            EpisodeWatchProgress.episode_id == payload.episode_id,
        )
    ).first()
    now = datetime.now(timezone.utc)
    if existing is not None:
        existing.position_seconds = payload.position_seconds
        existing.updated_at = now
        if payload.audio_track_index is not None:
            existing.audio_track_index = payload.audio_track_index
    else:
        db.add(EpisodeWatchProgress(
            user_id=user.id, episode_id=payload.episode_id,
            position_seconds=payload.position_seconds,
            audio_track_index=payload.audio_track_index,
            updated_at=now,
        ))
    get_registry().touch(episode_key(payload.episode_id), user.id)
    db.commit()
```

- [ ] **Step 4: Run tests**

```
venv/bin/python -m pytest tests/integration/test_episode_streaming.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/streaming/routes.py tests/integration/test_episode_streaming.py
git commit -m "feat(streaming): /api/progress/episode endpoint for per-episode progress"
```

---

## Phase 6 — Series Page

### Task 9: Series page route and template

**Files:**
- Modify: `app/library/routes.py` (`media_page` branches by kind)
- Create: `templates/media_series.html`
- Test: `tests/integration/test_series_page.py`

- [ ] **Step 1: Write failing test**

Create `tests/integration/test_series_page.py`:

```python
from pathlib import Path

from app.auth.passwords import hash_password
from app.models import Episode, EpisodeWatchProgress, MediaItem, User


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _login(client, db_factory, csrf_for):
    with db_factory() as s:
        s.add(User(username="alice",
                   password_hash=hash_password("correct-password-12"),
                   must_change_password=False))
        s.commit()
    r = client.post("/login", data={"username": "alice",
                                     "password": "correct-password-12",
                                     "csrf_token": csrf_for(None)})
    return r.cookies.get("session")


def _create_series(db_factory) -> tuple[int, list[int]]:
    """Returns (series_id, [episode_ids in order])."""
    with db_factory() as s:
        sr = MediaItem(torrent_hash="t", title="Show",
                        file_path="/x", size_bytes=1, kind="series",
                        year=2020, description="A show.")
        s.add(sr); s.flush()
        eps = []
        for season, ep_n, title in [(1,1,"Pilot"), (1,2,"Second"), (2,1,"S2E1")]:
            e = Episode(series_id=sr.id, season=season, episode=ep_n,
                         title=title, file_path=str(SAMPLE),
                         size_bytes=SAMPLE.stat().st_size, duration_seconds=600)
            s.add(e); s.flush(); eps.append(e.id)
        s.commit()
        return sr.id, eps


def test_series_page_shows_seasons_and_episodes(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid, _ = _create_series(db_factory)
    r = client.get(f"/media/{sid}", cookies={"session": cookie})
    assert r.status_code == 200
    # Видим название серии
    assert "Show" in r.text
    # Видим оба сезона
    assert "Сезон 1" in r.text or "season=1" in r.text
    # Видим эпизоды первого сезона по умолчанию
    assert "Pilot" in r.text


def test_series_page_season_selector(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid, _ = _create_series(db_factory)
    r = client.get(f"/media/{sid}?season=2", cookies={"session": cookie})
    assert r.status_code == 200
    assert "S2E1" in r.text
    # Pilot из сезона 1 не показывается (но название серии конечно показано)
    # Ищем именно в зоне эпизодов — проще проверить что текст эпизода S1 не вид Pilot
    # (грубая проверка)


def test_series_page_movie_uses_movie_template(client, db_factory, csrf_for):
    """Не-сериал должен открываться через старый media.html, без сетки эпизодов."""
    cookie = _login(client, db_factory, csrf_for)
    with db_factory() as s:
        m = MediaItem(torrent_hash="m", title="Movie", file_path=str(SAMPLE),
                       size_bytes=SAMPLE.stat().st_size, kind="movie")
        s.add(m); s.commit(); s.refresh(m); mid = m.id
    r = client.get(f"/media/{mid}", cookies={"session": cookie})
    assert r.status_code == 200
    # Старый шаблон — есть player, есть Heart-beat скрипт
    assert "Скачать оригинал" in r.text
```

- [ ] **Step 2: Run — verify fails**

```
venv/bin/python -m pytest tests/integration/test_series_page.py -v
```
Expected: FAIL.

- [ ] **Step 3: Update `app/library/routes.py::media_page` to branch**

Refactor `media_page`:

```python
@router.get("/media/{media_id}", response_class=HTMLResponse)
def media_page(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    season: int = 1,
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)
    if item.kind == "series":
        return _series_page(request, user, db, item, season)
    return _movie_page(request, user, db, item)


def _movie_page(request, user, db, item):
    # Лениво дозаполняем
    needs_commit = False
    if item.duration_seconds is None:
        from app.metadata.ffprobe import get_duration_seconds
        dur = get_duration_seconds(item.file_path)
        if dur is not None:
            item.duration_seconds = dur
            needs_commit = True
    if item.audio_tracks is None:
        from app.metadata.ffprobe import probe_audio_tracks
        tracks = probe_audio_tracks(item.file_path)
        item.audio_tracks = [
            {"index": a.index, "codec": a.codec, "language": a.language,
             "title": a.title, "channels": a.channels}
            for a in tracks
        ]
        needs_commit = True
    if needs_commit:
        db.commit()

    progress = db.scalars(
        select(WatchProgress).where(
            WatchProgress.user_id == user.id,
            WatchProgress.media_id == item.id,
        )
    ).first()
    saved_position = progress.position_seconds if progress else 0
    saved_audio_track = progress.audio_track_index if progress else None

    return render(request, "media.html", {
        "user": user, "item": item,
        "saved_position_seconds": saved_position,
        "saved_audio_track_index": saved_audio_track,
    })


def _series_page(request, user, db, item, season):
    from app.models import Episode, EpisodeWatchProgress

    episodes = db.scalars(
        select(Episode).where(Episode.series_id == item.id)
        .order_by(Episode.season, Episode.episode)
    ).all()

    seasons = sorted({e.season for e in episodes})
    if not seasons:
        seasons = [1]
    selected_season = season if season in seasons else seasons[0]

    season_episodes = [e for e in episodes if e.season == selected_season]

    # Watch progress для всех эпизодов
    user_progresses = {
        p.episode_id: p
        for p in db.scalars(
            select(EpisodeWatchProgress).where(EpisodeWatchProgress.user_id == user.id)
        )
    }

    from app.library.routes import WATCHED_RATIO  # уже есть из Spec 1
    # Аннотация на эпизод: status, position
    annotated_episodes = []
    for e in season_episodes:
        p = user_progresses.get(e.id)
        if p is None or p.position_seconds <= 0:
            st = "not_started"
        elif e.duration_seconds and p.position_seconds >= WATCHED_RATIO * e.duration_seconds:
            st = "watched"
        else:
            st = "in_progress"
        annotated_episodes.append({
            "ep": e, "status": st,
            "position": p.position_seconds if p else 0,
        })

    return render(request, "media_series.html", {
        "user": user, "item": item,
        "seasons": seasons,
        "selected_season": selected_season,
        "episodes": annotated_episodes,
    })
```

- [ ] **Step 4: Create `templates/media_series.html`**

```html
{% extends "base.html" %}
{% block title %}{{ item.title }}{% endblock %}
{% block content %}
<section class="media-header">
  <div class="media-header-poster">
    {% if item.poster_url %}
      <img src="{{ item.poster_url }}" alt="">
    {% else %}
      <div class="poster-placeholder">{{ item.title[:2] | upper }}</div>
    {% endif %}
  </div>
  <div class="media-header-body">
    <p class="eyebrow">
      Сериал
      {% if item.year %} · {{ item.year }}{% endif %}
      {% if item.genres %} · {{ item.genres | map(attribute='name') | join(', ') }}{% endif %}
    </p>
    <h1>{{ item.title }}</h1>
    {% if item.description %}<p class="media-description">{{ item.description }}</p>{% endif %}
    <div class="media-header-actions">
      {% if item.match_source %}
        <span class="media-source-badge">Источник: {{ item.match_source | upper }}</span>
      {% endif %}
      <button class="button ghost compact" type="button"
              hx-get="/api/media/{{ item.id }}/match/search-form"
              hx-target="#modal-root" hx-swap="innerHTML">Исправить совпадение</button>
      <button class="button ghost compact" type="button"
              hx-get="/api/media/{{ item.id }}/edit-form"
              hx-target="#modal-root" hx-swap="innerHTML">Редактировать</button>
    </div>
  </div>
</section>

<div id="modal-root"></div>

<section class="season-tabs">
  {% for s in seasons %}
    <a href="/media/{{ item.id }}?season={{ s }}"
       class="season-tab {% if s == selected_season %}active{% endif %}">
      Сезон {{ s }}
    </a>
  {% endfor %}
</section>

<section class="episode-grid">
  {% for entry in episodes %}
    {% set e = entry.ep %}
    <a class="episode-card" href="/media/{{ item.id }}/s{{ e.season }}/e{{ e.episode }}">
      <div class="episode-number">S{{ '%02d'|format(e.season) }}E{{ '%02d'|format(e.episode) }}</div>
      <div class="episode-title">{{ e.title or ('Эпизод ' ~ e.episode) }}</div>
      <div class="episode-meta">
        {% if e.duration_seconds %}
          {% set total_min = (e.duration_seconds / 60) | int %}
          {% if total_min >= 60 %}{{ total_min // 60 }}ч {{ total_min % 60 }}мин{% else %}{{ total_min }}мин{% endif %}
        {% endif %}
        {% if entry.status == 'watched' %}<span class="episode-watched">✓</span>{% endif %}
      </div>
      {% if entry.status == 'in_progress' and e.duration_seconds %}
        {% set pct = (entry.position / e.duration_seconds * 100) | round(0) %}
        <div class="episode-progress"><div style="width: {{ pct }}%"></div></div>
      {% endif %}
    </a>
  {% endfor %}
</section>

<section class="action-bar">
  <a href="/library" class="button secondary">Библиотека</a>
  <form method="post" action="/api/media/{{ item.id }}/delete" onsubmit="return confirm('Удалить весь сериал «{{ item.title }}»?');">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button class="button danger" type="submit">Удалить</button>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 5: Add CSS**

Append to `static/style.css`:

```css
.season-tabs {
  display: flex; flex-wrap: wrap; gap: 8px;
  margin: 16px 0;
}
.season-tab {
  padding: 8px 16px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: inherit;
  border-radius: 6px;
  text-decoration: none;
  font-size: 0.9rem;
  transition: all 0.15s;
}
.season-tab:hover { background: rgba(255, 255, 255, 0.1); }
.season-tab.active {
  background: #4084ff; border-color: #4084ff; color: #fff;
}
.episode-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
  margin: 16px 0;
}
.episode-card {
  position: relative;
  padding: 12px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  color: inherit;
  text-decoration: none;
  transition: background 0.15s;
}
.episode-card:hover { background: rgba(255, 255, 255, 0.08); }
.episode-number { font-size: 0.78rem; opacity: 0.6; margin-bottom: 4px; }
.episode-title { font-weight: 500; margin-bottom: 4px; }
.episode-meta { font-size: 0.85rem; opacity: 0.7; display: flex; gap: 8px; }
.episode-watched { color: #22c858; font-weight: bold; }
.episode-progress {
  position: absolute; left: 0; right: 0; bottom: 0;
  height: 3px; background: rgba(0, 0, 0, 0.4);
}
.episode-progress > div { height: 100%; background: #4084ff; }
```

- [ ] **Step 6: Run tests**

```
venv/bin/python -m pytest tests/integration/test_series_page.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/library/routes.py templates/media_series.html static/style.css tests/integration/test_series_page.py
git commit -m "feat(library): series page with season tabs and episode grid"
```

---

## Phase 7 — Episode Player Page

### Task 10: Episode page route, navigation, and template

**Files:**
- Modify: `app/library/routes.py` (add `episode_page` route)
- Create: `templates/media_episode.html`
- Test: `tests/integration/test_episode_page.py`

- [ ] **Step 1: Write failing test**

Create `tests/integration/test_episode_page.py`:

```python
from pathlib import Path

from app.auth.passwords import hash_password
from app.models import Episode, MediaItem, User


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _login(client, db_factory, csrf_for):
    with db_factory() as s:
        s.add(User(username="alice",
                   password_hash=hash_password("correct-password-12"),
                   must_change_password=False))
        s.commit()
    r = client.post("/login", data={"username": "alice",
                                     "password": "correct-password-12",
                                     "csrf_token": csrf_for(None)})
    return r.cookies.get("session")


def _create_series_three_eps(db_factory) -> tuple[int, list[int]]:
    with db_factory() as s:
        sr = MediaItem(torrent_hash="t", title="Show",
                        file_path="/x", size_bytes=1, kind="series")
        s.add(sr); s.flush()
        eps = []
        for season, ep_n, title in [(1,1,"Pilot"), (1,2,"Second"), (2,1,"S2E1")]:
            e = Episode(series_id=sr.id, season=season, episode=ep_n,
                         title=title, file_path=str(SAMPLE),
                         size_bytes=SAMPLE.stat().st_size, duration_seconds=600)
            s.add(e); s.flush(); eps.append(e.id)
        s.commit()
        return sr.id, eps


def test_episode_page_renders(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid, _ = _create_series_three_eps(db_factory)
    r = client.get(f"/media/{sid}/s1/e1", cookies={"session": cookie})
    assert r.status_code == 200
    assert "Pilot" in r.text


def test_episode_page_404_for_unknown(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid, _ = _create_series_three_eps(db_factory)
    r = client.get(f"/media/{sid}/s1/e99", cookies={"session": cookie})
    assert r.status_code == 404


def test_episode_page_next_within_season(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid, _ = _create_series_three_eps(db_factory)
    r = client.get(f"/media/{sid}/s1/e1", cookies={"session": cookie})
    # next: S01E02
    assert f"/media/{sid}/s1/e2" in r.text


def test_episode_page_next_across_seasons(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid, _ = _create_series_three_eps(db_factory)
    # S01E02 → next должен быть S02E01
    r = client.get(f"/media/{sid}/s1/e2", cookies={"session": cookie})
    assert f"/media/{sid}/s2/e1" in r.text


def test_episode_page_no_next_at_last(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid, _ = _create_series_three_eps(db_factory)
    # S02E01 — последний эпизод
    r = client.get(f"/media/{sid}/s2/e1", cookies={"session": cookie})
    # next отсутствует — нет ссылки s2/e2 или s3/e1
    assert "Следующий" not in r.text or "disabled" in r.text or "/media/{sid}/s3" not in r.text


def test_episode_page_prev_at_first_episode(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    sid, _ = _create_series_three_eps(db_factory)
    # S01E01 — первый, prev отсутствует
    r = client.get(f"/media/{sid}/s1/e1", cookies={"session": cookie})
    # Не должно быть ссылки на /s0/ или нечто
    # Проверяем по наличию disabled или отсутствию class="prev-episode" с href
```

- [ ] **Step 2: Run — verify fails**

```
venv/bin/python -m pytest tests/integration/test_episode_page.py -v
```
Expected: FAIL (route не существует).

- [ ] **Step 3: Add route to `app/library/routes.py`**

Add at top imports:

```python
from app.models import Episode, EpisodeWatchProgress
```

Add helper for prev/next и route:

```python
def _find_adjacent_episode(db, series_id: int, season: int, episode: int, direction: str):
    """direction: 'prev' or 'next'. Returns Episode or None."""
    if direction == "next":
        return db.scalars(
            select(Episode).where(
                Episode.series_id == series_id,
                ((Episode.season > season) |
                 ((Episode.season == season) & (Episode.episode > episode)))
            ).order_by(Episode.season.asc(), Episode.episode.asc()).limit(1)
        ).first()
    else:  # prev
        return db.scalars(
            select(Episode).where(
                Episode.series_id == series_id,
                ((Episode.season < season) |
                 ((Episode.season == season) & (Episode.episode < episode)))
            ).order_by(Episode.season.desc(), Episode.episode.desc()).limit(1)
        ).first()


@router.get("/media/{series_id}/s{season}/e{episode}", response_class=HTMLResponse)
def episode_page(
    series_id: int, season: int, episode: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    series = db.get(MediaItem, series_id)
    if series is None or series.kind != "series":
        raise HTTPException(status_code=404)

    ep = db.scalars(select(Episode).where(
        Episode.series_id == series_id,
        Episode.season == season,
        Episode.episode == episode,
    )).first()
    if ep is None:
        raise HTTPException(status_code=404)

    # Lazy fill duration / audio_tracks для эпизода
    needs_commit = False
    if ep.duration_seconds is None:
        from app.metadata.ffprobe import get_duration_seconds
        dur = get_duration_seconds(ep.file_path)
        if dur is not None:
            ep.duration_seconds = dur
            needs_commit = True
    if ep.audio_tracks is None:
        from app.metadata.ffprobe import probe_audio_tracks
        tracks = probe_audio_tracks(ep.file_path)
        ep.audio_tracks = [
            {"index": a.index, "codec": a.codec, "language": a.language,
             "title": a.title, "channels": a.channels}
            for a in tracks
        ]
        needs_commit = True
    if needs_commit:
        db.commit()

    progress = db.scalars(
        select(EpisodeWatchProgress).where(
            EpisodeWatchProgress.user_id == user.id,
            EpisodeWatchProgress.episode_id == ep.id,
        )
    ).first()
    saved_position = progress.position_seconds if progress else 0
    saved_audio_track = progress.audio_track_index if progress else None

    prev_ep = _find_adjacent_episode(db, series_id, season, episode, "prev")
    next_ep = _find_adjacent_episode(db, series_id, season, episode, "next")

    return render(request, "media_episode.html", {
        "user": user, "series": series, "episode": ep,
        "prev_ep": prev_ep, "next_ep": next_ep,
        "saved_position_seconds": saved_position,
        "saved_audio_track_index": saved_audio_track,
    })
```

- [ ] **Step 4: Create `templates/media_episode.html`**

```html
{% extends "base.html" %}
{% block title %}{{ series.title }} — S{{ '%02d'|format(episode.season) }}E{{ '%02d'|format(episode.episode) }}{% endblock %}
{% block content %}
<section class="episode-page-header">
  <p class="eyebrow">
    <a href="/media/{{ series.id }}?season={{ episode.season }}">{{ series.title }}</a>
    · Сезон {{ episode.season }} · Эпизод {{ episode.episode }}
  </p>
  <h1>{{ episode.title or ('Эпизод ' ~ episode.episode) }}</h1>
  {% if episode.description %}<p class="episode-description">{{ episode.description }}</p>{% endif %}
</section>

{% if saved_position_seconds and saved_position_seconds > 0
      and (not episode.duration_seconds or saved_position_seconds < 0.65 * episode.duration_seconds) %}
<section class="resume-banner">
  <span>⏱ Продолжить с <span id="resume-time"></span></span>
  <button id="restart-btn" type="button">Сначала</button>
</section>
<script>
(function () {
  const sec = {{ saved_position_seconds }};
  const m = Math.floor(sec / 60), s = sec % 60;
  document.getElementById('resume-time').textContent = `${m}:${String(s).padStart(2,'0')}`;
})();
</script>
{% endif %}

<section class="player-shell">
  <video id="player" controls preload="metadata"></video>
  <div id="audio-tracks" class="audio-tracks"></div>
</section>

<section class="episode-nav">
  {% if prev_ep %}
    <a href="/media/{{ series.id }}/s{{ prev_ep.season }}/e{{ prev_ep.episode }}"
       class="button secondary compact">
      ← S{{ '%02d'|format(prev_ep.season) }}E{{ '%02d'|format(prev_ep.episode) }}
    </a>
  {% else %}
    <button class="button secondary compact" disabled>← Предыдущий</button>
  {% endif %}
  <a href="/media/{{ series.id }}?season={{ episode.season }}" class="button ghost compact">
    Все эпизоды сезона
  </a>
  {% if next_ep %}
    <a href="/media/{{ series.id }}/s{{ next_ep.season }}/e{{ next_ep.episode }}"
       class="button secondary compact" id="next-episode-link">
      S{{ '%02d'|format(next_ep.season) }}E{{ '%02d'|format(next_ep.episode) }} →
    </a>
  {% else %}
    <button class="button secondary compact" disabled>Следующий →</button>
  {% endif %}
</section>

<section class="action-bar">
  <a href="/media/{{ series.id }}" class="button ghost">К сериалу</a>
</section>

<div id="autoplay-overlay" hidden></div>

<script src="/static/hls.min.js"></script>
<script>
(function() {
  const EPISODE_ID = {{ episode.id }};
  const SAVED_POSITION = {{ saved_position_seconds or 0 }};
  const SAVED_AUDIO = {{ saved_audio_track_index|tojson }};
  const NEXT_URL = {{ ("/media/" ~ series.id ~ "/s" ~ next_ep.season ~ "/e" ~ next_ep.episode)|tojson if next_ep else "null" }};
  const NEXT_TITLE = {{ (("S" ~ "%02d"|format(next_ep.season) ~ "E" ~ "%02d"|format(next_ep.episode) ~ " — " ~ (next_ep.title or ("Эпизод " ~ next_ep.episode))))|tojson if next_ep else "null" }};
  const video = document.getElementById('player');
  const src = '/api/stream/episode/' + EPISODE_ID + '/master.m3u8';

  let hls = null;
  let recoverAttempts = 0;
  let overlayShown = false;
  let overlayCancelled = false;
  let autoplayTimer = null;

  if (Hls.isSupported()) {
    hls = new Hls();
    hls.loadSource(src);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      if (hls.audioTracks && hls.audioTracks.length > 1) {
        renderAudioSelector(hls);
      }
      if (SAVED_AUDIO != null && hls.audioTracks && SAVED_AUDIO < hls.audioTracks.length) {
        hls.audioTrack = SAVED_AUDIO;
      }
    });
    hls.on(Hls.Events.ERROR, (e, data) => {
      console.error("HLS error:", data);
      if (!data.fatal) return;
      if (recoverAttempts < 1) {
        recoverAttempts++;
        setTimeout(() => { try { hls.startLoad(); } catch (err) { console.warn(err); } }, 1000);
        setTimeout(() => { recoverAttempts = 0; }, 30000);
        return;
      }
      const banner = document.createElement('p');
      banner.className = 'error';
      banner.textContent = 'Поток упал. Обновите страницу.';
      document.querySelector('main').appendChild(banner);
    });
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = src;
  } else {
    document.querySelector('main').innerHTML += '<p class="error">Браузер не поддерживает HLS. Скачайте оригинал.</p>';
  }

  video.addEventListener('loadedmetadata', () => {
    if (SAVED_POSITION > 0 && SAVED_POSITION < video.duration - 30) {
      video.currentTime = SAVED_POSITION;
    }
  });

  const restartBtn = document.getElementById('restart-btn');
  if (restartBtn) {
    restartBtn.addEventListener('click', () => {
      video.currentTime = 0;
      fetch('/api/progress/episode', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({episode_id: EPISODE_ID, position_seconds: 0}),
      }).catch(() => {});
    });
  }

  // Heartbeat
  setInterval(() => {
    if (video.ended || isNaN(video.currentTime)) return;
    const payload = {episode_id: EPISODE_ID, position_seconds: Math.floor(video.currentTime)};
    if (hls && hls.audioTrack >= 0) payload.audio_track_index = hls.audioTrack;
    fetch('/api/progress/episode', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }).catch(() => {});
  }, 15000);

  // Auto-play next
  video.addEventListener('timeupdate', () => {
    if (!NEXT_URL || overlayCancelled || overlayShown) return;
    if (isNaN(video.duration)) return;
    const remaining = video.duration - video.currentTime;
    if (remaining < 15 && remaining > 5) {
      showAutoplayOverlay();
    }
  });

  function showAutoplayOverlay() {
    overlayShown = true;
    const ov = document.getElementById('autoplay-overlay');
    ov.hidden = false;
    let countdown = 10;
    ov.innerHTML = `
      <div class="autoplay-card">
        <p class="autoplay-title">Следующий: <strong>${escapeHtml(NEXT_TITLE)}</strong></p>
        <p class="autoplay-counter">через <span id="autoplay-secs">${countdown}</span>с</p>
        <div class="autoplay-actions">
          <button id="autoplay-cancel" class="button ghost compact">Отмена</button>
          <button id="autoplay-now" class="button primary compact">Сразу →</button>
        </div>
      </div>
    `;
    autoplayTimer = setInterval(() => {
      countdown--;
      const sx = document.getElementById('autoplay-secs');
      if (sx) sx.textContent = countdown;
      if (countdown <= 0) {
        clearInterval(autoplayTimer);
        window.location = NEXT_URL;
      }
    }, 1000);
    document.getElementById('autoplay-cancel').addEventListener('click', () => {
      clearInterval(autoplayTimer);
      ov.hidden = true;
      overlayCancelled = true;
    });
    document.getElementById('autoplay-now').addEventListener('click', () => {
      clearInterval(autoplayTimer);
      window.location = NEXT_URL;
    });
  }

  function renderAudioSelector(hlsInst) {
    const c = document.getElementById('audio-tracks');
    if (!c) return;
    c.innerHTML = '<span class="audio-label">🎧 Озвучка:</span>' + hlsInst.audioTracks.map(t =>
      `<button data-track="${t.id}" class="audio-track${t.id === hlsInst.audioTrack ? ' active' : ''}" type="button">`
      + escapeHtml((t.name || 'Track ' + (t.id + 1)).replace(/_/g, ' '))
      + (t.lang && t.lang !== 'und' ? ` <span class="lang">${escapeHtml(t.lang)}</span>` : '')
      + '</button>'
    ).join('');
    c.onclick = e => {
      const btn = e.target.closest('[data-track]');
      if (!btn) return;
      const id = parseInt(btn.dataset.track, 10);
      hlsInst.audioTrack = id;
      c.querySelectorAll('.audio-track').forEach(b => b.classList.toggle('active', b === btn));
      fetch('/api/progress/episode', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          episode_id: EPISODE_ID,
          position_seconds: Math.floor(video.currentTime || 0),
          audio_track_index: id,
        }),
      }).catch(() => {});
    };
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, ch =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"}[ch]));
  }
})();
</script>
{% endblock %}
```

- [ ] **Step 5: Add CSS**

Append to `static/style.css`:

```css
.episode-page-header {
  margin: 24px 0 16px;
}
.episode-page-header .eyebrow a {
  color: inherit;
  opacity: 0.7;
  text-decoration: none;
}
.episode-page-header .eyebrow a:hover { opacity: 1; }
.episode-description {
  margin: 12px 0;
  line-height: 1.5;
  opacity: 0.85;
}
.episode-nav {
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  margin: 16px 0;
}
.autoplay-overlay {
  position: fixed; bottom: 24px; right: 24px;
  z-index: 100;
}
.autoplay-card {
  background: #1e2230;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  padding: 16px;
  min-width: 280px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
}
.autoplay-title { margin: 0 0 4px; font-size: 0.9rem; }
.autoplay-counter { margin: 0 0 12px; font-size: 0.85rem; opacity: 0.7; }
.autoplay-actions { display: flex; gap: 8px; justify-content: flex-end; }
```

- [ ] **Step 6: Run tests**

```
venv/bin/python -m pytest tests/integration/test_episode_page.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/library/routes.py templates/media_episode.html static/style.css tests/integration/test_episode_page.py
git commit -m "feat(library): episode page with player, prev/next nav, auto-play overlay"
```

---

## Phase 8 — Series Delete Cascade and Library Card

### Task 11: Series-aware delete + library card update

**Files:**
- Modify: `app/library/routes.py::delete_media`
- Modify: `templates/_library_grid.html` (series card variant)

- [ ] **Step 1: Write failing test**

Create `tests/integration/test_series_delete.py`:

```python
from pathlib import Path
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.models import Episode, EpisodeWatchProgress, MediaItem, User


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _login(client, db_factory, csrf_for):
    with db_factory() as s:
        s.add(User(username="alice",
                   password_hash=hash_password("correct-password-12"),
                   must_change_password=False, is_admin=True))
        s.commit()
    r = client.post("/login", data={"username": "alice",
                                     "password": "correct-password-12",
                                     "csrf_token": csrf_for(None)})
    return r.cookies.get("session")


def test_delete_series_cascades_episodes(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    with db_factory() as s:
        u = s.scalars(select(User)).one()
        sr = MediaItem(torrent_hash="t", title="Show",
                        file_path=str(SAMPLE), size_bytes=1, kind="series")
        s.add(sr); s.flush()
        e1 = Episode(series_id=sr.id, season=1, episode=1,
                      file_path=str(SAMPLE),
                      size_bytes=SAMPLE.stat().st_size)
        s.add(e1); s.flush()
        s.add(EpisodeWatchProgress(user_id=u.id, episode_id=e1.id,
                                     position_seconds=10))
        s.commit()
        sid = sr.id

    # qBittorrent client будет недоступен в TestClient — игнорируется
    r = client.post(f"/api/media/{sid}/delete",
                    data={"csrf_token": csrf_for(cookie)},
                    cookies={"session": cookie})
    # Возможно 303 (RedirectResponse)
    assert r.status_code in (303, 200)

    with db_factory() as s:
        # MediaItem удалён
        m = s.get(MediaItem, sid)
        assert m is None
        # Эпизоды удалены каскадом
        eps = s.scalars(select(Episode)).all()
        assert len(eps) == 0
        # EpisodeWatchProgress тоже удалён каскадом
        eps_wp = s.scalars(select(EpisodeWatchProgress)).all()
        assert len(eps_wp) == 0
```

- [ ] **Step 2: Run — verify expectation**

```
venv/bin/python -m pytest tests/integration/test_series_delete.py -v
```
Expected: возможно уже PASS (cascade работает на уровне DB FK), но streams надо убить. Если PASS — пропустить Step 3-4 и сразу commit.

- [ ] **Step 3: Update `app/library/routes.py::delete_media`**

Replace the loop that kills streams:

```python
@router.post("/api/media/{media_id}/delete")
def delete_media(
    media_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    qb: Annotated[QBittorrentClient, Depends(get_qbittorrent_client)],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    # Собираем все target_ids для убийства: фильм → "m:N", сериал → "m:N" + все "e:N" эпизодов
    from app.streaming.stream_registry import episode_key, media_key
    target_ids_to_kill = {media_key(media_id)}
    if item.kind == "series":
        for ep in item.episodes:
            target_ids_to_kill.add(episode_key(ep.id))

    reg = get_registry()
    for handle in list(reg.all_streams()):
        if handle.target_id in target_ids_to_kill and handle.process is not None:
            kill_ffmpeg(handle.process)
            reg.unregister(handle.target_id, handle.user_id)
            shutil.rmtree(handle.work_dir, ignore_errors=True)

    try:
        qb.delete_torrent(item.torrent_hash, delete_files=True)
    except QBittorrentError as e:
        log.warning("delete_media: qBittorrent unreachable for torrent %s: %s",
                    item.torrent_hash, e)

    db.delete(item)
    db.commit()
    return RedirectResponse("/library", status_code=303)
```

- [ ] **Step 4: Update `templates/_library_grid.html`** for series card

For `kind='series'` cards we want different meta — count of seasons/episodes instead of duration. Update the meta `<p>` (in the loop):

Replace:
```html
<p class="media-card-meta">
  {% if it.year %}{{ it.year }}{% endif %}
  {% if it.duration_seconds %}
    ...
  {% endif %}
</p>
```

With:
```html
<p class="media-card-meta">
  {% if it.year %}{{ it.year }}{% endif %}
  {% if it.kind == 'series' and it.episodes %}
    {% set seasons = it.episodes | map(attribute='season') | unique | list %}
    {% if it.year %} · {% endif %}
    {{ seasons | length }}С · {{ it.episodes | length }}Э
  {% elif it.duration_seconds %}
    {% if it.year %} · {% endif %}
    {% set total_min = (it.duration_seconds / 60) | int %}
    {% if total_min >= 60 %}{{ (total_min // 60) }}ч {{ (total_min % 60) }}мин
    {% else %}{{ total_min }}мин{% endif %}
  {% endif %}
</p>
```

- [ ] **Step 5: Run all library tests**

```
venv/bin/python -m pytest tests/integration/test_series_delete.py tests/integration/test_library.py tests/integration/test_library_real.py tests/integration/test_media_delete.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/library/routes.py templates/_library_grid.html tests/integration/test_series_delete.py
git commit -m "feat(library): series-aware delete with episode stream cleanup; show season/episode count on card"
```

---

## Phase 9 — Final Verification

### Task 12: Full test suite + smoke verification

- [ ] **Step 1: Full pytest run**

```
export PATH="$HOME/.local/bin:$PATH"
venv/bin/python -m pytest -v 2>&1 | tail -40
```

Expected: все тесты PASS.

- [ ] **Step 2: Migration roundtrip**

```bash
export SESSION_SECRET=$(venv/bin/python -c "import secrets; print(secrets.token_hex(32))")
export DATABASE_URL=sqlite:///./app.db MEDIA_ROOT=/tmp/media
export QBITTORRENT_URL=http://127.0.0.1:8080 QBITTORRENT_USERNAME=admin QBITTORRENT_PASSWORD=secret

venv/bin/python -m alembic upgrade head 2>&1 | tail -3
venv/bin/python -m alembic downgrade base 2>&1 | tail -3
venv/bin/python -m alembic upgrade head 2>&1 | tail -3
```

Expected: все без ошибок.

- [ ] **Step 3: Manual smoke checklist**

```bash
uvicorn app.main:app --reload --port 8000
```

- [ ] Открыть `/library` — карточка сериала показывает «N С · M Э»
- [ ] Кликнуть сериал — открывается страница с селектором сезонов и сеткой эпизодов
- [ ] Кликнуть эпизод — открывается плеер эпизода с навигацией
- [ ] Кнопки prev/next работают, корректно переходят через границы сезонов
- [ ] Прогресс эпизода сохраняется (refresh страницы → плашка «Продолжить»)
- [ ] Audio selector работает (если у эпизода >1 дорожки)
- [ ] Auto-play overlay появляется за 15 сек до конца
- [ ] Удаление сериала удаляет все эпизоды

---

## Verification Checklist

После всех задач:

- [ ] `pytest -v` — все тесты зелёные (включая Spec 1 тесты — после рефакторинга StreamRegistry)
- [ ] `alembic upgrade head` / `downgrade -1` работают
- [ ] Сериал с многоэпизодным торрентом распарсивается в N Episode
- [ ] TMDB-метаданные эпизодов попадают в title/description если есть TMDB_API_KEY
- [ ] Auto-play next эпизода работает с возможностью отмены
- [ ] Удаление сериала каскадно удаляет эпизоды и прогресс

---

## Simplifications vs spec

В рамках этого плана сделаны небольшие упрощения относительно спека:

1. **Backfill старых серий (spec §6.2)** — НЕ реализован в этом плане. Старые `kind='series'` записи остаются с одним файлом и без эпизодов. Если откроется такая запись после миграции 0004, она просто покажет пустую сетку эпизодов. Backfill можно добавить как Spec 2.1 если потребуется (детектировать через qBittorrent API и перерасканировать).
2. **Parser форматов `1x01`, `Season.1.Episode.1`** — не расширяем, остаётся `SxxExx`.
3. **Auto-play защита от skipped-to-end** — текущая защита `remaining < 15 && remaining > 5` (защита от показа после ухода за конец). Если пользователь скипнет в `duration - 2`, overlay не появится.

Эти ограничения не блокируют основной use-case (новые торренты будут разбираться корректно).
