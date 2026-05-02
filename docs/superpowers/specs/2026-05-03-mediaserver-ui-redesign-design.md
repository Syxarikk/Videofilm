# MediaServer: редизайн UI + удаление 2FA

**Дата:** 2026-05-03
**Статус:** черновик, ждёт ревью пользователя
**Связанные документы:** `docs/superpowers/specs/2026-05-02-family-media-server-design.md` (исходный дизайн)

---

## 1. Контекст

MediaServer развёрнут на `film.syxarik.ru` за Authentik (forward-auth proxy на nginx). Authentik уже отвечает за пароль, сессию и опциональную 2FA. Внутри MediaServer текущий UI — голый HTML без стилей (`static/style.css` ≈ 12 строк), и параллельно работает дублирующий локальный auth с собственной 2FA (TOTP + backup-коды).

Цель этого спека:
1. Убрать дублирующую 2FA внутри приложения — пароль остаётся локальный, но без второго фактора.
2. Сделать визуально полноценный UI в духе Plex/Apple TV+ — тёмный, кинематографичный, синий акцент.

## 2. Что меняем (in scope)

- Удаление модулей и страниц 2FA (TOTP + backup-коды).
- Полная переписка `static/style.css` — дизайн-система с CSS-переменными.
- Переписка всех 11 шаблонов в `templates/` под новый стиль.
- Миграция БД: drop колонок `users.totp_enabled`, `users.totp_secret_encrypted` + drop таблицы `backup_codes`.
- Удаление 2FA-related тестов, корректировка остальных integration-тестов под новый flow логина.

## 3. Что НЕ меняем (out of scope)

- **Authentik** и nginx-конфиг — не трогаем, они уже работают.
- **Backend-логика** торрентов, библиотеки, стриминга, загрузок — без изменений.
- **HTMX** — остаётся для фрагмент-рендеринга (прогресс загрузок, модалка добавления торрента).
- **API-роуты** для библиотеки/торрентов/стриминга — без изменений.
- **Локальный пароль и `/login`** — остаётся (вариант B из брейншторма). Ввод пароля идёт после Authentik. Не идеально, но не цель этого спека.
- **Страница `/change-password`** — остаётся.
- **Шрифт Google Fonts, Tailwind, frontend build-step** — не вводим. Остаёмся на чистом CSS + system font stack.

## 4. Удаление 2FA — детальный скоуп

### 4.1 Файлы на удаление

| Путь | Причина |
|---|---|
| `app/auth/totp.py` | TOTP-генерация и шифрование секрета |
| `app/auth/backup_codes.py` | Backup-коды |
| `templates/enroll_2fa.html` | Шаблон активации |
| `templates/verify_totp.html` | Шаблон ввода кода при логине |
| `tests/unit/test_totp.py` | Unit-тесты TOTP |

### 4.2 Файлы на правку

| Путь | Что меняем |
|---|---|
| `app/auth/routes.py` | Удалить роуты `/enroll-2fa` (GET/POST) и `/verify-totp` (GET/POST). В `login_post` упростить логику redirect: после ввода пароля сразу промотировать сессию до full и редиректить на `/library` (если не нужно менять пароль) или на `/change-password`. Удалить импорты totp и backup_codes. |
| `app/models.py` | Удалить из `User`: `totp_enabled`, `totp_secret_encrypted`. Удалить класс `BackupCode`. |
| `app/auth/sessions.py` | Без изменений. Механика partial-сессий (`is_partial`, `promote_session`) остаётся — нужна для шага `/change-password` после первого логина. |
| `app/auth/deps.py` | Без изменений. `get_current_user_partial` остаётся — используется в `/change-password`. |
| `app/admin/routes.py` | Если есть UI-флаги «2FA активна» — убрать. |
| `tests/conftest.py` | Если есть фикстуры с TOTP — упростить. |
| `tests/integration/test_login_flow.py` | Переписать под flow без TOTP. |
| `tests/integration/test_first_login_setup.py` | То же — теперь после смены пароля сразу `/library`. |
| `tests/integration/test_admin_users.py` | Убрать проверки полей `totp_enabled`. |
| `scripts/create_admin.py` | Если выставляет `must_change_password=True` — оставить. Никаких полей TOTP не трогаем (их не будет в схеме). |
| `README.md` | Удалить из инструкции упоминание «активировать 2FA → отсканировать QR → сохранить backup-коды». |

### 4.3 Миграция БД

Создать новую миграцию `migrations/versions/0002_drop_2fa.py` (Alembic). Используем `batch_alter_table` для совместимости с SQLite, который не поддерживает `DROP COLUMN` напрямую.

```python
"""drop 2fa

Revision ID: 0002
Revises: 0001
"""
revision = '0002'
down_revision = '0001'

def upgrade():
    with op.batch_alter_table('backup_codes') as batch_op:
        batch_op.drop_index('ix_backup_codes_user_id')
    op.drop_table('backup_codes')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('totp_enabled')
        batch_op.drop_column('totp_secret_encrypted')

def downgrade():
    # Зеркало 0001: восстанавливаем колонки и таблицу с тем же типом
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('totp_secret_encrypted', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default='0'))
    op.create_table('backup_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('code_hash', sa.String(length=255), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('backup_codes') as batch_op:
        batch_op.create_index('ix_backup_codes_user_id', ['user_id'], unique=False)
```

### 4.4 Поток логина после удаления 2FA

```
POST /login (username + password)
   ├─ user.must_change_password? → /change-password (partial session)
   │     POST /change-password → promote session, /library
   └─ иначе → promote session, /library
```

Раньше было 4 ветки (с TOTP/без, с change-password/без). Теперь 2 ветки.

## 5. Дизайн-система

### 5.1 Цветовая палитра (CSS-переменные в `:root`)

```css
:root {
  /* Поверхности */
  --bg:           #0a0a0f;   /* основной фон */
  --surface:      #141420;   /* шапка, карточки, попапы */
  --surface-2:    #1c1c2e;   /* hover, modal, активный input */
  --border:       #2a2a3a;   /* разделители, рамки */

  /* Текст */
  --text:         #f5f5f7;   /* основной */
  --text-dim:     #8c8ca0;   /* вторичный, метаданные */
  --text-faint:   #5c5c70;   /* placeholder, отключённое */

  /* Акценты */
  --accent:       #3b82f6;   /* кнопки, ссылки, активный пункт меню */
  --accent-hover: #2563eb;   /* hover для accent */
  --success:      #10b981;   /* загружено, рейтинг, статус «качается» */
  --danger:       #ef4444;   /* ошибки, удаление */
  --warning:      #f59e0b;   /* предупреждения, паузы */

  /* Геометрия */
  --radius-sm:    4px;
  --radius:       8px;
  --radius-lg:    12px;
  --radius-pill:  999px;
}
```

### 5.2 Типографика

- **Шрифт:** `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, system-ui, sans-serif` — система знает лучше нас.
- **Базовый размер:** 15px (на десктопе), `line-height: 1.5`.
- **Заголовки:** жирные (700–800), отрицательный letter-spacing (`-0.01em` … `-0.02em`).
- **Метки и статусы:** `text-transform: uppercase; letter-spacing: 0.1em; font-size: 0.7rem;`.

### 5.3 Глобальные правила

```css
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: ...;
  min-height: 100vh;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
```

### 5.4 Компоненты

| Компонент | Класс | Описание |
|---|---|---|
| Кнопка primary | `.btn` | Синий фон, белый текст, hover-подъём |
| Кнопка secondary | `.btn .btn-secondary` | Поверхность-2, рамка, тёмный |
| Кнопка ghost | `.btn .btn-ghost` | Прозрачный фон, hover-фон |
| Кнопка danger | `.btn .btn-danger` | Красный, для удаления |
| Поле ввода | `.input` | Тёмный фон, синяя рамка при focus |
| Карточка | `.card` | Surface, padding, border-radius |
| Постер | `.poster` | aspect-ratio 2/3, overlay, hover-наезд |
| Бейдж | `.badge` | Маленький pill (NEW, HDR, 4K, SUB) |
| Таблица | `.table` | Тёмная, разделители, hover на строках |
| Прогресс-бар | `.progress` | Фон + заполнение акцентом |
| Шапка | `header.topnav` | Sticky, blur, граница снизу |
| Аватарка | `.avatar` | 26–32px круг, градиент, инициал |
| Modal | `.modal` | Затемнение фона + центрированная карточка |
| Toast | `.toast` | Угловое уведомление (для ошибок/успеха) |
| Hero-блок | `.hero` | Большой backdrop с градиентом + заголовок |

## 6. Изменения по страницам

### 6.1 `templates/base.html`
Новый каркас:
- Sticky `<header class="topnav">` с логотипом, навигацией, аватаркой.
- Навигация показывается только если `user`. Активный пункт подсвечивается через `{% if request.url.path.startswith('/library') %}active{% endif %}`.
- Бейдж рядом с «Загрузки» — количество активных загрузок (через context-processor или передачу из роута).

### 6.2 `templates/login.html`
Центрированная карточка ~400px, дальше базовый stack: логотип / заголовок «Вход» / поле логина / поле пароля / кнопка primary. Ошибка над полями. Без шапки/навигации (logged-out).

### 6.3 `templates/change_password.html`
Центрированная карточка, заголовок «Смена пароля», два поля (новый + повтор), подсказка «минимум 12 символов», primary-кнопка. Без шапки.

### 6.4 `templates/library.html`
Главная страница после логина:
- **Hero-блок** (если в библиотеке есть хотя бы один файл) — последний добавленный медиафайл с большим градиентным фоном, метаданными, кнопкой «▶ Смотреть».
- **Полки**: «Продолжить просмотр» (если такая фича есть в backend; если нет — пропускаем сейчас, добавляем placeholder), «Недавно добавленное».
- **Сетка** `grid-template-columns: repeat(auto-fill, minmax(170px, 1fr))`.
- **Постер** — `aspect-ratio: 2/3`, градиентный фон вместо постерной картинки (постеров у нас нет, так что симулируем как в мокапе), overlay снизу с названием и годом, бейджи (NEW / разрешение).
- **Empty state** — если библиотека пустая: иллюстративная карточка с подсказкой «Добавьте первый торрент» + кнопка → `/torrents/add`.

### 6.5 `templates/media.html`
Страница одного файла:
- Большой backdrop сверху (постерный градиент), внизу — название, метаданные, кнопка «▶ Смотреть» (запуск плеера) и «Скачать оригинал».
- Плеер (HLS) встраивается под hero — в той же тёмной теме, без лишнего chrome.
- Кнопка «Удалить» — `.btn-danger` справа, в углу карточки. Подтверждение через `.modal` (компонент из дизайн-системы, §5.4) — не нативный `confirm()`.

### 6.6 `templates/add_torrent.html`
Карточка по центру, две вкладки:
- **Magnet-ссылка** — большое текстовое поле + primary-кнопка.
- **`.torrent` файл** — drag&drop зона + fallback `<input type=file>`.

Отображение прогресса добавления — toast снизу.

### 6.7 `templates/downloads.html`
Таблица с колонками: Иконка статуса · Название · Прогресс-бар (с %) · Скорость · ETA · Действия (пауза / удалить).
- Статусы: 🟢 «Качается», ⏸ «Пауза», ✅ «Готово», ❌ «Ошибка» — через цветные точки.
- HTMX обновляет каждую строку раз в 2 сек (если backend это поддерживает).
- Прогресс-бар внутри ячейки — тонкий, синий.
- Empty state — «Нет активных загрузок».

### 6.8 `templates/admin_users.html`
Таблица: Логин · Роль (admin/user) · Создан · Последний вход (если есть) · Действия (удалить, сбросить пароль).
Кнопка «➕ Создать пользователя» сверху → модалка с полями логин + временный пароль.
Empty state не нужен — там всегда есть админ.

### 6.9 `templates/admin_health.html`
Сетка карточек-метрик 2–3 колонки:
- Использование диска (текст + прогресс-бар, цвет меняется на danger >85%)
- Активные загрузки (число)
- База данных (размер файла, последний бэкап если есть)
- qBittorrent (статус подключения)

### 6.10 Удаляются: `enroll_2fa.html`, `verify_totp.html`

## 7. Реализация

### 7.1 Стек
- Чистый CSS в `static/style.css` с CSS-переменными (без SCSS, без Tailwind, без build-step).
- Существующий HTMX (`static/htmx.min.js`) — для интерактивности.
- Существующий HLS.js (`static/hls.min.js`) — для плеера.
- Шрифт — system stack, без Google Fonts (быстрее, без внешних запросов).
- Иконки — inline SVG в шаблонах. Нет внешних библиотек.

### 7.2 Порядок работ
1. Миграция БД (drop 2FA).
2. Удаление 2FA-кода (модули, роуты, шаблоны, тесты).
3. Новый `static/style.css` с дизайн-системой.
4. Новый `templates/base.html`.
5. Переписка `login.html` и `change_password.html` (формы — самое простое).
6. Переписка `library.html` (главная, самая важная).
7. Переписка `media.html` + плеер.
8. Переписка `add_torrent.html`, `downloads.html`.
9. Переписка `admin_users.html`, `admin_health.html`.
10. Прогон всех тестов, починка интеграционных по необходимости.
11. Ручная проверка в браузере на десктопе и мобильнике.

### 7.3 Адаптивность
- Mobile-first не нужен (приложение скорее для десктопа), но базовая адаптивность есть:
  - `<= 640px`: шапка с гамбургером, навигация в overlay.
  - Постер-сетка: `minmax(120px, 1fr)` на узких экранах.
  - Hero на мобильнике без backdrop, только заголовок и кнопка.

### 7.4 Доступность
- Контраст текст/фон: `#f5f5f7` на `#0a0a0f` ≈ 18:1, проходит WCAG AAA.
- Все интерактивные элементы фокусируемы (`:focus-visible` с синей рамкой).
- Аria-label на иконках без текста.

## 8. Тестирование

- **Удаление 2FA:** прогнать существующие integration-тесты, починить упавшие (логин-flow), удалить 2FA-специфичные.
- **Дизайн:** ручная проверка каждой страницы в Firefox + Chrome на ширине 1920 / 1280 / 768 / 375. Скриншот для каждой страницы для PR-описания.
- **Доступность:** ручная проверка focus-стейтов через Tab-навигацию (видна синяя рамка на каждом интерактивном элементе).

## 9. Риски и оговорки

- **Без постерных картинок** — у нас нет TMDb-интеграции, постеры симулируем градиентом по hash от названия. Выглядит прилично, но не как у Plex с реальными постерами. Это известный trade-off; добавлять TMDb можно отдельным спеком.
- **Локальный пароль остаётся** — пользователь логинится дважды (Authentik + локальный). Это сознательный выбор (вариант B). Если потом захочется убрать локальный auth полностью — отдельный спек.
- **HLS.js + HTMX в тёмной теме** — нужно проверить, что плеер не показывает белые controls (могут быть нативные).

## 10. Не-цели

- Постеры из TMDb / OMDb — позже.
- «Продолжить просмотр» с реальным трекингом позиции просмотра — позже.
- Push-уведомления о завершении загрузки — позже.
- Тёмная/светлая тема toggle — нет, только тёмная.
- Локализация — только русский, как сейчас.
- PWA / offline — нет.
