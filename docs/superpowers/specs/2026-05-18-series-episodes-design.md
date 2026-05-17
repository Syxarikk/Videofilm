# Spec 2 — Сериалы с разбиением на эпизоды

**Дата:** 2026-05-18
**Статус:** черновик, ждёт ревью
**Зависит от:** Spec 1 (`2026-05-17-catalog-player-fixes-design.md`) — реализован
**Следующий шаг:** план реализации (writing-plans)

---

## 1. Цели и контекст

В Spec 1 «сериал» — это `MediaItem` с `kind='series'` и одним самым большим видеофайлом из торрента. Если торрент содержит весь сезон сериала, в библиотеке отображается только одна серия (тот файл, что крупнее). Это «нечестно» — пользователь хочет видеть все эпизоды и переключаться между ними.

Spec 2 это исправляет:

1. Сканер распознаёт сериальные торренты и создаёт `Episode`-записи для каждого видеофайла с `SxxExx`-паттерном.
2. Поддерживаются многосезонные торренты (`Breaking.Bad.S01-S05.Complete/`) — один MediaItem-сериал содержит N эпизодов с разными `(season, episode)`.
3. UI: страница сериала с селектором сезона и сеткой эпизодов. Каждый эпизод — своя страница плеера с собственным прогрессом, аудиодорожкой, навигацией.
4. Auto-play следующего эпизода в стиле Netflix.
5. Метаданные эпизодов (название, описание, дата выхода) тянутся из TMDB `/tv/{id}/season/{n}`.

**Не в scope:**
- Mark-as-watched вручную
- Subtitles
- Скачивание отдельного эпизода (торрент целиком — есть)
- Recommendations / sharing

---

## 2. Модель данных

### 2.1 Новая таблица `episodes`

| Поле | Тип | Назначение |
|---|---|---|
| `id` | Integer PK | |
| `series_id` | Integer FK `media_items.id` ON DELETE CASCADE, NOT NULL, indexed | Привязка к карточке сериала (`MediaItem.kind='series'`) |
| `season` | Integer NOT NULL | Номер сезона |
| `episode` | Integer NOT NULL | Номер эпизода в сезоне |
| `title` | String(512) NULL | Название эпизода (из TMDB или из имени файла; NULL → UI рисует «Эпизод N») |
| `description` | Text NULL | Описание эпизода (из TMDB) |
| `file_path` | String(1024) NOT NULL | Путь к видеофайлу |
| `size_bytes` | BigInteger NOT NULL | Размер файла |
| `duration_seconds` | Integer NULL | ffprobe; lazy-fill при открытии страницы эпизода |
| `audio_tracks` | JSON NULL | ffprobe; lazy-fill аналогично |
| `tmdb_episode_id` | Integer NULL | Для re-fetch отдельной серии (редко надо) |
| `air_date` | Date NULL | Дата выхода (из TMDB) |
| `added_at` | DateTime(tz=True) NOT NULL DEFAULT now | |

UNIQUE constraint: `(series_id, season, episode)`. Один S01E05 на сериал.

Индексы:
- `ix_episodes_series_id` на `series_id`
- `ix_episodes_series_season_episode` UNIQUE на `(series_id, season, episode)` (двойная роль: уникальность + сортировка)

### 2.2 Новая таблица `episode_watch_progress`

Отдельная от существующей `watch_progress` (которая остаётся для фильмов).

| Поле | Тип | |
|---|---|---|
| `id` | Integer PK | |
| `user_id` | FK users CASCADE NOT NULL | |
| `episode_id` | FK episodes CASCADE NOT NULL | |
| `position_seconds` | Integer NOT NULL DEFAULT 0 | |
| `audio_track_index` | Integer NULL | |
| `updated_at` | DateTime(tz=True) NOT NULL onupdate=now | |

UNIQUE constraint: `(user_id, episode_id)`.

Почему отдельная таблица, а не nullable `episode_id` в `watch_progress`: тогда нарушается уникальность `(user_id, media_id)` для фильмов и появляются хрупкие CHECK constraints. Отдельная таблица — проще и яснее.

### 2.3 Relationship на `MediaItem`

Добавляем `MediaItem.episodes: list[Episode]` (lazy="selectin") — для удобного доступа к эпизодам сериала из шаблона.

При `kind='series'` поля `duration_seconds` и `audio_tracks` теряют смысл (они «общие» для всего сериала, что бессмысленно). Сканер для сериалов ставит их в NULL. Существующие данные с этих полей (из Spec 1, если торрент был сериальным) можно не чистить — UI просто их не показывает для серий.

---

## 3. Сканер: разбор многоэпизодных торрентов

### 3.1 Детекция «сериального» торрента

В `scan_once()`, расширяя текущую логику:

```python
# Текущая: один файл (самый большой) → один MediaItem
# Новая: если в торренте >=2 видеофайлов с SxxExx → сериал

video_files = [f for f in _all_videos(t.content_path)]  # рекурсивно
episodic = [
    (f, parse_title(f.name))
    for f in video_files
]
episodic = [(f, pt) for f, pt in episodic if pt.season is not None and pt.episode is not None]

if len(episodic) >= 2:
    # Сериал
    series_title = _common_series_title(episodic)  # см. ниже
    parsed_series = ParsedTitle(title=series_title, year=None, season=None, episode=None, kind_hint="tv")
    # Создаём MediaItem с kind='series'
    series_item = MediaItem(
        torrent_hash=t.hash, title=series_title,
        file_path=t.content_path, size_bytes=sum(f.stat().st_size for f, _ in episodic),
        kind='series', duration_seconds=None, audio_tracks=None,
        match_status='pending',
    )
    # TMDB-матч серии (по title)
    match = find_match(parsed_series, tmdb=tmdb, kinopoisk=kinopoisk)
    if match: ... # применяем как раньше

    # Для каждого сезона запрашиваем episode-метаданные TMDB
    season_episodes_meta: dict[int, dict[int, EpisodeMeta]] = {}
    if match and match.source == 'tmdb' and tmdb is not None:
        unique_seasons = {pt.season for _, pt in episodic}
        for s in unique_seasons:
            season_data = tmdb.get_tv_season(match.external_id, s)  # {episode_number → meta}
            season_episodes_meta[s] = season_data

    # Создаём Episode-записи
    for f, pt in episodic:
        meta = season_episodes_meta.get(pt.season, {}).get(pt.episode)
        ep = Episode(
            series_id=series_item.id,
            season=pt.season, episode=pt.episode,
            title=(meta.name if meta else None),
            description=(meta.overview if meta else None),
            file_path=str(f),
            size_bytes=f.stat().st_size,
            tmdb_episode_id=(meta.id if meta else None),
            air_date=(meta.air_date if meta else None),
        )
        # duration_seconds и audio_tracks — лениво при открытии эпизода
        session.add(ep)
else:
    # Старая логика: один фильм/серия → один MediaItem
    ...
```

### 3.2 Общий префикс названий

`_common_series_title(episodic)`:
- Берём `ParsedTitle.title` от первого файла
- Если у всех файлов `pt.title` совпадает (после нормализации) — возвращаем его
- Иначе — берём имя директории торрента или хеш как fallback

Пример:
- `Breaking.Bad.S01E01.mkv`, `Breaking.Bad.S01E02.mkv` → парсер вернёт title="Breaking Bad" для обоих → берём «Breaking Bad»

### 3.3 TMDB `get_tv_season`

Расширяем `TmdbClient`:

```python
@dataclass(frozen=True, slots=True)
class TmdbEpisodeMeta:
    id: int                  # tmdb_episode_id
    episode_number: int
    name: str | None
    overview: str | None
    air_date: str | None    # ISO "YYYY-MM-DD"

def get_tv_season(self, tv_id: int, season_number: int) -> dict[int, TmdbEpisodeMeta]:
    """Возвращает {episode_number: meta} для одного сезона. {} при ошибке."""
    try:
        r = self._client.get(f"/tv/{tv_id}/season/{season_number}", params={"language": "ru-RU"})
        r.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        log.warning("TMDB get_tv_season(%d, %d) failed: %s", tv_id, season_number, e)
        return {}
    d = r.json()
    episodes = d.get("episodes") or []
    result = {}
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

Один HTTP-вызов на сезон. Если у пользователя нет TMDB ключа — пропускаем (эпизоды создаются без title/description).

### 3.4 Расширение `title_parser` (опционально, если потребуется)

Текущий парсер понимает только `SxxExx`. Дополнительные форматы `1x01`, `Season.1.Episode.5` — пока за пределами scope (можно расширить позже).

---

## 4. UI

### 4.1 Карточка сериала в `_library_grid.html`

В существующей сетке, для `kind='series'`:

- Постер как сейчас
- Бейдж «Сериал»
- Под названием: `{количество сезонов} С · {количество эпизодов} Э` вместо длительности
- Прогресс-бар: процент **просмотренных эпизодов** = `count(watched_episodes) / count(all_episodes) * 100`
- ✓ Галочка если все эпизоды досмотрены

Расчёт делается в роуте `library_page`: для серий аннотируем `episodes_total`, `episodes_watched`. Шаблон отрисовывает.

«Досмотрен» эпизод = `EpisodeWatchProgress.position_seconds >= 0.65 * episode.duration_seconds` (тот же порог, что в Spec 1).

### 4.2 `/media/{series_id}` для сериалов — `media_series.html`

Шаблон выбирается в `media_page`:

```python
if item.kind == 'series':
    return render(request, "media_series.html", {...})
else:
    return render(request, "media.html", {...})  # текущий
```

Содержимое `media_series.html`:

```
[Хедер серии: постер, описание, жанры, кнопки Match/Edit] — как сейчас в media.html

[Селектор сезона: dropdown или кнопки]
  Если сезонов ≤ 5 — кнопки «Сезон 1» «Сезон 2» …
  Иначе — <select>

[Сетка эпизодов сезона]
  Каждая карточка эпизода:
    - № серии
    - Название
    - Длительность (если duration_seconds заполнен)
    - Прогресс-бар (если эпизод начат)
    - ✓ Галочка если досмотрен
    - Клик → /media/{series_id}/s{N}/e{N}
```

Селектор сезона — HTMX: смена сезона → `GET /media/{series_id}?season={N}` отдаёт партиал `_episode_grid.html`. (Или просто пере-рендер с query parameter.)

### 4.3 `/media/{series_id}/s{season}/e{episode}` — `media_episode.html`

Аналог текущего `media.html` для фильма, но:

**Хедер:**
```
{ Постер сериала маленький }
СЕРИАЛ · {series.year} · Жанры
{series.title}
Сезон {N} · Эпизод {M} · {episode.title or 'Эпизод '+M}
{episode.description or ''}
[Источник: TMDB] [Назад к сериалу]
```

**Плашка «Продолжить с MM:SS»** — для эпизода (`EpisodeWatchProgress`).

**Плеер** — мульти-аудио HLS как для фильма, но `/api/stream/episode/{episode_id}/master.m3u8`.

**Селектор аудио** — как для фильма.

**Под плеером — навигация эпизодов:**
```
[← S{N}E{M-1} «Предыдущий»]   [Эпизоды сезона]   [«Следующий» S{N}E{M+1} →]
```

Логика prev/next:
- prev: предыдущий по `(season, episode)`. Если M=1, ищем последний эпизод сезона N-1. Если и его нет — disabled.
- next: следующий. Если M=last_in_season, ищем S{N+1}E1. Если и его нет — disabled.

Запрос на бэке: `SELECT * FROM episodes WHERE series_id=? AND (season, episode) > (S, E) ORDER BY season, episode LIMIT 1`.

**Auto-play оверлей:**

```js
video.addEventListener('timeupdate', () => {
  const remaining = video.duration - video.currentTime;
  if (remaining < 15 && remaining > 5 && nextEpisodeUrl) {
    showAutoplayOverlay(nextEpisodeUrl);
  }
});

function showAutoplayOverlay(url) {
  if (overlayShown) return;
  overlayShown = true;
  // создать DOM: «Следующий: S01E06 — Crazy Handful... [10] [Отмена] [Сразу →]»
  // setInterval отсчитывает 10→0, по 0 → window.location = url
  // [Отмена] → клир интервала, hide overlay, overlayCancelled = true
  // [Сразу] → window.location = url сразу
}
```

Если `nextEpisodeUrl is null` (последний эпизод) — оверлей не показываем.

### 4.4 CSS дополнения

- `.season-tabs` — кнопки сезонов
- `.episode-grid` — сетка эпизодов (grid 3-4 в ряд на desktop)
- `.episode-card` — карточка с прогрессом
- `.episode-nav` — prev/next под плеером
- `.autoplay-overlay` — оверлей в правом нижнем углу, position: fixed

---

## 5. Стриминг и роуты

### 5.1 StreamRegistry — ключ стал гетерогенным

`StreamHandle.media_id: int` → `StreamHandle.target_id: str`. Формат:
- `"m:42"` для фильма (MediaItem id 42)
- `"e:128"` для эпизода (Episode id 128)

Все callsites:
- `get_registry().get("m:42", user_id)` для фильмов
- `get_registry().get("e:128", user_id)` для эпизодов

Хелперы:
```python
def media_key(media_id: int) -> str: return f"m:{media_id}"
def episode_key(episode_id: int) -> str: return f"e:{episode_id}"
```

Все существующие тесты streaming обновляются под `target_id` (substring "m:42" вместо int 42). Это плановое изменение в Phase 4 плана.

### 5.2 Новые роуты в `app/streaming/routes.py`

- `GET /api/stream/episode/{episode_id}/master.m3u8`
- `GET /api/stream/episode/{episode_id}/v{n}/playlist.m3u8`
- `GET /api/stream/episode/{episode_id}/v{n}/seg_NNNNN.ts`

Логика идентична фильмам, но `source = episode.file_path`, `audio_tracks = episode.audio_tracks`, ключ registry = `f"e:{episode_id}"`.

Существующие `/api/stream/{media_id}/...` остаются для фильмов.

### 5.3 Прогресс эпизода

Новый эндпоинт:

- `POST /api/progress/episode` — body `{episode_id, position_seconds, audio_track_index?}` → upsert в `episode_watch_progress`, `touch()` registry-стрим эпизода.

Существующий `/api/progress` остаётся для фильмов.

### 5.4 JS клиента `media_episode.html`

Идентичен `media.html` для фильма, но:
- `MEDIA_ID` → `EPISODE_ID`
- URL streamim: `/api/stream/episode/{EPISODE_ID}/master.m3u8`
- Heartbeat: `POST /api/progress/episode`
- Plus auto-play оверлей (см. 4.3)

---

## 6. Удаление и backfill

### 6.1 Удаление сериала

`POST /api/media/{media_id}/delete` для `kind='series'`:

1. Получить все эпизоды этого сериала.
2. Для каждого эпизода: найти и убить ffmpeg-стримы (ключ registry `f"e:{ep.id}"`), удалить work_dir.
3. qBittorrent: удалить торрент с файлами.
4. `db.delete(item)` — CASCADE снесёт эпизоды и `episode_watch_progress`.

### 6.2 Backfill старых сериалов

После миграции `0004` существующие `MediaItem` с `kind='series'` ссылаются на один файл и не имеют `Episode`-записей.

Триггер backfill: при открытии `/media/{series_id}` если `len(item.episodes) == 0`:

```python
def _backfill_episodes(item: MediaItem, db: Session, tmdb=None) -> int:
    """Сканируем директорию торрента, создаём Episode-записи. Возвращает кол-во."""
    torrent_dir = Path(item.file_path).parent  # или используем qBittorrent client.get_torrent(hash).content_path
    video_files = [...]  # как в сканере
    episodic = [(f, pt) for f, pt in ... if pt.season and pt.episode]
    if len(episodic) < 2:
        return 0  # не сериал на самом деле
    # ... создаём Episode-записи, опционально подтягиваем TMDB
    db.commit()
    return len(episodic)
```

Если backfill вернул 0 — отдаём страницу как для фильма (`media.html`), помечаем `kind='movie'`. Если >0 — отдаём страницу сериала.

---

## 7. Миграция `0004_episodes.py`

```python
"""Episodes for series support

Revision ID: 0004
Revises: 0003
"""
def upgrade():
    op.create_table(
        "episodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("series_id", sa.Integer(),
                  sa.ForeignKey("media_items.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("episode", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_path", sa.String(1024), nullable=False),
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


def downgrade():
    op.drop_index("ix_episode_watch_progress_user_episode", table_name="episode_watch_progress")
    op.drop_table("episode_watch_progress")
    op.drop_index("ix_episodes_series_season_episode", table_name="episodes")
    op.drop_index("ix_episodes_series_id", table_name="episodes")
    op.drop_table("episodes")
```

---

## 8. Тесты

### 8.1 Unit

- `tests/unit/test_episode_grouping.py`:
  - 3 файла `Show.S01E01.mkv`, `Show.S01E02.mkv`, `Show.S02E01.mkv` → детекция как сериал, общий title="Show", episodic=[(s1,e1), (s1,e2), (s2,e1)]
  - 1 файл `Show.S01E01.mkv` → НЕ сериал (только 1 эпизод), создаётся как фильм
  - 2 файла без SxxExx → как фильм (берём самый большой)
  - 2 файла со смешанными именами → общий title — имя директории

- `tests/unit/test_tmdb_season.py`:
  - `get_tv_season(1396, 1)` → словарь `{1: TmdbEpisodeMeta('Pilot'), 2: TmdbEpisodeMeta('Cat\'s'), ...}`
  - 404 → пустой dict
  - Timeout → пустой dict
  - Эпизод без `name` → `meta.name is None`

### 8.2 Integration

- `tests/integration/test_episodes.py`:
  - `scan_once` с qb-фикстурой содержащей `Show.S01E01.mkv`+`Show.S01E02.mkv` (симлинки на sample.mp4) → создан MediaItem `kind='series'` + 2 Episode
  - Multi-season fixture: S01+S02 файлы → один сериал с 4 эпизодами
  - С замоканным TMDB: проверяем что title/description берутся из мока

- `tests/integration/test_episode_player.py`:
  - GET `/media/{series_id}` → шаблон media_series.html, в HTML видна сетка эпизодов
  - GET `/media/{series_id}/s1/e1` → шаблон media_episode.html
  - GET `/api/stream/episode/{ep_id}/master.m3u8` → 200, ffmpeg запускается
  - POST `/api/progress/episode` → upsert в episode_watch_progress
  - GET `/media/{series_id}/s1/e99` → 404
  - prev_episode/next_episode определяются корректно через границы сезонов

- `tests/integration/test_series_delete.py`:
  - Удаление сериала с 5 эпизодами → все эпизоды и progress удалены CASCADE, ffmpeg-стримы убиты

### 8.3 Manual smoke checklist

- [ ] Добавить торрент сериала (2+ эпизода) → в библиотеке появилась карточка серии
- [ ] Открыть страницу сериала → видна сетка эпизодов с названиями TMDB
- [ ] Открыть эпизод → плеер играет, аудиоселектор показывается если >1 дорожки
- [ ] Прогресс эпизода сохраняется → вернуться через 30 сек → плашка «Продолжить»
- [ ] Auto-play оверлей появляется за 15 сек до конца, переходит на следующий
- [ ] Кнопки prev/next работают, корректно переходят на границах сезона
- [ ] Удаление сериала удаляет все эпизоды

---

## 9. Риски

1. **Имена файлов разнородные** — парсер сейчас понимает только `SxxExx`. Файлы типа `Show.1x01.mkv` или `Show.Season.1.Episode.1.mkv` будут проигнорированы (попадут в «не эпизод» категорию). Принимаем как ограничение Spec 2. Расширение парсера — Spec 2.1.
2. **TMDB без перевода эпизодов** — `episode.name` может быть NULL → UI рисует `Эпизод 5`.
3. **Многосезонный backfill** — 5-сезонный сериал с 60 эпизодами → ~5 TMDB-запросов + 60 ffprobe-вызовов. ~3-5 секунд при первом открытии. Допустимо.
4. **StreamRegistry рефакторинг** — изменение ключа касается фильмов тоже. Все тесты streaming должны пройти после рефакторинга.
5. **Auto-play race** — если пользователь скипнул в конец, оверлей покажется и сразу запустит next. Защита: показываем только если `position > duration - 30` И `position < duration - 5`.
6. **Race на одновременный backfill** — два пользователя одновременно открыли страницу сериала, оба запускают backfill, оба создают эпизоды → дубликаты. Защита: UNIQUE на `(series_id, season, episode)` — второй вставит и упадёт на ConflictError, ловим как ожидаемое.

---

## 10. Что НЕ в этом спеце

- Mark-as-watched вручную (по кнопке)
- Skip intro / outro
- Subtitles selection
- Эпизоды как самостоятельные карточки в библиотеке
- Sharing / recommendations
- Парсинг форматов `1x01`, `Season.1.Episode.1` и т.п. (только `SxxExx`)

---

## 11. План реализации (preview)

Полный план — отдельный документ `docs/superpowers/plans/2026-05-18-series-episodes-plan.md` (следующим шагом через writing-plans).

Грубо порядок этапов:

1. Модель `Episode` + `EpisodeWatchProgress` + миграция 0004.
2. TMDB `get_tv_season` + парсер группировки сериального торрента.
3. Сканер: детекция и создание `Episode`-записей с TMDB метаданными.
4. StreamRegistry рефакторинг (ключ → "m:N"/"e:N"), все тесты streaming зелёные.
5. Стриминг роуты для эпизодов + `/api/progress/episode`.
6. Шаблон `media_series.html` со страницей сериала + сеткой эпизодов.
7. Шаблон `media_episode.html` с плеером эпизода + навигацией prev/next.
8. Auto-play оверлей.
9. Backfill старых серий + удаление сериала с CASCADE.
10. Тесты по всему + smoke check.

Грубая оценка: ~30-40 часов.
