# Catalog, Metadata, Audio Selection & Player Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать каталог библиотеки (метаданные TMDB/Kinopoisk, постеры, типы, жанры, фильтры, поиск, ручное редактирование), полную поддержку выбора аудиодорожки в плеере, исправить баг с потерей стрима на паузе и добавить «продолжить с места».

**Architecture:** FastAPI + Jinja2 + HTMX (без SPA). Бэк-метаданные через httpx-клиенты к TMDB (приоритет) и Kinopoisk (fallback). Авто-матч в сканере при обнаружении нового торрента. HLS-стриминг — мульти-вариант master playlist с несколькими аудио-рендициями, всегда (даже для 1 аудио, для унификации роутов).

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0, Alembic, Jinja2, HTMX, hls.js, ffmpeg/ffprobe, httpx, respx (моки HTTP в тестах), pytest.

**Spec:** [`docs/superpowers/specs/2026-05-17-catalog-player-fixes-design.md`](../specs/2026-05-17-catalog-player-fixes-design.md)

---

## Setup Notes

**Git status:** Проект сейчас не в git-репозитории. Перед началом — `git init && git add -A && git commit -m "initial import"`. Если решено не использовать git — пропускайте шаги «Commit», но поэтапные коммиты сильно рекомендуются.

**Test infrastructure:** Тесты использует pytest + TestClient (Starlette). Уже есть `tests/conftest.py` с фикстурами `client`, `db_factory`, `csrf_for`, `env`. HTTP-моки — через `respx` (уже в `requirements.txt`).

**API ключи для тестов:** Тесты НЕ должны делать реальных HTTP-запросов к TMDB/Kinopoisk. Все клиенты мокаются через `respx`. Если в `.env` нет ключей — это нормально, авто-матч просто пропускается.

**Fixtures:** `tests/fixtures/sample.mp4` уже есть. Дополнительно потребуется `tests/fixtures/multi_audio.mkv` (создаётся в Task 39).

---

## Phase 1 — Player Bugfix (no model changes needed)

Самая маленькая ценная итерация. Можно зашипить отдельно.

### Task 1: Watchdog idle threshold raised to 300s

**Files:**
- Modify: `app/streaming/watchdog.py:11`
- Test: `tests/unit/test_streaming_watchdog.py` (add new test)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_streaming_watchdog.py`:

```python
from app.streaming import watchdog


def test_idle_threshold_seconds_is_300():
    # Защита от регрессии: порог watchdog должен быть 300с, чтобы
    # переживать паузу + временную потерю heartbeat.
    assert watchdog.IDLE_THRESHOLD_SECONDS == 300.0
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_streaming_watchdog.py::test_idle_threshold_seconds_is_300 -v
```
Expected: FAIL (assertion: 60.0 != 300.0)

- [ ] **Step 3: Update watchdog constant**

In `app/streaming/watchdog.py`:

```python
IDLE_THRESHOLD_SECONDS = 300.0
SWEEP_INTERVAL_SECONDS = 15.0
```

- [ ] **Step 4: Run all watchdog tests**

```
pytest tests/unit/test_streaming_watchdog.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/streaming/watchdog.py tests/unit/test_streaming_watchdog.py
git commit -m "fix(streaming): raise idle threshold to 300s to survive pause + heartbeat gaps"
```

---

### Task 2: Heartbeat fires during pause

**Files:**
- Modify: `templates/media.html:47-55`
- Test: `tests/integration/test_streaming.py` (extend)

- [ ] **Step 1: Write the failing test (server-side, verifying touch happens on paused-progress)**

Add to `tests/integration/test_streaming.py`:

```python
def test_progress_endpoint_touches_stream_registry(client, db_factory, csrf_for):
    """heartbeat при паузе должен touch'ить registry, чтобы watchdog не убил стрим"""
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    # Стартуем стрим
    r = client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 200

    reg = get_registry()
    handle = next((h for h in reg.all_streams() if h.media_id == mid), None)
    assert handle is not None
    old_access = handle.last_access

    # Имитируем heartbeat на паузе (POST /api/progress)
    import time
    time.sleep(0.05)
    r = client.post(
        "/api/progress",
        json={"media_id": mid, "position_seconds": 100},
        cookies={"session": cookie},
    )
    assert r.status_code == 204

    # last_access должен обновиться
    handle2 = next((h for h in reg.all_streams() if h.media_id == mid), None)
    assert handle2 is not None
    assert handle2.last_access > old_access
```

- [ ] **Step 2: Run test to verify it passes (this behavior already exists server-side)**

```
pytest tests/integration/test_streaming.py::test_progress_endpoint_touches_stream_registry -v
```
Expected: PASS (бэк уже делает `touch()` в `progress()`, см. `streaming/routes.py:124`).

- [ ] **Step 3: Update client-side heartbeat in `templates/media.html`**

Replace the current `setInterval` block (lines ~47-55):

```html
<script>
(function() {
  const video = document.getElementById('player');
  const src = '/api/stream/{{ item.id }}/playlist.m3u8';

  let hls = null;
  if (Hls.isSupported()) {
    hls = new Hls();
    hls.loadSource(src);
    hls.attachMedia(video);
    hls.on(Hls.Events.ERROR, (e, data) => {
      console.error("HLS error:", data);
      if (data.fatal) {
        document.querySelector('main').innerHTML += '<p class="error">Поток упал. Обновите страницу.</p>';
      }
    });
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = src;
  } else {
    document.querySelector('main').innerHTML += '<p class="error">Браузер не поддерживает HLS. Скачайте оригинал.</p>';
  }

  // Heartbeat — каждые 15с, даже на паузе (чтобы watchdog не убил стрим).
  setInterval(() => {
    if (video.ended || isNaN(video.currentTime)) return;
    fetch('/api/progress', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({media_id: {{ item.id }}, position_seconds: Math.floor(video.currentTime)}),
    }).catch(e => console.warn('progress save failed', e));
  }, 15000);
})();
</script>
```

Только условие в `setInterval` изменилось (убрали `video.paused`) и интервал стал 15 секунд.

- [ ] **Step 4: Manually verify**

```bash
uvicorn app.main:app --reload --port 8000
```

Открыть видео, поставить на паузу >2 минут, продолжить — стрим не должен умереть. (Это ручная проверка; автоматизировать тяжело без headless-браузера.)

- [ ] **Step 5: Commit**

```bash
git add templates/media.html tests/integration/test_streaming.py
git commit -m "fix(player): send heartbeat during pause so watchdog doesn't kill stream"
```

---

### Task 3: HLS auto-recovery on fatal error

**Files:**
- Modify: `templates/media.html` (JS секция)

- [ ] **Step 1: Update error handler in `templates/media.html`**

Replace the `hls.on(Hls.Events.ERROR, …)` block:

```js
let recoverAttempts = 0;
hls.on(Hls.Events.ERROR, (e, data) => {
  console.error("HLS error:", data);
  if (!data.fatal) return;
  if (recoverAttempts < 1) {
    recoverAttempts++;
    // Серверный watchdog мог убить ffmpeg → запрос плейлиста перезапустит его.
    setTimeout(() => {
      try { hls.startLoad(); } catch (err) { console.warn('startLoad failed', err); }
    }, 1000);
    // Сбрасываем счётчик через 30с — если стрим работает стабильно, следующий fatal снова попадёт в recovery.
    setTimeout(() => { recoverAttempts = 0; }, 30000);
    return;
  }
  // Второй fatal за 30с → показать баннер
  const banner = document.createElement('p');
  banner.className = 'error';
  banner.textContent = 'Поток упал. Обновите страницу.';
  document.querySelector('main').appendChild(banner);
});
```

- [ ] **Step 2: Manually verify**

```bash
uvicorn app.main:app --reload --port 8000
```

Открыть видео, в другом терминале убить ffmpeg процесс (через `ps | grep ffmpeg` и `kill`). Через 1-2 секунды плеер должен сам восстановиться (новый ffmpeg стартует при первом 410 на сегмент → бэк создаст новый стрим).

- [ ] **Step 3: Commit**

```bash
git add templates/media.html
git commit -m "fix(player): auto-recover on HLS fatal error via hls.startLoad()"
```

---

## Phase 2 — Data Model & Migration

### Task 4: Extend models.py with new columns and tables

**Files:**
- Modify: `app/models.py`
- Test: `tests/unit/test_models.py` (add tests)

- [ ] **Step 1: Write failing test for new fields**

Append to `tests/unit/test_models.py`:

```python
from app.models import Base, MediaItem, WatchProgress, Genre, MediaItemGenre


def test_media_item_has_metadata_fields():
    cols = {c.name for c in MediaItem.__table__.columns}
    required = {
        "duration_seconds", "description", "poster_url", "year",
        "kind", "tmdb_id", "kinopoisk_id", "match_status", "match_source",
        "audio_tracks",
    }
    missing = required - cols
    assert not missing, f"missing fields on MediaItem: {missing}"


def test_genre_model_exists():
    cols = {c.name for c in Genre.__table__.columns}
    assert cols == {"id", "name"}


def test_media_item_genres_m2m_exists():
    cols = {c.name for c in MediaItemGenre.__table__.columns}
    assert cols == {"media_id", "genre_id"}


def test_watch_progress_has_audio_track_index():
    cols = {c.name for c in WatchProgress.__table__.columns}
    assert "audio_track_index" in cols
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_models.py -v
```
Expected: FAIL (ImportError для `Genre`, `MediaItemGenre`).

- [ ] **Step 3: Update `app/models.py`**

Replace the file content with:

```python
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    is_partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Genre(Base):
    __tablename__ = "genres"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class MediaItemGenre(Base):
    __tablename__ = "media_item_genres"

    media_id: Mapped[int] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    genre_id: Mapped[int] = mapped_column(
        ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    torrent_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    # — каталог-метаданные (Spec 1) —
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    poster_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kinopoisk_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    match_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    audio_tracks: Mapped[list | None] = mapped_column(JSON, nullable=True)

    genres: Mapped[list["Genre"]] = relationship(
        "Genre",
        secondary="media_item_genres",
        lazy="selectin",
    )


class WatchProgress(Base):
    __tablename__ = "watch_progress"
    __table_args__ = (UniqueConstraint("user_id", "media_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    media_id: Mapped[int] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), nullable=False)
    position_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    audio_track_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_models.py -v
```
Expected: PASS (включая старые тесты).

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/unit/test_models.py
git commit -m "feat(model): add catalog metadata fields, Genre/MediaItemGenre m2m, audio_track_index"
```

---

### Task 5: Alembic migration `0003_catalog_metadata_and_audio.py`

**Files:**
- Create: `migrations/versions/0003_catalog_metadata_and_audio.py`

- [ ] **Step 1: Identify previous revision**

```bash
ls migrations/versions/
```

Должно быть `0001_initial_schema.py` и `0002_remove_2fa.py`. Открой `0002_remove_2fa.py` и найди значение `revision` — это `down_revision` для новой миграции. (Допустим, это `"0002"` — подставь реально найденное.)

- [ ] **Step 2: Create the migration file**

Create `migrations/versions/0003_catalog_metadata_and_audio.py`:

```python
"""catalog metadata, genres, audio_tracks, audio_track_index

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"  # ← заменить на найденное в Step 1
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # media_items: добавить метаданные
    with op.batch_alter_table("media_items") as batch:
        batch.add_column(sa.Column("duration_seconds", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch.add_column(sa.Column("poster_url", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("year", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("kind", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("tmdb_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("kinopoisk_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column(
            "match_status", sa.String(length=16),
            nullable=False, server_default="pending",
        ))
        batch.add_column(sa.Column("match_source", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("audio_tracks", sa.JSON(), nullable=True))
        batch.create_index("ix_media_items_kind", ["kind"])
        batch.create_index("ix_media_items_year", ["year"])
        batch.create_index("ix_media_items_title", ["title"])

    # genres
    op.create_table(
        "genres",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
    )

    # media_item_genres (m2m)
    op.create_table(
        "media_item_genres",
        sa.Column("media_id", sa.Integer(),
                  sa.ForeignKey("media_items.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("genre_id", sa.Integer(),
                  sa.ForeignKey("genres.id", ondelete="CASCADE"),
                  primary_key=True),
    )
    op.create_index(
        "ix_media_item_genres_genre_id", "media_item_genres", ["genre_id"]
    )

    # watch_progress: audio_track_index
    with op.batch_alter_table("watch_progress") as batch:
        batch.add_column(sa.Column("audio_track_index", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("watch_progress") as batch:
        batch.drop_column("audio_track_index")

    op.drop_index("ix_media_item_genres_genre_id", table_name="media_item_genres")
    op.drop_table("media_item_genres")
    op.drop_table("genres")

    with op.batch_alter_table("media_items") as batch:
        batch.drop_index("ix_media_items_title")
        batch.drop_index("ix_media_items_year")
        batch.drop_index("ix_media_items_kind")
        batch.drop_column("audio_tracks")
        batch.drop_column("match_source")
        batch.drop_column("match_status")
        batch.drop_column("kinopoisk_id")
        batch.drop_column("tmdb_id")
        batch.drop_column("kind")
        batch.drop_column("year")
        batch.drop_column("poster_url")
        batch.drop_column("description")
        batch.drop_column("duration_seconds")
```

- [ ] **Step 3: Apply migration to dev DB**

```bash
alembic upgrade head
```

Expected: no errors. Проверить колонки:

```bash
python -c "
from sqlalchemy import create_engine, inspect
e = create_engine('sqlite:///./app.db')
i = inspect(e)
for c in i.get_columns('media_items'):
    print(c['name'])
"
```

Должны быть видны: `duration_seconds`, `description`, `poster_url`, `year`, `kind`, `tmdb_id`, `kinopoisk_id`, `match_status`, `match_source`, `audio_tracks`.

- [ ] **Step 4: Test downgrade roundtrip**

```bash
alembic downgrade -1
alembic upgrade head
```

Expected: оба без ошибок.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0003_catalog_metadata_and_audio.py
git commit -m "feat(db): migration 0003 — catalog metadata, genres, audio fields"
```

---

## Phase 3 — Metadata Package: ffprobe & Types

### Task 6: Create metadata package skeleton + types

**Files:**
- Create: `app/metadata/__init__.py`
- Create: `app/metadata/types.py`
- Test: `tests/unit/test_metadata_types.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_metadata_types.py`:

```python
from app.metadata.types import MetadataMatch, AudioTrack


def test_metadata_match_is_frozen_dataclass():
    m = MetadataMatch(
        source="tmdb", external_id=123, title="X", year=2020,
        kind="movie", description=None, poster_url=None,
        genres=[], score=0.9,
    )
    assert m.title == "X"
    # frozen
    import dataclasses
    assert dataclasses.is_dataclass(m)


def test_audio_track_basic():
    t = AudioTrack(index=0, codec="aac", language="rus", title="Дубляж", channels=6)
    assert t.language == "rus"
    assert t.channels == 6
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_metadata_types.py -v
```
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Create the package**

Create `app/metadata/__init__.py` (empty):

```python
```

Create `app/metadata/types.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


KindLiteral = Literal["movie", "series", "cartoon", "anime", "documentary", "show", "other"]


@dataclass(frozen=True, slots=True)
class MetadataMatch:
    """Унифицированный формат для одного матча из любого источника."""
    source: Literal["tmdb", "kinopoisk"]
    external_id: int
    title: str
    year: int | None
    kind: KindLiteral
    description: str | None
    poster_url: str | None
    genres: list[str]
    score: float


@dataclass(frozen=True, slots=True)
class AudioTrack:
    """Описание одной аудиодорожки из файла (через ffprobe)."""
    index: int
    codec: str
    language: str | None
    title: str | None
    channels: int
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_metadata_types.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/metadata/__init__.py app/metadata/types.py tests/unit/test_metadata_types.py
git commit -m "feat(metadata): add types module with MetadataMatch and AudioTrack"
```

---

### Task 7: ffprobe.get_duration_seconds

**Files:**
- Create: `app/metadata/ffprobe.py`
- Test: `tests/unit/test_ffprobe.py`

- [ ] **Step 1: Write failing test (mocked subprocess)**

Create `tests/unit/test_ffprobe.py`:

```python
from unittest.mock import patch, MagicMock
import json

from app.metadata.ffprobe import get_duration_seconds


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
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_ffprobe.py -v
```
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Create `app/metadata/ffprobe.py` (duration only for now)**

```python
"""Обёртки над ffprobe для извлечения метаданных файла."""
from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)


def get_duration_seconds(file_path: str) -> int | None:
    """Длительность файла в секундах (округлённо до int). None — если ffprobe упал."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                file_path,
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("ffprobe failed for %s: %s", file_path, e)
        return None
    if result.returncode != 0:
        log.warning("ffprobe returned %d for %s", result.returncode, file_path)
        return None
    raw = result.stdout.strip()
    try:
        return int(round(float(raw)))
    except (ValueError, TypeError):
        log.warning("ffprobe duration unparseable for %s: %r", file_path, raw)
        return None
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_ffprobe.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/metadata/ffprobe.py tests/unit/test_ffprobe.py
git commit -m "feat(metadata): add ffprobe.get_duration_seconds"
```

---

### Task 8: ffprobe.probe_audio_tracks

**Files:**
- Modify: `app/metadata/ffprobe.py`
- Modify: `tests/unit/test_ffprobe.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_ffprobe.py`:

```python
import json as _json
from app.metadata.ffprobe import probe_audio_tracks


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
    assert tracks[0].index == 0  # 0-based внутри списка audio-стримов
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
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_ffprobe.py -v
```
Expected: FAIL (function not defined).

- [ ] **Step 3: Add `probe_audio_tracks` to `app/metadata/ffprobe.py`**

Append to the file:

```python
import json

from app.metadata.types import AudioTrack


def probe_audio_tracks(file_path: str) -> list[AudioTrack]:
    """Список аудиодорожек в файле. Пустой список при ошибке или отсутствии аудио."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index,codec_name,channels:stream_tags=language,title",
                "-of", "json",
                file_path,
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("ffprobe (audio) failed for %s: %s", file_path, e)
        return []
    if result.returncode != 0:
        log.warning("ffprobe (audio) returned %d for %s", result.returncode, file_path)
        return []
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as e:
        log.warning("ffprobe (audio) bad JSON for %s: %s", file_path, e)
        return []
    streams = data.get("streams") or []
    tracks: list[AudioTrack] = []
    for i, s in enumerate(streams):
        tags = s.get("tags") or {}
        tracks.append(AudioTrack(
            index=i,
            codec=s.get("codec_name") or "unknown",
            language=tags.get("language"),
            title=tags.get("title"),
            channels=int(s.get("channels") or 0),
        ))
    return tracks
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_ffprobe.py -v
```
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add app/metadata/ffprobe.py tests/unit/test_ffprobe.py
git commit -m "feat(metadata): add probe_audio_tracks"
```

---

## Phase 4 — Title Parser Refactor

### Task 9: Refactor `parse_title` to return `ParsedTitle`

**Files:**
- Modify: `app/torrents/title_parser.py`
- Modify: `tests/unit/test_title_parser.py`
- Modify: `app/torrents/scanner.py:60` (use `.title` field)

- [ ] **Step 1: Update tests (start with failing version)**

Replace `tests/unit/test_title_parser.py`:

```python
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
    ("No.Year.Here.1080p.mkv", "No Year Here", None, None, None, None),
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
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_title_parser.py -v
```
Expected: FAIL.

- [ ] **Step 3: Update `app/torrents/title_parser.py`**

Replace contents:

```python
"""Извлечение читаемого названия + года + season/episode из имени файла торрента."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal


_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_SE_RE = re.compile(r"^[Ss](\d{1,2})[Ee](\d{1,3})$")
_NOISE_TOKENS = {
    "1080p", "2160p", "720p", "480p",
    "BluRay", "BRRip", "DVDRip", "WEB", "WEB-DL", "WEBRip", "HDTV", "HDR", "HDR10", "DV",
    "x264", "x265", "H264", "H265", "HEVC", "AVC",
    "AAC", "AC3", "DTS", "DDP", "DD5.1", "5.1", "7.1", "FLAC",
    "REMUX", "PROPER", "REPACK", "EXTENDED", "DIRECTORS-CUT",
}
_NOISE_LOWER = {t.lower() for t in _NOISE_TOKENS}


@dataclass(frozen=True, slots=True)
class ParsedTitle:
    title: str
    year: int | None
    season: int | None
    episode: int | None
    kind_hint: Literal["movie", "tv"] | None


def parse_title(filename: str) -> ParsedTitle:
    original_stem = PurePosixPath(filename).stem
    if not original_stem:
        return ParsedTitle(title=filename, year=None, season=None, episode=None, kind_hint=None)

    stem = original_stem
    if "-" in stem and not _has_word_boundary(stem):
        stem = stem.rsplit("-", 1)[0]

    tokens = [t for t in re.split(r"[.\s_]+", stem) if t]

    title_parts: list[str] = []
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    saw_noise = False

    for tok in tokens:
        if _YEAR_RE.match(tok):
            year = int(tok)
            break
        m = _SE_RE.match(tok)
        if m:
            season = int(m.group(1))
            episode = int(m.group(2))
            break
        if tok.lower() in _NOISE_LOWER:
            saw_noise = True
            continue
        title_parts.append(tok)

    if not title_parts:
        return ParsedTitle(title=original_stem, year=None, season=None, episode=None, kind_hint=None)

    kind_hint: Literal["movie", "tv"] | None
    if season is not None:
        kind_hint = "tv"
    elif year is not None:
        kind_hint = "movie"
    elif saw_noise:
        # есть шум, но ни года, ни сериального маркера — будем считать фильмом
        kind_hint = "movie"
    else:
        kind_hint = None
        # Возвращаем оригинал, как раньше делал старый код
        return ParsedTitle(title=original_stem, year=None, season=None, episode=None, kind_hint=None)

    title = " ".join(title_parts)
    return ParsedTitle(title=title, year=year, season=season, episode=episode, kind_hint=kind_hint)


def _has_word_boundary(s: str) -> bool:
    return any(c in s for c in (" ", ".", "_"))
```

- [ ] **Step 4: Run title parser tests**

```
pytest tests/unit/test_title_parser.py -v
```
Expected: PASS.

- [ ] **Step 5: Update caller in `app/torrents/scanner.py`**

In `app/torrents/scanner.py:60`, change:

```python
title=parse_title(video.name),
```

to:

```python
title=parse_title(video.name).title,
```

(Только это поле; остальные подключим в Task 16, когда уже будет matcher.)

- [ ] **Step 6: Run scanner tests**

```
pytest tests/integration/test_library_scanner.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/torrents/title_parser.py app/torrents/scanner.py tests/unit/test_title_parser.py
git commit -m "refactor(title-parser): return ParsedTitle dataclass with year/season/episode/kind_hint"
```

---

## Phase 5 — Config: API Keys

### Task 10: Add TMDB and Kinopoisk API key fields to config

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Test: `tests/unit/test_settings_validation.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_settings_validation.py`:

```python
def test_tmdb_and_kinopoisk_keys_optional(monkeypatch):
    # Уже выставлены в conftest все REQUIRED поля.
    # Проверяем что TMDB/Kinopoisk keys опциональные.
    from app.config import get_settings, Settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.tmdb_api_key is None
    assert s.kinopoisk_api_key is None


def test_tmdb_key_picked_up_from_env(monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", "fake-tmdb-key")
    from app.config import get_settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.tmdb_api_key == "fake-tmdb-key"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_settings_validation.py -v
```
Expected: FAIL (no attribute `tmdb_api_key`).

- [ ] **Step 3: Update `app/config.py`**

Add the two fields inside the `Settings` class (after `hls_work_root`):

```python
    tmdb_api_key: str | None = None
    kinopoisk_api_key: str | None = None
```

- [ ] **Step 4: Update `.env.example`**

Append to the file:

```
# Опционально — авто-метаданные с TMDB.
# Получить ключ: themoviedb.org → Settings → API → API Read Access Token (v4)
TMDB_API_KEY=

# Опционально — fallback на Kinopoisk если TMDB не нашёл.
# Получить ключ: kinopoiskapiunofficial.tech (~500 запросов/день)
KINOPOISK_API_KEY=
```

- [ ] **Step 5: Run tests**

```
pytest tests/unit/test_settings_validation.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/config.py .env.example tests/unit/test_settings_validation.py
git commit -m "feat(config): add optional TMDB_API_KEY and KINOPOISK_API_KEY"
```

---

## Phase 6 — TMDB Client

### Task 11: TmdbClient.search

**Files:**
- Create: `app/metadata/tmdb.py`
- Test: `tests/unit/test_tmdb_client.py`

- [ ] **Step 1: Write failing test (respx mocks)**

Create `tests/unit/test_tmdb_client.py`:

```python
import httpx
import respx
import pytest

from app.metadata.tmdb import TmdbClient


@pytest.fixture
def client():
    return TmdbClient(api_key="fake-key")


@respx.mock
def test_search_movie_returns_results(client):
    respx.get("https://api.themoviedb.org/3/search/movie").mock(
        return_value=httpx.Response(200, json={
            "results": [
                {"id": 1, "title": "Inception", "release_date": "2010-07-16",
                 "overview": "A thief...", "poster_path": "/abc.jpg",
                 "genre_ids": [28, 18]},
            ],
        })
    )
    results = client.search("Inception", year=2010, kind_hint="movie")
    assert len(results) == 1
    assert results[0]["id"] == 1
    assert results[0]["title"] == "Inception"


@respx.mock
def test_search_tv_uses_tv_endpoint(client):
    route = respx.get("https://api.themoviedb.org/3/search/tv").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client.search("Breaking Bad", year=None, kind_hint="tv")
    assert route.called


@respx.mock
def test_search_no_hint_uses_multi(client):
    route = respx.get("https://api.themoviedb.org/3/search/multi").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client.search("Anything", year=None, kind_hint=None)
    assert route.called


@respx.mock
def test_search_returns_empty_on_error(client):
    respx.get("https://api.themoviedb.org/3/search/movie").mock(
        return_value=httpx.Response(401)
    )
    assert client.search("X", year=None, kind_hint="movie") == []


@respx.mock
def test_search_returns_empty_on_timeout(client):
    respx.get("https://api.themoviedb.org/3/search/movie").mock(
        side_effect=httpx.ReadTimeout("timeout")
    )
    assert client.search("X", year=None, kind_hint="movie") == []
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_tmdb_client.py -v
```
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Create `app/metadata/tmdb.py`**

```python
"""Клиент к TMDB API v3 (Bearer auth, v4 Read Access Token)."""
from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)


TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG_BASE = "https://image.tmdb.org/t/p/w500"


class TmdbClient:
    def __init__(self, api_key: str, timeout: float = 5.0):
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=TMDB_BASE,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def search(self, title: str, year: int | None,
               kind_hint: Literal["movie", "tv"] | None) -> list[dict[str, Any]]:
        """Возвращает сырые результаты поиска. На ошибке — []."""
        if kind_hint == "tv":
            endpoint = "/search/tv"
            params = {"query": title, "language": "ru-RU"}
            if year is not None:
                params["first_air_date_year"] = year
        elif kind_hint == "movie":
            endpoint = "/search/movie"
            params = {"query": title, "language": "ru-RU"}
            if year is not None:
                params["year"] = year
        else:
            endpoint = "/search/multi"
            params = {"query": title, "language": "ru-RU"}

        try:
            r = self._client.get(endpoint, params=params)
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("TMDB search failed for %r: %s", title, e)
            return []
        return r.json().get("results") or []
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_tmdb_client.py -v
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/metadata/tmdb.py tests/unit/test_tmdb_client.py
git commit -m "feat(tmdb): add TmdbClient with search endpoint"
```

---

### Task 12: TmdbClient.get_movie / get_tv → MetadataMatch

**Files:**
- Modify: `app/metadata/tmdb.py`
- Modify: `tests/unit/test_tmdb_client.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_tmdb_client.py`:

```python
from app.metadata.types import MetadataMatch


@respx.mock
def test_get_movie_returns_metadata_match(client):
    respx.get("https://api.themoviedb.org/3/movie/27205").mock(
        return_value=httpx.Response(200, json={
            "id": 27205,
            "title": "Начало",
            "release_date": "2010-07-15",
            "overview": "Описание Начала на русском.",
            "poster_path": "/abc.jpg",
            "genres": [{"id": 28, "name": "Боевик"}, {"id": 18, "name": "Драма"}],
            "production_countries": [{"iso_3166_1": "US"}],
        })
    )
    m = client.get_movie(27205)
    assert isinstance(m, MetadataMatch)
    assert m.source == "tmdb"
    assert m.external_id == 27205
    assert m.title == "Начало"
    assert m.year == 2010
    assert m.kind == "movie"
    assert m.description == "Описание Начала на русском."
    assert m.poster_url == "https://image.tmdb.org/t/p/w500/abc.jpg"
    assert "Боевик" in m.genres and "Драма" in m.genres


@respx.mock
def test_get_movie_animation_us_is_cartoon(client):
    respx.get("https://api.themoviedb.org/3/movie/1").mock(
        return_value=httpx.Response(200, json={
            "id": 1, "title": "Toy Story", "release_date": "1995-11-22",
            "overview": "", "poster_path": "/p.jpg",
            "genres": [{"id": 16, "name": "Анимация"}, {"id": 35, "name": "Комедия"}],
            "production_countries": [{"iso_3166_1": "US"}],
        })
    )
    m = client.get_movie(1)
    assert m.kind == "cartoon"


@respx.mock
def test_get_tv_animation_jp_is_anime(client):
    respx.get("https://api.themoviedb.org/3/tv/2").mock(
        return_value=httpx.Response(200, json={
            "id": 2, "name": "Naruto", "first_air_date": "2002-10-03",
            "overview": "", "poster_path": "/n.jpg",
            "genres": [{"id": 16, "name": "Анимация"}],
            "origin_country": ["JP"],
        })
    )
    m = client.get_tv(2)
    assert m.kind == "anime"


@respx.mock
def test_get_tv_returns_series_kind(client):
    respx.get("https://api.themoviedb.org/3/tv/3").mock(
        return_value=httpx.Response(200, json={
            "id": 3, "name": "Breaking Bad", "first_air_date": "2008-01-20",
            "overview": "", "poster_path": "/bb.jpg",
            "genres": [{"id": 18, "name": "Драма"}],
            "origin_country": ["US"],
        })
    )
    m = client.get_tv(3)
    assert m.kind == "series"


@respx.mock
def test_get_movie_documentary(client):
    respx.get("https://api.themoviedb.org/3/movie/4").mock(
        return_value=httpx.Response(200, json={
            "id": 4, "title": "Doc", "release_date": "2020-01-01",
            "overview": "", "poster_path": None,
            "genres": [{"id": 99, "name": "Документальный"}],
            "production_countries": [{"iso_3166_1": "US"}],
        })
    )
    m = client.get_movie(4)
    assert m.kind == "documentary"
    assert m.poster_url is None
```

- [ ] **Step 2: Run tests to verify fail**

```
pytest tests/unit/test_tmdb_client.py -v
```
Expected: FAIL (`get_movie`, `get_tv` not defined).

- [ ] **Step 3: Replace `app/metadata/tmdb.py` entirely**

Полностью заменить файл `app/metadata/tmdb.py` (склеиваем search + новые методы + хелперы в одной декларации, чтобы не было двойного class):

```python
"""Клиент к TMDB API v3 (Bearer auth, v4 Read Access Token)."""
from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

from app.metadata.types import KindLiteral, MetadataMatch

log = logging.getLogger(__name__)


TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG_BASE = "https://image.tmdb.org/t/p/w500"


def _map_kind_from_tmdb(media_type: Literal["movie", "tv"],
                         genres: list[str],
                         country_codes: list[str]) -> KindLiteral:
    if "Документальный" in genres:
        return "documentary"
    if "Анимация" in genres:
        if "JP" in country_codes:
            return "anime"
        if media_type == "movie":
            return "cartoon"
        return "series"
    return "movie" if media_type == "movie" else "series"


def _parse_year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        return int(date_str[:4])
    except (ValueError, TypeError):
        return None


def _poster_url(poster_path: str | None) -> str | None:
    if not poster_path:
        return None
    return f"{TMDB_IMG_BASE}{poster_path}"


class TmdbClient:
    def __init__(self, api_key: str, timeout: float = 5.0):
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=TMDB_BASE,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def search(self, title: str, year: int | None,
               kind_hint: Literal["movie", "tv"] | None) -> list[dict[str, Any]]:
        if kind_hint == "tv":
            endpoint, params = "/search/tv", {"query": title, "language": "ru-RU"}
            if year is not None:
                params["first_air_date_year"] = year
        elif kind_hint == "movie":
            endpoint, params = "/search/movie", {"query": title, "language": "ru-RU"}
            if year is not None:
                params["year"] = year
        else:
            endpoint, params = "/search/multi", {"query": title, "language": "ru-RU"}
        try:
            r = self._client.get(endpoint, params=params)
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("TMDB search failed for %r: %s", title, e)
            return []
        return r.json().get("results") or []

    def get_movie(self, tmdb_id: int) -> MetadataMatch | None:
        try:
            r = self._client.get(f"/movie/{tmdb_id}", params={"language": "ru-RU"})
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("TMDB get_movie %d failed: %s", tmdb_id, e)
            return None
        d = r.json()
        genres = [g["name"] for g in (d.get("genres") or [])]
        country_codes = [c["iso_3166_1"] for c in (d.get("production_countries") or [])]
        return MetadataMatch(
            source="tmdb",
            external_id=tmdb_id,
            title=d.get("title") or "",
            year=_parse_year(d.get("release_date")),
            kind=_map_kind_from_tmdb("movie", genres, country_codes),
            description=d.get("overview") or None,
            poster_url=_poster_url(d.get("poster_path")),
            genres=genres,
            score=1.0,
        )

    def get_tv(self, tmdb_id: int) -> MetadataMatch | None:
        try:
            r = self._client.get(f"/tv/{tmdb_id}", params={"language": "ru-RU"})
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("TMDB get_tv %d failed: %s", tmdb_id, e)
            return None
        d = r.json()
        genres = [g["name"] for g in (d.get("genres") or [])]
        country_codes = list(d.get("origin_country") or [])
        return MetadataMatch(
            source="tmdb",
            external_id=tmdb_id,
            title=d.get("name") or "",
            year=_parse_year(d.get("first_air_date")),
            kind=_map_kind_from_tmdb("tv", genres, country_codes),
            description=d.get("overview") or None,
            poster_url=_poster_url(d.get("poster_path")),
            genres=genres,
            score=1.0,
        )
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_tmdb_client.py -v
```
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add app/metadata/tmdb.py tests/unit/test_tmdb_client.py
git commit -m "feat(tmdb): add get_movie/get_tv returning MetadataMatch with kind mapping"
```

---

## Phase 7 — Kinopoisk Client

### Task 13: KinopoiskClient.search

**Files:**
- Create: `app/metadata/kinopoisk.py`
- Test: `tests/unit/test_kinopoisk_client.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_kinopoisk_client.py`:

```python
import httpx
import respx
import pytest

from app.metadata.kinopoisk import KinopoiskClient


@pytest.fixture
def client():
    return KinopoiskClient(api_key="fake-kp-key")


@respx.mock
def test_search_returns_results(client):
    respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/search-by-keyword"
    ).mock(return_value=httpx.Response(200, json={
        "films": [
            {"filmId": 100, "nameRu": "Тест", "year": "2020", "posterUrl": "https://kp/p.jpg"},
        ],
    }))
    results = client.search("Тест", year=2020)
    assert len(results) == 1
    assert results[0]["filmId"] == 100


@respx.mock
def test_search_uses_x_api_key_header(client):
    route = respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/search-by-keyword"
    ).mock(return_value=httpx.Response(200, json={"films": []}))
    client.search("X", year=None)
    assert route.called
    assert route.calls.last.request.headers["X-API-KEY"] == "fake-kp-key"


@respx.mock
def test_search_empty_on_error(client):
    respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/search-by-keyword"
    ).mock(return_value=httpx.Response(401))
    assert client.search("X", year=None) == []


@respx.mock
def test_search_increments_quota_counter(client):
    respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/search-by-keyword"
    ).mock(return_value=httpx.Response(200, json={"films": []}))
    assert client.quota_used_today() == 0
    client.search("X", year=None)
    assert client.quota_used_today() == 1
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_kinopoisk_client.py -v
```
Expected: FAIL.

- [ ] **Step 3: Create `app/metadata/kinopoisk.py`**

```python
"""Клиент к Kinopoisk Unofficial API."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


KP_BASE = "https://kinopoiskapiunofficial.tech/api/v2.2"
DAILY_LIMIT = 500


class KinopoiskClient:
    def __init__(self, api_key: str, timeout: float = 5.0):
        self._client = httpx.Client(
            base_url=KP_BASE,
            timeout=timeout,
            headers={"X-API-KEY": api_key, "Accept": "application/json"},
        )
        self._quota_count = 0
        self._quota_day_start = time.time()

    def close(self) -> None:
        self._client.close()

    def quota_used_today(self) -> int:
        self._maybe_reset_quota()
        return self._quota_count

    def quota_ok(self) -> bool:
        return self.quota_used_today() < DAILY_LIMIT

    def _maybe_reset_quota(self) -> None:
        if time.time() - self._quota_day_start > 86400:
            self._quota_count = 0
            self._quota_day_start = time.time()

    def _bump_quota(self) -> None:
        self._maybe_reset_quota()
        self._quota_count += 1

    def search(self, title: str, year: int | None) -> list[dict[str, Any]]:
        if not self.quota_ok():
            log.info("Kinopoisk daily quota exhausted, skipping search for %r", title)
            return []
        try:
            r = self._client.get("/films/search-by-keyword", params={"keyword": title})
            self._bump_quota()
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("Kinopoisk search failed for %r: %s", title, e)
            return []
        films = r.json().get("films") or []
        if year is not None:
            films = [f for f in films if str(f.get("year") or "") == str(year)]
        return films
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_kinopoisk_client.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/metadata/kinopoisk.py tests/unit/test_kinopoisk_client.py
git commit -m "feat(kinopoisk): add KinopoiskClient.search with rate limit counter"
```

---

### Task 14: KinopoiskClient.get_film → MetadataMatch

**Files:**
- Modify: `app/metadata/kinopoisk.py`
- Modify: `tests/unit/test_kinopoisk_client.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_kinopoisk_client.py`:

```python
from app.metadata.types import MetadataMatch


@respx.mock
def test_get_film_returns_metadata_match(client):
    respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/100"
    ).mock(return_value=httpx.Response(200, json={
        "kinopoiskId": 100,
        "nameRu": "Иван Васильевич меняет профессию",
        "year": 1973,
        "description": "Описание...",
        "posterUrl": "https://kp/p.jpg",
        "genres": [{"genre": "комедия"}, {"genre": "фантастика"}],
        "type": "FILM",
    }))
    m = client.get_film(100)
    assert isinstance(m, MetadataMatch)
    assert m.source == "kinopoisk"
    assert m.external_id == 100
    assert m.title == "Иван Васильевич меняет профессию"
    assert m.year == 1973
    assert m.kind == "movie"
    assert m.poster_url == "https://kp/p.jpg"
    assert "комедия" in m.genres


@respx.mock
def test_get_film_series_kind(client):
    respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/200"
    ).mock(return_value=httpx.Response(200, json={
        "kinopoiskId": 200, "nameRu": "Сериал X", "year": 2020,
        "description": "", "posterUrl": None,
        "genres": [{"genre": "драма"}], "type": "TV_SERIES",
    }))
    m = client.get_film(200)
    assert m.kind == "series"


@respx.mock
def test_get_film_returns_none_on_error(client):
    respx.get(
        "https://kinopoiskapiunofficial.tech/api/v2.2/films/999"
    ).mock(return_value=httpx.Response(404))
    assert client.get_film(999) is None
```

- [ ] **Step 2: Run tests**

```
pytest tests/unit/test_kinopoisk_client.py -v
```
Expected: FAIL (`get_film` not defined).

- [ ] **Step 3: Add `get_film` to `app/metadata/kinopoisk.py`**

Append to `KinopoiskClient`:

```python
    def get_film(self, kp_id: int) -> "MetadataMatch | None":
        from app.metadata.types import MetadataMatch
        if not self.quota_ok():
            log.info("Kinopoisk daily quota exhausted, skipping get_film(%d)", kp_id)
            return None
        try:
            r = self._client.get(f"/films/{kp_id}")
            self._bump_quota()
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("Kinopoisk get_film(%d) failed: %s", kp_id, e)
            return None
        d = r.json()
        kp_type = (d.get("type") or "").upper()
        kind = "series" if kp_type in ("TV_SERIES", "MINI_SERIES", "TV_SHOW") else "movie"
        genres = [g.get("genre") for g in (d.get("genres") or []) if g.get("genre")]
        year = d.get("year")
        try:
            year = int(year) if year else None
        except (ValueError, TypeError):
            year = None
        return MetadataMatch(
            source="kinopoisk",
            external_id=kp_id,
            title=d.get("nameRu") or d.get("nameOriginal") or "",
            year=year,
            kind=kind,
            description=d.get("description") or None,
            poster_url=d.get("posterUrl") or None,
            genres=genres,
            score=1.0,
        )
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_kinopoisk_client.py -v
```
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add app/metadata/kinopoisk.py tests/unit/test_kinopoisk_client.py
git commit -m "feat(kinopoisk): add get_film returning MetadataMatch"
```

---

## Phase 8 — Matcher Orchestrator

### Task 15: matcher.find_match with TMDB → Kinopoisk fallback

**Files:**
- Create: `app/metadata/matcher.py`
- Test: `tests/unit/test_matcher.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_matcher.py`:

```python
from unittest.mock import MagicMock

from app.metadata.matcher import find_match, _is_confident, _normalize
from app.metadata.types import MetadataMatch
from app.torrents.title_parser import ParsedTitle


def _make_tmdb_match(title="X", year=2020, score=1.0):
    return MetadataMatch(
        source="tmdb", external_id=1, title=title, year=year,
        kind="movie", description=None, poster_url=None,
        genres=[], score=score,
    )


def test_normalize_lowercase_and_strip_punct():
    assert _normalize("Inception (2010)!") == "inception 2010"


def test_is_confident_with_year_match():
    parsed = ParsedTitle(title="Inception", year=2010, season=None, episode=None, kind_hint="movie")
    top = _make_tmdb_match(title="Inception", year=2010)
    assert _is_confident(top, parsed)


def test_is_confident_with_year_mismatch():
    parsed = ParsedTitle(title="Inception", year=2010, season=None, episode=None, kind_hint="movie")
    top = _make_tmdb_match(title="Inception", year=2015)
    assert not _is_confident(top, parsed)


def test_is_confident_no_year_requires_higher_similarity():
    parsed = ParsedTitle(title="Some Long Title", year=None, season=None, episode=None, kind_hint=None)
    top_low = _make_tmdb_match(title="Some Other Title", year=None)
    assert not _is_confident(top_low, parsed)

    top_high = _make_tmdb_match(title="Some Long Title", year=None)
    assert _is_confident(top_high, parsed)


def test_find_match_uses_tmdb_first_on_confident():
    parsed = ParsedTitle(title="Inception", year=2010, season=None, episode=None, kind_hint="movie")
    tmdb = MagicMock()
    tmdb.search.return_value = [{"id": 27205, "title": "Inception", "release_date": "2010-07-15"}]
    tmdb.get_movie.return_value = _make_tmdb_match(title="Inception", year=2010)
    kp = MagicMock()
    m = find_match(parsed, tmdb=tmdb, kinopoisk=kp)
    assert m is not None
    assert m.source == "tmdb"
    assert m.title == "Inception"
    kp.search.assert_not_called()


def test_find_match_falls_back_to_kinopoisk_if_tmdb_empty():
    parsed = ParsedTitle(title="Союзмультфильм", year=1970,
                         season=None, episode=None, kind_hint="movie")
    tmdb = MagicMock(); tmdb.search.return_value = []
    kp = MagicMock()
    kp.quota_ok.return_value = True
    kp.search.return_value = [{"filmId": 100, "nameRu": "Союзмультфильм", "year": "1970"}]
    kp.get_film.return_value = MetadataMatch(
        source="kinopoisk", external_id=100, title="Союзмультфильм", year=1970,
        kind="cartoon", description=None, poster_url=None, genres=[], score=1.0,
    )
    m = find_match(parsed, tmdb=tmdb, kinopoisk=kp)
    assert m is not None
    assert m.source == "kinopoisk"


def test_find_match_returns_none_when_both_empty():
    parsed = ParsedTitle(title="Nothing", year=None, season=None, episode=None, kind_hint=None)
    tmdb = MagicMock(); tmdb.search.return_value = []
    kp = MagicMock(); kp.quota_ok.return_value = True; kp.search.return_value = []
    assert find_match(parsed, tmdb=tmdb, kinopoisk=kp) is None


def test_find_match_skips_tmdb_when_client_none():
    parsed = ParsedTitle(title="X", year=2020, season=None, episode=None, kind_hint="movie")
    kp = MagicMock()
    kp.quota_ok.return_value = True
    kp.search.return_value = [{"filmId": 1, "nameRu": "X", "year": "2020"}]
    kp.get_film.return_value = MetadataMatch(
        source="kinopoisk", external_id=1, title="X", year=2020,
        kind="movie", description=None, poster_url=None, genres=[], score=1.0,
    )
    m = find_match(parsed, tmdb=None, kinopoisk=kp)
    assert m is not None and m.source == "kinopoisk"


def test_find_match_returns_none_when_no_clients():
    parsed = ParsedTitle(title="X", year=2020, season=None, episode=None, kind_hint="movie")
    assert find_match(parsed, tmdb=None, kinopoisk=None) is None


def test_find_match_skips_kinopoisk_when_quota_exhausted():
    parsed = ParsedTitle(title="X", year=2020, season=None, episode=None, kind_hint="movie")
    tmdb = MagicMock(); tmdb.search.return_value = []
    kp = MagicMock(); kp.quota_ok.return_value = False
    m = find_match(parsed, tmdb=tmdb, kinopoisk=kp)
    assert m is None
    kp.search.assert_not_called()
```

- [ ] **Step 2: Run tests to verify fail**

```
pytest tests/unit/test_matcher.py -v
```
Expected: FAIL.

- [ ] **Step 3: Create `app/metadata/matcher.py`**

```python
"""Оркестратор: TMDB → fallback Kinopoisk → None."""
from __future__ import annotations

import difflib
import logging
import re
from typing import TYPE_CHECKING

from app.metadata.types import MetadataMatch

if TYPE_CHECKING:
    from app.metadata.tmdb import TmdbClient
    from app.metadata.kinopoisk import KinopoiskClient
    from app.torrents.title_parser import ParsedTitle

log = logging.getLogger(__name__)


_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    s = s.lower()
    s = _PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _is_confident(match: MetadataMatch, parsed: "ParsedTitle") -> bool:
    sim = _similarity(match.title, parsed.title)
    if parsed.year is not None:
        if match.year is None:
            # candidate без года, а parsed — с годом: маловероятно правильно
            return sim >= 0.9
        if abs(match.year - parsed.year) > 1:
            return False
        return sim >= 0.7
    return sim >= 0.85


def _tmdb_top_to_match(client: "TmdbClient", result: dict, hint: str | None) -> MetadataMatch | None:
    media_type = result.get("media_type") or hint
    rid = result.get("id")
    if rid is None:
        return None
    if media_type == "tv":
        return client.get_tv(rid)
    return client.get_movie(rid)


def find_match(
    parsed: "ParsedTitle",
    tmdb: "TmdbClient | None",
    kinopoisk: "KinopoiskClient | None",
) -> MetadataMatch | None:
    # 1. TMDB
    if tmdb is not None:
        results = tmdb.search(parsed.title, year=parsed.year, kind_hint=parsed.kind_hint)
        if results:
            top = _tmdb_top_to_match(tmdb, results[0], parsed.kind_hint)
            if top is not None and _is_confident(top, parsed):
                return top

    # 2. Kinopoisk fallback
    if kinopoisk is not None and kinopoisk.quota_ok():
        results = kinopoisk.search(parsed.title, year=parsed.year)
        if results:
            kp_id = results[0].get("filmId") or results[0].get("kinopoiskId")
            if kp_id is None:
                return None
            top = kinopoisk.get_film(int(kp_id))
            if top is not None and _is_confident(top, parsed):
                return top

    return None
```

- [ ] **Step 4: Run tests**

```
pytest tests/unit/test_matcher.py -v
```
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add app/metadata/matcher.py tests/unit/test_matcher.py
git commit -m "feat(metadata): add matcher.find_match with TMDB primary + Kinopoisk fallback"
```

---

## Phase 9 — Scanner Integration

### Task 16: Scanner uses ParsedTitle + ffprobe + matcher

**Files:**
- Modify: `app/torrents/scanner.py`
- Modify: `app/deps.py` (add `get_metadata_clients` factory)
- Test: `tests/integration/test_scanner_match.py`

- [ ] **Step 1: Add factory for metadata clients in `app/deps.py`**

Append to `app/deps.py`:

```python
@lru_cache(maxsize=1)
def get_tmdb_client():
    from app.metadata.tmdb import TmdbClient
    s = get_settings()
    if not s.tmdb_api_key:
        return None
    return TmdbClient(s.tmdb_api_key)


@lru_cache(maxsize=1)
def get_kinopoisk_client():
    from app.metadata.kinopoisk import KinopoiskClient
    s = get_settings()
    if not s.kinopoisk_api_key:
        return None
    return KinopoiskClient(s.kinopoisk_api_key)
```

- [ ] **Step 2: Update `app/torrents/scanner.py::scan_once` to wire everything**

Replace `scan_once` function body:

```python
def scan_once(
    qb: _QbProto,
    session: Session,
    *,
    tmdb=None,
    kinopoisk=None,
) -> int:
    """Один проход. Возвращает число добавленных media_items.

    tmdb/kinopoisk — необязательные клиенты (None ⇒ авто-матч пропускается).
    """
    from app.metadata.ffprobe import get_duration_seconds, probe_audio_tracks
    from app.metadata.matcher import find_match
    from app.models import Genre

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
        video = _find_largest_video(t.content_path)
        if video is None:
            log.info("scan_once: no video file in %s, skipping", t.content_path)
            continue

        parsed = parse_title(video.name)
        duration = get_duration_seconds(str(video))
        audio = probe_audio_tracks(str(video))
        audio_dicts = [
            {"index": a.index, "codec": a.codec, "language": a.language,
             "title": a.title, "channels": a.channels}
            for a in audio
        ]

        # default kind on hint
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
                normalized = gname.strip()
                if not normalized:
                    continue
                existing = session.scalars(
                    select(Genre).where(Genre.name == normalized)
                ).first()
                if existing is None:
                    existing = Genre(name=normalized)
                    session.add(existing)
                    session.flush()
                item.genres.append(existing)
        else:
            item.match_status = "failed"

        session.add(item)
        added += 1
    return added
```

Also extend `scanner_loop`:

```python
async def scanner_loop(
    qb: _QbProto,
    factory: sessionmaker[Session],
    interval_seconds: float = 10.0,
    *,
    tmdb=None,
    kinopoisk=None,
) -> None:
    while True:
        try:
            with factory() as s:
                added = scan_once(qb, s, tmdb=tmdb, kinopoisk=kinopoisk)
                s.commit()
            if added:
                log.info("scanner: added %d new media item(s)", added)
        except Exception as e:
            log.exception("scanner_loop iteration failed: %s", e)
        await asyncio.sleep(interval_seconds)
```

And in `app/main.py::lifespan`, pass the clients in:

```python
from app.deps import get_tmdb_client, get_kinopoisk_client
scanner_task = asyncio.create_task(
    scanner_loop(
        get_qbittorrent_client(),
        get_db_factory(),
        interval_seconds=10.0,
        tmdb=get_tmdb_client(),
        kinopoisk=get_kinopoisk_client(),
    )
)
```

- [ ] **Step 3: Write integration test**

Create `tests/integration/test_scanner_match.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.torrents.scanner import scan_once
from app.torrents.types import TorrentInfo
from app.metadata.types import MetadataMatch
from app.models import MediaItem, Genre


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

    with db_factory() as s:
        # name parser must produce title "Sample Movie" with year 2024
        # → нужен файл «Sample.Movie.2024.1080p.BluRay.mkv» рядом с реальным sample.mp4
        # Создадим симлинк:
        import os, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sym = Path(tmp) / "Sample.Movie.2024.1080p.BluRay.mkv"
            os.symlink(SAMPLE, sym)
            t = TorrentInfo(hash="aaa", content_path=str(sym), is_complete=True)
            added = scan_once(_qb_with([t]), s, tmdb=tmdb, kinopoisk=None)
        s.commit()

    assert added == 1
    with db_factory() as s:
        m = s.scalars(__import__('sqlalchemy').select(MediaItem)).one()
        assert m.title == "Sample Movie"
        assert m.year == 2024
        assert m.kind == "movie"
        assert m.match_status == "matched"
        assert m.match_source == "tmdb"
        assert m.tmdb_id == 1
        assert m.duration_seconds is not None  # ffprobe
        assert m.audio_tracks is not None
        assert len(m.genres) == 1 and m.genres[0].name == "Драма"


def test_scan_once_failed_match_without_keys(db_factory, tmp_path):
    import os
    sym = tmp_path / "Unknown.Some.Title.1080p.mkv"
    os.symlink(SAMPLE, sym)
    t = TorrentInfo(hash="bbb", content_path=str(sym), is_complete=True)

    with db_factory() as s:
        added = scan_once(_qb_with([t]), s, tmdb=None, kinopoisk=None)
        s.commit()

    assert added == 1
    with db_factory() as s:
        m = s.scalars(__import__('sqlalchemy').select(MediaItem)).one()
        assert m.match_status == "failed"
        assert m.match_source is None
        # title parser must give that back
        assert m.title == "Unknown Some Title"
```

- [ ] **Step 4: Run all scanner tests**

```
pytest tests/integration/test_scanner_match.py tests/integration/test_library_scanner.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/deps.py app/torrents/scanner.py app/main.py tests/integration/test_scanner_match.py
git commit -m "feat(scanner): integrate ffprobe + matcher; auto-fill metadata on new torrents"
```

---

### Task 17: Lazy fill of duration/audio_tracks on /media/{id} open

**Files:**
- Modify: `app/library/routes.py:33-43` (`media_page`)
- Test: `tests/integration/test_library.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/integration/test_library.py`:

```python
from pathlib import Path
from app.auth.passwords import hash_password
from app.models import MediaItem, User


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def test_media_page_backfills_duration_if_missing(client, db_factory, csrf_for):
    """Если у MediaItem нет duration_seconds (старая запись) — открытие /media/{id} должно его дозаполнить."""
    with db_factory() as s:
        s.add(User(username="alice", password_hash=hash_password("correct-password-12"),
                   must_change_password=False))
        s.commit()
        m = MediaItem(
            torrent_hash="h1", title="Old", file_path=str(SAMPLE),
            size_bytes=SAMPLE.stat().st_size,
            duration_seconds=None, audio_tracks=None, match_status="pending",
        )
        s.add(m); s.commit(); s.refresh(m); mid = m.id

    r = client.post("/login", data={
        "username": "alice", "password": "correct-password-12",
        "csrf_token": csrf_for(None),
    })
    cookie = r.cookies.get("session")

    r = client.get(f"/media/{mid}", cookies={"session": cookie})
    assert r.status_code == 200

    with db_factory() as s:
        m = s.get(MediaItem, mid)
        assert m.duration_seconds is not None  # дозаполнили
        assert m.audio_tracks is not None
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/integration/test_library.py::test_media_page_backfills_duration_if_missing -v
```
Expected: FAIL.

- [ ] **Step 3: Update `app/library/routes.py::media_page`**

Replace the function:

```python
@router.get("/media/{media_id}", response_class=HTMLResponse)
def media_page(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    # Лениво дозаполняем duration_seconds и audio_tracks для старых записей.
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

    # Прогресс текущего пользователя
    progress = db.scalars(
        select(WatchProgress).where(
            WatchProgress.user_id == user.id,
            WatchProgress.media_id == media_id,
        )
    ).first()
    saved_position = progress.position_seconds if progress else 0
    saved_audio_track = progress.audio_track_index if progress else None

    return render(request, "media.html", {
        "user": user,
        "item": item,
        "saved_position_seconds": saved_position,
        "saved_audio_track_index": saved_audio_track,
    })
```

Add at the top of the file:

```python
from app.models import MediaItem, User, WatchProgress
```

(replace previous import line).

- [ ] **Step 4: Run tests**

```
pytest tests/integration/test_library.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/library/routes.py tests/integration/test_library.py
git commit -m "feat(library): lazy-fill duration_seconds and audio_tracks on media page open + pass watch_progress to template"
```

---

## Phase 10 — Continue Watching UI

### Task 18: Resume banner + currentTime restore on media page

**Files:**
- Modify: `templates/media.html`

- [ ] **Step 1: Add resume banner and currentTime restore script**

Edit `templates/media.html`. Add above `<section class="player-shell">`:

```html
{% if saved_position_seconds and saved_position_seconds > 0
      and (not item.duration_seconds or saved_position_seconds < 0.65 * item.duration_seconds) %}
<section class="resume-banner">
  <span>⏱ Продолжить с <span id="resume-time">{{ saved_position_seconds }}</span> сек</span>
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
```

Then update the main `<script>` block, replacing it entirely with:

```html
<script src="/static/hls.min.js"></script>
<script>
(function() {
  const MEDIA_ID = {{ item.id }};
  const SAVED_POSITION = {{ saved_position_seconds }};
  const SAVED_AUDIO = {{ saved_audio_track_index|tojson }};
  const video = document.getElementById('player');
  const src = '/api/stream/' + MEDIA_ID + '/master.m3u8';

  let hls = null;
  let recoverAttempts = 0;

  function attachPlayer() {
    if (Hls.isSupported()) {
      hls = new Hls();
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        if (hls.audioTracks && hls.audioTracks.length > 1) {
          renderAudioSelector(hls);
        }
        if (SAVED_AUDIO != null && SAVED_AUDIO < hls.audioTracks.length) {
          hls.audioTrack = SAVED_AUDIO;
        }
      });
      hls.on(Hls.Events.ERROR, (e, data) => {
        console.error("HLS error:", data);
        if (!data.fatal) return;
        if (recoverAttempts < 1) {
          recoverAttempts++;
          setTimeout(() => {
            try { hls.startLoad(); } catch (err) { console.warn(err); }
          }, 1000);
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
  }

  attachPlayer();

  // Восстановление сохранённой позиции
  video.addEventListener('loadedmetadata', () => {
    if (SAVED_POSITION > 0 && SAVED_POSITION < video.duration - 30) {
      video.currentTime = SAVED_POSITION;
    }
  });

  // Кнопка "Сначала"
  const restartBtn = document.getElementById('restart-btn');
  if (restartBtn) {
    restartBtn.addEventListener('click', () => {
      video.currentTime = 0;
      fetch('/api/progress', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({media_id: MEDIA_ID, position_seconds: 0}),
      }).catch(() => {});
    });
  }

  // Heartbeat — даже на паузе
  setInterval(() => {
    if (video.ended || isNaN(video.currentTime)) return;
    const payload = {media_id: MEDIA_ID, position_seconds: Math.floor(video.currentTime)};
    if (hls && hls.audioTrack >= 0) payload.audio_track_index = hls.audioTrack;
    fetch('/api/progress', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }).catch(e => console.warn('progress save failed', e));
  }, 15000);

  // === Audio selector ===
  function renderAudioSelector(hlsInst) {
    const c = document.getElementById('audio-tracks');
    if (!c) return;
    c.innerHTML = '<span class="audio-label">🎧 Озвучка:</span>' + hlsInst.audioTracks.map(t =>
      `<button data-track="${t.id}" class="audio-track${t.id === hlsInst.audioTrack ? ' active' : ''}" type="button">`
      + escapeHtml(humanize(t.name || 'Track ' + (t.id + 1)))
      + (t.lang && t.lang !== 'und' ? ` <span class="lang">${escapeHtml(t.lang)}</span>` : '')
      + '</button>'
    ).join('');
    c.onclick = e => {
      const btn = e.target.closest('[data-track]');
      if (!btn) return;
      const id = parseInt(btn.dataset.track, 10);
      hlsInst.audioTrack = id;
      c.querySelectorAll('.audio-track').forEach(b => b.classList.toggle('active', b === btn));
      fetch('/api/progress', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          media_id: MEDIA_ID,
          position_seconds: Math.floor(video.currentTime || 0),
          audio_track_index: id,
        }),
      }).catch(() => {});
    };
  }
  function humanize(s) { return s.replace(/_/g, ' '); }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, ch => (
      {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"}[ch]
    ));
  }
})();
</script>
```

Also add empty selector container under `<section class="player-shell">`:

```html
<section class="player-shell">
  <video id="player" controls preload="metadata"></video>
  <div id="audio-tracks" class="audio-tracks"></div>
</section>
```

- [ ] **Step 2: Add CSS for resume banner**

Append to `static/style.css`:

```css
.resume-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  background: rgba(64, 132, 255, 0.12);
  border-left: 3px solid #4084ff;
  margin: 12px 0;
  font-size: 0.95rem;
}
.resume-banner button {
  margin-left: auto;
  padding: 4px 12px;
  border: 1px solid rgba(255, 255, 255, 0.25);
  background: transparent;
  color: inherit;
  border-radius: 4px;
  cursor: pointer;
}
.resume-banner button:hover { background: rgba(255, 255, 255, 0.08); }
```

- [ ] **Step 3: Manually verify**

```bash
uvicorn app.main:app --reload --port 8000
```

Открыть фильм, посмотреть 30 секунд, обновить страницу — должна быть плашка «Продолжить», плеер автоматически перематывает на 30 сек.

- [ ] **Step 4: Commit**

```bash
git add templates/media.html static/style.css
git commit -m "feat(player): add resume banner, restore saved position and audio track"
```

---

## Phase 11 — Multi-Audio HLS

### Task 19: Refactor start_hls for multi-variant master playlist

**Files:**
- Modify: `app/streaming/ffmpeg_runner.py`
- Modify: `tests/unit/test_ffmpeg_runner.py`

- [ ] **Step 1: Write failing test (cmd contents)**

Append to `tests/unit/test_ffmpeg_runner.py`:

```python
from unittest.mock import patch
from app.metadata.types import AudioTrack
from app.streaming.ffmpeg_runner import HlsParams


@patch("app.streaming.ffmpeg_runner.subprocess.Popen")
def test_start_hls_cmd_for_no_audio(mock_popen):
    from app.streaming.ffmpeg_runner import start_hls
    start_hls(HlsParams(source="/v.mkv", work_dir="/tmp/w", seek_seconds=0.0,
                         audio_tracks=[]))
    cmd = mock_popen.call_args[0][0]
    assert "-master_pl_name" in cmd
    # var_stream_map с одним вариантом v:0
    i = cmd.index("-var_stream_map")
    assert cmd[i+1] == "v:0"


@patch("app.streaming.ffmpeg_runner.subprocess.Popen")
def test_start_hls_cmd_for_one_audio(mock_popen):
    from app.streaming.ffmpeg_runner import start_hls
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
    from app.streaming.ffmpeg_runner import start_hls
    audio = [
        AudioTrack(index=0, codec="ac3", language="rus", title="Дубляж", channels=6),
        AudioTrack(index=1, codec="aac", language="eng", title=None, channels=2),
        AudioTrack(index=2, codec="aac", language="rus", title="Комментарии", channels=2),
    ]
    start_hls(HlsParams(source="/v.mkv", work_dir="/tmp/w", seek_seconds=0.0,
                         audio_tracks=audio))
    cmd = mock_popen.call_args[0][0]
    # должны быть -map для видео + трёх аудио (индексы 0, 1, 2)
    assert cmd.count("-map") == 4
    map_args = [cmd[j+1] for j, x in enumerate(cmd) if x == "-map"]
    assert "0:v:0" in map_args
    assert "0:a:0" in map_args
    assert "0:a:1" in map_args
    assert "0:a:2" in map_args


@patch("app.streaming.ffmpeg_runner.subprocess.Popen")
def test_start_hls_seek_preserved(mock_popen):
    from app.streaming.ffmpeg_runner import start_hls
    start_hls(HlsParams(source="/v.mkv", work_dir="/tmp/w", seek_seconds=42.5,
                         audio_tracks=[]))
    cmd = mock_popen.call_args[0][0]
    i = cmd.index("-ss")
    assert cmd[i+1] == "42.500"
```

- [ ] **Step 2: Run tests to verify fail**

```
pytest tests/unit/test_ffmpeg_runner.py -v -k "cmd_for or seek_preserved"
```
Expected: FAIL (`HlsParams` doesn't accept `audio_tracks`).

- [ ] **Step 3: Update `app/streaming/ffmpeg_runner.py`**

Replace `HlsParams` and `start_hls`:

```python
"""On-the-fly HLS-транскодинг с мульти-вариантным master playlist."""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import logging

from app.metadata.types import AudioTrack

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HlsParams:
    source: str
    work_dir: str
    seek_seconds: float
    audio_tracks: list[AudioTrack] = field(default_factory=list)


def _sanitize_name(name: str) -> str:
    # var_stream_map name не может содержать пробелы и запятые
    return name.replace(",", " ").replace(" ", "_") or "Track"


def _build_var_stream_map(audio_tracks: list[AudioTrack]) -> str:
    if not audio_tracks:
        return "v:0"
    parts = ["v:0,agroup:audio"]
    for i, t in enumerate(audio_tracks):
        name = _sanitize_name(t.title or t.language or f"Track {i+1}")
        lang = t.language or "und"
        parts.append(f"a:{i},agroup:audio,language:{lang},name:{name}")
    return " ".join(parts)


def start_hls(params: HlsParams) -> subprocess.Popen:
    Path(params.work_dir).mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-loglevel", "warning", "-nostdin"]
    if params.seek_seconds > 0:
        cmd += ["-ss", f"{params.seek_seconds:.3f}"]
    cmd += ["-i", params.source, "-map", "0:v:0"]
    for t in params.audio_tracks:
        cmd += ["-map", f"0:a:{t.index}"]

    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    if params.audio_tracks:
        cmd += ["-c:a", "aac", "-b:a", "128k"]

    cmd += [
        "-f", "hls",
        "-hls_time", "6",
        "-hls_list_size", "0",
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", _build_var_stream_map(params.audio_tracks),
        "-hls_segment_filename", f"{params.work_dir}/v%v/seg_%05d.ts",
        f"{params.work_dir}/v%v/playlist.m3u8",
    ]

    log.debug("ffmpeg cmd: %s", shlex.join(cmd))
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )


def wait_for_first_segment(work_dir: str | Path, timeout: float = 15.0) -> bool:
    """Ждёт появления первого сегмента в v0/."""
    deadline = time.time() + timeout
    work = Path(work_dir) / "v0"
    while time.time() < deadline:
        if work.exists() and any(work.glob("seg_*.ts")):
            return True
        time.sleep(0.1)
    return False


def kill(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
    except ProcessLookupError:
        pass
```

- [ ] **Step 4: Update the existing integration test for ffmpeg**

In `tests/unit/test_ffmpeg_runner.py`, update first test:

```python
def test_start_hls_creates_master_playlist_and_segments(work_dir):
    assert SAMPLE.exists()
    work_dir.mkdir()
    proc = start_hls(HlsParams(
        source=str(SAMPLE),
        work_dir=str(work_dir),
        seek_seconds=0.0,
        audio_tracks=[],  # sample.mp4 — простой, аудио мы здесь не пробуем
    ))
    try:
        ok = wait_for_first_segment(work_dir, timeout=15.0)
        assert ok, "ffmpeg не создал ни одного сегмента за 15 секунд"

        master = work_dir / "master.m3u8"
        v0_playlist = work_dir / "v0" / "playlist.m3u8"
        assert master.exists()
        assert v0_playlist.exists()
        assert "#EXTM3U" in master.read_text()
        assert "seg_" in v0_playlist.read_text()
    finally:
        kill(proc)
```

Replace `test_kill_terminates_process` similarly to use `audio_tracks=[]`. Same with `test_seek_offset_starts_later_in_video`.

- [ ] **Step 5: Run tests**

```
pytest tests/unit/test_ffmpeg_runner.py -v
```
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add app/streaming/ffmpeg_runner.py tests/unit/test_ffmpeg_runner.py
git commit -m "feat(streaming): switch to multi-variant master playlist (always)"
```

---

### Task 20: Streaming routes — master.m3u8 and v{n} subpaths

**Files:**
- Modify: `app/streaming/routes.py`
- Modify: `tests/integration/test_streaming.py`

- [ ] **Step 1: Update tests for new URL scheme**

Replace `tests/integration/test_streaming.py`:

```python
import shutil
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.models import MediaItem, User, WatchProgress
from app.streaming.stream_registry import get_registry


SAMPLE = Path(__file__).parent.parent / "fixtures" / "sample.mp4"


def _logged_in(client, db_factory, csrf_for):
    with db_factory() as s:
        s.add(User(
            username="alice", password_hash=hash_password("correct-password-12"),
            must_change_password=False,
        ))
        s.commit()
    r = client.post("/login", data={
        "username": "alice", "password": "correct-password-12", "csrf_token": csrf_for(None)
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
        reg.unregister(h.media_id, h.user_id)


def _create_media(db_factory, sample: Path) -> int:
    with db_factory() as s:
        m = MediaItem(torrent_hash="h", title="Test", file_path=str(sample),
                      size_bytes=sample.stat().st_size)
        s.add(m); s.commit(); s.refresh(m)
        return m.id


def test_master_starts_ffmpeg_and_returns_m3u8(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    r = client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert "#EXTM3U" in r.text


def test_legacy_playlist_redirects_to_master(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    r = client.get(f"/api/stream/{mid}/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 301
    assert r.headers["location"].endswith(f"/api/stream/{mid}/master.m3u8")


def test_variant_playlist_returned(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    # Сначала через master стартуем ffmpeg
    r = client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})
    assert r.status_code == 200

    r2 = client.get(f"/api/stream/{mid}/v0/playlist.m3u8", cookies={"session": cookie})
    assert r2.status_code == 200
    assert "#EXTM3U" in r2.text


def test_segment_in_subdir(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})

    # дождёмся сегмента
    for _ in range(150):
        r = client.get(f"/api/stream/{mid}/v0/playlist.m3u8", cookies={"session": cookie})
        if "seg_" in r.text:
            break
        time.sleep(0.1)

    seg_name = next((line for line in r.text.splitlines() if line.startswith("seg_")), None)
    assert seg_name
    r2 = client.get(f"/api/stream/{mid}/v0/{seg_name}", cookies={"session": cookie})
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "video/mp2t"


def test_variant_unknown_index_404(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})
    # ffmpeg для sample.mp4 без аудио → существует только v0
    r = client.get(f"/api/stream/{mid}/v99/playlist.m3u8", cookies={"session": cookie})
    assert r.status_code == 404


def test_segment_rejects_path_traversal(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})
    r = client.get(f"/api/stream/{mid}/v0/..%2Fetc%2Fpasswd", cookies={"session": cookie})
    assert r.status_code == 404


def test_progress_endpoint_upserts_watch_progress(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    r = client.post(
        "/api/progress",
        json={"media_id": mid, "position_seconds": 42},
        cookies={"session": cookie},
    )
    assert r.status_code == 204

    with db_factory() as s:
        wp = s.scalars(select(WatchProgress).where(WatchProgress.media_id == mid)).one()
        assert wp.position_seconds == 42


def test_progress_endpoint_accepts_audio_track_index(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)
    r = client.post(
        "/api/progress",
        json={"media_id": mid, "position_seconds": 42, "audio_track_index": 1},
        cookies={"session": cookie},
    )
    assert r.status_code == 204
    with db_factory() as s:
        wp = s.scalars(select(WatchProgress).where(WatchProgress.media_id == mid)).one()
        assert wp.audio_track_index == 1


def test_progress_endpoint_touches_stream_registry(client, db_factory, csrf_for):
    cookie = _logged_in(client, db_factory, csrf_for)
    mid = _create_media(db_factory, SAMPLE)

    r = client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})
    assert r.status_code == 200

    reg = get_registry()
    handle = next((h for h in reg.all_streams() if h.media_id == mid), None)
    assert handle is not None
    old_access = handle.last_access

    time.sleep(0.05)
    r = client.post(
        "/api/progress",
        json={"media_id": mid, "position_seconds": 100},
        cookies={"session": cookie},
    )
    assert r.status_code == 204

    handle2 = next((h for h in reg.all_streams() if h.media_id == mid), None)
    assert handle2.last_access > old_access
```

- [ ] **Step 2: Replace `app/streaming/routes.py` with new routes**

```python
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.config import get_settings
from app.deps import get_db
from app.models import MediaItem, User, WatchProgress
from app.streaming.ffmpeg_runner import HlsParams, kill, start_hls, wait_for_first_segment
from app.streaming.stream_registry import StreamHandle, get_registry


api_router = APIRouter(prefix="/api/stream")
progress_router = APIRouter(prefix="/api")


def _audio_tracks_from_media(media: MediaItem):
    from app.metadata.types import AudioTrack
    if not media.audio_tracks:
        return []
    return [
        AudioTrack(
            index=a["index"], codec=a["codec"], language=a.get("language"),
            title=a.get("title"), channels=a.get("channels", 0),
        )
        for a in media.audio_tracks
    ]


def _ensure_stream(media: MediaItem, user_id: int) -> StreamHandle:
    reg = get_registry()
    existing = reg.get(media.id, user_id)
    if existing is not None:
        reg.touch(media.id, user_id)
        return existing
    settings = get_settings()
    Path(settings.hls_work_root).mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(
        prefix=f"hls_m{media.id}_u{user_id}_",
        dir=settings.hls_work_root,
    ))
    audio_tracks = _audio_tracks_from_media(media)
    proc = start_hls(HlsParams(
        source=media.file_path, work_dir=str(work_dir),
        seek_seconds=0.0, audio_tracks=audio_tracks,
    ))
    handle = StreamHandle(media_id=media.id, user_id=user_id, work_dir=str(work_dir), process=proc)
    reg.register(handle)
    if not wait_for_first_segment(work_dir, timeout=15.0):
        kill(proc)
        reg.unregister(media.id, user_id)
        raise HTTPException(status_code=503, detail="ffmpeg не выдал первый сегмент за 15с")
    return handle


@api_router.get("/{media_id}/master.m3u8")
def stream_master(
    media_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    media = db.get(MediaItem, media_id)
    if media is None:
        raise HTTPException(status_code=404)
    handle = _ensure_stream(media, user.id)
    master = Path(handle.work_dir) / "master.m3u8"
    # Подождать немного, если master ещё не записан
    import time as _t
    deadline = _t.time() + 5.0
    while not master.exists() and _t.time() < deadline:
        _t.sleep(0.1)
    if not master.exists():
        raise HTTPException(status_code=503, detail="master playlist ещё не сгенерирован")
    return Response(
        content=master.read_bytes(),
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@api_router.get("/{media_id}/playlist.m3u8")
def legacy_playlist_redirect(media_id: int):
    return RedirectResponse(f"/api/stream/{media_id}/master.m3u8", status_code=301)


_VARIANT_RE = re.compile(r"^v\d+$")
_SEGMENT_NAME_RE = re.compile(r"^seg_\d{5}\.ts$")


@api_router.get("/{media_id}/{variant}/playlist.m3u8")
def stream_variant_playlist(
    media_id: int,
    variant: str,
    user: Annotated[User, Depends(get_current_user)],
):
    if not _VARIANT_RE.match(variant):
        raise HTTPException(status_code=404)
    reg = get_registry()
    handle = reg.get(media_id, user.id)
    if handle is None:
        raise HTTPException(status_code=410, detail="стрим уже завершён, обновите страницу")
    playlist = Path(handle.work_dir) / variant / "playlist.m3u8"
    if not playlist.exists():
        raise HTTPException(status_code=404)
    reg.touch(media_id, user.id)
    return Response(
        content=playlist.read_bytes(),
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@api_router.get("/{media_id}/{variant}/{segment_name}")
def stream_segment(
    media_id: int,
    variant: str,
    segment_name: str,
    user: Annotated[User, Depends(get_current_user)],
):
    if not _VARIANT_RE.match(variant) or not _SEGMENT_NAME_RE.match(segment_name):
        raise HTTPException(status_code=404)
    reg = get_registry()
    handle = reg.get(media_id, user.id)
    if handle is None:
        raise HTTPException(status_code=410, detail="стрим уже завершён, обновите страницу")
    seg_path = Path(handle.work_dir) / variant / segment_name
    if not seg_path.exists():
        raise HTTPException(status_code=404)
    reg.touch(media_id, user.id)
    return FileResponse(
        str(seg_path),
        media_type="video/mp2t",
        headers={"Cache-Control": "no-store"},
    )


class _ProgressIn(BaseModel):
    media_id: int
    position_seconds: int
    audio_track_index: int | None = None


@progress_router.post("/progress", status_code=204, include_in_schema=False)
def progress(
    payload: Annotated[_ProgressIn, Body()],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    existing = db.scalars(
        select(WatchProgress).where(
            WatchProgress.user_id == user.id,
            WatchProgress.media_id == payload.media_id,
        )
    ).first()
    now = datetime.now(timezone.utc)
    if existing is not None:
        existing.position_seconds = payload.position_seconds
        existing.updated_at = now
        if payload.audio_track_index is not None:
            existing.audio_track_index = payload.audio_track_index
    else:
        db.add(WatchProgress(
            user_id=user.id, media_id=payload.media_id,
            position_seconds=payload.position_seconds,
            audio_track_index=payload.audio_track_index,
            updated_at=now,
        ))
    get_registry().touch(payload.media_id, user.id)
    db.commit()
```

- [ ] **Step 3: Run tests**

```
pytest tests/integration/test_streaming.py -v
```
Expected: PASS (all tests).

- [ ] **Step 4: Commit**

```bash
git add app/streaming/routes.py tests/integration/test_streaming.py
git commit -m "feat(streaming): master.m3u8 + v{n} variant routes; legacy /playlist.m3u8 → 301"
```

---

### Task 21: Multi-audio integration test with real fixture

**Files:**
- Create: `tests/integration/test_stream_audio_switch.py`
- Create script: `scripts/create_multi_audio_fixture.sh` (документация)
- Manual: create `tests/fixtures/multi_audio.mkv` (~1 MB) с двумя аудиодорожками

- [ ] **Step 1: Document fixture creation**

Create `scripts/create_multi_audio_fixture.sh`:

```bash
#!/usr/bin/env bash
# Создаёт тестовый mkv с 2 аудиодорожками (рус + англ) на основе sample.mp4.
# Использует ffmpeg.
set -euo pipefail

SRC="tests/fixtures/sample.mp4"
OUT="tests/fixtures/multi_audio.mkv"

if [ ! -f "$SRC" ]; then
  echo "Missing $SRC"; exit 1
fi

# Создаём два audio-трека: оригинал + копию (с разным language tag).
ffmpeg -y -i "$SRC" -i "$SRC" \
  -map 0:v:0 -map 0:a:0 -map 1:a:0 \
  -c:v copy -c:a copy \
  -metadata:s:a:0 language=rus -metadata:s:a:0 title="Дубляж" \
  -metadata:s:a:1 language=eng -metadata:s:a:1 title="Original" \
  "$OUT"

echo "Created $OUT"
```

Make executable: `chmod +x scripts/create_multi_audio_fixture.sh`.

- [ ] **Step 2: Generate the fixture**

```bash
bash scripts/create_multi_audio_fixture.sh
```

Verify:
```bash
ffprobe -v error -select_streams a -show_entries stream=index:stream_tags=language,title tests/fixtures/multi_audio.mkv
```

Expected: два audio-стрима с language=rus/eng.

- [ ] **Step 3: Write integration test**

Create `tests/integration/test_stream_audio_switch.py`:

```python
import time
from pathlib import Path

import pytest

from app.auth.passwords import hash_password
from app.models import MediaItem, User
from app.streaming.stream_registry import get_registry


MULTI_AUDIO = Path(__file__).parent.parent / "fixtures" / "multi_audio.mkv"


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    reg = get_registry()
    for h in list(reg.all_streams()):
        if h.process is not None:
            from app.streaming.ffmpeg_runner import kill
            kill(h.process)
        reg.unregister(h.media_id, h.user_id)


@pytest.mark.skipif(not MULTI_AUDIO.exists(),
                    reason="multi_audio.mkv fixture missing; run scripts/create_multi_audio_fixture.sh")
def test_master_playlist_lists_audio_renditions(client, db_factory, csrf_for):
    from app.metadata.ffprobe import probe_audio_tracks
    tracks = probe_audio_tracks(str(MULTI_AUDIO))
    assert len(tracks) == 2

    audio_dicts = [
        {"index": a.index, "codec": a.codec, "language": a.language,
         "title": a.title, "channels": a.channels}
        for a in tracks
    ]
    with db_factory() as s:
        s.add(User(username="alice",
                   password_hash=hash_password("correct-password-12"),
                   must_change_password=False))
        s.commit()
        m = MediaItem(
            torrent_hash="ma", title="Multi", file_path=str(MULTI_AUDIO),
            size_bytes=MULTI_AUDIO.stat().st_size, audio_tracks=audio_dicts,
        )
        s.add(m); s.commit(); s.refresh(m); mid = m.id

    r = client.post("/login", data={"username": "alice",
                                     "password": "correct-password-12",
                                     "csrf_token": csrf_for(None)})
    cookie = r.cookies.get("session")

    r = client.get(f"/api/stream/{mid}/master.m3u8", cookies={"session": cookie})
    assert r.status_code == 200
    master = r.text
    assert "#EXT-X-MEDIA:TYPE=AUDIO" in master
    # Должно быть две AUDIO записи
    assert master.count("#EXT-X-MEDIA:TYPE=AUDIO") == 2
    # И языки
    assert "LANGUAGE=\"rus\"" in master
    assert "LANGUAGE=\"eng\"" in master
```

- [ ] **Step 4: Run integration test**

```
pytest tests/integration/test_stream_audio_switch.py -v
```
Expected: PASS (или SKIPPED если фикстура не создана).

- [ ] **Step 5: Commit**

```bash
git add scripts/create_multi_audio_fixture.sh tests/integration/test_stream_audio_switch.py tests/fixtures/multi_audio.mkv
git commit -m "test(streaming): integration test for multi-audio master playlist"
```

---

### Task 22: Add CSS for audio selector

**Files:**
- Modify: `static/style.css`

- [ ] **Step 1: Append CSS**

Append to `static/style.css`:

```css
.audio-tracks {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin: 12px 0;
  font-size: 0.9rem;
}
.audio-tracks .audio-label {
  font-weight: 500;
  opacity: 0.7;
  margin-right: 4px;
}
.audio-track {
  padding: 6px 12px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  background: rgba(255, 255, 255, 0.04);
  color: inherit;
  border-radius: 16px;
  cursor: pointer;
  font-size: 0.9rem;
  transition: all 0.15s;
}
.audio-track:hover { background: rgba(255, 255, 255, 0.1); }
.audio-track.active {
  background: #4084ff;
  border-color: #4084ff;
  color: #fff;
}
.audio-track .lang {
  opacity: 0.7;
  margin-left: 4px;
  font-size: 0.85em;
}
```

- [ ] **Step 2: Manually verify (with fixture from Task 21)**

```bash
uvicorn app.main:app --reload --port 8000
```

Создать MediaItem с multi_audio.mkv (вручную в БД), открыть `/media/{id}` → должен появиться селектор с двумя кнопками «Дубляж (rus)» и «Original (eng)». Клик переключает аудио.

- [ ] **Step 3: Commit**

```bash
git add static/style.css
git commit -m "feat(player): style audio track selector"
```

---

## Phase 12 — Library Toolbar, Filter, Sort, Search

### Task 23: Backend — query params + watch_status computation

**Files:**
- Modify: `app/library/routes.py::library_page`
- Modify: `tests/integration/test_library.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/integration/test_library.py`:

```python
from app.auth.passwords import hash_password
from app.models import MediaItem, User, Genre, WatchProgress


def _make_user(db_factory, name="alice"):
    with db_factory() as s:
        u = User(username=name, password_hash=hash_password("correct-password-12"),
                 must_change_password=False)
        s.add(u); s.commit(); s.refresh(u)
        return u.id


def _login(client, csrf_for, name="alice"):
    r = client.post("/login", data={
        "username": name, "password": "correct-password-12", "csrf_token": csrf_for(None),
    })
    return r.cookies.get("session")


def _seed_library(db_factory):
    with db_factory() as s:
        g1 = Genre(name="Драма"); g2 = Genre(name="Боевик"); g3 = Genre(name="Комедия")
        s.add_all([g1, g2, g3]); s.flush()
        items = [
            MediaItem(torrent_hash="t1", title="Inception", file_path="/x/1.mkv",
                      size_bytes=1, year=2010, kind="movie", duration_seconds=8880,
                      description="Sci-fi about dreams"),
            MediaItem(torrent_hash="t2", title="Breaking Bad", file_path="/x/2.mkv",
                      size_bytes=2, year=2008, kind="series", duration_seconds=2700,
                      description="A chemistry teacher"),
            MediaItem(torrent_hash="t3", title="Inglourious Basterds", file_path="/x/3.mkv",
                      size_bytes=3, year=2009, kind="movie", duration_seconds=9300,
                      description="WW2 movie by Tarantino"),
        ]
        s.add_all(items); s.flush()
        items[0].genres.extend([g1, g2])  # Drama + Action
        items[1].genres.append(g1)         # Drama
        items[2].genres.extend([g1, g2])
        s.commit()
        return [i.id for i in items]


def test_library_search_by_title_substring(client, db_factory, csrf_for):
    _make_user(db_factory)
    ids = _seed_library(db_factory)
    cookie = _login(client, csrf_for)
    r = client.get("/library?q=incepti", cookies={"session": cookie})
    assert r.status_code == 200
    assert "Inception" in r.text
    assert "Breaking Bad" not in r.text


def test_library_search_by_description(client, db_factory, csrf_for):
    _make_user(db_factory)
    _seed_library(db_factory)
    cookie = _login(client, csrf_for)
    r = client.get("/library?q=chemistry", cookies={"session": cookie})
    assert "Breaking Bad" in r.text
    assert "Inception" not in r.text


def test_library_filter_by_kind(client, db_factory, csrf_for):
    _make_user(db_factory)
    _seed_library(db_factory)
    cookie = _login(client, csrf_for)
    r = client.get("/library?kind=series", cookies={"session": cookie})
    assert "Breaking Bad" in r.text
    assert "Inception" not in r.text


def test_library_filter_by_genre(client, db_factory, csrf_for):
    _make_user(db_factory)
    _seed_library(db_factory)
    cookie = _login(client, csrf_for)
    r = client.get("/library?genre=Боевик", cookies={"session": cookie})
    assert "Inception" in r.text
    assert "Breaking Bad" not in r.text


def test_library_sort_by_year_desc(client, db_factory, csrf_for):
    _make_user(db_factory)
    _seed_library(db_factory)
    cookie = _login(client, csrf_for)
    r = client.get("/library?sort=year_desc", cookies={"session": cookie})
    # Inception (2010) должен быть выше Breaking Bad (2008)
    pos_inc = r.text.find("Inception")
    pos_bb = r.text.find("Breaking Bad")
    assert 0 <= pos_inc < pos_bb


def test_library_status_watched_filter(client, db_factory, csrf_for):
    uid = _make_user(db_factory)
    ids = _seed_library(db_factory)
    with db_factory() as s:
        # Inception: position 100 of 8880 → in_progress
        s.add(WatchProgress(user_id=uid, media_id=ids[0], position_seconds=100))
        # Breaking Bad: position 2000 of 2700 → 74% → watched
        s.add(WatchProgress(user_id=uid, media_id=ids[1], position_seconds=2000))
        s.commit()

    cookie = _login(client, csrf_for)
    r = client.get("/library?status=watched", cookies={"session": cookie})
    assert "Breaking Bad" in r.text
    assert "Inception" not in r.text

    r = client.get("/library?status=in_progress", cookies={"session": cookie})
    assert "Inception" in r.text
    assert "Breaking Bad" not in r.text

    r = client.get("/library?status=not_started", cookies={"session": cookie})
    assert "Inglourious Basterds" in r.text


def test_library_htmx_returns_partial_grid_only(client, db_factory, csrf_for):
    _make_user(db_factory)
    _seed_library(db_factory)
    cookie = _login(client, csrf_for)
    r = client.get("/library?q=incepti", cookies={"session": cookie},
                   headers={"HX-Request": "true"})
    assert r.status_code == 200
    # Партиал не должен содержать <html> или toolbar
    assert "<html" not in r.text.lower()
    assert "library-toolbar" not in r.text
```

- [ ] **Step 2: Run tests to verify fail**

```
pytest tests/integration/test_library.py -v
```
Expected: FAIL.

- [ ] **Step 3: Update `app/library/routes.py::library_page`**

Replace function:

```python
from sqlalchemy import and_, or_

WATCHED_RATIO = 0.65


def _compute_status(progress: WatchProgress | None, duration: int | None) -> str:
    if progress is None or progress.position_seconds <= 0:
        return "not_started"
    if duration is None:
        return "in_progress"
    if progress.position_seconds >= WATCHED_RATIO * duration:
        return "watched"
    return "in_progress"


@router.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    q: str | None = None,
    kind: str | None = None,
    genre: str | None = None,
    sort: str = "new",
    status: str | None = None,
):
    from app.models import Genre

    stmt = select(MediaItem)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(MediaItem.title.ilike(like),
                              MediaItem.description.ilike(like)))
    if kind:
        stmt = stmt.where(MediaItem.kind == kind)
    if genre:
        stmt = stmt.where(MediaItem.genres.any(Genre.name == genre))

    # Сортировка
    if sort == "old":
        stmt = stmt.order_by(MediaItem.added_at.asc())
    elif sort == "title_asc":
        stmt = stmt.order_by(MediaItem.title.asc())
    elif sort == "year_desc":
        stmt = stmt.order_by(MediaItem.year.desc().nullslast(), MediaItem.title.asc())
    elif sort == "year_asc":
        stmt = stmt.order_by(MediaItem.year.asc().nullsfirst(), MediaItem.title.asc())
    else:  # "new" default
        stmt = stmt.order_by(MediaItem.added_at.desc())

    items = db.scalars(stmt).unique().all()

    # Прогресс юзера для всех item'ов
    progresses = {
        wp.media_id: wp
        for wp in db.scalars(
            select(WatchProgress).where(WatchProgress.user_id == user.id)
        )
    }

    # Аннотируем статусом и фильтруем по нему (если задан)
    annotated = []
    for it in items:
        wp = progresses.get(it.id)
        st = _compute_status(wp, it.duration_seconds)
        if status and st != status:
            continue
        annotated.append({"item": it, "status": st,
                          "position": wp.position_seconds if wp else 0})

    # Все жанры для select'а в toolbar
    all_genres = [g.name for g in db.scalars(select(Genre).order_by(Genre.name))]

    template = "_library_grid.html" if request.headers.get("HX-Request") else "library.html"
    return render(request, template, {
        "user": user,
        "items": annotated,
        "filters": {"q": q or "", "kind": kind or "", "genre": genre or "",
                    "sort": sort, "status": status or ""},
        "all_genres": all_genres,
    })
```

- [ ] **Step 4: Run tests**

```
pytest tests/integration/test_library.py -v
```
Expected: FAIL (templates `library.html` и `_library_grid.html` не обновлены ещё — фиксим в Task 24).

Если тест `test_library_search_by_title_substring` падает на parse template — ОК, проигнорировать. Будет зелёный после Task 24.

- [ ] **Step 5: Commit**

```bash
git add app/library/routes.py tests/integration/test_library.py
git commit -m "feat(library): query params (q, kind, genre, sort, status), status filtering, htmx partial detection"
```

---

### Task 24: Library template + partial + toolbar + cards

**Files:**
- Create: `templates/_library_grid.html`
- Modify: `templates/library.html`
- Modify: `static/style.css`

- [ ] **Step 1: Create the grid partial**

Create `templates/_library_grid.html`:

```html
{% if not items %}
  <section class="empty-state">
    <p class="eyebrow">Пусто</p>
    <h2>Ничего не найдено</h2>
    <p>Попробуйте сменить фильтры или добавить новый magnet.</p>
    <a class="button primary" href="/add-torrent">Добавить magnet</a>
  </section>
{% else %}
<div class="media-grid">
  {% for entry in items %}
    {% set it = entry.item %}
    <article class="media-card">
      <a class="media-poster {% if not it.poster_url %}placeholder{% endif %}"
         href="/media/{{ it.id }}" aria-label="Открыть {{ it.title }}">
        {% if it.poster_url %}
          <img src="{{ it.poster_url }}" alt="" loading="lazy">
        {% else %}
          <span>{{ it.title[:2] | upper }}</span>
        {% endif %}
        {% if it.kind %}
          <span class="media-card-badge kind">{{ {
            'movie':'Фильм','series':'Сериал','cartoon':'Мультфильм',
            'anime':'Аниме','documentary':'Док','show':'Шоу','other':'Другое'
          }[it.kind] }}</span>
        {% endif %}
        {% if entry.status == 'watched' %}
          <span class="media-card-badge watched" title="Досмотрено">✓</span>
        {% endif %}
        {% if entry.status == 'in_progress' and it.duration_seconds %}
          {% set pct = (entry.position / it.duration_seconds * 100) | round(0) %}
          <div class="media-card-progress"><div style="width: {{ pct }}%"></div></div>
        {% endif %}
      </a>
      <div class="media-card-body">
        <h3><a href="/media/{{ it.id }}">{{ it.title }}</a></h3>
        <p class="media-card-meta">
          {% if it.year %}{{ it.year }}{% endif %}
          {% if it.duration_seconds %}
            {% if it.year %} · {% endif %}
            {% set total_min = (it.duration_seconds / 60) | int %}
            {% if total_min >= 60 %}{{ (total_min // 60) }}ч {{ (total_min % 60) }}мин
            {% else %}{{ total_min }}мин{% endif %}
          {% endif %}
        </p>
        <div class="card-actions">
          <a class="button secondary compact" href="/media/{{ it.id }}">Смотреть</a>
          <a class="button ghost compact" href="/api/download/{{ it.id }}">Скачать</a>
        </div>
      </div>
    </article>
  {% endfor %}
</div>
{% endif %}
```

- [ ] **Step 2: Update `templates/library.html`**

Replace full content:

```html
{% extends "base.html" %}
{% block title %}Библиотека{% endblock %}
{% block content %}
<section class="hero-panel">
  <div>
    <p class="eyebrow">Медиатека</p>
    <h1>Библиотека</h1>
    <p class="lead">Все загруженные фильмы и видео доступны здесь.</p>
  </div>
  <div class="hero-actions">
    <a class="button primary" href="/add-torrent">Добавить magnet</a>
    <a class="button secondary" href="/downloads">Загрузки</a>
  </div>
</section>

<form class="library-toolbar"
      hx-get="/library"
      hx-trigger="change, keyup changed delay:300ms from:#q-input"
      hx-target=".media-grid-wrap"
      hx-push-url="true">
  <input id="q-input" type="search" name="q" placeholder="Поиск…" value="{{ filters.q }}">
  <select name="kind">
    <option value="">Все типы</option>
    <option value="movie" {% if filters.kind == 'movie' %}selected{% endif %}>Фильмы</option>
    <option value="series" {% if filters.kind == 'series' %}selected{% endif %}>Сериалы</option>
    <option value="cartoon" {% if filters.kind == 'cartoon' %}selected{% endif %}>Мультфильмы</option>
    <option value="anime" {% if filters.kind == 'anime' %}selected{% endif %}>Аниме</option>
    <option value="documentary" {% if filters.kind == 'documentary' %}selected{% endif %}>Документальное</option>
    <option value="show" {% if filters.kind == 'show' %}selected{% endif %}>Шоу</option>
    <option value="other" {% if filters.kind == 'other' %}selected{% endif %}>Другое</option>
  </select>
  <select name="genre">
    <option value="">Все жанры</option>
    {% for g in all_genres %}
      <option value="{{ g }}" {% if filters.genre == g %}selected{% endif %}>{{ g }}</option>
    {% endfor %}
  </select>
  <select name="sort">
    <option value="new" {% if filters.sort == 'new' %}selected{% endif %}>Новые сначала</option>
    <option value="old" {% if filters.sort == 'old' %}selected{% endif %}>Старые сначала</option>
    <option value="title_asc" {% if filters.sort == 'title_asc' %}selected{% endif %}>По названию A→Я</option>
    <option value="year_desc" {% if filters.sort == 'year_desc' %}selected{% endif %}>Год ↓</option>
    <option value="year_asc" {% if filters.sort == 'year_asc' %}selected{% endif %}>Год ↑</option>
  </select>
  <select name="status">
    <option value="">Все статусы</option>
    <option value="not_started" {% if filters.status == 'not_started' %}selected{% endif %}>Не начато</option>
    <option value="in_progress" {% if filters.status == 'in_progress' %}selected{% endif %}>В процессе</option>
    <option value="watched" {% if filters.status == 'watched' %}selected{% endif %}>Досмотрено</option>
  </select>
</form>

<div class="media-grid-wrap">
  {% include "_library_grid.html" %}
</div>
{% endblock %}
```

- [ ] **Step 3: Update CSS — toolbar + cards**

Append to `static/style.css`:

```css
.library-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 16px 0;
  padding: 12px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
}
.library-toolbar input[type="search"] {
  flex: 1 1 200px;
  padding: 6px 12px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: inherit;
  border-radius: 6px;
}
.library-toolbar select {
  padding: 6px 10px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: inherit;
  border-radius: 6px;
}
.media-card { position: relative; }
.media-poster {
  position: relative;
  display: block;
  aspect-ratio: 2 / 3;
  overflow: hidden;
}
.media-poster img {
  width: 100%; height: 100%;
  object-fit: cover;
  display: block;
}
.media-card-badge {
  position: absolute;
  padding: 2px 8px;
  font-size: 0.78rem;
  background: rgba(0, 0, 0, 0.72);
  color: #fff;
  border-radius: 4px;
}
.media-card-badge.kind { top: 8px; left: 8px; }
.media-card-badge.watched {
  top: 8px; right: 8px;
  width: 26px; height: 26px;
  display: flex; align-items: center; justify-content: center;
  background: rgba(34, 200, 88, 0.85);
  border-radius: 50%;
  padding: 0;
}
.media-card-progress {
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 3px;
  background: rgba(0, 0, 0, 0.5);
}
.media-card-progress > div {
  height: 100%;
  background: #4084ff;
}
.media-card-meta {
  font-size: 0.85rem;
  opacity: 0.7;
  margin: 4px 0 8px;
}
```

- [ ] **Step 4: Run library tests**

```
pytest tests/integration/test_library.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/library.html templates/_library_grid.html static/style.css
git commit -m "feat(library): toolbar with filter/sort/search; poster cards with progress and badges"
```

---

## Phase 13 — Media Page Header + Edit

### Task 25: Media page header with metadata

**Files:**
- Modify: `templates/media.html`
- Modify: `static/style.css`

- [ ] **Step 1: Add metadata header to `templates/media.html`**

Replace the top section (the `section.section-head` + `section.media-title` block) with:

```html
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
      {{ {
        'movie':'Фильм','series':'Сериал','cartoon':'Мультфильм',
        'anime':'Аниме','documentary':'Документальное','show':'Шоу','other':''
      }.get(item.kind, '') }}
      {% if item.year %} · {{ item.year }}{% endif %}
      {% if item.duration_seconds %}
        {% set total_min = (item.duration_seconds / 60) | int %}
        · {% if total_min >= 60 %}{{ total_min // 60 }}ч {{ total_min % 60 }}мин
          {% else %}{{ total_min }}мин{% endif %}
      {% endif %}
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
              hx-target="#modal-root"
              hx-swap="innerHTML">Исправить совпадение</button>
      <button class="button ghost compact" type="button"
              hx-get="/api/media/{{ item.id }}/edit-form"
              hx-target="#modal-root"
              hx-swap="innerHTML">Редактировать</button>
    </div>
  </div>
</section>

<div id="modal-root"></div>
```

(Старый блок `section.section-head.media-title` уберём целиком.)

- [ ] **Step 2: Append CSS for header**

Append to `static/style.css`:

```css
.media-header {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 24px;
  margin: 24px 0;
}
.media-header-poster img,
.media-header-poster .poster-placeholder {
  width: 100%;
  aspect-ratio: 2/3;
  object-fit: cover;
  border-radius: 8px;
  display: block;
}
.media-header-poster .poster-placeholder {
  background: rgba(255, 255, 255, 0.06);
  display: flex; align-items: center; justify-content: center;
  font-size: 3rem;
  font-weight: bold;
}
.media-description {
  margin: 12px 0;
  line-height: 1.5;
}
.media-header-actions {
  display: flex; flex-wrap: wrap; gap: 8px;
  margin-top: 16px;
}
.media-source-badge {
  padding: 4px 10px;
  font-size: 0.85rem;
  background: rgba(64, 132, 255, 0.18);
  border-radius: 12px;
}
@media (max-width: 640px) {
  .media-header { grid-template-columns: 1fr; }
  .media-header-poster { max-width: 60%; margin: 0 auto; }
}
```

- [ ] **Step 3: Manually verify**

```bash
uvicorn app.main:app --reload --port 8000
```

Открыть `/media/{id}` существующего фильма с заполненными metadata.

- [ ] **Step 4: Commit**

```bash
git add templates/media.html static/style.css
git commit -m "feat(media): header with poster, metadata, edit/rematch buttons"
```

---

### Task 26: Edit endpoint + form (modal)

**Files:**
- Modify: `app/library/routes.py`
- Create: `templates/_media_edit_modal.html`
- Test: `tests/integration/test_media_edit.py`

- [ ] **Step 1: Write failing tests**

Create `tests/integration/test_media_edit.py`:

```python
from pathlib import Path
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.models import MediaItem, User, Genre


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


def _create_media(db_factory) -> int:
    with db_factory() as s:
        m = MediaItem(torrent_hash="t1", title="Old title", file_path=str(SAMPLE),
                      size_bytes=1, kind="movie")
        s.add(m); s.commit(); s.refresh(m)
        return m.id


def test_edit_form_returns_modal(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.get(f"/api/media/{mid}/edit-form", cookies={"session": cookie})
    assert r.status_code == 200
    assert 'name="title"' in r.text
    assert 'name="description"' in r.text


def test_edit_updates_fields(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.post(
        f"/api/media/{mid}/edit",
        data={"title": "New", "description": "X", "kind": "movie",
              "genres": "Драма,Боевик",
              "poster_url": "https://x/p.jpg",
              "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 200 or r.status_code == 204
    # HX-Redirect header
    assert r.headers.get("HX-Redirect") == f"/media/{mid}"

    with db_factory() as s:
        m = s.get(MediaItem, mid)
        assert m.title == "New"
        assert m.description == "X"
        assert m.match_status == "manual"
        assert m.match_source == "manual"
        names = {g.name for g in m.genres}
        assert names == {"Драма", "Боевик"}
        assert m.poster_url == "https://x/p.jpg"


def test_edit_creates_new_genres(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    client.post(
        f"/api/media/{mid}/edit",
        data={"title": "T", "description": "", "kind": "movie",
              "genres": "Новый жанр",
              "poster_url": "",
              "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    with db_factory() as s:
        g = s.scalars(select(Genre).where(Genre.name == "Новый жанр")).one()
        assert g is not None


def test_edit_requires_csrf(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.post(
        f"/api/media/{mid}/edit",
        data={"title": "X", "description": "", "kind": "movie", "genres": "",
              "poster_url": "", "csrf_token": "bad-token"},
        cookies={"session": cookie},
    )
    assert r.status_code == 403
```

- [ ] **Step 2: Run tests — fail**

```
pytest tests/integration/test_media_edit.py -v
```
Expected: FAIL.

- [ ] **Step 3: Add edit endpoints to `app/library/routes.py`**

Append to the file:

```python
from fastapi import Form
from fastapi.responses import Response as FastResponse


@router.get("/api/media/{media_id}/edit-form", response_class=HTMLResponse)
def edit_form(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)
    from app.models import Genre
    all_genres = [g.name for g in db.scalars(select(Genre).order_by(Genre.name))]
    return render(request, "_media_edit_modal.html", {
        "user": user, "item": item, "all_genres": all_genres,
    })


@router.post("/api/media/{media_id}/edit")
def edit_media(
    media_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    title: Annotated[str, Form()],
    description: Annotated[str, Form()],
    kind: Annotated[str, Form()],
    genres: Annotated[str, Form()],
    poster_url: Annotated[str, Form()],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    from app.models import Genre

    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    item.title = title.strip() or item.title
    item.description = description.strip() or None
    if kind in {"movie", "series", "cartoon", "anime", "documentary", "show", "other"}:
        item.kind = kind
    item.poster_url = poster_url.strip() or None

    # genres: CSV
    new_names = [g.strip() for g in genres.split(",") if g.strip()]
    item.genres.clear()
    for name in new_names:
        existing = db.scalars(select(Genre).where(Genre.name == name)).first()
        if existing is None:
            existing = Genre(name=name)
            db.add(existing); db.flush()
        item.genres.append(existing)

    item.match_status = "manual"
    item.match_source = "manual"
    db.commit()

    return FastResponse(status_code=204, headers={"HX-Redirect": f"/media/{media_id}"})
```

- [ ] **Step 4: Create `templates/_media_edit_modal.html`**

```html
<div class="modal-backdrop" onclick="if(event.target===this) this.remove()">
  <div class="modal">
    <h2>Редактировать</h2>
    <form method="post" action="/api/media/{{ item.id }}/edit" hx-post="/api/media/{{ item.id }}/edit">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">

      <label>Название
        <input type="text" name="title" value="{{ item.title }}" required>
      </label>

      <label>Описание
        <textarea name="description" rows="4">{{ item.description or '' }}</textarea>
      </label>

      <label>Тип
        <select name="kind">
          <option value="movie" {% if item.kind == 'movie' %}selected{% endif %}>Фильм</option>
          <option value="series" {% if item.kind == 'series' %}selected{% endif %}>Сериал</option>
          <option value="cartoon" {% if item.kind == 'cartoon' %}selected{% endif %}>Мультфильм</option>
          <option value="anime" {% if item.kind == 'anime' %}selected{% endif %}>Аниме</option>
          <option value="documentary" {% if item.kind == 'documentary' %}selected{% endif %}>Документальное</option>
          <option value="show" {% if item.kind == 'show' %}selected{% endif %}>Шоу</option>
          <option value="other" {% if item.kind == 'other' %}selected{% endif %}>Другое</option>
        </select>
      </label>

      <label>Жанры (через запятую)
        <input type="text" name="genres"
               value="{{ item.genres | map(attribute='name') | join(', ') }}"
               list="all-genres-datalist"
               placeholder="Боевик, Драма">
        <datalist id="all-genres-datalist">
          {% for g in all_genres %}<option value="{{ g }}">{% endfor %}
        </datalist>
      </label>

      <label>URL постера
        <input type="url" name="poster_url" value="{{ item.poster_url or '' }}"
               placeholder="https://example.com/poster.jpg">
      </label>

      <div class="modal-actions">
        <button type="button" class="button ghost"
                onclick="document.querySelector('.modal-backdrop').remove()">Отмена</button>
        <button type="submit" class="button primary">Сохранить</button>
      </div>
    </form>
  </div>
</div>
```

- [ ] **Step 5: Add CSS for modal**

Append to `static/style.css`:

```css
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.modal {
  background: #1e2230;
  padding: 24px;
  border-radius: 8px;
  min-width: 360px;
  max-width: 560px;
  width: 100%;
  max-height: 90vh;
  overflow: auto;
}
.modal h2 { margin-top: 0; }
.modal label {
  display: block;
  margin: 12px 0;
  font-size: 0.9rem;
  color: rgba(255, 255, 255, 0.8);
}
.modal label input,
.modal label textarea,
.modal label select {
  display: block;
  width: 100%;
  margin-top: 4px;
  padding: 8px;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: inherit;
  border-radius: 4px;
  font-size: 0.95rem;
}
.modal-actions {
  display: flex; justify-content: flex-end; gap: 8px;
  margin-top: 16px;
}
```

- [ ] **Step 6: Run tests**

```
pytest tests/integration/test_media_edit.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/library/routes.py templates/_media_edit_modal.html static/style.css tests/integration/test_media_edit.py
git commit -m "feat(media): edit modal — change title/description/kind/genres/poster_url"
```

---

## Phase 14 — Re-Match Dialog

### Task 27: Re-match endpoints

**Files:**
- Modify: `app/library/routes.py`
- Test: `tests/integration/test_match_endpoints.py`

- [ ] **Step 1: Write failing tests**

Create `tests/integration/test_match_endpoints.py`:

```python
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from app.auth.passwords import hash_password
from app.models import MediaItem, User, Genre
from app.metadata.types import MetadataMatch


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


def _create_media(db_factory) -> int:
    with db_factory() as s:
        m = MediaItem(torrent_hash="t", title="Inception",
                      file_path=str(SAMPLE), size_bytes=1, kind="movie", year=2010)
        s.add(m); s.commit(); s.refresh(m)
        return m.id


def test_search_form_returns_dialog(client, db_factory, csrf_for):
    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.get(f"/api/media/{mid}/match/search-form", cookies={"session": cookie})
    assert r.status_code == 200
    assert 'name="query"' in r.text


@patch("app.library.routes.get_tmdb_client")
@patch("app.library.routes.get_kinopoisk_client")
def test_search_returns_combined_results(mock_kp_factory, mock_tmdb_factory, client, db_factory, csrf_for):
    mock_tmdb = mock_tmdb_factory.return_value
    mock_tmdb.search.return_value = [
        {"id": 27205, "title": "Inception", "release_date": "2010-07-15",
         "poster_path": "/p.jpg"},
    ]
    mock_tmdb.get_movie.return_value = MetadataMatch(
        source="tmdb", external_id=27205, title="Inception", year=2010,
        kind="movie", description="...", poster_url="https://example/p.jpg",
        genres=[], score=1.0,
    )
    mock_kp_factory.return_value = None  # Kinopoisk выключен

    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.post(
        f"/api/media/{mid}/match/search",
        data={"query": "Inception", "year": "2010", "kind": "movie",
              "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code == 200
    assert "Inception" in r.text
    assert "TMDB" in r.text


@patch("app.library.routes.get_tmdb_client")
@patch("app.library.routes.get_kinopoisk_client")
def test_apply_writes_metadata(mock_kp_factory, mock_tmdb_factory, client, db_factory, csrf_for):
    mock_tmdb = mock_tmdb_factory.return_value
    mock_tmdb.get_movie.return_value = MetadataMatch(
        source="tmdb", external_id=27205, title="Начало", year=2010,
        kind="movie", description="Описание.", poster_url="https://example/p.jpg",
        genres=["Боевик", "Драма"], score=1.0,
    )
    mock_kp_factory.return_value = None

    cookie = _login(client, db_factory, csrf_for)
    mid = _create_media(db_factory)
    r = client.post(
        f"/api/media/{mid}/match/apply",
        data={"source": "tmdb", "external_id": "27205",
              "csrf_token": csrf_for(cookie)},
        cookies={"session": cookie},
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("HX-Redirect") == f"/media/{mid}"
    with db_factory() as s:
        m = s.get(MediaItem, mid)
        assert m.title == "Начало"
        assert m.tmdb_id == 27205
        assert m.match_status == "matched"
        names = {g.name for g in m.genres}
        assert names == {"Боевик", "Драма"}
```

- [ ] **Step 2: Run tests — fail**

```
pytest tests/integration/test_match_endpoints.py -v
```
Expected: FAIL.

- [ ] **Step 3: Add module-level imports + endpoints to `app/library/routes.py`**

**Important:** добавьте импорты на уровне модуля (НЕ внутри функций) — иначе `@patch("app.library.routes.get_tmdb_client")` в тестах не сработает.

В начале файла, рядом с другими `from app.deps import …`, добавить:

```python
from app.deps import get_tmdb_client, get_kinopoisk_client
```

Затем append к файлу:

```python
@router.get("/api/media/{media_id}/match/search-form", response_class=HTMLResponse)
def match_search_form(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)
    return render(request, "_match_dialog.html", {"user": user, "item": item, "results": None})


@router.post("/api/media/{media_id}/match/search", response_class=HTMLResponse)
def match_search(
    media_id: int,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    query: Annotated[str, Form()],
    year: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "",
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    parsed_year = int(year) if year.strip().isdigit() else None
    hint = "tv" if kind == "series" else ("movie" if kind in ("movie","cartoon","anime","documentary","show","other") else None)

    results = []
    tmdb = get_tmdb_client()
    if tmdb is not None:
        raw = tmdb.search(query, year=parsed_year, kind_hint=hint)
        for r in raw[:10]:
            results.append({
                "source": "tmdb",
                "external_id": r.get("id"),
                "title": r.get("title") or r.get("name") or "(?)",
                "year": (r.get("release_date") or r.get("first_air_date") or "")[:4] or None,
                "poster": (("https://image.tmdb.org/t/p/w92" + r["poster_path"])
                           if r.get("poster_path") else None),
                "kind": "tv" if r.get("media_type") == "tv" or hint == "tv" else "movie",
            })

    kp = get_kinopoisk_client()
    if kp is not None and kp.quota_ok():
        raw = kp.search(query, year=parsed_year)
        for r in raw[:10]:
            results.append({
                "source": "kinopoisk",
                "external_id": r.get("filmId") or r.get("kinopoiskId"),
                "title": r.get("nameRu") or r.get("nameOriginal") or "(?)",
                "year": str(r.get("year")) if r.get("year") else None,
                "poster": r.get("posterUrlPreview") or r.get("posterUrl"),
                "kind": r.get("type", "FILM"),
            })

    return render(request, "_match_dialog.html", {
        "user": user, "item": item, "results": results, "query": query,
    })


@router.post("/api/media/{media_id}/match/apply")
def match_apply(
    media_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    source: Annotated[str, Form()],
    external_id: Annotated[int, Form()],
    _csrf: Annotated[None, Depends(verify_csrf)] = None,
):
    from app.models import Genre

    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404)

    match = None
    if source == "tmdb":
        client = get_tmdb_client()
        if client is None:
            raise HTTPException(status_code=400, detail="TMDB не сконфигурирован")
        # Сначала пробуем movie, затем tv — простой fallback
        match = client.get_movie(external_id) or client.get_tv(external_id)
    elif source == "kinopoisk":
        client = get_kinopoisk_client()
        if client is None:
            raise HTTPException(status_code=400, detail="Kinopoisk не сконфигурирован")
        match = client.get_film(external_id)
    else:
        raise HTTPException(status_code=400, detail="unknown source")

    if match is None:
        raise HTTPException(status_code=502, detail="не удалось получить детали")

    item.title = match.title
    item.description = match.description
    item.poster_url = match.poster_url
    item.year = match.year
    item.kind = match.kind
    if source == "tmdb":
        item.tmdb_id = match.external_id
        item.kinopoisk_id = None
    else:
        item.kinopoisk_id = match.external_id
        item.tmdb_id = None
    item.match_source = source
    item.match_status = "matched"

    # Жанры — заменяем
    item.genres.clear()
    for gname in match.genres:
        normalized = gname.strip()
        if not normalized:
            continue
        existing = db.scalars(select(Genre).where(Genre.name == normalized)).first()
        if existing is None:
            existing = Genre(name=normalized); db.add(existing); db.flush()
        item.genres.append(existing)

    db.commit()
    return FastResponse(status_code=204, headers={"HX-Redirect": f"/media/{media_id}"})
```

- [ ] **Step 4: Create `templates/_match_dialog.html`**

```html
<div class="modal-backdrop" onclick="if(event.target===this) this.remove()">
  <div class="modal">
    <h2>Исправить совпадение</h2>
    <form hx-post="/api/media/{{ item.id }}/match/search"
          hx-target=".modal" hx-swap="outerHTML">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <label>Запрос
        <input type="text" name="query" value="{{ query or item.title }}" required autofocus>
      </label>
      <div style="display: flex; gap: 8px;">
        <label style="flex: 1;">Год
          <input type="number" name="year" value="{{ item.year or '' }}" min="1900" max="2100">
        </label>
        <label style="flex: 1;">Тип
          <select name="kind">
            <option value="">Любой</option>
            <option value="movie">Фильм</option>
            <option value="series">Сериал</option>
          </select>
        </label>
      </div>
      <div class="modal-actions">
        <button type="submit" class="button primary">Искать</button>
      </div>
    </form>

    {% if results is not none %}
      <hr>
      <h3>Результаты</h3>
      {% if not results %}
        <p>Ничего не найдено. Попробуйте другой запрос.</p>
      {% else %}
        <div class="match-results">
          {% for r in results %}
            <div class="match-result">
              {% if r.poster %}<img src="{{ r.poster }}" alt="" loading="lazy">{% endif %}
              <div class="match-result-body">
                <h4>{{ r.title }}{% if r.year %} ({{ r.year }}){% endif %}</h4>
                <p class="match-result-source">{{ r.source | upper }}</p>
              </div>
              <form hx-post="/api/media/{{ item.id }}/match/apply">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <input type="hidden" name="source" value="{{ r.source }}">
                <input type="hidden" name="external_id" value="{{ r.external_id }}">
                <button type="submit" class="button secondary compact">Выбрать</button>
              </form>
            </div>
          {% endfor %}
        </div>
      {% endif %}
    {% endif %}

    <div class="modal-actions">
      <button type="button" class="button ghost"
              onclick="document.querySelector('.modal-backdrop').remove()">Закрыть</button>
    </div>
  </div>
</div>
```

- [ ] **Step 5: Add CSS for match results**

Append to `static/style.css`:

```css
.match-results {
  display: flex; flex-direction: column; gap: 12px;
  max-height: 400px; overflow-y: auto;
  margin: 12px 0;
}
.match-result {
  display: grid;
  grid-template-columns: 60px 1fr auto;
  gap: 12px;
  align-items: center;
  padding: 8px;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 6px;
}
.match-result img {
  width: 60px; height: 90px;
  object-fit: cover; border-radius: 4px;
}
.match-result h4 { margin: 0; font-size: 1rem; }
.match-result-source {
  font-size: 0.78rem; opacity: 0.6; margin: 4px 0 0;
}
```

- [ ] **Step 6: Run tests**

```
pytest tests/integration/test_match_endpoints.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/library/routes.py templates/_match_dialog.html static/style.css tests/integration/test_match_endpoints.py
git commit -m "feat(media): re-match dialog — search TMDB+Kinopoisk, apply chosen match"
```

---

## Phase 15 — Cleanup & Final Tests

### Task 28: Full test suite + manual smoke test

**Files:**
- All — verification

- [ ] **Step 1: Run full pytest**

```
pytest -v
```

Expected: all tests pass. Если какие-то старые тесты упали из-за изменений URL — поправить.

- [ ] **Step 2: Run app and smoke-test all flows manually**

```bash
uvicorn app.main:app --reload --port 8000
```

Чек-лист:
- [ ] Логин работает
- [ ] Открыть фильм, поставить на паузу 3+ минуты, продолжить — стрим не падает
- [ ] Закрыть/открыть страницу фильма — продолжает с сохранённого места
- [ ] Если есть TMDB_API_KEY: новый торрент получает постер/описание/жанры
- [ ] Кнопка «Редактировать» открывает модалку, изменения сохраняются
- [ ] Кнопка «Исправить совпадение» работает (если есть TMDB)
- [ ] В библиотеке: поиск по подстроке, фильтры по типу/жанру/статусу, сортировка
- [ ] На карточке: постер (если есть), прогресс-бар (если в процессе), галочка (если досмотрено)
- [ ] Файл с >1 аудиодорожкой: селектор аудио появляется, переключение работает, выбор сохраняется между сессиями

- [ ] **Step 3: Commit any cleanups**

```bash
git add -A
git commit -m "chore: final polish after smoke test"
```

---

## Verification Checklist

После выполнения всего плана:

- [ ] `pytest -v` — все тесты зелёные
- [ ] `alembic upgrade head` — миграция применяется без ошибок
- [ ] `alembic downgrade -1` — откат тоже работает (без drop важных данных)
- [ ] Селектор аудио появляется только для файлов с >1 audio track
- [ ] Прогресс восстанавливается между сессиями (включая audio_track_index)
- [ ] Watchdog не убивает паузу <5 минут
- [ ] При смерти ffmpeg плеер сам восстанавливается (1 попытка через 1 сек, потом 30-сек cooldown)
- [ ] TMDB → Kinopoisk fallback работает (или хотя бы выключается если ключей нет)
- [ ] Фильтр статуса в библиотеке корректно использует порог 65%

---

## Simplifications vs spec (явно отложено)

В рамках этого плана сделаны небольшие упрощения относительно спека — функционал не теряется, но и не блестит. Если потребуется — это материал для follow-up Spec 1.1:

1. **Постер: только URL, без загрузки файла.** Спек §5.5 описывал альтернативу `<input type="file">` с сохранением в `static/posters/uploaded/{id}.{ext}`. В этом плане форма редактирования принимает только URL — пользователь хостит картинку где-то и вставляет ссылку. Загрузка файла добавится в Spec 1.1, если попросят.
2. **Жанры в форме редактирования: CSV-инпут с datalist.** Спек описывал «чипы с крестиком + автокомплит». Реализация через CSV + datalist значительно проще, функционально эквивалентно.
3. **Multi-select по жанрам в библиотеке.** Спек явно сказал «однозначный, расширим если попросят» — оставлено.
4. **Локальное кэширование постеров.** Используем CDN-URL TMDB/Kinopoisk напрямую.
5. **Лимит-счётчик Kinopoisk in-memory.** Сбрасывается при перезапуске процесса. На домашнем сервере с одним пользователем это норма, но на бóльшую нагрузку нужен persistent счётчик (Redis или БД).
