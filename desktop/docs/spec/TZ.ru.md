> **Решение Павла от 07.10.2026:** полный отказ от веб-консоли, не перенос в backlog. Реализуются CLI/терминал и PyQt5/Qt Widgets; MCP остаётся доступом агентов. Все прежние требования W1 отменены.

# SEOHEAD Desktop: техническое задание и требования к дизайну

> **Назначение:** передать это ТЗ Claude Code, который будет проектировать визуальный дизайн и реализовывать приложение. Это требования к функционалу, дизайн-системе, компоновке, состояниям, быстродействию и проверкам. Готовых макетов, рисунков или сгенерированного интерфейса в комплекте нет. MD3/Site Kit — исходные требования; визуальную проработку выполняет Claude.


Дата: 2026-10-06. Статус: подготовлено для реализации и ревью; приложение ещё не реализовано и не принято. Проверенный исходный код: `~/Work/tools/seotools`, HEAD `6252dc2e13558e9e0fcaa70ee14a597ec0b760ec`. Документ локальный, на русском; публичные изменения репозитория оформляются на английском.

## 1. Принятое продуктовое решение

Основной новый интерфейс — самостоятельное настольное приложение на **Python + PyQt5 + Qt Widgets** для macOS, Linux и Windows. Веб-консоль полностью отменена и исключена из объёма работ. Пользовательские интерфейсы — приложение PyQt5 и CLI/терминал. CLI, локальный MCP и терминальный наблюдатель продолжают работать с тем же ядром и теми же проектами. Прямое решение Павла о desktop, CLI и полном отказе от веб-консоли заменяет прежний запрет интерфейсов в накопленных инструкциях; остальные правила доказательности, явных действий и сохранности данных сохраняются.

Назначение: рабочая консоль SEO-специалиста, который работает вместе с Claude или другим агентом. Человек выбирает проект, видит цель, задачи, источники данных, результаты и текущую работу, изучает URL и оставляет предложения. Агент ведёт работу через существующий MCP. Ручной запуск скана — дополнительный полноценный сценарий, а не обязательная замена агентной работе.

Электрон, встроенная веб-оболочка и QtWebEngine не используются. Окно и таблицы — Qt Widgets; QSS отвечает за визуальные токены. Рендеринг сайтов существующим опциональным crawler-компонентом может использовать внешний браузерный процесс: это источник измерений, а не движок интерфейса. Установка браузера для просмотра уже сохранённого проекта не требуется.

Скриншоты Screaming Frog — образец информационной архитектуры, плотности таблиц и управления панелями. Они не дают права объявить копию функций готовой: в них нет данных и измерений. Брендинг, код, ассеты и цветовая схема Screaming Frog не копируются. PyQt5 подходит для выбранного класса приложения; наличие библиотечных виджетов не доказывает скорость на больших сканах.

## 2. Уровни готовности и объём

| Этап | Пользовательский результат | Что требуется для приёмки |
|---|---|---|
| D0: инженерный пробник | Настоящее окно PyQt5, таблица с ленивой моделью, чтение небольшого существующего проекта | Запуск и ввод на трёх ОС, библиотечный lockfile, базовые RSS/latency; не макет в браузере |
| D1: MVP наблюдения | Выбрать проект, увидеть работу, открыть историю сканов и полноценно исследовать сохранённые URL | Чтение живых и завершённых артефактов, поиск/сортировка, инспектор, панели, ошибки, DPI и производительность |
| D2: работа специалиста и агента | Заметки/цели, статусы triage/read/ack, связанные задачи, методы и контракты | Настоящий MCP round trip с отдельным consumer; отсутствие автоматического исполнения текста |
| D3: ручное управление | Создать и подтвердить конфигурацию запуска, наблюдать, отменить/продолжить где поддерживается; получить отчёт | Preview равен submission; собственник/сайт/бюджеты; восстановление после restart; все три collector modes представлены честно |
| D4: desktop v1 | Упакованное приложение для macOS/Windows/Linux, удобное ежедневное использование | Полная платформенная матрица, реальные screenshots/input traces, инструкции, notices, обновление и rollback |

D1 — первый полезный релиз, а не сокращение общего ТЗ. D2–D4 остаются обязательным объёмом desktop v1. Режим URL-инспектора и проектная консоль входят в D1; заметки входят в D2; ручное управление входит в D3. Веб-консоль не входит в дорожную карту; работаем над CLI/терминалом и native PyQt5.

## 3. Основные пользовательские сценарии

1. **Открыть текущую работу.** При запуске — список явно открытых ранее проектов и «Открыть проект». Имя, домен, путь, время последнего наблюдения, наличие активного процесса. Открытие папки проверяет `project.json`; не создаёт проект, не инициализирует checklist и не начинает сбор. При переносе проекта предлагается указать его новый путь; совпадение UUID проверяется. Старые пути не сканируются рекурсивно.
2. **Работать рядом с агентом.** Открыть проект и раздел «Работа», увидеть принятую цель, текущие `custom:` задачи, приоритет, следующий шаг, причины блокировки, workflow и артефакты. Изменение агентом через MCP становится видимо без перезапуска окна; оно не сбрасывает выделение и не прерывает редактор заметки.
3. **Исследовать скан.** Выбрать сайт → запуск → «URL». В центре таблица, справа сводка, снизу детали выбранной записи. Клик по входящей/исходящей ссылке открывает соответствующий сохранённый URL в том же запуске и добавляет шаг в историю назад/вперёд. Ненайденная страница показывает «URL известен из ссылки; страница не сохранена». Открытие сайта в системном браузере — отдельное действие.
4. **Оставить замечание.** Из задачи, URL или finding создать заметку с явными ссылками на контекст. «Сохранить» возвращает ID inbox-записи. Отдельно показываются чтение агентом, triage, подтверждение обработки и принятие цели. Статус «Сохранено» не превращается в «Агент работает».
5. **Запустить скан вручную.** Из проекта выбрать collector и сохранённый профиль, проверить сайт, цель/задачу, scope, стоимость и лимиты, нажать финальное «Запустить». Виден реальный job/run ID. Тот же запуск доступен наблюдателю и MCP. Повторный клик/восстановление доставки не создаёт дубликат.
6. **Вернуться позже.** Окно можно закрыть; принятый durable job продолжает работу согласно его owner/backend contract. При открытии восстановлены проект, вид, фильтры и выбранный scan ID. Запуск, потерявший worker, отмечен как interrupted/abandoned/unknown согласно доказательствам. Автоматического повторного скана нет.
7. **Проверить результат и отчёт.** Перейти от finding к исходным данным, получить отчёт из сохранённого audit, увидеть охват и ограничения. «Отчёт сформирован», «проверен специалистом», «доставлен» — разные состояния. Наличие файла не подтверждает решение SEO-проблем.

## 4. Информационная архитектура и компоновка

Главное окно использует нативную системную рамку. Заголовок — `SEOHEAD · <проект> · <сайт>`. Верхняя полоса 56 logical px: переключатель проекта, домен/роль сайта, выбор сохранённого запуска, справа «Новый скан» и меню окна. Навигация 208 px, сворачиваемая в rail 64 px: «Работа», «Сайты», «Сканы», «URL», «Проблемы», «Методы», «Входящие», «Отчёты», «Журнал». Цель и работа не теряются среди SEO-вкладок.

Два именованных представления одного проекта:

- **Проект.** В центре компактные таблицы текущих задач и запусков; справа цель, следующий шаг и охват; снизу детали выделенной задачи/запуска. Никакой сетки огромных карточек вместо рабочей информации.
- **URL-инспектор.** Таблица URL занимает основную площадь; справа Overview/Issues/Structure/Response times; снизу вкладки выбранного URL. Это режим в стиле Screaming Frog с собственным дизайном SEOHEAD.

Основа — `QMainWindow` и вложенные `QSplitter`, `QStackedWidget`, `QTabBar/QTabWidget`; плавающие окна по умолчанию не нужны. `QDockWidget` допустим только если необходимое поведение скрытия/перемещения не решается штатным splitter и если пройдены native DPI/keyboard tests. При ширине <1100 px navigation сворачивается; правую и нижнюю панели можно открыть как одну активную auxiliary area. Минимальная рабочая область 960×640 logical px; 1280×800 и 1440×900 — основные acceptance layouts. При более узком окне доступен resize notice и меню открытия проекта; элементы не пропадают без объяснения.

### Управление рабочим местом

| Управление | Поведение |
|---|---|
| Скрыть/показать панель | Меню «Вид» и кнопка заголовка панели; действие не изменяет данные и не останавливает run |
| Развернуть таблицу | Временный focus mode скрывает боковую/нижнюю области; повторное действие возвращает прежние размеры |
| Splitter | Мышь и клавиатура; сохраняются последняя ненулевая ширина/высота; double click по handle возвращает default |
| Вкладки | Независимые списки для основной, нижней и правой областей; видимость, порядок, поиск, reset |
| Настройка вкладок | Диалог с «Доступные»/«Показывать»; checkbox и кнопки вверх/вниз доступны без drag; отмена не меняет layout |
| Скрытие выбранной вкладки | Фокус переходит к ближайшей видимой; нельзя оставить группу без доступного базового представления |
| Сохранённый вид | Именованный filter/sort/columns descriptor; registry finding views переиспользуется; URL-view — новый тип, не переименованный finding view |
| Несколько окон | Одно окно = один текущий проект; «Открыть в новом окне»; наблюдение одного проекта несколькими окнами разрешено; revisions защищают записи |
| Сброс | Отдельно «Сбросить этот вид» и «Сбросить все панели»; проектные данные не затрагиваются |

UI preferences хранятся в `QSettings` стандартного user config location, с `schema_version`, platform-neutral IDs, project UUID, geometry и ограниченным MRU ≤20. Это настройки интерфейса, не новая база задач. Геометрия проверяется относительно доступных экранов; отключённый монитор не оставляет окно невидимым. Персональные фильтры и размеры не пишутся в скан. Переносимые project saved views — через явное сохранение существующего project contract. Автообнаружение активного проекта из Claude без явного project ID/пути не обещается.

## 5. Компоненты, состояния и реальные источники

Все названия ниже проверены на указанном HEAD. «Новый адаптер» означает отсутствующий на этом HEAD интерфейс, который должен быть спроектирован/реализован отдельно и зарегистрирован через общую цепочку core → shared handler → CLI/MCP, когда это новая публичная возможность. Desktop не читает произвольные внутренние JSON/SQL в обход таких контрактов.

| Компонент | Состояния и данные | Уже существующий вход | Доработка для desktop |
|---|---|---|---|
| Выбор/карточка проекта | valid/missing/unsupported/corrupt; UUID, site, facts, refs | `servers/project_handlers.py:project_open`, `projects/workspace.py:open_project` | MRU adapter; открытие по explicit directory |
| Работа | running/blocked/stale/review/completed/not_agreed/excluded; scope, evidence | `project_progress`, `project_checklist_page`, `project_task_detail`, `project_checklist_update/record` | Qt projections; редактор custom task только через revision contract |
| Цели/inbox | saved/read/triaged/acknowledged; proposed/accepted/rejected | `project_inbox_submit/list/read/triage/acknowledge/goal/unread` | UI workflow; app не присваивает consumer ID Claude |
| Быстрые live-показатели | primary/competitor, measured/unknown/stale; rates/budgets/phase | `handlers.py:project_activity` → `observer.observe_activity` → `run_observation.status` | Coalesced reader; TTL и возраст измерения видимы |
| Общая сводка | checklist, coverage axes, policy, retained scans | `project_observe`, `project_progress` | Более редкая/по инвалидированию загрузка; не вызывать тяжёлый observe на каждый repaint |
| История сканов | native/SF/import/reanalysis, lifecycle, completeness, source | `project_scans`, `scan_list`, `scan_status` | Selected-run model; light metadata projection при дорогом status |
| Таблица URL | recorded URL/page identity, status, metadata, capability | `scan_inspect(table=pages)` пригоден для bounded initial read | **Новый `scan-url-query`**: projection, whole-result filters/sort, stable cursors, output/time budget |
| Детали URL | section-specific state, fields, source body and identity | `scan_evidence`, `scan_navigation`, `storage.bodies.read_document`, retained corpus | **Новый `scan-url-detail`**: per-URL join и bounded body/headers/structured sections |
| Inlinks/путь | retained occurrences, raw/rendered, coverage, limits | `scan_link_inspect(view=inlinks/path)` → `storage/link_queries.py` | Qt model; direction=outlinks требует расширения общего query, не полного чтения links |
| Findings | severity/check/URL, provenance, detail | `projects/observer.py:findings_page/finding_detail` и соответствующие shared handlers | Bounded models; устранить известные streamed/companion gaps до claiming complete |
| Saved finding views | definition/revision/coverage/segments | `project_view_list/show/save`, `observer.saved_view_page` | UI editor с preview; URL views — отдельная типизированная схема |
| Методы/контракты | catalogue ID/hash/version, текст, prerequisites, task evidence | `skill_list/show`, `scenario_show`, `projects/catalogue.py`, `runtime.playbook_*` | Чтение выбранного метода и ссылок; «зарегистрирован» отдельно от «выполнен» |
| Workflow | context, accepted goal, registered prompt, incomplete custom task IDs, checkpoints | `workflow_start/checkpoint/status/resume/execute`, `projects/execution.py` | UI отображение; `workflow_execute` лишь записывает supplied outcomes, НЕ запускает команды |
| Запуск/отмена | draft/preview/confirmed/queued/running/cancel_requested/terminal | `bot/wizard.py:WizardSession, JobSubmitter`, `job_contracts.py`, `remote_api/backend.py:SQLiteJobBackend`, `crawl_site`, SF handlers | **Local desktop submission/controller adapter** к общему backend; не локальный shell launcher в кнопке |
| Журнал | bounded tail, phase/run/site/time, truncation | observer log + run events + retained paths | **Paged log read adapter** если existing tail не поддерживает navigation/limits; без произвольного filesystem browser |
| Отчёты/экспорт | generating/available/partial/failed/reviewed, hashes/format/scope | `scan_export`, shared report handlers, `reports/`, `sf/reporters/` | Jobs/receipt UI; открыть файл через OS; исходный scan не overwrite |

Нельзя использовать `scan_status` каждые 500 ms на большом файле: текущий код проходит `pages` для outcomes. Нельзя полагать, что `scan_evidence` с `limit` уже ограничивает все его вложенные секции одинаково. Потребитель обязан проверить фактическую byte/time bound каждого используемого пути. `scan_inspect` использует offset и фиксированный order; сортировка только загруженной страницы не является сортировкой скана.

### Контракт новых URL-проекций

`scan-url-query(input, projection, filters, order_by, direction, cursor, limit=200, max_bytes=1 MiB, timeout_ms=1000)` — предлагаемая сигнатура, не существующая команда. Поля и операции — whitelist; SQL-параметры не строятся из пользовательского текста. Ответ: `schema`, `scan_uuid`, `evidence_revision`, `source_kind`, `query_fingerprint`, `rows`, `next_cursor`, `has_more`, `total {value,state}`, `coverage`, `warnings`, `truncation_reason`. Stable tie-breaker — scan-owned page identity. Успешный cursor привязан к query/scan/revision; просроченный cursor не смешивает snapshot, а предлагает обновить выдачу с сохранением selected URL. Count может быть unknown/pending; UI не блокируется ради COUNT по всему набору.

`scan-url-detail(input, page_identity, section, representation, cursor, max_bytes, timeout_ms)` возвращает ту же identity envelope и section state. Каждая секция загружается отдельно. Недоступное поле имеет `reason`; absent, null, measured empty и redacted различаются. Header/body join использует точный retained response/document ID, а не совпадение URL без времени и representation. Outlinks также возвращаются occurrence pages с лимитами и provenance.

Дорогие индексы не создаются при чтении source artifact. Для неподдерживаемой сортировки UI честно сообщает limitation либо запускает явно обозначенную bounded подготовку производного query cache; permanent second crawler/task database запрещена. Поиск по подстроке без индекса может быть дорогим: не обещать 200 ms на любом фильтре. Regex в MVP отсутствует; добавлять только с отдельным bounded engine contract.

## 6. URL-инспектор: данные и взаимодействия

Базовые колонки: адрес, HTTP status, content type, indexability/state/reason, title, description, H1, canonical, depth, retained in/outlink counts, response time и observed_at. Колонки availability формируются по capabilities конкретного источника. Неизмеренный счётчик — «Нет данных», не 0. URL identity exact, без самовольного lower-case path, удаления slash или склейки разных схем.

Основные группы: «Все URL», «Внутренние», «Внешние», «Ответы», «Метаданные», «Заголовки», «Контент», «Изображения», «Canonical», «Директивы», «Hreflang», «JavaScript», «Ссылки», «Структурированные данные», «Sitemap». Наличие группы показывает доступные данные и причины отсутствия, не обещает все проверки SF. Низкоприоритетные AMP/pagination/meta-keywords/validation представлены в настраиваемом каталоге возможностей, если в ядре есть подходящая projection; пустая декоративная вкладка не считается реализацией.

| Нижняя вкладка | Содержимое | Правило отсутствия/безопасного чтения |
|---|---|---|
| Сведения | URL, run/source IDs, metadata, timings, coverage, selected finding | Значения исходные; computed поля подписаны |
| Входящие/исходящие | URL, anchor, rel, position, representation, occurrence ID, source context | Показывать дубликаты; source/target navigation в сохранённом scan; graph partial не равен orphan |
| HTTP headers | Retained request/response, redirect hops, timestamp | Только сохранённые поля; redaction names без значений; новый запрос не возникает при клике |
| Cookies | Сохранённые безопасные cookie observations и их source | Нет cookies в retained contract → «Не сохранено этим источником»; не читать системный browser profile и не додумывать Set-Cookie |
| HTML | Retained static source как текст, hash, encoding, retention reason | `QPlainTextEdit`, bounded decoded bytes; удалённые ресурсы/JS не исполняются |
| Rendered DOM | Retained rendered source отдельно от static | Отсутствие Playwright не мешает чтению сохранённого DOM; если DOM не сохранён — reason |
| JSON-LD/Schema | Retained blocks, parse/validation results, location if measured | Не обещать Google rich-result eligibility; JSON pretty view не переписывает исходник |
| Ресурсы/Console/Navigation | Captured resource/console/route evidence, capture window, omitted counts | Только доступные retained sections; route event не доказывает human click |
| Проверки | Поддержанные, выполненные, skipped, failed/unavailable checks по URL | «Проверка зарегистрирована» не равно executed; все причины доступны |
| Происхождение | build/revision, settings fingerprint, scan/evidence/document IDs, timestamps | Секреты редактированы ядром; data origin не заменяется текстом агента |

При наличии сохранённого screenshot разрешён `QLabel/QGraphicsView` со статичной картинкой и масштабом. «Rendered page» без screenshot означает текст DOM/metadata, а не встроенный браузер. Rich text из сайта не передаётся в `QTextBrowser` с разрешёнными внешними ресурсами. Исходные code locations показываются только при реальной source mapping/repository evidence; селектор DOM не называется строкой Python/PHP/JS приложения.

Single click выбирает строку и обновляет нижнюю панель; double click открывает внутренние подробности. Link action «Перейти к сохранённому URL» отдельно от «Открыть сайт в браузере». Shift/Ctrl/Cmd selection, прямоугольное выделение ячеек, копирование TSV и «Копировать URL» обязательны. Большой экспорт проходит existing streaming exporter; Ctrl+A не materialize 1M строк. Копирование >10k ячеек предлагает экспорт, не зависает. В status line: выбранные строки/ячейки, displayed range, measured total or unknown, active filter и время snapshot.

## 7. Прогресс и честные состояния

Одновременно отображаются независимые native, SF и sitemap-based runs с `site_id/project_uuid/run_id`, mode, phase, source, budgets и measured rate. Один primary и competitor не сливаются в общие проценты. В каждом run: discovered/queued/inflight/committed/excluded/failed как разные величины; elapsed и resource limits; latest observation age; collector/controller liveness; result artifact и completeness.

Оси не смешиваются:

- «Обработано известных URL»: `done / (done + queued + inflight)` только при измеренных счётчиках; denominator растёт при discovery; лимит URL не является размером сайта.
- «Выполнение согласованных задач»: строго existing `coverage.audit_tasks`; без explicit agreed plan и измеренного ненулевого denominator — unknown с причиной.
- «Охват страниц/источников»: размер известной популяции и наблюдённая часть; sample подписан.
- «Исправление проблем»: ledger/remediation evidence отдельно от task completion.

Configured requests/s — «Лимит запросов/с». Measured recent rate — «Сейчас, запросов/с», с окном измерения и возрастом; скорость страниц/с и запросов/с не взаимозаменяемы. UI clock/animation не генерирует новые значения скорости. У SF counters отображаются только распознанные retained telemetry; иначе phase и «Счётчик недоступен». Завершённый процесс с partial scan не зелёный complete. Числа не рисуются как 0 перед первой загрузкой.

Общие состояния компонента: loading (первичная загрузка), ready, empty measured, not requested, unsupported, unavailable, partial, stale, failed, cancelled. Последние успешные данные при ошибке остаются с timestamp и заметным stale/error banner. Истёкшая liveness отметка не анимируется как текущая активность. Текст ошибки даёт действие: повторить чтение, выбрать другой scan, показать diagnostics; трассировки доступны в журнале, не захватывают экран.

## 8. Человек, агент, задачи и inbox

Источник задач — существующий checklist/coverage, не отдельная GUI task DB. `custom:` задачи связываются с принятым goal, зарегистрированным prompt/scenario, scope/site, dependencies и expected evidence. Completion записывается только через core и его evidence/review проверки. Приоритет показывает источник (operator/policy), время/ревизию и обоснование; auto-sort UI не меняет business priority.

Inbox composer принимает обычный текст, в том числе системную диктовку. Вложения — ссылки на retained evidence IDs, а не скрытая отправка файлов в LLM. Ограничение текста берётся из existing inbox schema. При storage failure/revision conflict draft сохраняется в памяти; приложение предлагает перечитать актуальное состояние и повторить явное сохранение. Закрытие с несохранённым draft спрашивает сохранить/отбросить/остаться; принудительное завершение может потерять несохранённое, это отражается в recovery help.

Настройка интеграции показывает process consumer identity и project allowlist. Утверждать «Claude подключён» можно только при реальном подтверждённом MCP interaction/receipt; наличие конфиг-файла недостаточно. GUI не читает чат Claude, не притворяется его consumer и не отправляет сообщения в чат. Агент получает unread summary на следующем project-scoped MCP call; полное чтение, triage, acknowledgment и goal acceptance — отдельные явные операции. Текст заметки не интерпретируется как shell command/prompt для автоматического dispatch.

При race GUI/MCP writes передают `expected_revision`; конфликт сохраняет draft и показывает актуальную revision, а не last-write-wins. Opening/selection/preview никогда не записывает read/ack за человека или агента. Изменение definition hash, scope или parent evidence отражает stale статус связанных результатов.

## 9. Ручной запуск, отмена и продолжение

Диалог «Новый скан»: проект/site → цель и связанная задача → collector → profile/config → limits/retention → preview → финальное «Запустить». Значения валидируются current core, включая robots/scope/network policy. UI не создаёт собственные default budget rules. Для домашнего local runtime login не нужен: пользователь работает под своей OS identity с локальными путями и явным выбором проекта. Секреты — только configured references; никаких credential values в preview/логах.

Preview содержит exact target и scope, collector mode, selected task/goal, config fingerprint, max URLs/requests/time, concurrency, rate ceiling, render policy/отдельные затраты, capture/store policy, destination и ожидаемые изменения проекта/файлов. Paid provider calls не входят в стандартный запуск; если профиль их включает, cost/spend authorization показывается отдельным реальным gate. После изменения поля прежняя confirmation устаревает.

Existing wizard contract различает `preview` и `confirming`; UI показывает review page и отдельную явную стартовую кнопку, соблюдая transitions. Нельзя тихо пропустить state только ради одного модального окна. Local adapter переиспользует existing `JobSubmitter`/durable backend; GUI не импортирует `remote_api.app` и не поднимает HTTP listener. Не нужен новый queue backend. Если current backend only native, SF/sitemap execution bindings добавляются в общий controller как отдельные verticals; ready status не заявляется до их контракта и тестов.

Повторный submit одного dispatch ID идемпотентен; намеренный повторный скан получает новый ID и preview. Отмена меняет state на cancel_requested до реального подтверждения worker; уже сохранённые данные остаются partial/interrupted. Native resume переиспользует существующий checkpoint/core с validation provenance/config. SF/sitemap показывают resume только при реальной поддержке collector/controller; иначе «Новый запуск по этим настройкам», с новым ID. Workflow resume возобновляет orchestration contract и не равен автоматическому network resume.

У активного запуска ownership проверяется перед управлением. Нельзя произвольно убивать PID чужого процесса или читать liveness PID как разрешение cancel. Для simultaneously native+SF+sitemap на одном origin нужен общий admission: суммарный resource/request budget; если общий throttle ещё не реализован, same-origin collectors ставятся в очередь, а не каждый получает полный лимит. Разные сайты показываются параллельно в общем observability screen; schedule по локальной capacity явный. Экспорт/SF/render не блокируют Qt event loop.

При выходе: observer-only окно закрывается сразу; при unsaved draft — решение о draft; подтверждённые durable jobs продолжаются с сообщением об этом. Если выбранное исполнение ещё связано с UI process lifetime, UI обязан предложить безопасную отмену и дождаться receipt; «продолжить в фоне» появляется только после доказанного durable ownership. Автоматическое воскресение uncertain execution после restart запрещено.

## 10. Qt-архитектура и ресурсы

Предлагаемые модули внутри optional desktop package: entry/app shell; Qt views; `QAbstractTableModel` implementations; adapters to shared handlers; worker dispatch; UI preferences; theme/icons. Это границы ответственности, а не требование создавать framework с десятками абстракций. Core не импортирует Qt. CLI/MCP без desktop extra не загружают Qt и продолжают проходить registration tests.

`QApplication` и все QWidget/model mutation живут в main thread. UI thread не выполняет DB queries, filesystem stats больших деревьев, hash validation, network/crawl, body decompression, report parsing или full audit load. `data()` модели возвращает cached display values или placeholder, никогда SQL. Bounded worker создаёт и закрывает свои соединения в том же thread; connection/transaction не передаётся через signals. Qt queued signals несут небольшие immutable result envelopes.

Для D1 достаточно одного light reader worker и одного serial mutation worker. CPU-heavy existing analyzer/report/crawl изолируется в existing job worker/process, чтобы Python GIL не превращал QThread в ложную гарантию отзывчивости. Встроенный запуск через shell text запрещён; process command/argv формируются типизированно там, где этого требует existing backend. Панель «Диагностика» показывает UI/reader/job/render/SF PIDs и версии без секретов.

Таблица — `QTableView` + `QAbstractTableModel` + `QStyledItemDelegate`. Нельзя создавать `QTableWidgetItem`/widget на каждую строку/ячейку, загружать все страницы в pandas или использовать proxy sort над неполным cache как global sort. Первоначальная page size 200, prefetch соседней страницы; cache максимум 2,000 decoded rows и 16 MiB (первое достигнутое ограничение). LRU cache привязан к scan UUID/revision/query/projection. Предпросмотр отдельного текста ≤2 MiB decoded по умолчанию, explicit bounded bigger view ≤8 MiB; link/detail page ≤1 MiB. У source reader может быть более строгий предел — его не обходить.

Request queue: ≤8 reader requests, одна latest request для каждого component key; старые selection/filter responses отбрасываются по generation token. Active request отменяется cooperative SQLite progress handler/interrupt в owning worker, где поддерживается. Даже без немедленной отмены UI не принимает результат от прежнего URL. Mutation queue ≤1 active + explicit pending state; повторные clicks не копятся. Signal coalescing ≤10 visual updates/s; latest snapshots заменяются, terminal outcomes/receipts не теряются. Logs — bounded ring ≤2 MiB/5,000 отображаемых строк, с указанием отброшенного старого диапазона; сам исходный файл не обрезается.

Refresh: lightweight activity target 500 ms при visible window, без overlapping polls; full project projection 2–5 s или invalidate; selected scan table обновляется только при изменении identity/revision; скрытая вкладка не перечитывает данные. Watcher — hint, periodic bounded check — fallback; WAL/SHM/audit companion/config/state входят в invalidation identity. Рисование часов каждые 500 ms не означает новое измерение collector. Свёрнутое окно снижает refresh до 2–5 s, не влияет на collector.

Read transactions краткие: page snapshot → bounded rows → close → signal. Между clicks/polls transaction не удерживается; долгий reader не удерживает WAL без бюджета. `with sqlite3.Connection` сам по себе не закрывает соединение; требуется `closing`/`finally close` по принятому core contract. Известные найденные review проблемы SQLite lifecycle и streamed segment/coverage companion — prerequisites исправления/проверки используемых путей, UI не скрывает их кешем. Частота обновления не оправдывает 27 утечек соединений или неполную сводку.

## 11. Бюджеты производительности и доказательства

Числа ниже — **целевые критерии будущей приёмки**, не текущие измерения. Для каждого прогона сохраняются SHA, OS/build, CPU/RAM/диск, display scale, Python/PyQt/Qt/SQLite версии, fixture hash/row counts/edge density/body sizes, cold/warm condition, process tree RSS, latency percentiles, peak disk/WAL и screenshot/video/input trace. P95 — минимум 200 операций; startup — минимум 10 запусков. RSS Linux/macOS/Windows нормализуется в MiB с указанием метода; сумма process RSS подписывается как сумма, shared pages могут считаться повторно.

| Стадия/операция | Цель на baseline 4 CPU cores, 16 GiB RAM, SSD | Условия |
|---|---|---|
| Cold window shell | ≤2.0 s usable shell; ≤4.0 s first project rows | Без crawl/report imports в UI; packaged one-dir; 5k fixture |
| UI process idle RSS | ≤180 MiB после открытия shell; ≤256 MiB после 30 min inspection | Отдельно от core/worker; D0 подтверждает реалистичность, изменение цели требует объяснения |
| Reader worker RSS | ≤320 MiB peak на 50k/1M synthetic query fixtures | Структура зависимостей может требовать process split; не считывать все body/page models |
| Input/cached selection | P95 ≤50 ms; event-loop stall max ≤200 ms | Во время чтения, update storms, resize и worker failure |
| Indexed page/filter/sort | P95 ≤300 ms warm 50k; ≤750 ms 1M | Existing index paths, 200 rows/1 MiB; expensive substring отдельно |
| Неиндексированный запрос | Cancel visible ≤100 ms; deadline ≤2 s, далее partial/too expensive | Не выдавать page-local filtering за global result |
| URL detail metadata | P95 ≤300 ms warm; text preview ≤750 ms | Detail ≤2 MiB decoded; representation/source checked |
| Scroll | P95 frame/input delay ≤50 ms, cache miss показывает placeholders | Нет synchronous queries и repaints всей таблицы |
| Activity freshness | UI видит доступный новый telemetry snapshot ≤1.0 s P95 | Target poll 500 ms; измеряется producer timestamp, не animation |
| Steady-state cache/handles | После 1,000 URL switches рост UI RSS ≤32 MiB; handles/DB connections возвращаются к baseline ±5 | После стабилизации cache; ownership validated |
| Queue pressure | 10,000 synthetic updates/min в течение 10 min без неограниченного роста | Coalescing допускается, terminal outcomes и receipts сохраняются |
| UI cost during collection | На 5k controlled fixture slowdown collector ≤10% относительно observer-off | Raw run отдельно от rendered/SF; одинаковый budget and response fixture |

Core analyzer/RSS, live native, rendered browser и SF memory измеряются **отдельно**, не спрятаны в 256 MiB UI budget. Их реальные current limits берутся из existing settings/profile и показываются перед запуском. UI overhead не повышает текущий public live URL ceiling. `docs/SCAN_CAPACITY_PROFILE.md` содержит завершённый synthetic sparse 1M storage test; он не доказывает 1M live fetch, dense links, rendered capture или full audit/report. Existing 10k/50k whole-path ограничения в `SQLITE_ACCEPTANCE.md` остаются отдельными release findings до нового evidence.

Fixtures: F5 — 5k pages, Unicode/long URLs, mixed statuses, missing/null/empty/redacted fields; F50 — 50k actual retained synthetic pages + bounded dense links, concurrent writer; F1M — 1M actual synthetic query rows через допустимый native storage fixture, явно `synthetic viewer capacity`, без live site claims; FP — interrupted/partial, missing companions, stale artifacts; FC — two projects/three sites/parallel native+SF+sitemap telemetry; FE — DB locked/corrupt/permission denied/disk full/slow reader. F1M seeded frontier с 20k pages не засчитывается как 1M page-table acceptance.

## 12. Дизайн: MD3 Site Kit для Qt

Дизайн опирается на Site Kit: светлая рабочая среда, primary `#1565C0`, фон `#FDFCFF`, Roboto, база отступов 4 logical px. Это адаптация принципов Material Design 3 к Qt Widgets, не официальный Google MD3 Qt-компонентный набор. Google Sheets/Excel — reference для читаемости и selection; их бренд/пиксельная копия не требуются.

| Токен | Значение/правило |
|---|---|
| Surface/base/raised | `#FDFCFF` / `#FFFFFF` / `#F4F6FA` |
| Primary/on-primary/selected | `#1565C0` / `#FFFFFF` / `#E3F0FF` |
| Text/secondary/outline | `#1B1B1F` / `#45464F` / `#74777F`; тонкие dividers `#DDE1E7` |
| Semantic | Error `#B3261E`, warning `#7A4F00`, success `#246B3C`; всегда icon+text, не только цвет |
| Typography | Roboto 13 logical px body/table, 12 metadata, 14 controls, 20 section title; системный fallback при невозможности загрузки font |
| Density | Default row 32 px; compact 28; comfortable 40. Header 36; text поля ≥36; primary buttons 36–40 |
| Spacing/radii | Multiples of 4; gaps 8/12/16/24; radii 4/8 inputs, 12 menus, 16 dialogs, 28 prominent pills; таблица без круглой карточки вокруг каждой строки |
| Elevation | Main workspace flat; лёгкая отделённость popover/dialog, без постоянных тяжёлых shadows |
| Focus | Видимая 2 px outline, logical order, не стирается QSS; selected row и keyboard focus различаются |

Таблица: тонкие вертикальные separators по необходимости, умеренные alternating rows, header contrast, right-aligned numbers с одинаковой разрядностью, middle-elide только для URL с full tooltip/copy, multiline detail для полной строки. Status badges компактные, без эмодзи. Нет градиентов, декоративных иллюстраций вместо empty-state текста, бесконечных shimmer и dark theme в данном ТЗ. System high-contrast должен сохранять читаемость; если system palette required, accessibility variant имеет приоритет над точным цветом.

QSS централизован через theme tokens; не лепить style strings по каждому экрану. Widget states normal/hover/pressed/focus/disabled/error/read-only определены один раз. Нативные menu/file dialogs/scrollbars сохраняются, если стилизация ухудшает platform input/accessibility. Анимации ≤120 ms и только для affordance; reduced motion отключает их. Сканы не используют непрерывный декоративный spinner при stale status.

### Иконки и собственный знак

Material Symbols **Outlined**, optical size 24, weight 400, grade 0, fill 0; статичные SVG ресурсы, упакованные локально. Системные font ligatures не нужны: не будет квадратов при отсутствии icon font. Active state — tinted container и label, не смешение outlined/filled наборов. Hover/pressed state задаётся control; disabled — заданный disabled foreground с доступным tooltip. Decorative icons скрыты от screen reader; action имеет accessibleName и tooltip.

Официальный источник: `https://github.com/google/material-design-icons`, каталог `symbols/web/<name>/materialsymbolsoutlined/`; конкретный файл/commit/hash и Apache-2.0 notice фиксируются в asset manifest при добавлении. Имена ниже — выбранный registry vocabulary; implementation проверяет существование каждого upstream SVG и не подменяет похожим third-party пакетом. Roboto пакуется отдельно с license именно выбранного font artifact.

| Semantic key | Material Symbol | Русская подпись/tooltip |
|---|---|---|
| project.open | `folder_open` | Открыть проект |
| project.switch | `workspaces` | Выбрать проект |
| work.tasks | `checklist` | Задачи проекта |
| work.goal | `flag` | Цель и согласованный объём |
| sites | `language` | Сайты проекта |
| runs | `manage_search` | Сохранённые сканы |
| scan.start | `play_arrow` | Новый скан / Запустить после проверки |
| scan.cancel | `stop_circle` | Запросить остановку |
| scan.resume | `resume` | Продолжить сохранённый запуск |
| scan.history | `history` | История запусков |
| url.table | `table_view` | Таблица URL |
| issues | `rule` | Найденные проблемы |
| methods | `menu_book` | Сценарии и навыки |
| inbox | `inbox` | Заметки и цели |
| note.add | `edit_note` | Добавить заметку |
| reports | `description` | Отчёты |
| logs | `terminal` | Журнал работы |
| search | `search` | Поиск в текущем наборе |
| filter | `filter_list` | Фильтры |
| sort | `sort` | Сортировка |
| view.columns | `view_column` | Колонки и их порядок |
| view.settings | `tune` | Настроить представление |
| view.sidebar | `side_navigation` | Показать/скрыть навигацию |
| view.inspector | `bottom_panel_open` | Показать детали |
| view.expand | `fullscreen` | Развернуть рабочую область |
| view.restore | `fullscreen_exit` | Вернуть панели |
| navigation.back/next | `arrow_back` / `arrow_forward` | Предыдущий / следующий сохранённый URL |
| external.open | `open_in_new` | Открыть в системном браузере |
| copy | `content_copy` | Копировать |
| export | `download` | Экспортировать сохранённые данные |
| source | `code` | Сохранённый исходный текст |
| headers | `http` | HTTP-заголовки |
| cookies | `cookie` | Сохранённые сведения о cookies |
| links | `link` | Сохранённые связи |
| info/success/error | `info` / `check_circle` / `error` | Сведения / Выполнено с указанным охватом / Ошибка |
| warning/stale/unavailable | `warning` / `update` / `help` | Ограничение / Устарело / Нет измерения |

Собственный знак SEOHEAD должен спроектировать Claude: компактный, читаемый в 16/24/32 px, согласованный с Material Symbols и primary palette. Готовый SVG-концепт не приложен; создание знака относится к последующей дизайнерской работе. Для application icon экспортировать `.icns`/`.ico`/PNG нужных размеров при packaging; source SVG остаётся каноном. Не использовать лягушку, Google G и эмодзи.

## 13. Клавиатура, доступность и локализация

QKeySequence StandardKey для Open/Copy/Find/Close; Cmd на macOS и Ctrl на Windows/Linux. `Cmd/Ctrl+O` — открыть проект, `Cmd/Ctrl+F` — поиск текущей таблицы, `Esc` — закрыть transient view/отменить draft dialog, не остановить scan. `Alt+Left/Right` — история URL (на macOS разрешён стандартный platform equivalent); `F6` — области; `Tab/Shift+Tab` — controls; arrows/page/home/end — таблица; Enter — detail; Space — checkbox. Отдельная команда меню запуска/отмены без опасной односимвольной клавиши. Global shortcuts действуют только в пределах приложения.

Весь путь открыть проект → скан → URL → фильтр → детали → заметка → возврат доступен без мыши. Заголовки таблиц, row/column counts, selected cells и статусы доступны Qt accessibility. Проверки VoiceOver/NVDA/Orca включают чтение header и строки, имена icon-only buttons, focus после dialog, отсутствие ловушки клавиатуры. DPI 100/125/150/200%, resize, mixed-DPI screens; размеры — logical px, font scale не режет текст. Контраст обычного текста ≥4.5:1, controls/focus ≥3:1, без reliance on color.

UI по-русски, sentence case; stable IDs, code/errors contracts на английском с человекочитаемым переводом в presentation layer. URLs, paths, identifiers сохраняют оригинал. Русские, латинские, IDN и длинные строки проверяются. Исходный clipboard текст не нормализуется незаметно. Qt `.ts/.qm` допустимы для последующей английской локали; обязательна отделимость UI strings от business logic.

## 14. Платформы, сборка и распространение

PyPI на дату проверки предоставляет PyQt5 5.15.11 wheels для macOS ARM64/x86_64, Windows x86_64 и Linux x86_64. Это подтверждает наличие пакетов, а не успешный запуск SEOHEAD. Начальный desktop runtime target — **CPython 3.12**, PyQt5 5.15.11; точные PyQt5-Qt5/sip/Qt plugins lockfiles фиксируются отдельно для OS после D0 probe. Нынешний local Python 3.14 не объявляется поддержанным без native test. Существующий core Python ≥3.10 не сужается только ради GUI; GUI extra может иметь более узкий runtime support.

| Платформа desktop v1 | Обязательная проверка | Упаковка/ограничение |
|---|---|---|
| macOS ARM64, minimum target macOS 13 | Native launch, menu/shortcuts, Retina, open/save, Unicode path, restart, RSS | Отдельная arm64 `.app`; development build не выдаётся за notarized release |
| macOS x86_64, minimum target macOS 13 | Те же основные сценарии на Intel/runtime, не только cross-build | Отдельный x64 build; universal2 не обещается автоматически |
| Windows 11 x64 | Native launch, paths с пробелами/кириллицей, locks, 100/125/150/200% DPI, clipboard, restart | One-dir bundle с launcher; installer/signing отдельный release шаг |
| Linux x64, Ubuntu 24.04 baseline, X11 | Native visible window, Qt plugin deps, clipboard, file dialogs, fonts, locks | One-dir/AppImage выбор после D0; dependency manifest и clean-machine smoke |
| Linux x64, Wayland session | Native Wayland plugin при наличии в выбранной сборке; если используется XWayland, это явно указано и проверено | XWayland compatibility не называется native Wayland; выбранная support lane фиксируется до D4 |

Windows ARM64/Linux ARM64/старые OS — отдельный последующий compatibility scope; требование трёх OS выполнено только после указанных native lanes. Нельзя объявить Windows готовым на основании macOS screenshot или Linux `offscreen` test. CI virtual display пригоден для headless widget regressions, но не подтверждает window manager, native input, accessibility, DPI и visual acceptance.

Упаковка: первично PyInstaller one-dir, сборка на каждой target OS/architecture. One-file self-extraction не выбирается ради красивого одного файла, если ухудшает startup/AV/temporary disk footprint. Передача Python с bundle избавляет конечного пользователя от Python setup. UI package содержит выбранные Qt plugins/SVG/fonts/notices; render browsers и SF не становятся обязательными компонентами observer-only installation. Отдельный `desktop` extra и entrypoint; базовая установка CLI/MCP не тянет Qt.

Update: desktop показывает version/build и путь к release instructions; автоматическая установка/самозамена не входит в D1. D4 rollback сохраняет projects и settings, migrations только explicit/versioned. Подписание/notarization/покупка сертификатов/лицензий не выполняются в рамках этого ТЗ и не предполагаются уже существующими. Не менять OS security settings ради запуска тестовой сборки.

PyQt доступен по GPLv3 либо коммерческой лицензии Riverbank; это не LGPL-binding. Core сейчас MIT. До публичного desktop distribution фиксируется конкретный лицензированный вариант bundle и obligations, включая Qt и assets. Отдельный optional package сам по себе не доказывает юридическую изоляцию. Для GPL-варианта комплект поставки должен соответствовать его условиям; для закрытого distribution рассматривается commercial license. Разработка и локальный D0 не требуют заранее оплачивать лицензию. Никакое license clearance этим документом не объявляется.

## 15. Ошибки, сохранность и внешние действия

Чтение проекта/URL не выполняет сеть. Saved artifacts считаются недоверенными данными: bounded decode/parse, no JS/commands, запрещены path traversal и произвольные external URI schemes. File opener разрешает OS-open только выбранного локального артефакта; URL opener — HTTP(S) и явный пользовательский click. Inputs с секретами не попадают в diagnostics export.

Corrupt/unsupported scan не мигрируется автоматически; сообщается version/reason и доступны другие проекты/сканы. Busy SQLite не превращается в empty state; retry bounded, UI живой. Snapshot live SQLite — через existing Backup API, не копирование `.sqlite` без WAL. Disk full сохраняет последние committed data и честный terminal failure. Error recovery не удаляет scan/companion.

Opening existing project, editing layout и чтение локальных данных обратимы. Запуск скана имеет explicit preview/confirm как часть продукта. Удаление/prune, публикация, платный provider, cloud write, отправка отчёта требуют собственного осмысленного действия и existing core gates; они не привязаны к обычному «Сохранить вид». В D1/D2 remote transport отсутствует. Не делать произвольный terminal/shell editor внутри приложения: журнал — display, команды — whitelist capability actions.

## 16. Этапы реализации малыми вертикалями

1. **D0 capability spike.** Optional dependency/entrypoint, blank native shell, one cached table model, worker reader, native 3-OS startup and versions. Записать baseline latency/RSS до визуальной полировки. Не внедрять все экраны одновременно.
2. **D1a project observer.** Open project, MRU, project_activity + progress + scans; один проект и два реальных concurrent processes. Empty/missing/corrupt/running/stale states; restore window. Пакетные core registration tests остаются зелёными.
3. **D1b URL vertical.** Новый bounded query/detail core contract, CLI/MCP parity, model-backed table, full-result sort/filter, inlinks/outlinks/body/provenance. 5k → 50k → 1M synthetic viewer fixtures. Сначала доказать запросы/selection, затем saved views/tab configure.
4. **D1c workspace design.** MD3 tokens, three-pane layout, panel/tab settings, keyboard/DPI/accessibility, собственный brand SVG и asset manifest. Применить review по SF reference; не переносить неподдержанные функции как работающие.
5. **D2 inbox and work.** Compose/save/receipt/revision conflict, goals/triage/task links, method details; реальный stdio MCP consumer получает заметку и записывает scoped task/evidence, GUI показывает результат.
6. **D3 native manual lifecycle.** Config preview/confirm → existing job backend → activity → cancellation/resume → saved report; two-process restart/idempotency и same-origin admission. Затем SF и sitemap bindings с их отдельными capabilities.
7. **D4 packaging.** Three OS/required architectures, clean-machine launch, native keyboard/DPI/screenshots, resource benchmarks, notices, update/rollback guide. Public docs boundary меняется вместе с реально принятой функциональностью, не заранее.
8. **Граница интерфейсов.** CLI/терминал и native PyQt5 используют одно ядро; local stdio MCP обслуживает ИИ-агентов. Отдельная браузерная консоль отменена и не реализуется.

Ревью каждого slice включает source/contract tests и наблюдение настоящего окна. Макет, зелёные unit tests, screenshot пустого окна и report агента по отдельности не приёмка. Известные core blockers исправляются в своих небольших source changes до подключения затронутых UI capabilities; незатронутый D0/D1 observer может идти параллельно.

## 17. Приёмочная матрица и правило выпуска

Claude должен оформить требования разделов 3–16 в приёмочную матрицу given/when/then, stage, expected evidence и исходный status `not_run`. Отдельно проверяются functional, correctness, performance, error/recovery, MCP и native platform checks. Готовая JSON-матрица в текстовый комплект не включена. Ни одна строка не считается passed лишь потому, что требование описано в ТЗ.

Критический пользовательский маршрут desktop v1: открыть существующий проект → увидеть актуальную агентную работу → выбрать правильный сайт и скан → найти URL → проверить source/provenance/coverage → написать заметку → увидеть реальный triage и task → вручную подтвердить новый допустимый scan → отменить или дождаться terminal outcome → открыть сохранённый отчёт → перезапустить приложение и продолжить с тем же контекстом. Маршрут выполняется на каждой обязательной OS lane, а collector-specific части — где есть соответствующий лицензированный/установленный collector с явным supported/unavailable результатом.

Release evidence содержит support matrix pass/fail/unavailable с ссылками на traces, versions, metrics и artifacts. Недоступный SF не блокирует native observer, но не позволяет объявить SF lane accepted. Неизмеренная производительность не заменяется обещанием «не лагает». Acceptance report перечисляет фактические пределы, включая актуальные whole-path core limitations.

## 18. Проверенные источники и границы этого исследования

- Репозиторные источники на указанном HEAD: `AGENTS.md`, `pyproject.toml`, `docs/PROJECTS.md`, `docs/TERMINAL.md`, `docs/STORAGE.md`, `docs/PLATFORMS.md`, `docs/SCAN_CAPACITY_PROFILE.md`, `docs/SQLITE_ACCEPTANCE.md`, `docs/GUIDED_SCAN_ADAPTER.md`, `.claude/skills/control/SKILL.md` и перечисленные в таблицах modules.
- Дизайн-канон: `~/Work/.claude/skills/site-kit/SKILL.md`; карта проекта: `~/Work/.claude/skills/seohead/SKILL.md` и current reference. Старые counts/пути из reference не перенесены в актуальные claims.
- [PyQt5 PyPI](https://pypi.org/project/PyQt5/) — версия 5.15.11 и platform wheels; [Riverbank: PyQt и лицензии](https://www.riverbankcomputing.com/software/pyqt/) — GPLv3/commercial, platform scope.
- [Qt 5.15 Model/View](https://doc.qt.io/archives/qt-5.15/model-view-programming.html), [Threads and QObjects](https://doc.qt.io/archives/qt-5.15/threads-qobject.html), [High DPI](https://doc.qt.io/archives/qt-5.15/highdpi.html) — основа выбранных Qt механизмов; application budgets/UX решения настоящего ТЗ являются проектными требованиями, не обещаниями Qt.
- [Material Symbols guide](https://developers.google.com/fonts/docs/material_symbols), [официальный icon repository](https://github.com/google/material-design-icons), [Roboto source](https://github.com/googlefonts/roboto-3-classic) — происхождение assets; выбранные файлы и notices фиксируются при реализации.
- [PyInstaller operating mode](https://www.pyinstaller.org/en/stable/operating-mode.html) — native per-platform build и bundled Python; не доказательство готовой сборки SEOHEAD.
- Четыре пользовательских screenshot references и read-only SF 19.8 AX audit независимого агента: подтверждены three-pane/tabs/settings patterns; набор пуст, populated-row behavior/performance не проверены. Capture ScreenCaptureKit `-3811` не позволил принять screenshots живого SF; это ограничение evidence, не основание заявить visual acceptance.

При составлении данного ТЗ не устанавливались программы и не создавался интерфейс приложения. Проверки и изменения SEOHEAD, выполненные в других этапах работы, не являются приёмкой ещё не реализованного desktop-клиента. Этот комплект содержит только текстовое ТЗ. Визуальный дизайн и макеты должен разработать Claude; графика и интерфейс в этой работе не создавались.


## Референс фирменного дизайна сайта SEOHEAD

Перед проектированием прочитай `Дизайн_сайта_SEOHEAD.md` и папку `Референс_сайта/`. Там извлечённая система действующего Next.js-сайта. Рекомендуемая основа — **SEOHEAD Desktop**: фирменные цвета/Roboto/Material Symbols сохраняются, компоновку и плотность адаптируй под Qt. MD3/Site Kit служит референсом, а не обязательной копией всех веб-компонентов. Это уточнение имеет приоритет над формулировками раздела 12 о строгом следовании всем MD3-компонентам. Дизайн и макеты создаёт Claude; здесь добавлены только текст и исходные примеры.

## Обязательный формат работы Claude: функциональный дизайн PyQt5 + QSS

Это уточнение определяет формат дизайнерского результата. **Claude проектирует дизайн-систему и все основные экраны как запускаемый нативный прототип PyQt5, оформленный QSS.** Картинки, HTML-макет, Figma-файл или описание сами по себе не заменяют этот результат. На данном этапе поручена работа Claude; текущий комплект ТЗ не содержит созданного приложения.

### Разделение ответственности

- **QSS:** внешний вид виджетов и их подэлементов — цвета, шрифты, рамки, радиусы, отступы, selected/hover/pressed/focus/disabled и семантические состояния.
- **Qt Widgets + layouts:** настоящие окна, панели, таблицы, вкладки, диалоги, расположение, растяжение, минимальные размеры и сворачивание. Использовать QMainWindow, QSplitter, QDockWidget, QTabWidget, QTableView, QBoxLayout/QGridLayout и другие подходящие Qt-компоненты.
- **Python/signals/controllers/models:** выбор URL, переключение режимов, фильтры, сортировка, открытия/закрытия, валидация и взаимодействия. Данные для таблиц идут через QAbstractTableModel, не через widget на каждую ячейку.

В QSS нет веб Flexbox/Grid, CSS custom properties var(), обычных CSS transition/transform/box-shadow. Нельзя описать весь интерфейс одним QSS. Анимации, если нужны, реализовать средствами Qt; тени применять выборочно и измерять стоимость. Qt layouts и styles должны образовывать единый функциональный дизайн.

### Дизайн-система, пригодная к реализации

1. Использовать `Дизайн_сайта_SEOHEAD.md` и извлечённые source tokens как брендовый референс. Система приложения — **SEOHEAD Desktop**: цвета/Roboto/Material Symbols сохраняются, плотность и композиция адаптируются под рабочую консоль. Полная копия всех MD3/web-компонентов не обязательна.
2. Создать один канонический набор токенов для приложения: semantic colors, typography, spacing, shapes, density и допустимое motion. У каждого токена есть назначение; не рассыпать hex/размеры по Python-коду отдельных экранов.
3. Из токенов получать согласованные QSS-файлы/строки и Qt-параметры layout. Подстановку значений выполнять в Python/поддерживаемом механизме проекта, без обещания браузерного var().
4. Описать компоненты: primary/secondary/destructive actions, fields/search, chips/filters, badges/status/counts, tabs, headers/cells, navigation, inspector, toolbar, splitter, dialogs и feedback. Для каждой группы дать размеры, состояния, keyboard focus, доступное имя и поведение.
5. Для вариантов применять понятные objectName/dynamic properties, например semantic role, status и density. Предусмотреть обновление affected widget style при изменении свойства. Не переустанавливать весь application stylesheet на каждом обновлении прогресса.
6. Material Symbols использовать как самостоятельный согласованный набор. Подготовить нужные лицензированные локальные ресурсы и registry. Не зависеть от CDN/icon-font availability и не заменять действия случайными emoji.
7. Системная рамка окна и OS-адаптация остаются нативными. Не реализовывать окно как нарисованную картинку с фиктивными кнопками.

### Какие экраны должны стать функциональными прототипами

- Выбор проекта и восстановление последнего рабочего контекста.
- Проектная консоль: задачи, цели, методы/скиллы, согласованное покрытие, активность и история.
- URL-workbench: настоящая таблица, источники/фильтры/сортировка и detail-pane выбранной строки.
- Детали URL и снимков: доступные измерения, исходные доказательства, извлечение, provenance; явно показать недоступные данные.
- Сравнение сканов, finding/remediation/recheck-контекст.
- Заметки и inbox со стадиями read/triage/ack/accept, без смешения с выполнением задачи.
- Подготовка ручного скана: настройки, scope/effects/resource preview и явное подтверждение.
- Наблюдение native/SF/sitemap, отмена/продолжение и честные terminal/partial/stale состояния.
- Просмотр/подготовка отчёта и инженерного handoff; состояние недоступного формата/выгрузки.
- Настройки видимости/порядка вкладок, плотности и сохранения рабочей области.

Прототип может разрабатываться последовательными вертикалями, но все перечисленные экраны остаются объёмом ТЗ. Каждый прототип должен использовать переиспользуемые компоненты и ту же систему токенов, которую применит приложение.

### Данные и проверяемые взаимодействия

Для design/demo режима использовать небольшие синтетические fixtures и явно подписанный режим «Демо». Не запускать сеть, реальные сканы или платные действия ради проверки дизайна. Demo provider не создаёт второй crawler/analyzer/task registry и не выдаёт придуманные данные за результаты проекта.

В демо обязаны работать: выбор строки и её detail, tab/mode switching, hide/show/resize/restore panels, column visibility, search/filter/sort по declared dataset, раскрытие контекста, input validation и draft handling. Кнопка подтверждения скана показывает предусмотренный сценарий/receipt в демо и не запускает настоящий crawler. При подключении реального ядра сохраняются его guards/revisions/effect gates; сортировка demo cache не объявляется глобальной сортировкой полного скана.

Показать состояния empty/loading/ready/error/unavailable/partial/stale/disabled/conflict. Проверить длинные URL, кириллицу, большой текст, несколько параллельных источников, быстрый выбор строк и устаревший ответ worker. Недоступные cookies/headers/HTML отображаются как недоступные, не как пустой успешный результат.

### Что передать после дизайнерской работы

- Запускаемый прототип: Python entrypoint, Qt-компоненты, layouts/models/controllers, QSS, tokens и локальные ресурсы с notices.
- Краткое описание компонентов и mapping «экран → QSS/Qt component → state → interaction → real core/adapter».
- Команды запуска и воспроизводимые синтетические fixtures. Отдельно указать, какие действия demo-only, какие действительно подключены к ядру и что ещё требует реализации.
- Скриншоты/записи **работающего Qt-прототипа** как доказательства, а не самостоятельная замена ему.
- Проверки широких/компактных окон, восстановления панелей, DPI/keyboard/accessibility и скорости. Цвета/состояния должны быть согласованы на macOS, Linux и Windows. Непроверенные platform lanes остаются непроверенными.

Готовность дизайна определяется пригодностью системы к реализации и работающими взаимодействиями, а не количеством изображений. Прототип не доказывает готовность полного приложения, миллионного краула, всех фоновых операций или всех ОС автоматически.



## Проверка требований перед реализацией

Прочитай `Проверка_требований_и_решения.md`: там сверка требований с фактическим каркасом, принятые архитектурные решения, обязательные границы и все три утверждённых выбора владельца. Она уточняет current readiness: приложение — D0 Mac preparation, не принятый D1–D4 релиз. Новые `scan-url-query/detail` в ТЗ — предлагаемые контракты, не существующие команды.

## Утверждённые решения владельца — 2026-10-07

Павел явно ответил на все три вопроса:

1. **Поставка: приложение + ядро одним комплектом.** Готовый релиз не требует отдельной установки Python, seotools, CLI или ручного выбора пути к ядру. В поставке — GUI и совместимое ядро одной проверенной версии, с доступными CLI и local MCP. Обновление/rollback учитывает их совместимость, схемы и сохранённые проекты. Один общий source/core, а не новая реализация функций в GUI. Процессы могут быть разделены ради latency/RSS; это не два независимых движка.
2. **Первый релиз: локальные проекты.** GUI и агент работают с локальными проектами/сканами на выбранном компьютере. Не нужен сервер, hosted login, cloud account или remote connector. VPS/remote runtime — следующий этап, не блокер первого desktop release. Локальные сканы сайта могут делать явно подтверждённые сетевые запросы через существующие guards; «локальный проект» не означает запрет разрешённого collector network.
3. **Распространение: открытый исходный код.** Поставка должна включать исходники, инструкции сборки, воспроизводимые dependencies/platform manifests и необходимые license/NOTICE материалы. Учесть GPLv3/commercial условия PyQt5 и условия Qt/font/icon/dependencies до distribution; не менять молча MIT-лицензию существующего ядра и не считать open-source словом автоматической проверкой всех obligations. Коммерческая покупка или платная активация не разрешается этим ответом автоматически.

Эти ответы приняты, повторно их не спрашивать. Они имеют приоритет над ранними pending/recommended формулировками. Нынешний 73MiB demo `.app` с внешним --core-cli ещё НЕ соответствует окончательной единой поставке; это подготовительный D0. Обязательные platform lanes macOS/Linux/Windows и весь D1–D4 объём остаются.

### Обязательная приёмка единого комплекта

- Чистый supported компьютер без отдельно установленного Python/seohead: bundle запускается, открывает/создаёт local project и выполняет зарегистрированные supported операции через поставленное ядро.
- GUI, комплектные CLI/MCP и worker показывают согласованную build/schema identity. Unsupported artifact/capability — явное состояние, не ложный успех.
- Real URL read/detail и задачи/inbox не требуют hardcoded `~/...` путей. Runtime resources и default locations находятся portable/platform-safe способом.
- Проект можно перенести: пути выбираются явно, UUID/provenance сохраняются; миграция и update/rollback не повреждают исходные данные.
- Optional rendering browsers/SF и внешние провайдеры имеют отдельные capabilities/setup/licence gates; базовый просмотр сохранённых данных не требует их установки. SF-бинарник/платные аккаунты не входят автоматически в ядро bundle.
- Remote login/SSH/VPS/auth не появляются в первом релизе как обязательный startup/setup step. Web — после desktop, как уже определено.

