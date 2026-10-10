# Хватает ли ядра на дизайн — матрица покрытия (09.10.2026)

Метод: каждый лист канваса (`design/v2/canvas`, 86 досок; блоки данных есть на 76) разобран на блоки данных и действия. По каждому блоку проверено, чем он обеспечен в ядре — по коду и реальным вызовам на `examples/qa-site` (проект из двух сканов, 60 URL) и на базе ≈1 млн URL (только чтение). Платные провайдеры и боевые сайты не использовались, ядро не менялось.

Статусы: **есть** — вызов существует и отдаёт всё нужное; **частично** — есть, но не хватает полей, фильтра, сортировки, пагинации, скорости или формата; **нет** — в ядре нет; **только UI** — ядро не нужно (настройки приложения, навигация, оверлеи).

## Итог

Строк матрицы: **447** (блоков данных/действий без «только UI»: **390**).

| Статус | Строк | Доля от блоков, которым нужно ядро |
|---|---|---|
| есть | 80 | 21 % |
| частично | 205 | 53 % |
| нет | 105 | 27 % |
| только UI | 57 | — |

**Покрытие ядром: 47 %** (есть = 1, частично = ½, нет = 0). Строго «есть» — **21 %**.

| Группа | Строк | есть | частично | нет | только UI | Покрытие | Строго «есть» |
|---|---|---|---|---|---|---|---|
| G1 · проект, работа, запуски | 105 | 12 | 61 | 18 | 14 | 47 % | 13 % |
| G2 · настройка скана, краулер, установка | 117 | 34 | 48 | 25 | 10 | 54 % | 32 % |
| G3 · URL, проблемы, сравнение, состояния | 117 | 23 | 48 | 39 | 7 | 43 % | 21 % |
| G4 · настройки, источники, агент, оверлеи | 108 | 11 | 48 | 23 | 26 | 43 % | 13 % |

Подробные матрицы: `Work/tools/reports/seotools/desktop-impl-20261009/core-coverage/matrix-G1..G4.{json,md}` (с доказательствами: команды и замеры).

## По листам

| Лист | Заголовок | есть | частично | нет | UI | Покрытие |
|---|---|---|---|---|---|---|
| Log | Журнал работы | 0 | 0 | 3 | 1 | 0 % |
| Help | Справка (F1) | 0 | 0 | 1 | 1 | 0 % |
| SetSpend | Расходы платных API | 0 | 1 | 4 | 1 | 10 % |
| UrlHtml | URL · HTML | 1 | 0 | 5 | 0 | 17 % |
| UrlResources | URL · Ресурсы | 0 | 1 | 2 | 1 | 17 % |
| SetAgentConfig | Агент настраивает приложение | 0 | 1 | 2 | 2 | 17 % |
| ErrorsCore | Фатальные состояния ядра | 0 | 2 | 3 | 2 | 20 % |
| ProjSchedule | Расписание сканов | 0 | 2 | 2 | 0 | 25 % |
| Palette | Действия и переходы | 0 | 1 | 1 | 1 | 25 % |
| StatesIssues | Состояния «Проблемы» | 0 | 2 | 2 | 0 | 25 % |
| StatesSearch | Состояния «Поиск в HTML» | 0 | 1 | 1 | 0 | 25 % |
| SetAgent | Агент | 0 | 2 | 2 | 1 | 25 % |
| Menus | Меню приложения | 0 | 1 | 1 | 2 | 25 % |
| SetSourceKey | Источник — Arsenkin (API-токен) | 0 | 4 | 3 | 0 | 29 % |
| UrlSchema | URL · Структурированные данные | 0 | 2 | 1 | 0 | 33 % |
| SetCore | Настройки — Ядро | 0 | 2 | 1 | 1 | 33 % |
| Onboarding | Первый запуск · мастер | 1 | 1 | 2 | 1 | 38 % |
| Tasks | Задачи (доска) | 0 | 3 | 1 | 0 | 38 % |
| ScExtract | Скан — извлечение | 0 | 3 | 1 | 0 | 38 % |
| ScProfiles | Скан — профили | 0 | 3 | 1 | 0 | 38 % |
| UrlRedirects | URL · Редиректы и canonical | 0 | 3 | 1 | 0 | 38 % |
| SetScan | Настройки — Сканы по умолчанию | 0 | 3 | 1 | 1 | 38 % |
| SetMcp | Настройки — MCP-сервер | 0 | 3 | 1 | 1 | 38 % |
| Main | URL-инспектор | 4 | 8 | 9 | 1 | 38 % |
| Search | Поиск в HTML | 2 | 3 | 4 | 1 | 39 % |
| TaskDetail | Задача (карточка) | 0 | 4 | 1 | 0 | 40 % |
| UrlLinks | URL · Ссылки | 0 | 4 | 1 | 0 | 40 % |
| ProjExport | Экспорт и отчёты | 0 | 5 | 1 | 1 | 42 % |
| CrawlerResults | Краулер · результаты | 1 | 3 | 2 | 2 | 42 % |
| Watch | seohead watch · наблюдатель в терминале | 2 | 2 | 3 | 0 | 43 % |
| Scans | Сканы проекта (наблюдение) | 2 | 8 | 3 | 0 | 46 % |
| Modals | Все модалки | 2 | 8 | 3 | 3 | 46 % |
| Handoff | Отчёт исполнителям | 1 | 3 | 1 | 0 | 50 % |
| ReportPreview | Отчёт · предпросмотр | 1 | 3 | 1 | 0 | 50 % |
| ProjSettings | Настройки проекта | 1 | 3 | 1 | 2 | 50 % |
| Methods | Методы | 0 | 3 | 0 | 1 | 50 % |
| ScanRun | Скан №3 · идёт | 1 | 4 | 1 | 0 | 50 % |
| NewTab | Новая вкладка | 0 | 1 | 0 | 2 | 50 % |
| ScScope | Скан — область | 2 | 2 | 2 | 1 | 50 % |
| Install | Установка и первый запуск | 1 | 1 | 1 | 3 | 50 % |
| MainB | URL-инспектор · раскладка с деталями справа | 1 | 2 | 1 | 0 | 50 % |
| UrlHistory | URL · История по сканам | 1 | 2 | 1 | 0 | 50 % |
| Compare | Сравнение сканов | 3 | 4 | 3 | 0 | 50 % |
| StatesCompare | Состояния «Сравнение» | 2 | 0 | 2 | 0 | 50 % |
| Settings | Настройки (контейнер, 12 разделов) | 0 | 1 | 0 | 1 | 50 % |
| SetSources | Настройки — Источники данных | 1 | 6 | 1 | 0 | 50 % |
| SetData | Хранилище и данные | 1 | 2 | 1 | 1 | 50 % |
| SetUpdates | Обновления | 0 | 1 | 0 | 1 | 50 % |
| SetAbout | О программе | 1 | 1 | 1 | 1 | 50 % |
| Crawler | Режим «Краулер» | 4 | 12 | 3 | 1 | 53 % |
| Project | Проектная консоль | 1 | 10 | 0 | 0 | 55 % |
| ScRender | Скан — JS-рендеринг | 3 | 3 | 2 | 1 | 56 % |
| CLI | Интерфейс командной строки | 3 | 2 | 2 | 0 | 57 % |
| ScRobots | Скан — robots | 3 | 1 | 2 | 1 | 58 % |
| Issues | Проблемы | 4 | 5 | 2 | 0 | 59 % |
| Inbox | Входящие | 1 | 4 | 0 | 1 | 60 % |
| ScRequest | Скан — запрос | 1 | 4 | 0 | 0 | 60 % |
| SetSourceDetail | Источник — Google Search Console (OAuth) | 2 | 8 | 0 | 1 | 60 % |
| ScanDialog | Новый скан | 7 | 8 | 3 | 0 | 61 % |
| UrlHeaders | URL · Заголовки | 2 | 1 | 1 | 1 | 62 % |
| CompareUrl | Сравнение одного URL | 1 | 3 | 0 | 0 | 62 % |
| StatesMain | Состояния таблицы URL | 1 | 3 | 0 | 0 | 62 % |
| SetNotify | Уведомления | 2 | 1 | 1 | 2 | 62 % |
| ScSpeed | Скан — скорость | 4 | 1 | 2 | 0 | 64 % |
| ScStorage | Скан — хранение | 3 | 3 | 1 | 0 | 64 % |
| Start | Старт · проекты | 1 | 2 | 0 | 2 | 67 % |
| Simple | Проект (простой режим) | 2 | 4 | 0 | 0 | 67 % |
| Loading | Загрузки и состояния данных | 1 | 2 | 0 | 1 | 67 % |
| Notify | Уведомления и плашки | 1 | 2 | 0 | 0 | 67 % |
| SetGeneral | Настройки — Общие | 1 | 1 | 0 | 2 | 75 % |
| TabSettings | Вкладки и панели | 0 | 0 | 0 | 2 | — |
| ScanList | Новый скан · список URL | 0 | 0 | 0 | 1 | — |
| SetView | Настройки — Вид | 0 | 0 | 0 | 1 | — |
| SetKeys | Горячие клавиши | 0 | 0 | 0 | 1 | — |
| Shortcuts | Горячие клавиши (шпаргалка) | 0 | 0 | 0 | 1 | — |
| Themes | Темы оформления и язык | 0 | 0 | 0 | 1 | — |

## Пробелы → issue в ядре (метка `core-gap`)

310 строк «нет» и «частично» сгруппированы в 38 тем: одна тема — один issue со списком всех затронутых блоков (таблица «лист → блок → чего не хватает»). Так одинаковая причина, которая видна на десятке листов, не превращается в десяток дублей.

| Issue | Тема | Строк | Важность | Блокирует шаги |
|---|---|---|---|---|
| [#922](https://github.com/PavloSEO/seohead-tools/issues/922) | Модель задач: исполнитель, описание, комментарии, актор в истории, сортировка и фильтры | 21 | blocker | 5, 7, 8, 9 |
| [#923](https://github.com/PavloSEO/seohead-tools/issues/923) | Журнал проекта: структурированные события (источник, актор, текст) с постраничным чтением | 21 | blocker | 5, 6, 7, 8 |
| [#926](https://github.com/PavloSEO/seohead-tools/issues/926) | Remediation-ledger из CLI/MCP: создание, ingest скана, связь находка → задача → проверка | 14 | blocker | 5, 7, 8 |
| [#927](https://github.com/PavloSEO/seohead-tools/issues/927) | scan-url-query: фильтр, сортировка и total по всей базе скана | 12 | blocker | 5, 6, 8, 9 |
| [#929](https://github.com/PavloSEO/seohead-tools/issues/929) | seohead mcp status|enable|disable: общее состояние MCP для приложения и CLI | 11 | blocker | 5, 7, 8, 9 |
| [#930](https://github.com/PavloSEO/seohead-tools/issues/930) | Цепочки редиректов и canonical по URL; срабатывание REDIRECT_CHAIN/LOOP | 11 | blocker | 5, 6, 8, 9 |
| [#931](https://github.com/PavloSEO/seohead-tools/issues/931) | Лёгкий путь чтения скана: без полной валидации при каждом открытии | 10 | blocker | 5, 6, 8, 9 |
| [#980](https://github.com/PavloSEO/seohead-tools/issues/980) | Находки: постраничное чтение audit-v2 без полной материализации | 10 | blocker | 5, 6, 8, 9 |
| [#933](https://github.com/PavloSEO/seohead-tools/issues/933) | Таблицы «как в SF»: вкладки, колонки, агрегаты, total для scan-inspect | 10 | blocker | 5, 6, 7, 8, 9 |
| [#935](https://github.com/PavloSEO/seohead-tools/issues/935) | Ресурсы страницы (картинки, CSS, JS): вес, статус, тип | 8 | blocker | 5, 6, 7, 8 |
| [#936](https://github.com/PavloSEO/seohead-tools/issues/936) | Чтение HTML-тела страницы (scan-url-detail / отдельный читатель) | 8 | blocker | 5, 6, 8 |
| [#938](https://github.com/PavloSEO/seohead-tools/issues/938) | compare-crawls: сравнение страниц (только в №1/№2, смена ответа/title, слов), сегменты разделов | 7 | blocker | 5, 7, 8, 9 |
| [#942](https://github.com/PavloSEO/seohead-tools/issues/942) | Краулер без проекта: журнал запусков и лимит скорости на хост | 4 | blocker | 5, 6, 8 |
| [#944](https://github.com/PavloSEO/seohead-tools/issues/944) | Входящие: фильтры, тип «вопрос», ответ агента, ссылки на URL | 4 | blocker | 7, 8 |
| [#945](https://github.com/PavloSEO/seohead-tools/issues/945) | Права агента: allowlist проектов, разрешения, журнал действий, last_agent_activity | 4 | blocker | 7, 8 |
| [#948](https://github.com/PavloSEO/seohead-tools/issues/948) | Структурированные данные по URL (JSON-LD, microdata) с ошибками валидации | 3 | blocker | 5, 8 |
| [#949](https://github.com/PavloSEO/seohead-tools/issues/949) | Расходы и лимиты платных API: журнал, ₽, месячные лимиты, подтверждение | 3 | blocker | 5, 7, 8 |
| [#920](https://github.com/PavloSEO/seohead-tools/issues/920) | Прочие пробелы ядра по листам дизайна | 23 | high | 5, 6, 8 |
| [#921](https://github.com/PavloSEO/seohead-tools/issues/921) | Живой прогресс скана: найдено/ошибки/ETA/процент, пауза, остановка по id | 22 | high | 5, 6, 7, 8, 9 |
| [#924](https://github.com/PavloSEO/seohead-tools/issues/924) | Исходящие и входящие ссылки по URL: rel, HTTP цели, внутр./внешняя, lookup по URL | 19 | high | 5, 6, 7, 8, 9 |
| [#925](https://github.com/PavloSEO/seohead-tools/issues/925) | Поля листов настройки скана без соответствующей настройки ядра; метаданные настроек (диапазоны, перечисления, группы) | 17 | high | 5, 6, 7, 8 |
| [#934](https://github.com/PavloSEO/seohead-tools/issues/934) | Источники данных: реестр (Topvisor, DataForSEO, SF), проверка доступа, даты и срок токена | 9 | high | 7, 8 |
| [#979](https://github.com/PavloSEO/seohead-tools/issues/979) | Версия и совместимость ядра: core-info, seohead doctor, handshake контракта | 7 | high | 5, 7, 8, 9 |
| [#939](https://github.com/PavloSEO/seohead-tools/issues/939) | Поиск в HTML: прогресс, отмена, regex, несколько сниппетов, чтение пакета без лишних документов | 6 | high | 8, 9 |
| [#940](https://github.com/PavloSEO/seohead-tools/issues/940) | Расписание проекта: день недели/время, запуск по расписанию | 5 | high | 5, 7, 8 |
| [#941](https://github.com/PavloSEO/seohead-tools/issues/941) | Профили скана в ядре (сохранение, выбор, умолчания проекта) | 5 | high | 5, 7 |
| [#943](https://github.com/PavloSEO/seohead-tools/issues/943) | Сохранённые виды: удаление, переименование, выразительные фильтры | 4 | high | 5, 8 |
| [#946](https://github.com/PavloSEO/seohead-tools/issues/946) | Цель проекта: время принятия/выполнения, план аудита для знаменателя KPI | 3 | high | 5, 7, 8 |
| [#950](https://github.com/PavloSEO/seohead-tools/issues/950) | JS-рендеринг: режим Авто, viewport, блокировка ресурсов, проверка движка | 3 | high | 5, 8 |
| [#954](https://github.com/PavloSEO/seohead-tools/issues/954) | Экстракторы XPath/Regex и проверка на образце | 2 | high | 5, 8 |
| [#956](https://github.com/PavloSEO/seohead-tools/issues/956) | Тайминги ответа: DNS, connect, TLS, TTFB, загрузка | 1 | high | 8 |
| [#928](https://github.com/PavloSEO/seohead-tools/issues/928) | Отчёты: язык ru для DOCX/XLSX, отчёты по исполнителям, прогресс и отмена экспорта | 11 | medium | 5, 6, 7, 8, 9 |
| [#947](https://github.com/PavloSEO/seohead-tools/issues/947) | Настройки проекта: название (label), политика, чтение/запись с ревизией | 3 | medium | 5 |
| [#952](https://github.com/PavloSEO/seohead-tools/issues/952) | Реестр проектов и коды ошибок project-open (not_found и др.) | 2 | medium | 5, 8 |
| [#953](https://github.com/PavloSEO/seohead-tools/issues/953) | Дешёвый опрос: since_revision / not_modified и объём ответа project-observe | 2 | medium | 5, 9 |
| [#955](https://github.com/PavloSEO/seohead-tools/issues/955) | Методы и сценарии: структурированный список (id, название, шаги), scenario list | 1 | medium | 5, 8 |
| [#951](https://github.com/PavloSEO/seohead-tools/issues/951) | seohead watch / tui: флаги --scan, --lang, --compact, русские подписи | 3 | low | — |
| [#957](https://github.com/PavloSEO/seohead-tools/issues/957) | HTTP-заголовки запроса/ответа по URL: полнота и маскирование | 1 | low | 8 |

## Что блокирует шаги 5–9 и в каком порядке закрывать

Блокирующим считаем пробел важности `blocker`/`high`, без которого экран либо нельзя сделать честно, либо он нарушает бюджеты (страница ≤ 1 с, 200 строк, без полной материализации). В Desktop можно временно обойтись адаптерами и деградацией (например, листать `scan-inspect` без сортировки), но «Нет данных» вместо работающей функции — это «нет», а не «есть».

### Шаг 5 · Экраны по сценарию

| Issue | Тема | Блоков | Из них blocker |
|---|---|---|---|
| [#926](https://github.com/PavloSEO/seohead-tools/issues/926) | Remediation-ledger из CLI/MCP: создание, ingest скана, связь находка → задача → проверка | 3 | 1 |
| [#948](https://github.com/PavloSEO/seohead-tools/issues/948) | Структурированные данные по URL (JSON-LD, microdata) с ошибками валидации | 1 | 1 |
| [#927](https://github.com/PavloSEO/seohead-tools/issues/927) | scan-url-query: фильтр, сортировка и total по всей базе скана | 1 | 1 |
| [#922](https://github.com/PavloSEO/seohead-tools/issues/922) | Модель задач: исполнитель, описание, комментарии, актор в истории, сортировка и фильтры | 6 | 0 |
| [#925](https://github.com/PavloSEO/seohead-tools/issues/925) | Поля листов настройки скана без соответствующей настройки ядра; метаданные настроек (диапазоны, перечисления, группы) | 4 | 0 |
| [#923](https://github.com/PavloSEO/seohead-tools/issues/923) | Журнал проекта: структурированные события (источник, актор, текст) с постраничным чтением | 2 | 0 |
| [#931](https://github.com/PavloSEO/seohead-tools/issues/931) | Лёгкий путь чтения скана: без полной валидации при каждом открытии | 2 | 0 |
| [#980](https://github.com/PavloSEO/seohead-tools/issues/980) | Находки: постраничное чтение audit-v2 без полной материализации | 1 | 0 |
| [#940](https://github.com/PavloSEO/seohead-tools/issues/940) | Расписание проекта: день недели/время, запуск по расписанию | 1 | 0 |
| [#921](https://github.com/PavloSEO/seohead-tools/issues/921) | Живой прогресс скана: найдено/ошибки/ETA/процент, пауза, остановка по id | 1 | 0 |
| [#943](https://github.com/PavloSEO/seohead-tools/issues/943) | Сохранённые виды: удаление, переименование, выразительные фильтры | 1 | 0 |
| [#920](https://github.com/PavloSEO/seohead-tools/issues/920) | Прочие пробелы ядра по листам дизайна | 1 | 0 |
| [#954](https://github.com/PavloSEO/seohead-tools/issues/954) | Экстракторы XPath/Regex и проверка на образце | 1 | 0 |
| [#935](https://github.com/PavloSEO/seohead-tools/issues/935) | Ресурсы страницы (картинки, CSS, JS): вес, статус, тип | 1 | 0 |

### Шаг 6 · Режим «Краулер»

| Issue | Тема | Блоков | Из них blocker |
|---|---|---|---|
| [#923](https://github.com/PavloSEO/seohead-tools/issues/923) | Журнал проекта: структурированные события (источник, актор, текст) с постраничным чтением | 2 | 1 |
| [#942](https://github.com/PavloSEO/seohead-tools/issues/942) | Краулер без проекта: журнал запусков и лимит скорости на хост | 1 | 1 |
| [#920](https://github.com/PavloSEO/seohead-tools/issues/920) | Прочие пробелы ядра по листам дизайна | 2 | 0 |
| [#921](https://github.com/PavloSEO/seohead-tools/issues/921) | Живой прогресс скана: найдено/ошибки/ETA/процент, пауза, остановка по id | 2 | 0 |
| [#925](https://github.com/PavloSEO/seohead-tools/issues/925) | Поля листов настройки скана без соответствующей настройки ядра; метаданные настроек (диапазоны, перечисления, группы) | 1 | 0 |
| [#931](https://github.com/PavloSEO/seohead-tools/issues/931) | Лёгкий путь чтения скана: без полной валидации при каждом открытии | 1 | 0 |
| [#927](https://github.com/PavloSEO/seohead-tools/issues/927) | scan-url-query: фильтр, сортировка и total по всей базе скана | 1 | 0 |
| [#933](https://github.com/PavloSEO/seohead-tools/issues/933) | Таблицы «как в SF»: вкладки, колонки, агрегаты, total для scan-inspect | 1 | 0 |
| [#924](https://github.com/PavloSEO/seohead-tools/issues/924) | Исходящие и входящие ссылки по URL: rel, HTTP цели, внутр./внешняя, lookup по URL | 1 | 0 |
| [#936](https://github.com/PavloSEO/seohead-tools/issues/936) | Чтение HTML-тела страницы (scan-url-detail / отдельный читатель) | 1 | 0 |
| [#930](https://github.com/PavloSEO/seohead-tools/issues/930) | Цепочки редиректов и canonical по URL; срабатывание REDIRECT_CHAIN/LOOP | 1 | 0 |
| [#980](https://github.com/PavloSEO/seohead-tools/issues/980) | Находки: постраничное чтение audit-v2 без полной материализации | 1 | 0 |

### Шаг 7 · MCP, источники, установщик

| Issue | Тема | Блоков | Из них blocker |
|---|---|---|---|
| [#945](https://github.com/PavloSEO/seohead-tools/issues/945) | Права агента: allowlist проектов, разрешения, журнал действий, last_agent_activity | 3 | 2 |
| [#929](https://github.com/PavloSEO/seohead-tools/issues/929) | seohead mcp status|enable|disable: общее состояние MCP для приложения и CLI | 5 | 1 |
| [#923](https://github.com/PavloSEO/seohead-tools/issues/923) | Журнал проекта: структурированные события (источник, актор, текст) с постраничным чтением | 4 | 1 |
| [#944](https://github.com/PavloSEO/seohead-tools/issues/944) | Входящие: фильтры, тип «вопрос», ответ агента, ссылки на URL | 2 | 1 |
| [#922](https://github.com/PavloSEO/seohead-tools/issues/922) | Модель задач: исполнитель, описание, комментарии, актор в истории, сортировка и фильтры | 1 | 1 |
| [#949](https://github.com/PavloSEO/seohead-tools/issues/949) | Расходы и лимиты платных API: журнал, ₽, месячные лимиты, подтверждение | 1 | 1 |
| [#934](https://github.com/PavloSEO/seohead-tools/issues/934) | Источники данных: реестр (Topvisor, DataForSEO, SF), проверка доступа, даты и срок токена | 3 | 0 |
| [#935](https://github.com/PavloSEO/seohead-tools/issues/935) | Ресурсы страницы (картинки, CSS, JS): вес, статус, тип | 2 | 0 |
| [#941](https://github.com/PavloSEO/seohead-tools/issues/941) | Профили скана в ядре (сохранение, выбор, умолчания проекта) | 1 | 0 |
| [#921](https://github.com/PavloSEO/seohead-tools/issues/921) | Живой прогресс скана: найдено/ошибки/ETA/процент, пауза, остановка по id | 1 | 0 |
| [#925](https://github.com/PavloSEO/seohead-tools/issues/925) | Поля листов настройки скана без соответствующей настройки ядра; метаданные настроек (диапазоны, перечисления, группы) | 1 | 0 |
| [#946](https://github.com/PavloSEO/seohead-tools/issues/946) | Цель проекта: время принятия/выполнения, план аудита для знаменателя KPI | 1 | 0 |
| [#940](https://github.com/PavloSEO/seohead-tools/issues/940) | Расписание проекта: день недели/время, запуск по расписанию | 1 | 0 |

### Шаг 8 · Пробелы данных ядра

| Issue | Тема | Блоков | Из них blocker |
|---|---|---|---|
| [#927](https://github.com/PavloSEO/seohead-tools/issues/927) | scan-url-query: фильтр, сортировка и total по всей базе скана | 8 | 3 |
| [#931](https://github.com/PavloSEO/seohead-tools/issues/931) | Лёгкий путь чтения скана: без полной валидации при каждом открытии | 3 | 3 |
| [#980](https://github.com/PavloSEO/seohead-tools/issues/980) | Находки: постраничное чтение audit-v2 без полной материализации | 6 | 2 |
| [#926](https://github.com/PavloSEO/seohead-tools/issues/926) | Remediation-ledger из CLI/MCP: создание, ingest скана, связь находка → задача → проверка | 5 | 2 |
| [#922](https://github.com/PavloSEO/seohead-tools/issues/922) | Модель задач: исполнитель, описание, комментарии, актор в истории, сортировка и фильтры | 6 | 1 |
| [#930](https://github.com/PavloSEO/seohead-tools/issues/930) | Цепочки редиректов и canonical по URL; срабатывание REDIRECT_CHAIN/LOOP | 4 | 1 |
| [#948](https://github.com/PavloSEO/seohead-tools/issues/948) | Структурированные данные по URL (JSON-LD, microdata) с ошибками валидации | 3 | 1 |
| [#933](https://github.com/PavloSEO/seohead-tools/issues/933) | Таблицы «как в SF»: вкладки, колонки, агрегаты, total для scan-inspect | 3 | 1 |
| [#938](https://github.com/PavloSEO/seohead-tools/issues/938) | compare-crawls: сравнение страниц (только в №1/№2, смена ответа/title, слов), сегменты разделов | 3 | 1 |
| [#936](https://github.com/PavloSEO/seohead-tools/issues/936) | Чтение HTML-тела страницы (scan-url-detail / отдельный читатель) | 2 | 1 |
| [#935](https://github.com/PavloSEO/seohead-tools/issues/935) | Ресурсы страницы (картинки, CSS, JS): вес, статус, тип | 1 | 1 |
| [#924](https://github.com/PavloSEO/seohead-tools/issues/924) | Исходящие и входящие ссылки по URL: rel, HTTP цели, внутр./внешняя, lookup по URL | 10 | 0 |
| [#921](https://github.com/PavloSEO/seohead-tools/issues/921) | Живой прогресс скана: найдено/ошибки/ETA/процент, пауза, остановка по id | 3 | 0 |
| [#939](https://github.com/PavloSEO/seohead-tools/issues/939) | Поиск в HTML: прогресс, отмена, regex, несколько сниппетов, чтение пакета без лишних документов | 3 | 0 |
| [#979](https://github.com/PavloSEO/seohead-tools/issues/979) | Версия и совместимость ядра: core-info, seohead doctor, handshake контракта | 2 | 0 |
| [#940](https://github.com/PavloSEO/seohead-tools/issues/940) | Расписание проекта: день недели/время, запуск по расписанию | 1 | 0 |
| [#923](https://github.com/PavloSEO/seohead-tools/issues/923) | Журнал проекта: структурированные события (источник, актор, текст) с постраничным чтением | 1 | 0 |
| [#943](https://github.com/PavloSEO/seohead-tools/issues/943) | Сохранённые виды: удаление, переименование, выразительные фильтры | 1 | 0 |
| [#920](https://github.com/PavloSEO/seohead-tools/issues/920) | Прочие пробелы ядра по листам дизайна | 1 | 0 |
| [#956](https://github.com/PavloSEO/seohead-tools/issues/956) | Тайминги ответа: DNS, connect, TLS, TTFB, загрузка | 1 | 0 |
| [#950](https://github.com/PavloSEO/seohead-tools/issues/950) | JS-рендеринг: режим Авто, viewport, блокировка ресурсов, проверка движка | 1 | 0 |
| [#934](https://github.com/PavloSEO/seohead-tools/issues/934) | Источники данных: реестр (Topvisor, DataForSEO, SF), проверка доступа, даты и срок токена | 1 | 0 |
| [#929](https://github.com/PavloSEO/seohead-tools/issues/929) | seohead mcp status|enable|disable: общее состояние MCP для приложения и CLI | 1 | 0 |

### Шаг 9 · Приёмка и 1M

| Issue | Тема | Блоков | Из них blocker |
|---|---|---|---|
| [#931](https://github.com/PavloSEO/seohead-tools/issues/931) | Лёгкий путь чтения скана: без полной валидации при каждом открытии | 4 | 4 |
| [#927](https://github.com/PavloSEO/seohead-tools/issues/927) | scan-url-query: фильтр, сортировка и total по всей базе скана | 3 | 3 |
| [#980](https://github.com/PavloSEO/seohead-tools/issues/980) | Находки: постраничное чтение audit-v2 без полной материализации | 3 | 2 |
| [#930](https://github.com/PavloSEO/seohead-tools/issues/930) | Цепочки редиректов и canonical по URL; срабатывание REDIRECT_CHAIN/LOOP | 2 | 1 |
| [#933](https://github.com/PavloSEO/seohead-tools/issues/933) | Таблицы «как в SF»: вкладки, колонки, агрегаты, total для scan-inspect | 2 | 1 |
| [#922](https://github.com/PavloSEO/seohead-tools/issues/922) | Модель задач: исполнитель, описание, комментарии, актор в истории, сортировка и фильтры | 1 | 1 |
| [#921](https://github.com/PavloSEO/seohead-tools/issues/921) | Живой прогресс скана: найдено/ошибки/ETA/процент, пауза, остановка по id | 2 | 0 |
| [#924](https://github.com/PavloSEO/seohead-tools/issues/924) | Исходящие и входящие ссылки по URL: rel, HTTP цели, внутр./внешняя, lookup по URL | 2 | 0 |
| [#939](https://github.com/PavloSEO/seohead-tools/issues/939) | Поиск в HTML: прогресс, отмена, regex, несколько сниппетов, чтение пакета без лишних документов | 1 | 0 |
| [#938](https://github.com/PavloSEO/seohead-tools/issues/938) | compare-crawls: сравнение страниц (только в №1/№2, смена ответа/title, слов), сегменты разделов | 1 | 0 |

### Предлагаемый порядок закрытия в ядре

**1. Скорость и чтение (разблокирует шаг 5 и бюджеты шага 9)**: [#931](https://github.com/PavloSEO/seohead-tools/issues/931), [#927](https://github.com/PavloSEO/seohead-tools/issues/927), [#953](https://github.com/PavloSEO/seohead-tools/issues/953), [#933](https://github.com/PavloSEO/seohead-tools/issues/933)

**2. Карточка URL (шаг 5, вкладки URL)**: [#924](https://github.com/PavloSEO/seohead-tools/issues/924), [#936](https://github.com/PavloSEO/seohead-tools/issues/936), [#957](https://github.com/PavloSEO/seohead-tools/issues/957), [#956](https://github.com/PavloSEO/seohead-tools/issues/956), [#930](https://github.com/PavloSEO/seohead-tools/issues/930), [#935](https://github.com/PavloSEO/seohead-tools/issues/935), [#948](https://github.com/PavloSEO/seohead-tools/issues/948)

**3. Работа над целью (шаг 5, «Работа», «Задачи», «Входящие», «Журнал»)**: [#926](https://github.com/PavloSEO/seohead-tools/issues/926), [#922](https://github.com/PavloSEO/seohead-tools/issues/922), [#946](https://github.com/PavloSEO/seohead-tools/issues/946), [#944](https://github.com/PavloSEO/seohead-tools/issues/944), [#923](https://github.com/PavloSEO/seohead-tools/issues/923), [#952](https://github.com/PavloSEO/seohead-tools/issues/952), [#921](https://github.com/PavloSEO/seohead-tools/issues/921)

**4. Краулер без проекта и настройка скана (шаг 6)**: [#942](https://github.com/PavloSEO/seohead-tools/issues/942), [#925](https://github.com/PavloSEO/seohead-tools/issues/925), [#941](https://github.com/PavloSEO/seohead-tools/issues/941), [#950](https://github.com/PavloSEO/seohead-tools/issues/950), [#954](https://github.com/PavloSEO/seohead-tools/issues/954)

**5. MCP, источники, установка (шаг 7)**: [#929](https://github.com/PavloSEO/seohead-tools/issues/929), [#979](https://github.com/PavloSEO/seohead-tools/issues/979), [#934](https://github.com/PavloSEO/seohead-tools/issues/934), [#949](https://github.com/PavloSEO/seohead-tools/issues/949), [#945](https://github.com/PavloSEO/seohead-tools/issues/945), [#951](https://github.com/PavloSEO/seohead-tools/issues/951)

**6. Отчёты, сравнение, расписание (шаг 8)**: [#980](https://github.com/PavloSEO/seohead-tools/issues/980), [#938](https://github.com/PavloSEO/seohead-tools/issues/938), [#928](https://github.com/PavloSEO/seohead-tools/issues/928), [#940](https://github.com/PavloSEO/seohead-tools/issues/940), [#955](https://github.com/PavloSEO/seohead-tools/issues/955), [#943](https://github.com/PavloSEO/seohead-tools/issues/943), [#947](https://github.com/PavloSEO/seohead-tools/issues/947), [#939](https://github.com/PavloSEO/seohead-tools/issues/939), [#920](https://github.com/PavloSEO/seohead-tools/issues/920)

## Известные расхождения листов с ядром (поправить в канвасе)

Версия ядра на листах 3.4.0, фактически 3.0.0; на листах «180 инструментов», в ядре 165 MCP-инструментов и 160 команд CLI; настроек скана 114, а не 42; умолчания в макете (concurrency 4, depth 10, свободное место 10 ГБ) не совпадают с ядром (1, 5, 1 ГиБ); MainB утверждает «исходящих ссылок нет в ядре» — они есть через `scan-link-inspect --view context`; нижняя строка листов Sc* показывает несуществующую команду `seohead crawl --profile standard`; XLSX больше 1 048 575 строк ядро делит на листы одного файла, а макет обещает отдельные файлы.
