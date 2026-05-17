# Spec 1 — Каталог библиотеки, метаданные TMDB/Kinopoisk, выбор озвучки, багфикс плеера

**Дата:** 2026-05-17
**Статус:** черновик, ждёт ревью
**Следующий шаг:** план реализации (см. writing-plans)

---

## 1. Цели и контекст

Текущее состояние проекта (План 2 завершён):

- FastAPI + Jinja2 + HTMX, SQLite/Postgres.
- qBittorrent-интеграция: пользователь добавляет magnet → qBittorrent качает → сканер опрашивает qBittorrent раз в 10 сек, забирает самый большой видеофайл из директории торрента, создаёт `MediaItem` с распарсенным из имени файла названием.
- HLS-стриминг: на каждый `(user, media)` стартует свой `ffmpeg`-процесс, который on-the-fly перекодирует в HLS-сегменты во временной директории. Watchdog убивает idle-стримы.

Что просят/требуется:

1. **Багфикс плеера.** При паузе >60 сек или потере heartbeat — стрим умирает, плеер показывает «Поток упал. Обновите страницу».
2. **Продолжение просмотра** (YouTube-стиль). Прогресс уже сохраняется на бэке (`watch_progress`), но плеер не использует его при открытии. Хочется: при возврате — продолжать с сохранённой позиции. Также — прогресс-бар на карточках в библиотеке и статус «досмотрено».
3. **Автоматические постеры и описания** из TMDB (приоритет) + Kinopoisk (fallback).
4. **Категоризация:** тип (фильм / сериал / мультфильм / аниме / документальное / шоу / другое) и жанры.
5. **Фильтрация и сортировка** в библиотеке.
6. **Поиск по ключевому слову** (по названию и описанию).
7. **Ручное редактирование** названий, описаний, постера, типа, жанров.
8. **Выбор аудиодорожки** в плеере (торрент-MKV обычно имеют несколько озвучек).

**В этот спец НЕ входит:** разделение сериального торрента на отдельные эпизоды с эпизодным плеером — это Spec 2 (отдельный документ).

---

## 2. Багфикс плеера и продолжение просмотра

### 2.1 Heartbeat на паузе

В `templates/media.html` `setInterval` сейчас выходит раньше времени, если видео на паузе. Изменение:

```js
setInterval(() => {
  if (video.ended || isNaN(video.currentTime)) return;
  // паузу больше не пропускаем — иначе watchdog убьёт стрим
  fetch('/api/progress', {...});
}, 15000);
```

Меняется и интервал: 10с → 15с. Этого достаточно (порог watchdog поднимается до 300с, см. ниже).

### 2.2 Поднять `IDLE_THRESHOLD_SECONDS` до 300

В `app/streaming/watchdog.py` константа `IDLE_THRESHOLD_SECONDS` меняется с `60.0` на `300.0`. Этого хватает, чтобы пережить временную потерю heartbeat (свёрнутая вкладка на мобильном, плохая сеть, и т.п.), но стрим всё ещё чистится для реально брошенных сессий через 5 минут.

### 2.3 Авто-восстановление в hls.js

В `templates/media.html` обработчик ошибки:

```js
let recoverAttempts = 0;
hls.on(Hls.Events.ERROR, (e, data) => {
  console.error("HLS error:", data);
  if (!data.fatal) return;
  if (recoverAttempts < 1) {
    recoverAttempts++;
    setTimeout(() => hls.startLoad(), 1000);
    setTimeout(() => { recoverAttempts = 0; }, 30000);
    return;
  }
  showRecoveryBanner();
});
```

`startLoad()` перезагружает плейлист → бэк вызывает `_ensure_stream()` → если процесса нет — стартует новый ffmpeg. Сегменты пересоздаются. hls.js перематывает плеер обратно на сохранённую `video.currentTime`. Если за 30 сек снова падает — показываем баннер «Поток упал, обнови страницу».

### 2.4 Продолжение с сохранённой позиции

Бэкенд `app/library/routes.py::media_page` теперь возвращает в контекст шаблона `saved_position_seconds` (значение из `WatchProgress` для текущего user и media, или 0).

Шаблон `media.html`:

```js
const SAVED_POSITION = {{ saved_position_seconds }};
video.addEventListener('loadedmetadata', () => {
  if (SAVED_POSITION > 0 && SAVED_POSITION < video.duration - 30) {
    video.currentTime = SAVED_POSITION;
  }
});
```

Над плеером — плашка, если `saved_position_seconds > 0` и не «досмотрено»:

```
⏱ Продолжить с 47:30   [Сначала]
```

«Сначала» по клику делает `video.currentTime = 0` и `fetch('/api/progress', {position_seconds: 0})`.

### 2.5 Статус «досмотрено» и прогресс на карточках

Правило: **`position_seconds >= 0.65 * duration_seconds` ⇒ досмотрено**. (Согласовано с пользователем: совпадает с примерами 60/90, 25/40, 15/20 в пределах ±1 мин.)

Три статуса:
- `not_started`: нет `WatchProgress` или `position_seconds == 0`
- `in_progress`: `0 < position_seconds < 0.65 * duration_seconds`
- `watched`: `position_seconds >= 0.65 * duration_seconds`

Для расчёта нужен `duration_seconds` — добавляется в `MediaItem` (см. §3).

### 2.6 Длительность через ffprobe

Новый модуль `app/metadata/ffprobe.py`:

```python
def get_duration_seconds(file_path: str) -> int | None:
    # ffprobe -v error -show_entries format=duration -of csv=p=0 file_path
    # парсим float → round(int). None если ffprobe упал.
```

Вызывается из `scan_once()` для новых файлов. Для существующих (миграция оставляет NULL) — лениво при первом открытии `/media/{id}`: если `duration_seconds is None`, запускаем синхронно и сохраняем.

---

## 3. Модель данных

### 3.1 Изменения в `media_items`

Добавляются колонки (все nullable или с дефолтом, миграция не ломает старые строки):

| Поле | Тип | Назначение |
|---|---|---|
| `duration_seconds` | `Integer` nullable | Длительность файла. Заполняется ffprobe в сканере; для старых — лениво при открытии. |
| `description` | `Text` nullable | Описание (TMDB/Kinopoisk или вручную). |
| `poster_url` | `String(1024)` nullable | CDN-URL TMDB/Kinopoisk или локальный путь `/static/posters/uploaded/{id}.{ext}` если загружен файл. |
| `year` | `Integer` nullable | Год выпуска. |
| `kind` | `String(32)` nullable | Один из: `movie`, `series`, `cartoon`, `anime`, `documentary`, `show`, `other`. Дефолт — `movie`; если в имени файла найден `SxxExx` — сканер ставит `series`. Может перезаписаться TMDB-мапом по жанру (anime/cartoon/documentary). |
| `tmdb_id` | `Integer` nullable | Id в TMDB после успешного матча. |
| `kinopoisk_id` | `Integer` nullable | Id в Kinopoisk если матч был оттуда. |
| `match_status` | `String(16)` NOT NULL default `'pending'` | `pending` / `matched` / `manual` / `failed`. |
| `match_source` | `String(16)` nullable | `tmdb` / `kinopoisk` / `manual` / NULL. |
| `audio_tracks` | `JSON` nullable | Список dict’ов из ffprobe — см. §6.1. |

Поле `title` остаётся как display-имя: после успешного матча или ручной правки — перезаписывается. Оригинал восстановим из `file_path` (там лежит исходный путь).

### 3.2 Новые таблицы

**`genres`** — нормализованный справочник:

```sql
CREATE TABLE genres (
  id INTEGER PRIMARY KEY,
  name VARCHAR(64) UNIQUE NOT NULL
);
```

Имена приходят из TMDB локализованно (`language=ru-RU`): «Боевик», «Драма», «Криминал»… При ручном вводе — дедупликация по `lower(name)`.

**`media_item_genres`** — m2m:

```sql
CREATE TABLE media_item_genres (
  media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
  genre_id INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
  PRIMARY KEY (media_id, genre_id)
);
CREATE INDEX ix_media_item_genres_genre_id ON media_item_genres(genre_id);
```

### 3.3 Изменения в `watch_progress`

Одна новая колонка:

| Поле | Тип | Назначение |
|---|---|---|
| `audio_track_index` | `Integer` nullable | Индекс последней выбранной аудиодорожки. Если NULL — плеер использует дефолтную из master playlist. |

### 3.4 Индексы

- `ix_media_items_kind` на `kind`
- `ix_media_items_year` на `year`
- `ix_media_items_title` на `title` (для `LIKE '%q%'`; на маленькой библиотеке достаточно)

### 3.5 Миграция

Один Alembic-файл `migrations/versions/0003_catalog_metadata_and_audio.py`. Все `ALTER TABLE … ADD COLUMN` + создание двух новых таблиц + индексы. `downgrade()` — обратные операции для безопасного отката.

Backfill миграцией не делаем — все nullable, сканер и lazy-fill дозаполнят.

---

## 4. Метаданные: TMDB и Kinopoisk

### 4.1 Структура нового пакета

```
app/metadata/
  __init__.py
  types.py          # MetadataMatch, AudioTrack — общие dataclass’ы
  tmdb.py           # TmdbClient
  kinopoisk.py      # KinopoiskClient
  matcher.py        # find_match(parsed) — оркестрация TMDB → Kinopoisk
  ffprobe.py        # get_duration_seconds, probe_audio_tracks
```

### 4.2 Общий формат — `MetadataMatch`

```python
@dataclass(frozen=True)
class MetadataMatch:
    source: Literal["tmdb", "kinopoisk"]
    external_id: int
    title: str
    year: int | None
    kind: Literal["movie", "series", "cartoon", "anime", "documentary", "show", "other"]
    description: str | None
    poster_url: str | None
    genres: list[str]
    score: float    # 0.0–1.0
```

### 4.3 TMDB-клиент (приоритетный источник)

- Endpoint: `https://api.themoviedb.org/3`
- Auth: Bearer-токен (TMDB v4 Read Access Token) → env `TMDB_API_KEY`
- Язык: `?language=ru-RU`
- Поиск:
  - Если `parsed.kind_hint == "tv"` → `/search/tv?query=…`
  - Если `parsed.kind_hint == "movie"` → `/search/movie?query=…`
  - Если `parsed.kind_hint is None` → `/search/multi?query=…` (TMDB сам решит тип)
- Детали: `/movie/{id}?language=ru-RU` или `/tv/{id}?language=ru-RU`
- Постеры: `https://image.tmdb.org/t/p/w500` + `poster_path`. Храним готовый URL — не кешируем локально (TMDB CDN стабильный).
- Маппинг `kind` (от TMDB → наш enum):
  - TMDB `movie` → `movie`
  - TMDB `tv` → `series`
  - Если в `genres` есть «Анимация» И тип `movie` → `cartoon`
  - Если в `genres` есть «Анимация» И страна происхождения `JP` (для `tv` или `movie`) → `anime`
  - Если в `genres` есть «Документальный» → `documentary`
  - `show` — НЕ автомапится (TMDB не различает talk-show отдельно); доступен только при ручном выборе в форме редактирования
  - `other` — только при ручном выборе

HTTP-клиент: `httpx.Client` (sync), таймаут 5 сек. Ошибки (401, 5xx, timeout) → логируем warning, возвращаем пустой список.

### 4.4 Kinopoisk-клиент (fallback)

- Endpoint: `https://kinopoiskapiunofficial.tech/api/v2.2`
- Auth: header `X-API-KEY` → env `KINOPOISK_API_KEY`
- Поиск: `/films/search-by-keyword?keyword={title}`
- Детали: `/films/{id}`
- Лимит ~500 запросов/день. In-memory счётчик (`_quota_used`, сбрасывается каждые 24 часа). Если исчерпан — `kinopoisk_quota_ok()` возвращает False, matcher пропускает.
- Постеры: `posterUrl` (Yandex CDN). Храним URL.
- Маппинг: `type == "FILM"` → `movie`, `type == "TV_SERIES"` → `series`, и т. д.

### 4.5 `find_match()` — оркестрация

```python
def find_match(parsed: ParsedTitle) -> MetadataMatch | None:
    if tmdb_enabled():
        results = tmdb.search(parsed.title, year=parsed.year, kind_hint=parsed.kind_hint)
        if results:
            top = results[0]
            if _is_confident(top, parsed):
                return _to_match(top, source="tmdb")
    if kinopoisk_enabled() and kinopoisk_quota_ok():
        results = kinopoisk.search(parsed.title, year=parsed.year)
        if results:
            top = results[0]
            if _is_confident(top, parsed):
                return _to_match(top, source="kinopoisk")
    return None
```

`_is_confident`:
- `difflib.SequenceMatcher(None, normalize(top.title), normalize(parsed.title)).ratio() >= threshold`
- threshold = 0.7 если `parsed.year` есть и совпадает ±1, иначе 0.85.

`tmdb_enabled()`/`kinopoisk_enabled()` = bool на наличие соответствующего API-ключа в конфиге.

### 4.6 Расширение парсера в `ParsedTitle`

Сейчас `app/torrents/title_parser.parse_title()` возвращает строку. Меняется на:

```python
@dataclass(frozen=True)
class ParsedTitle:
    title: str
    year: int | None
    season: int | None       # из SxxExx если есть
    episode: int | None
    kind_hint: Literal["movie", "tv"] | None  # "tv" если season/episode != None

def parse_title(filename: str) -> ParsedTitle: ...
```

Существующие callers (`scanner.scan_once`) подстраиваются на `.title`.

### 4.7 Интеграция в сканер

В `app/torrents/scanner.scan_once()`, после создания `MediaItem`:

1. `duration = get_duration_seconds(video)` → записать в `duration_seconds`.
2. `audio = probe_audio_tracks(video)` → записать в `audio_tracks`.
3. `parsed = parse_title(video.name)`.
4. Дефолтный `kind` = `"series"` если `parsed.kind_hint == "tv"`, иначе `"movie"`.
5. `match = find_match(parsed)`:
   - Если есть → перезаписать `title`, `description`, `poster_url`, `year`, `kind` (из mapping), `tmdb_id`/`kinopoisk_id`, `match_source`; `match_status = 'matched'`. Создать/найти `Genre`-строки и слинковать.
   - Если нет → `match_status = 'failed'`. Title оставляем из parser, kind по эвристике, поля метаданных NULL.

Всё синхронно в том же цикле сканера (httpx sync). Параллелизма нет — один HTTP/scan tick.

### 4.8 Конфиг

В `app/config.py` `Settings` добавляются:

```python
tmdb_api_key: str | None = None
kinopoisk_api_key: str | None = None
```

В `.env.example`:

```
# Опционально — авто-матч с TMDB.
# Получить ключ: themoviedb.org → Settings → API → API Read Access Token (v4)
TMDB_API_KEY=

# Опционально — fallback на Kinopoisk если TMDB не нашёл.
# Получить ключ: kinopoiskapiunofficial.tech (~500 запросов/день)
KINOPOISK_API_KEY=
```

Если оба пусты — авто-матч не работает, всё остальное (фильтры, поиск, ручное редактирование) работает.

---

## 5. UI

Все экраны — Jinja2 + HTMX (нет SPA).

### 5.1 `/library` — toolbar, фильтры, поиск, сортировка

Над сеткой карточек:

```
[ Поиск: ___________ ]   Тип: [Все ▾]   Жанр: [Все ▾]   Сортировка: [Новые ▾]   Статус: [Все ▾]
```

- **Поиск** — `<input>` с `hx-get="/library"` `hx-trigger="keyup changed delay:300ms"` `hx-target=".media-grid"` `hx-include="form"` (форма содержит остальные selects). Подстрока ищется по `title` и `description` (`LIKE '%q%'`, case-insensitive).
- **Тип** (kind): «Все / Фильмы / Сериалы / Мультфильмы / Аниме / Документальное / Шоу / Другое».
- **Жанр**: «Все» + список из `SELECT name FROM genres ORDER BY name`. Однозначный выбор.
- **Сортировка**: «Новые сначала» (по `added_at desc`, дефолт) / «Старые сначала» / «По названию A→Я» / «По году ↓» / «По году ↑».
- **Статус** (по `WatchProgress` текущего user): «Все / Не начато / В процессе / Досмотрено».

Все параметры — query-string. Бэк отдаёт партиал `_library_grid.html` при `HX-Request` заголовке, иначе полную страницу с заполненными значениями selects.

Бэк (`app/library/routes.py::library_page`):

```python
def library_page(request, user, db, q=None, kind=None, genre=None, sort="new", status=None):
    stmt = select(MediaItem).options(selectinload(MediaItem.genres))
    if q:
        stmt = stmt.where(or_(MediaItem.title.ilike(f"%{q}%"),
                              MediaItem.description.ilike(f"%{q}%")))
    if kind:
        stmt = stmt.where(MediaItem.kind == kind)
    if genre:
        stmt = stmt.where(MediaItem.genres.any(Genre.name == genre))
    stmt = stmt.outerjoin(WatchProgress,
                          and_(WatchProgress.media_id == MediaItem.id,
                               WatchProgress.user_id == user.id))
    # сортировка по sort
    # ... фильтр по status (in Python, через computed watched/in_progress)
```

### 5.2 Карточка в библиотеке

В `library.html` (и в партиале `_library_grid.html`):

- Если `it.poster_url` — `<img>` с постером; иначе текстовая заглушка с первыми двумя буквами.
- Под постером — тонкая прогресс-полоса (height: 3px) если `watch_status == 'in_progress'`, width = `(position/duration)*100%`.
- В углу постера — бейдж типа («Фильм» / «Сериал» / …).
- Если `watch_status == 'watched'` — иконка-галочка ✓ в углу постера.
- Под заголовком — мета: `{year} · {duration_hm} · {kind_localized}`.

`duration_hm` — формат `1ч 47мин` или `47мин` для коротких.

### 5.3 `/media/{id}` — страница просмотра

Хедер с постером и метой сбоку:

```
[ ПОСТЕР ]   ФИЛЬМ · 2024 · 1ч 47мин · Драма, Криминал
             Название
             Описание 3-4 строки…
             
             [Источник: TMDB ↗]   [Исправить совпадение]   [Редактировать]
```

Источник — текст-ссылка на TMDB/Kinopoisk страницу. «Исправить совпадение» и «Редактировать» — кнопки, открывают модалки (см. ниже).

Плашка «Продолжить с 47:30 [Сначала]» (если применимо) — над плеером.

Плеер — как раньше, обновлённый JS (см. §2).

Под плеером — селектор аудио (если дорожек >1):

```
🎧 Озвучка:  [ Дубляж (рус) ]  [ English ]  [ Комментарии режиссёра (рус) ]
```

Внизу — `[Скачать оригинал] [Библиотека] [Удалить]`.

### 5.4 Модалка «Исправить совпадение»

HTMX-модалка, открывается на кнопку:

```html
<button hx-get="/api/media/{{ item.id }}/match/search-form"
        hx-target="#modal">Исправить совпадение</button>
<div id="modal"></div>
```

Партиал `_match_dialog.html` содержит форму поиска:

```
Запрос: [Breaking Bad         ]   Год: [2008]   Тип: [Сериал ▾]
                                                          [Искать]

— Результаты —
┌──────────────────────────────────────────────────────────┐
│ [миниатюра] Breaking Bad (2008) · TMDB · Сериал           │
│                                          [Выбрать]         │
└──────────────────────────────────────────────────────────┘
```

Эндпоинты:

- `GET /api/media/{id}/match/search-form` — отдаёт партиал с пустой формой (pre-filled из текущего `title`/`year`/`kind`)
- `POST /api/media/{id}/match/search` — body `{query, year?, kind?}` → запускает TMDB + Kinopoisk поиск, отдаёт партиал со списком 10 топ-результатов
- `POST /api/media/{id}/match/apply` — body `{source, external_id}` → бэк делает `get_detail(external_id)`, перезаписывает `MediaItem` поля, ставит `match_status='matched'`, `match_source=source`. Возврат — `HX-Redirect: /media/{id}` для перезагрузки страницы.

CSRF: токен включается hidden-полем во все формы.

### 5.5 Форма «Редактировать»

Тоже модалка (партиал `_media_edit_modal.html`):

- Название (text)
- Описание (textarea)
- Тип (select)
- Жанры — UI: набор «чипов» текущих жанров с крестиком + `<input>` с автокомплитом из существующих + кнопкой «добавить»
- Постер: либо URL (`<input type="url">`), либо `<input type="file">` (фото сохраняется в `static/posters/uploaded/{id}.{ext}`)
- [Сохранить] [Отмена]

Эндпоинт `POST /api/media/{id}/edit` — multipart-форма. Бэк:

```python
if uploaded_file:
    save_to_static_posters(uploaded_file, item.id)
    item.poster_url = f"/static/posters/uploaded/{item.id}.{ext}"
elif poster_url_field:
    item.poster_url = poster_url_field
item.title = title
item.description = description
item.kind = kind
item.genres = _resolve_genres(genres_list)
item.match_status = "manual"
item.match_source = "manual"
```

После сохранения — `HX-Redirect: /media/{id}`.

### 5.6 Стили — `static/style.css`

Новые секции:
- `.library-toolbar` — панель фильтров (sticky, на телефоне в столбик)
- `.media-card-poster img` — изображение постера + aspect-ratio 2:3
- `.media-card-progress` — прогресс-полоска
- `.media-card-badge` — бейдж типа / галочка
- `.modal-backdrop` + `.modal` — универсальный контейнер для re-match/edit
- `.media-header` — хедер на странице медиа (grid с постером слева)
- `.audio-tracks` + `.audio-track` (active state) — селектор аудио

---

## 6. Выбор аудиодорожки

### 6.1 ffprobe для аудио

В `app/metadata/ffprobe.py`:

```python
@dataclass(frozen=True)
class AudioTrack:
    index: int             # 0-based внутри списка audio-стримов
    codec: str             # "aac", "ac3", "dts", "eac3", "opus", ...
    language: str | None   # ISO-639-2 из тегов: "rus", "eng", "jpn", или None
    title: str | None      # из MKV-тега title
    channels: int          # 2, 6, 8

def probe_audio_tracks(file_path: str) -> list[AudioTrack]:
    # ffprobe -v error -select_streams a \
    #   -show_entries stream=index,codec_name,channels:stream_tags=language,title \
    #   -of json file_path
```

Результат сохраняется в `MediaItem.audio_tracks` как список dict’ов (через `dataclasses.asdict`). Для существующих записей — лениво при первом открытии `/media/{id}`.

### 6.2 ffmpeg: мульти-вариант HLS (всегда через master)

Чтобы не плодить ветки на клиенте и сервере, **всегда** генерим мульти-вариант с master playlist — даже для 1 аудио. Это убирает «развилку» в роутах, JS, и watchdog/cleanup.

В `app/streaming/ffmpeg_runner.py::start_hls()` сигнатура:

```python
@dataclass(frozen=True, slots=True)
class HlsParams:
    source: str
    work_dir: str
    seek_seconds: float
    audio_tracks: list[AudioTrack]    # может быть пуст
```

Логика:

```python
cmd = ["ffmpeg", "-loglevel", "warning", "-nostdin",
       "-ss", seek, "-i", source,
       "-map", "0:v:0"]
for t in audio_tracks:
    cmd += ["-map", f"0:a:{t.index}"]

cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
if audio_tracks:
    cmd += ["-c:a", "aac", "-b:a", "128k"]
cmd += ["-f", "hls",
        "-hls_time", "6", "-hls_list_size", "0",
        "-master_pl_name", "master.m3u8"]

# var_stream_map всегда содержит видео-вариант v0
if audio_tracks:
    var_map_parts = ["v:0,agroup:audio"]
    for i, t in enumerate(audio_tracks):
        name = (t.title or t.language or f"Track {i+1}").replace(",", " ").replace(" ", "_")
        lang = t.language or "und"
        var_map_parts.append(f"a:{i},agroup:audio,language:{lang},name:{name}")
else:
    var_map_parts = ["v:0"]      # видео-only, без агрегации

cmd += ["-var_stream_map", " ".join(var_map_parts),
        "-hls_segment_filename", f"{work_dir}/v%v/seg_%05d.ts",
        f"{work_dir}/v%v/playlist.m3u8"]
```

ffmpeg создаёт:
```
work_dir/
  master.m3u8           ← всегда есть
  v0/playlist.m3u8      ← видео
  v0/seg_*.ts
  v1/playlist.m3u8      ← аудио 0 (если есть)
  v1/seg_*.ts
  v2/...                ← аудио 1 (если есть)
  ...
```

`wait_for_first_segment()` теперь проверяет `v0/seg_*.ts` (а не корневой `seg_*.ts`). Сигнатура без изменений — работа идёт от `work_dir / "v0"`.

Все аудио всегда транскодируются в AAC 128k — это для надёжного воспроизведения hls.js (AC3/DTS hls.js не играет). Видео жмётся ОДИН раз и шарится между всеми вариантами.

### 6.3 Стриминг-роуты

В `app/streaming/routes.py`:

- `GET /api/stream/{media_id}/master.m3u8` — отдаёт master playlist
- `GET /api/stream/{media_id}/playlist.m3u8` — **легаси-алиас**: 301-редирект на `master.m3u8`
- `GET /api/stream/{media_id}/v{n}/playlist.m3u8` — вариант playlist (`n` ∈ `^\d+$`, валидируется регуляркой)
- `GET /api/stream/{media_id}/v{n}/seg_NNNNN.ts` — сегмент из подпапки

Регулярки на `n` и `seg_NNNNN` — строгие, защита от `../`.

Старый flat-эндпоинт `/api/stream/{media_id}/seg_NNNNN.ts` (на уровне корня, без `v{n}`) — удаляется, потому что новых файлов на этом пути больше не будет, а старые клиенты сейчас не существуют (продакшна нет).

### 6.4 Клиент

В `media.html` JS подключается к `master.m3u8` всегда (бэк сам решит, что отдать):

```js
const src = '/api/stream/{{ item.id }}/master.m3u8';
hls.loadSource(src);
hls.attachMedia(video);

hls.on(Hls.Events.MANIFEST_PARSED, () => {
  if (hls.audioTracks && hls.audioTracks.length > 1) {
    renderAudioSelector(hls);
  }
  // Восстановить сохранённый трек:
  const savedIdx = {{ saved_audio_track_index|tojson }};
  if (savedIdx != null && savedIdx < hls.audioTracks.length) {
    hls.audioTrack = savedIdx;
  }
});

function renderAudioSelector(hls) {
  const c = document.getElementById('audio-tracks');
  c.innerHTML = hls.audioTracks.map(t =>
    `<button data-track="${t.id}" class="audio-track${t.id === hls.audioTrack ? ' active' : ''}">${escapeHtml(humanize(t.name))}${t.lang && t.lang !== 'und' ? ` <span class="lang">${escapeHtml(t.lang)}</span>` : ''}</button>`
  ).join('');
  c.onclick = e => {
    const btn = e.target.closest('[data-track]');
    if (!btn) return;
    const id = parseInt(btn.dataset.track);
    hls.audioTrack = id;
    c.querySelectorAll('.audio-track').forEach(b => b.classList.toggle('active', b === btn));
    // сохранить выбор
    fetch('/api/progress', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        media_id: {{ item.id }},
        position_seconds: Math.floor(video.currentTime || 0),
        audio_track_index: id
      })
    });
  };
}
```

`humanize()` — превращает `Russian_Dub` → `Russian Dub`.

### 6.5 API: расширение `/api/progress`

Текущий `_ProgressIn` — `{media_id, position_seconds}`. Расширяется опциональным:

```python
class _ProgressIn(BaseModel):
    media_id: int
    position_seconds: int
    audio_track_index: int | None = None
```

В `progress()` если `audio_track_index is not None` — пишем в `WatchProgress.audio_track_index`.

### 6.6 Edge cases

- **Файлы без аудио вообще** (`audio_tracks == []`) — ffmpeg-команда без `-map 0:a`, master.m3u8 с одним видео-вариантом. Селектор аудио не показывается.
- **Файлы с одной аудиодорожкой** — master.m3u8 с одним аудио-вариантом. Селектор тоже не показываем (JS проверяет `hls.audioTracks.length > 1`).
- **Существующие записи** (`audio_tracks == NULL`) — лениво пробуем `probe_audio_tracks(file_path)` при открытии `/media/{id}` и сохраняем. Дальше работает.

---

## 7. Сводка по миграциям, env, файлам

### 7.1 Миграция

`migrations/versions/0003_catalog_metadata_and_audio.py`:

1. `ALTER TABLE media_items ADD COLUMN duration_seconds INTEGER NULL`
2. `ALTER TABLE media_items ADD COLUMN description TEXT NULL`
3. `ALTER TABLE media_items ADD COLUMN poster_url VARCHAR(1024) NULL`
4. `ALTER TABLE media_items ADD COLUMN year INTEGER NULL`
5. `ALTER TABLE media_items ADD COLUMN kind VARCHAR(32) NULL`
6. `ALTER TABLE media_items ADD COLUMN tmdb_id INTEGER NULL`
7. `ALTER TABLE media_items ADD COLUMN kinopoisk_id INTEGER NULL`
8. `ALTER TABLE media_items ADD COLUMN match_status VARCHAR(16) NOT NULL DEFAULT 'pending'`
9. `ALTER TABLE media_items ADD COLUMN match_source VARCHAR(16) NULL`
10. `ALTER TABLE media_items ADD COLUMN audio_tracks JSON NULL`
11. `CREATE TABLE genres (...)`
12. `CREATE TABLE media_item_genres (...)`
13. `ALTER TABLE watch_progress ADD COLUMN audio_track_index INTEGER NULL`
14. Индексы: `ix_media_items_kind`, `ix_media_items_year`, `ix_media_items_title`, `ix_media_item_genres_genre_id`

`downgrade()` — обратные.

### 7.2 Env

Добавляются 2 опциональных:

```
TMDB_API_KEY=
KINOPOISK_API_KEY=
```

### 7.3 Карта файлов

**Новые:**
- `app/metadata/__init__.py`
- `app/metadata/types.py`
- `app/metadata/tmdb.py`
- `app/metadata/kinopoisk.py`
- `app/metadata/matcher.py`
- `app/metadata/ffprobe.py`
- `migrations/versions/0003_catalog_metadata_and_audio.py`
- `templates/_library_grid.html`
- `templates/_match_dialog.html`
- `templates/_media_edit_modal.html`
- `static/posters/uploaded/.gitkeep` (директория для аплоадов)
- `tests/unit/test_ffprobe.py`
- `tests/unit/test_tmdb_client.py`
- `tests/unit/test_kinopoisk_client.py`
- `tests/unit/test_matcher.py`
- `tests/unit/test_title_parser_parsed.py` (расширение существующего)
- `tests/unit/test_ffmpeg_runner_audio.py`
- `tests/integration/test_scanner_match.py`
- `tests/integration/test_library_filter_search.py`
- `tests/integration/test_match_endpoints.py`
- `tests/integration/test_media_edit.py`
- `tests/integration/test_stream_audio_switch.py`

**Изменяемые:**
- `app/models.py` — новые поля в `MediaItem`, `WatchProgress`; модели `Genre`, `MediaItemGenre`
- `app/config.py` — `tmdb_api_key`, `kinopoisk_api_key`
- `app/torrents/title_parser.py` — `ParsedTitle` dataclass
- `app/torrents/scanner.py` — ffprobe + audio + matcher + жанры
- `app/library/routes.py` — query params, partial-render, prefill контекста для `/media/{id}`
- `app/streaming/ffmpeg_runner.py` — мульти-вариант сборка
- `app/streaming/routes.py` — master.m3u8 + variant routes + легаси-алиас
- `app/streaming/watchdog.py` — `IDLE_THRESHOLD_SECONDS = 300.0`
- `templates/library.html` — toolbar, обновлённая карточка
- `templates/media.html` — новый хедер, плашка «продолжить», селектор аудио, обновлённый JS
- `static/style.css` — новые секции
- `.env.example` — TMDB_API_KEY, KINOPOISK_API_KEY

### 7.4 Зависимости

`requirements.txt` дополнения не требуются — `httpx` уже есть, ffmpeg/ffprobe — системные. Pillow/imagehash и т.п. **не** используются.

---

## 8. Тесты

### 8.1 Unit

- `test_title_parser_parsed`: `parse_title("Breaking.Bad.S01E05.HDTV.mkv")` → `ParsedTitle(title="Breaking Bad", year=None, season=1, episode=5, kind_hint="tv")`. Покрытие: фильмы с годом, сериалы, шум, дефисы в названиях, файлы без шума.
- `test_ffprobe`: моки `subprocess.run` с фиктивным JSON ffprobe-выводом, проверяем парсинг `duration_seconds` и `probe_audio_tracks` (multi-language, без тегов, разные codecs).
- `test_tmdb_client`: моки через `respx` (`httpx`-моки). Search с 0/1/N результатов, get-detail, ошибки (401, timeout). Маппинг kind по genres.
- `test_kinopoisk_client`: то же через respx. Проверка in-memory rate-limit счётчика.
- `test_matcher`: матрица случаев: TMDB нашёл уверенный → возврат TMDB; TMDB нашёл низкий score → fallback Kinopoisk; оба пусты → None; TMDB ключ отсутствует → сразу Kinopoisk; оба ключа отсутствуют → None.
- `test_ffmpeg_runner_audio`: моки `subprocess.Popen`. Для 0 / 1 / 3 audio_tracks проверяем, что команда содержит правильные `-map`, `-var_stream_map`, `-master_pl_name`.

### 8.2 Integration

- `test_scanner_match`: реальный sqlite, замоканный TMDB через respx, симулируем что qBittorrent отдал торрент с готовым видеофайлом-фикстурой. Проверяем: создаётся `MediaItem` с заполненными `title`, `description`, `poster_url`, `year`, `kind`, `match_status='matched'`, жанры в `media_item_genres`.
- `test_library_filter_search`: создаём в БД 5–6 разнотипных `MediaItem`. Делаем `GET /library` с разными query params: `?q=Bad`, `?kind=movie`, `?genre=Драма`, `?status=watched`, и комбинацию `?q=...&kind=...`. Проверяем содержимое HTML (классы карточек, заголовки).
- `test_match_endpoints`: создаём `MediaItem`. POST `/api/media/{id}/match/search` с мокированным TMDB → ожидаемые результаты. POST `/api/media/{id}/match/apply` → обновление полей. CSRF-проверка.
- `test_media_edit`: POST `/api/media/{id}/edit` с разными комбинациями полей (включая upload файла). Проверяем сохранение, что `match_status='manual'`.
- `test_stream_audio_switch`: требуется фикстура `tests/fixtures/multi_audio.mkv` (~1 МБ, 2 audio tracks). Запускаем реальный ffmpeg через `start_hls`. Ждём `master.m3u8`. Парсим: должно быть 2 `EXT-X-MEDIA:TYPE=AUDIO`, две папки `v1/` и `v2/`. Этот тест может быть медленным (~3-5 сек) — отдельный pytest mark `slow`.
- Существующие тесты плеера/стриминга подправить под новый URL `master.m3u8` (legacy-алиас при этом проверить отдельным тестом).

---

## 9. Риски и допущения

1. **TMDB rate limit при первом скане большой библиотеки.** Делаем последовательно (один HTTP / 10 секунд скана). На 100 файлов = ~15-20 минут, приемлемо.
2. **Кривые имена в торрентах → плохой матч.** Стратегия защиты: высокий confidence-порог (0.7 с годом, 0.85 без), `match_status='failed'` показывается пользователю явным образом, кнопка ре-матча всегда доступна.
3. **TMDB не находит русский/советский контент.** Fallback на Kinopoisk и/или manual.
4. **Kinopoisk нестабилен.** Лимит 500/день — учитываем; при 429 → пропускаем. Если API ляжет полностью — manual всё ещё работает.
5. **Лицензия постеров.** TMDB-постеры с TMDB CDN и указанием источника. Не копируем себе.
6. **Мульти-аудио ffmpeg — самый рискованный кусок.** Защита: fallback на flat HLS если `len(audio_tracks) <= 1`; интеграционный тест с реальной mkv-фикстурой; легаси-алиас на старый URL.
7. **Auto-recovery в hls.js при ре-старте ffmpeg.** ffmpeg стартует с `seek_seconds=0`, hls.js перемотает на сохранённую `video.currentTime`. Если в тестах увидим, что не работает (плеер «застрял»), добавим серверный seek через эндпоинт — но не сейчас.
8. **Производительность фильтра по жанрам через `MediaItem.genres.any(...)`.** На SQLite это subquery, на маленькой библиотеке (~100-500 записей) — миллисекунды. На большой — добавим composite index, но это вне scope.

---

## 10. Что в этом спеце нет (вне scope)

- Сериалы с эпизодами (Spec 2)
- Локальное кэширование постеров
- Multi-select по жанрам (только одиночный)
- Full-text search (FTS5)
- Subtitles selection (архитектура мульти-варианта легко расширяется, но добавим только если попросят)
- Локальная загрузка постеров с дискового кэша
- Параллелизация TMDB-запросов в сканере

---

## 11. План реализации (preview)

Полный план — отдельный документ `docs/superpowers/plans/2026-05-17-catalog-player-fixes-plan.md` (создаётся следующим шагом через writing-plans skill). Грубо порядок этапов:

1. Багфикс плеера (heartbeat на паузе, IDLE_THRESHOLD_SECONDS, auto-recovery) — самая маленькая ценная итерация, можно выпустить отдельно.
2. Миграция модели + Genre/MediaItemGenre + ffprobe-модуль (без TMDB пока).
3. TMDB-клиент + Kinopoisk-клиент + matcher + интеграция в сканер.
4. UI библиотеки: toolbar, фильтры, поиск, сортировка, карточка с постером и прогрессом.
5. UI медиа-страницы: хедер, ре-матч диалог, форма редактирования, плашка «продолжить».
6. Мульти-аудио ffmpeg + master.m3u8 + UI селектор + сохранение `audio_track_index`.
7. Тесты по каждому этапу + интеграционные.

Грубая оценка: ~37–50 часов суммарно.
