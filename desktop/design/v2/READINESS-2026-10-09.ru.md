# SEOHEAD Desktop: готовность кода к новому дизайну (09.10.2026)

Оценка только на чтение, код не менялся. Сверял канвас (30 бордов в `scratchpad/canvas/project/*.dc.html`, `assets/app.css`, `assets/ext.css`), спецификацию `Дизайн-система_SEOHEAD_Desktop.md` (v2), код Desktop (HEAD `3780337`, незакоммичены `ui/i18n.py`, `ui/translations.py`, `README.md`) и ядро `Work/tools/seotools` (HEAD `a7dff37a`, PR #915).

> [!info] Где код
> С 09.10.2026 приложение живёт в монорепозитории seohead-tools, каталог `desktop/` (история коммитов сохранена). Локальная отдельная копия удалена владельцем; незакоммиченный WIP i18n (`i18n.py`, `translations.py`) утрачен — язык RU/EN делать заново по §3. Ссылки `файл:строка` ниже — относительно `desktop/` (`src/seohead_desktop/…`).

---

## 1. Темы

### Как это работает сейчас
- Единственный источник — `theme/tokens.json`: 44 цвета в схеме MD3 с сайта, а также шрифт, отступы, плотность, раскладка, motion и радиусы.
- `theme_tokens()` читает JSON один раз и кэширует через `@lru_cache` (`ui/presentation.py:26-28`).
- `load_theme(app)` (`app.py:110-121`) склеивает цвета и скалярные токены, подставляет их в `theme/theme.qss` через `string.Template` и вызывает `app.setStyleSheet(...)` для всего приложения. Затем `install_popup_style` (`ui/popup_style.py:55`) скругляет меню и выпадашки масками.
- Второй QSS-шаблон: `ui/panels.py:27-46` (`component_stylesheet`). Он дописывается к общему листу в `app.py:3263` и в `ui/gallery.py:409`. Правила `QMenu` и `QComboBox QAbstractItemView` в нём дублируют `theme.qss:142-150` и местами противоречат им: `QMenu::item:selected` в одном месте использует `surface_container_low`, в другом `primary_container`.
- Динамические свойства уже используются, и это хорошая основа: `role=primary|danger|quiet|panelToggle` (`theme.qss:21-25, 61-66`), `tone=` у бейджа (`theme.qss:12-14`), `compact=` у навигации (`theme.qss:34-35`), `active=` у карточки запуска (`theme.qss:125`), `metric=` (`theme.qss:136`). Точечный re-polish уже написан: `ui/presentation.py:294-295` (StateBadge), `ui/work_monitor.py:197-198`, `app.py:2637-2638`. Всего `setProperty` встречается 41 раз, больше всего в `app.py` (19).

### Где стиль зашит в Python
- **Hex и rgba в Python: 0.** Это главный плюс. Все цвета берутся из токенов.
- **Цвет запекается при создании виджета** (5 мест). При смене темы эти иконки останутся старыми: `material_icon()` кэширует результат по паре (имя, цвет) через `@lru_cache(maxsize=128)` (`ui/icons.py:66`), а `_SvgEngine` фиксирует цвета состояний в `__init__` (`ui/icons.py:20-32`). Места: `app.py:441` (Новый скан), `app.py:860` (Стоп), `ui/presentation.py:413`, `ui/content_search_panel.py:79`, `ui/comparison_summary.py:55`. Кроме них 57 вызовов `icon(...)` берут цвет по умолчанию, `on_surface_variant`, и он тоже вшивается в движок иконки.
- **Цвет читается во время отрисовки.** Здесь смена темы сработает после сброса кэша: `ui/presentation.py:41-52` (SwitchCheckBox), `ui/presentation.py:338-340` (ручка сплиттера), `models.py:81` (`ForegroundRole` статуса).
- **Литералы в QSS вместо токенов:** 15 размеров шрифта в px (`theme.qss:6, 89-90, 96, 111, 128-138`, `ui/panels.py:31-33`), шрифт `Menlo` (`theme.qss:49`), радиусы 3, 4, 6 и 8 px.
- **Геометрия в коде:** 126 вызовов `setContentsMargins`, `setSpacing`, `resize`, `set*Width` и `setColumnWidth`. Больше всего в `app.py` (47) и `ui/work_monitor.py` (17). Ширина навигации задаётся через `setFixedWidth` (`app.py:261`, а в `gallery.py:190` стоит литерал 200).

### Расхождение палитры с дизайн-системой v2
`tokens.json` — это палитра сайта в духе MD3 с фиолетово-серыми поверхностями: `primary_container #D3E4FF`, `outline_variant #CAC4D0`. Спецификация и канвас описывают нейтрально-голубую консоль: `selected #E3F0FF`, `divider #DDE1E7`, `outline #C4C8D0`, `raised #F4F6FA`. Поэтому сейчас приложение не совпадает с канвасом даже в светлой теме. Ролей v2 в токенах нет: `selected/on_selected`, `base`, `raised`, `hover`, `divider`, `text_2/3`, `muted`, `disabled_*`, 6 пар бейджей, 4 вида баннеров, цвета данных (2xx…5xx) и дельт. Часть свойств v2 тоже не сделана: `role=tonal|text|icon`, `size=`, `badge=`, `note=`, `chip=`, `pill=`, `kpi=`, `invalid=`, `segmented=`.

### Что нужно для трёх тем и переключения на лету
1. Привести `tokens.json` к схеме v2: общий блок `scale` (размеры, радиусы, шрифты) и `themes: {light, dark, contrast}` с одинаковым набором ролей. В тест добавить проверку, что набор ключей у всех тем совпадает, и прогон контраста WCAG (AA для light и dark, AAA/7:1 для HC).
2. Сделать `ThemeManager(QObject)` с методом `apply(name)` и сигналом `changed`. Он делает `theme_tokens.cache_clear()` и `material_icon.cache_clear()`, заново генерирует QSS (это `load_theme` + `component_stylesheet`, сведённые в один шаблон), вызывает `app.setStyleSheet`, обновляет `QPalette` (Fusion берёт его для нестилизованных частей и `ForegroundRole`) и эмитит `changed`.
3. Подписчики на `changed`: перевыставляют иконки с явным цветом (5 мест выше) и иконки навигации (`app.py:251-256`), делают `viewport().update()` у таблиц.
4. Цена перерисовки. `app.setStyleSheet` на всё приложение — это полный re-polish всех виджетов. Для текущего масштаба (около 11 страниц, сотни виджетов, ленивые панели) это порядка 100–300 мс, одна пауза при переключении. Приемлемо, если не делать это на hover. Точечный `unpolish/polish` нужен только при смене dynamic property, и это уже реализовано.
5. HC-тема: обводка фокуса 2→3 px, без полупрозрачных фонов, границы у всех полей и строк. Тёмная: `QToolTip`, маски попапов и SVG-иконки тоже должны получать цвет из токенов (сейчас так и есть).
6. Хранить выбор в `QSettings("SEOHEAD", "DesktopPreparation")` под ключом `appearance/theme`. При старте применять тему до создания окна.

**Оценка: 3/5.** Фундамент правильный (один источник, свойства, 0 hex в Python). Не хватает схемы v2, нескольких палитр, менеджера тем и сброса кэшей. Это 2–3 дня, плюс 1 день на проектирование dark и HC палитр.

---

## 2. Каркас окна

### Как устроено сейчас (`app.py:156-368`)
Порядок сверху вниз:
1. QMenuBar (`app.py:296-341`): Проект / Агент / Справка / Вид. На macOS он нативный.
2. QToolBar `projectControls` с `topbar()` (`app.py:379-444`): меню-гамбургер, бренд, выбор проекта (combo), «Открыть», «Обновить», «Действия», «Отменить чтение», «Новый скан». Поля 10 px, итоговая высота около 52.
3. `addToolBarBreak`, затем QToolBar `scanContext` с `contextbar()` (`app.py:446-476`): «Сохранённый скан», выбор скана, бейдж, «Сравнить», source badge. Это вторая полоса.
4. Центральный виджет: `WorkspaceTabs` (`ui/workspace_tabs.py:160`, максимум 12) — третья полоса, `InlineNotice`, потом `QHBoxLayout`. В нём `QListWidget#navigation` (176/64, анимация ширины `app.py:221-224, 2622-2638`) и `PanelStack` с 11 страницами (`app.py:263-285`).
5. Страница URL (`app.py:677-812`): `WorkspaceSplitter` по горизонтали и вертикали, таблица, `QTabWidget` инспектора и «Сводка». Работа (`app.py:478-571`): вертикальный сплиттер, `WorkMonitor` и `QTabWidget`. Монитор можно вынести в отдельное окно (`ui/workspace.py:76`, QDockWidget).
6. QStatusBar.

### Чем отличается от канваса
| Канвас / спецификация | Сейчас | Разрыв |
|---|---|---|
| Одна шапка 52 px: проект → сайт → скан крошками, агент, ⌘K, ↻, «Новый скан» | две полосы-тулбара (52 + ~40) плюс меню | слить `topbar` и `contextbar` в один виджет с крошками; это не QToolBar |
| Полоса вкладок 36 px **под** шапкой; «+», «tune», ПКМ-меню | `WorkspaceTabs` в центральном виджете, ниже обоих тулбаров; вкладка около 40 px | порядок совпадает, нужно убрать среднюю полосу и уменьшить высоту; переиспользуется |
| Навигация 200 / rail 64, секции «Данные/Результат», счётчики, кнопка настроек внизу слева | QListWidget 176/64 без секций, счётчиков и футера | заменить на `QListView` с делегатом и моделью; есть анимация и компакт-логика |
| Статус-бар 26 px: режим и возраст слева, ядро и ID справа | обычный QStatusBar с `showMessage` | добавить постоянные виджеты |
| Вариант B деталей URL справа, аккордеон | только снизу (`QTabWidget` в вертикальном сплиттере) | новая раскладка «детали справа» |
| Нет вкладок «Проекта» (дефект 3 из топ-15) | `ProjectPanels(TabDeck)` с 15 вкладками (`ui/panels.py:488`) | убрать |

### Что переиспользовать, а что писать заново
- **Оставить:** `WorkspaceTabs`/`_WorkspaceTabBar`/`WorkspaceContext` (`ui/workspace_tabs.py`, 549 строк, 21 тест); `WorkspaceSplitter` и ручку; `TablePanel`, `PageModel`, `PageProxy` и `TabDeck` (`ui/components.py`); `WorkMonitor`; `ContentSearchPanel`; `ComparisonSummary`; `CrawlConfigurationDialog`; `ActionFinder` (⌘K); `StateBadge`, `InlineNotice`, `SwitchCheckBox`; иконки; весь слой `mcp_gateway`, `scan_manager`, `scan_runner`, `local_control`.
- **Переписать:** шапку (`topbar` + `contextbar` → `ui/shell/header.py`), навигацию (QListWidget → модель и делегат, секции, счётчики, футер с «Настройками»), статус-бар, стартовый экран (MRU-список вместо `show_startup_workspace`), диалог «Новый скан» (`scan_preview`, 229 строк внутри `MainWindow`, `app.py:2776-3004`) — под каркас диалога v2 с четырьмя карточками источника; страницу «Отчёты» (сейчас заглушка `app.py:573-585`).

### Мешает ли монолит `app.py`
Да, это блокер для параллельной работы и для i18n. `MainWindow` — один класс на 3100 строк и 150 методов, в нём 554 русские строки. Любая из пяти задач (каркас, темы, i18n, режимы, MCP) правит один файл. Делить нужно механически, без смены поведения, до начала дизайн-работ:

| Модуль | Что переносится (строки `app.py`) |
|---|---|
| `shell/main_window.py` | `__init__`-каркас, меню, шорткаты, `resizeEvent`, компакт и плотность (156-368, 2611-2770) |
| `shell/header.py`, `shell/navigation.py`, `shell/statusbar.py` | `topbar`/`contextbar` (379-476), навигация (2559-2645) |
| `session/project.py` | открытие и чтение проекта, наблюдатель, MRU (1060-1300, 2584-2610) |
| `session/requests.py` | шлюз MCP, `start/complete/cancel_command` (941-1059) |
| `session/workspace.py` | вкладки, раскладки, сохранение и восстановление (1979-2308) |
| `pages/work.py`, `urls.py`, `scans.py`, `tasks.py`, `inbox.py`, `journal.py`, `reports.py` | страницы и их `load_*` (478-940, 1340-1913) |
| `dialogs/new_scan.py` | `scan_preview` (2776-3004) |
| `runs/controller.py` | собственные запуски и опрос (3005-3238) |
| `control/dispatch.py` | `dispatch_control`, подключение агента (2342-2530) |
| `__main__.py` | `main()` и capture/SVG (3249-3317) |

Связь между частями — через сигналы и контроллеры, а не через `self.window.*`. Сейчас `content_search.py:122,174` ходит в `self.window.core_executable`, это нужно разорвать.

**Оценка каркаса: 2/5.** Деление — 2–3 дня с прогоном 256 тестов. Новый каркас — ещё 3–4 дня.

---

## 3. Локализация RU/EN

- `ui/i18n.py` (168 строк, WIP 08.10, не закоммичен). Как устроено: `Message(str)` хранит исходный русский шаблон и параметры; `bind(owner, method, value)` запоминает сеттер и геттер в `WeakKeyDictionary`; `locale.set_language("en")` перевыставляет только те тексты, которые пользователь не менял (сравнение `getter() != previous`). Есть обёртки `widget()` и `call()` для `addItem`, `addAction`, `addMenu`, `addTab`, `addRow`. Идея рабочая, переключение без перезапуска.
- `ui/translations.py` (19 строк): около 50 пар RU→EN.
- **Ни один модуль это не импортирует.** Интеграция — 0%. Подключено только `translations` → `i18n`.
- **Строки в коде** (подсчёт по AST, без docstring): **1387 кириллических литералов, 1122 уникальных.** По файлам: `app.py` 554, `ui/presentation.py` 238 (подписи полей `FIELDS`, `state_text`), `content_search_panel.py` 74, `work_monitor.py` 72, `help_guide.py` 68, `panels.py` 56, `components.py` 54, `crawl_configuration_dialog.py` 53, `comparison_summary.py` 48, `content_search.py` 36, `gallery.py` 34, `tabcatalogue.py` 29, `workspace.py` 26, `workspace_tabs.py` 23, `comparison.py` 18. Отдельно около 144 латинских «человеческих» строк, в основном ID и коды (`tabcatalogue.py`, `local_control.py`). Их надо разобрать вручную.
- **Ловушки:**
  1. Русские строки служат идентификаторами. Названия панелей «Навигация», «Сводка», «Инспектор URL» — ключи `_panel_intent` и `panel_actions` (21 место, `app.py:209, 302-309, 1995, 2023-2024`). `navigation_labels` используются по индексу. Сначала нужны ID, потом перевод.
  2. Тексты из ядра (подписи и описания `seo_crawl_describe_settings`, причины отказов) приходят на английском или в виде ключей. Это отдельный слой: либо карта ключ → подпись в Desktop, либо `--lang` в ядре.
  3. Числа и даты: сейчас `f"{n:,}"` даёт разделитель-запятую. Нужно форматирование через `QLocale`.
  4. Тесты: 84 строки с кириллицей в 17 файлах. При смене языка по умолчанию они сломаются. Решение — фиксировать язык `ru` в тестах.
  5. Кнопки `QDialogButtonBox` (Close/OK) переводит только `QTranslator` с `qtbase_ru.qm`. Его нужно положить в бандл.
- **Объём:** перевести всё на `message()`/`bind()` — около 1100 строк в 16 файлах, EN-каталог около 1100 пар, переключатель в «Настройках» плюс `QSettings`. **5–8 дней**, если делать после деления `app.py`. Альтернатива — стандартный `self.tr()` + `pylupdate5`/`.ts`/`.qm`. Для PyQt5 это нативнее, но переключение на лету потребует `retranslateUi()` в каждом классе, а это больше кода. Текущий `i18n.py` дешевле — рекомендую оставить его.

**Оценка: 1/5.**

---

## 4. Режимы: агент/проект и простой краулер

### Где Desktop предполагает проект
- `crawl_arguments()` отказывает без `project.json` (`scan_runner.py:131-133`) и всегда передаёт `--project`. `resume_arguments()` требует, чтобы скан лежал в `<project>/scans` (`scan_runner.py:177-181`).
- `ScanRequest` и `LocalScanManager` хранят `project` и `project_uuid` (`scan_manager.py:20-21, 43-44`).
- `scan_preview` открывается только при `project_directory` (`app.py:2777-2779`). Цель берётся из `project.site.target` (`app.py:2790`).
- Прогресс приходит только через `seo_project_observe` (опрос 0,5 с, `app.py:1213-1300, 3174-3196`).
- Шлюз MCP разделяет инструменты на `_DIRECTORY_TOOLS` (7 штук, привязаны к `set_project_scope`) и `_SCAN_TOOLS` (`mcp_gateway.py:43-64, 165`).
- `project_directory`, `current_project_uuid` и `project_result` упоминаются в `app.py` 82 раза. `WorkspaceContext` ключуется по `project_uuid`/`scan_uuid`. Локальное управление требует `project_uuid` для `new_tab` и `new_scan` (`local_control.py:28-39`).

### Что ядро уже умеет без проекта
- `seohead crawl-site --url URL --scan-out FILE` — обычный краул в SQLite без проекта. `--urls` и `--urls-file` — режим списка, ему обязателен `--scan-out` (`seohead/servers/handlers.py`, ветка `project_root is None`). Без `--scan-out` сканы падают в `./scans` от текущего каталога (`_default_scan_path`, `handlers.py:92-115`). В GUI так делать нельзя, путь всегда нужно передавать явно.
- Чтение сканов не зависит от проекта: `scan-inspect`, `scan-url-detail`, `scan-status`, `scan-link-inspect`, `scan-content-search`, `scan-export`, `compare-crawls` принимают `--scan FILE`; `scan-list --directory DIR` даёт список сканов в папке.
- `scan-status` читает frontier и счётчики прямо из SQLite. Подходит для опроса живого скана без проекта.

### Чего не хватает
| Где | Пробел |
|---|---|
| ядро | наблюдение запуска (`run_observation.start/finish`, события, `observer_run_id`) работает только с `project_root`; у краула без проекта нет журнала, возраста наблюдения и причины завершения, кроме тех, что в самом SQLite |
| ядро | без проекта не работают межсканновый пейсер origin (`ProjectOriginPacer`) и бюджет `admission`, то есть защита «не больше N запросов в секунду на хост» при параллельных сканах |
| Desktop | второй `crawl_arguments` без проекта (`--url`/`--urls-file` + `--scan-out <библиотека>/<host>-<uuid>.seohead`) |
| Desktop | «Библиотека сканов» — папка вроде `~/Library/Application Support/SEOHEAD/scans` (через `QStandardPaths`), список через `scan-list --directory` |
| Desktop | `ModeController`: в режиме краулера скрыть «Работа», «Задачи», «Входящие», «Отчёты исполнителям», агентскую заметку; оставить URL, проблемы, поиск HTML, сравнение, экспорт |
| Desktop | контекст вкладки без `project_uuid` (ключ — `scan_uuid` и путь); `local_control` без обязательного `project_uuid` |
| Desktop | действие «Сделать проектом» — импорт ad-hoc скана в проект (`seo_project_new` + перенос или ссылка на скан) |

**Насколько сложно:** ядро поддерживает краул и чтение — да. Прогресс через `scan-status` — да. Наблюдение и пейсинг без проекта — нет. Desktop: 4–6 дней на режим и библиотеку, ядро: 2–3 дня, если нужен журнал и пейсер для ad-hoc. Обходной путь: невидимый служебный проект «Без проекта», тогда ядро не трогаем. Его стоит обсудить с Павлом — это быстрее, но смешивает модели.

**Оценка: 2/5.**

---

## 5. Переключатель MCP и установка CLI

### Как Desktop говорит с ядром сейчас
- **Чтение:** один долгоживущий stdio-MCP клиент `PersistentMcpGateway` (`mcp_gateway.py:109`) запускает `<core> mcp` (`mcp_gateway.py:275`). Разрешено 17 инструментов плюс 2 опциональных (`mcp_gateway.py:19-40`); на запись — inbox, compare и verify.
- **Сканы:** `QProcess` с `seohead crawl-site …` (`scan_runner.py:18-90`), до трёх параллельно (`app.py:3007`). Поиск HTML — отдельный `QProcess` (`content_search.py:174`).
- **Ядро:** `--core-cli` либо `shutil.which("seohead")` (`app.py:163`). В бандле `package_arguments` подставляет вшитый CLI и проверяет хэш и инвентарь (`bundle.py:75-126`, `scripts/entrypoint.py`).
- **Управление окном агентом:** `local_control.py` (720 строк). `QLocalServer`, приватная папка `0700`, `control.json` `0600` с 256-битным токеном, протокол `seohead.desktop.control.v1`, 11 операций. Клиент — `control_cli.py` (CLI и stdio-MCP `desktop_*`, 11 инструментов). Включается вручную: меню «Агент → Подключить агента…» с выбором папки (`app.py:2378-2421`) или флаг `--agent-control DIR`. Живёт до закрытия окна.

### Что есть в ядре
- `seohead mcp [--profile full|audit|infra|quick-check|router] [--no-progress]` — **только stdio** (`seohead/cli.py:2682-2688, 2721-2729`; `mcp_server.py:3082-3096`). Нет ни демона, ни порта, ни файла состояния.
- `seohead/remote_api/app.py` — ASGI-заготовка без слушателя, ждёт #785/#786. К локальному MCP отношения не имеет.
- `control.json` и `local_control.py` — **это код Desktop, не ядра.** В ядре нет ни одного упоминания.

### Что такое «включить MCP» на самом деле
Stdio-сервер не «работает» сам по себе: его запускает клиент (Claude Code, Claude Desktop, Codex) по записи в своём конфиге. Поэтому переключатель — это:
1. **Регистрация** `seohead mcp` (ядро, 165 инструментов) и, по желанию, `seohead-desktop-agent --endpoint … mcp` (управление окном) в конфигах агентов: `~/.claude.json` / `.mcp.json`, `~/Library/Application Support/Claude/claude_desktop_config.json`, `~/.codex/config.toml`. Перед правкой — бэкап, правка атомарная.
2. **Общее состояние**, которое видят и CLI, и Desktop: файл `~/.config/seohead/mcp-state.json` (на Windows `%APPDATA%\seohead\`). В нём `enabled`, профиль, список зарегистрированных клиентов, путь CLI и версия, а также указатель на живой Desktop: `{instance_id, descriptor_path, pid, started_at}` **без токена**. Запись атомарная (tmp + rename), права `0600`.
3. **Команды ядра:** `seohead mcp status|enable|disable [--client claude-code|claude-desktop|codex] [--profile]`. Эту часть должно делать ядро, а не Desktop: CLI ставится и без GUI.
4. **Desktop:** переключатель в «Настройки → Ядро и MCP» (на канвасе он есть: Settings.dc.html, «Ядро и MCP») и индикатор в шапке («Агент · был 3 мин назад», TopBar.dc.html). Desktop вызывает `seohead mcp enable/disable`, а `status` читает из файла. Активность агента можно брать из `seo_project_observe` (consumer) или из журнала вызовов MCP.
5. Управление окном (`local_control`) включать автоматически при старте в `QStandardPaths.RuntimeLocation`, вместо диалога выбора папки, и публиковать указатель в общем файле.

**Не хватает:** всё из пунктов 2–5. Сейчас есть только транспорт и безопасный протокол управления окном. Ядро — 3–4 дня (команды и адаптеры трёх клиентов с бэкапами и тестами), Desktop — 1–2 дня.

### Упаковка и CLI в PATH
- **CLI в комплекте есть.** `scripts/build_macos.sh` собирает три PyInstaller-onedir: `seohead` (ядро), `seohead-desktop-agent` и `SEOHEAD Desktop`. Ядро кладётся в `Contents/Resources/core/seohead/seohead`, агент — в `Contents/Resources/agent/…`. Есть манифест с SHA ядра, `smoke_bundle.py`, ad-hoc `codesign`. Для Windows и Linux те же скрипты (`build_windows.ps1`, `build_linux.sh`) с раскладкой `resources/core/seohead/seohead(.exe)` (`packaging/bundle-spec.json`). Сборка проверена только на macOS; Windows и Linux не принимались.
- **Установщика нет**, PATH не трогается: «No system launcher is installed» (`docs/packaging.md:89, 121`). Подписи Developer ID и нотаризации нет — только ad-hoc.
- **Как поставить CLI в PATH:**
  - **macOS:** `.pkg` (`pkgbuild`/`productbuild`). Приложение уходит в `/Applications`, postinstall создаёт **wrapper-скрипт** `/usr/local/bin/seohead` с `exec "/Applications/SEOHEAD Desktop.app/Contents/Resources/core/seohead/seohead" "$@"`. Wrapper, а не симлинк: onedir-бутлоадер ищет `_internal` рядом с реальным путём, а wrapper не задевает проверку хэша бандла. Без прав администратора — пункт меню «Установить команду seohead» (как у VS Code) в `~/.local/bin` с подсказкой про PATH. Для раздачи вне своей машины нужны Developer ID и `notarytool`.
  - **Windows:** Inno Setup или WiX MSI. Добавить `{app}\resources\core\seohead` в пользовательский `PATH` (HKCU\Environment) и разослать `WM_SETTINGCHANGE`; ярлык в меню Пуск; деинсталлятор убирает PATH. Подпись Authenticode — по желанию, иначе предупреждение SmartScreen.
  - **Linux:** `.deb`/`.rpm` (fpm) с wrapper `/usr/bin/seohead` и `.desktop`-файлом, либо tar.gz плюс `install.sh` в `~/.local/bin` и `~/.local/share/applications`. AppImage для CLI неудобен.
- **Конфликт:** у Павла `seohead` уже есть в dev-venv `Work/tools/seotools/.venv`. Установщик не должен его перебивать. `seohead mcp status` должен показывать, какой бинарь активен и его commit (`--version`).

**Оценка: MCP 2/5, упаковка 3/5.** Установщик и PATH — 1,5–2 дня на macOS (без нотаризации), 2–3 дня на Windows, 1–2 дня на Linux. Плюс приёмка на реальных машинах.

---

## 6. Данные под экраны канваса (кратко)

| Экран | Нужно | Ядро | Desktop |
|---|---|---|---|
| Старт · проекты | MRU, открыть/создать | `project_open`/`project_new` есть | MRU есть (`recent_projects`), экрана нет; `project_new` не в allowlist |
| Работа · цель и задачи | KPI, задачи | `project_progress`, `checklist_page`, `task_detail` есть | есть (`work_page`) |
| Входящие | список, композер | `inbox_*` есть | list/submit есть; read/triage/ack/goal не в allowlist |
| Отчёты исполнителям | DOCX/XLSX, стадии | `report-build`, `scan-export`, `remediation-*` есть | **заглушка** (`app.py:573-585`), инструментов нет в allowlist |
| Новый скан · сайт/sitemap/список/SF | 4 источника | `crawl-site --urls/--urls-file`, `--sitemap-only`, `sf run` есть | в диалоге только «Спайдер» и «sitemap» (`app.py:2797-2800`); **списка URL и SF нет** |
| Сканы · наблюдение | живой монитор | `project_scans`, `scan_status`, `project_observe` есть | есть |
| Журнал | лента с пагинацией | пагинация запусков есть (`--run-offset/--run-limit`); **событий — нет**, максимум 200 на запуск (`seohead/projects/run_observation.py:28, 292`) | таблица и «Новее/Старее» по запускам есть |
| URL-инспектор A/B | фильтр и сортировка по всему скану | **`scan-url-query` нет**: `scan-inspect` даёт только offset/limit (`handlers.py:4208-4215`) | фильтр только по загруженной странице (`filter_urls`) |
| — исходящие ссылки | outlinks | **нет**: у `scan-link-inspect` только `path, inlinks, context` | только inlinks |
| — HTML-тело | исходный код | **нет** в `scan-url-detail` («no HTML body», `history_handlers.py`, `_scan_url_detail`). Обходные пути: `scan-snapshot`, `scan-extract`, `scan-content-search-page` (сниппеты) | `SourcePanel` показывает `source`, только если он пришёл (`ui/panels.py:49-80`) |
| — заголовки ответа | headers | в detail есть метаданные ответа | есть |
| Проблемы | проверки → задачи → подтверждение | `findings-view`, `remediation-*`, `verify-fixes` есть | `verify_fixes` в allowlist; findings и remediation — нет |
| Поиск в HTML | | есть | есть |
| Сравнение | KPI, сегменты | `compare-crawls`, `segment-diff`, `verify-fixes` | compare и verify есть; `segment_diff` нет; метрики «слов» и «входящих по сегменту» не проверены |
| Методы | каталог навыков | `skill-list/show`, `scenario-show` есть | **адаптера нет** |
| ⌘K / Новая вкладка / Настройка вкладок | | `project_view_*` есть | ⌘K и вкладки есть; сохранённые виды не подключены; экрана «Настройки» нет |

**Сделать в ядре:** `scan-url-query` (фильтры, сортировка, keyset-пагинация по `pages`), 3–4 дня; `outlinks` view в `scan-link-inspect`, 1–2 дня; ограниченное чтение тела (`scan-url-body --max-bytes`), 1–2 дня; постраничные события (`project-observe --event-offset` или отдельный журнал), 1–2 дня. **В Desktop:** адаптеры report, export, findings, remediation, skills и segment_diff, 4–6 дней.

---

## 7. Сводная готовность и порядок работ

| Область | Балл 0–5 | Почему |
|---|---|---|
| Темы (3 палитры, переключение на лету) | 3 | один источник, свойства, 0 hex в Python; нет схемы v2, палитр, менеджера и сброса кэшей |
| Каркас (шапка, вкладки, навигация, статус) | 2 | вкладки, сплиттеры и панели годятся; шапок две, навигация без секций, нет футера с настройками; монолит |
| Настройки (экран) | 1 | только `QSettings` для раскладки и motion; диалога нет |
| i18n RU/EN | 1 | механизм написан, не подключён; 1122 строки, русские ID |
| Режимы (проект / краулер) | 2 | ядро умеет ad-hoc краул и чтение; Desktop жёстко привязан к проекту |
| MCP-переключатель, общий статус | 2 | stdio-MCP и управление окном есть; нет общего файла состояния и команд `mcp status/enable` |
| Упаковка и CLI в PATH | 3 | CLI в бандле, манифест, подпись ad-hoc; нет установщиков, PATH и нотаризации |
| Данные под экраны | 3 | около 70% экранов покрыты ядром; 4 пробела в ядре, около 6 недостающих адаптеров |

### Порядок (вертикальные срезы)
0. **Каталог `desktop/` уже в монорепозитории** — собрать venv по `desktop/README.md`, прогнать тесты Desktop; i18n начать заново. *0,5 дня.*
1. **Деление `app.py`** на модули из раздела 2, без смены поведения. Зелёные тесты и `--capture`-снимки до и после совпадают попиксельно. *2–3 дня.*
2. **Срез «Темы»:** токены v2, light по канвасу, `ThemeManager`, сброс кэшей, dark и HC палитры, контраст-тест. *3–4 дня.*
3. **Срез «Каркас и Настройки»:** одна шапка 52 с крошками, вкладки 36, навигация 200/64 с секциями, счётчиками и кнопкой «Настройки» внизу слева, статус-бар 26; экран «Настройки» с разделами Общие / Вид (тема, плотность, детали снизу или справа) / Сканы / Ядро и MCP. Тема переключается вживую. *4–6 дней.*
4. **Срез «Язык»:** ID вместо русских ключей, перевод всех строк на `message()`/`bind()`, EN-каталог, `qtbase_ru/en.qm`, `QLocale`-форматирование, переключатель в Настройках. *5–8 дней.*
5. **Срез «Режим краулера»:** выбор режима на стартовом экране, библиотека сканов, `crawl-site --url/--urls-file --scan-out`, опрос `scan-status`, скрытие проектных разделов, «Сделать проектом». В ядре — наблюдение и пейсер без проекта, либо служебный проект. *Desktop 4–6 дней + ядро 2–3 дня.*
6. **Срез «MCP и CLI»:** ядро `seohead mcp status|enable|disable` + `mcp-state.json`; Desktop — переключатель, индикатор агента, автозапуск управления окном; установщик macOS `.pkg` с wrapper в PATH, затем Windows (Inno) и Linux (deb/tar). *Ядро 3–4 + Desktop 1–2 + установщики 5–7 дней.*
7. **Срез «Данные»** (параллельно с 3–6 в ядре): `scan-url-query` → URL-инспектор A/B по всему скану; outlinks; тело HTML; события журнала; адаптеры Отчётов, Проблем и Методов; источник «Список URL» и SF в «Новом скане». *Ядро 6–10 + Desktop 6–9 дней.*

**Итого:** около 35–55 рабочих дней (человек + агент) до полного соответствия канвасу с тремя темами, двумя языками, двумя режимами, MCP-переключателем и установщиками на трёх ОС. Минимальный показательный вариант — срезы 0–3 плюс macOS `.pkg` — около 12–16 дней.

### Решения Павла, без которых не начать
1. (Решено 09.10: Desktop — каталог `desktop/` в seohead-tools; старая сборка — мусор.)
2. Режим краулера: настоящий режим без проекта (правки в ядре) или служебный скрытый проект (быстрее)?
3. «Включить MCP» — это регистрировать ли Desktop правку конфигов Claude Code / Claude Desktop / Codex, и каких именно.
4. Установщики: только своя машина (ad-hoc) или раздача (Apple Developer ID и нотаризация, подпись Windows — платно)?
