> **Решение Павла от 07.10.2026:** полный отказ от веб-консоли, не перенос в backlog. Реализуются CLI/терминал и PyQt5/Qt Widgets; MCP остаётся доступом агентов. Все прежние требования W1 отменены.

# Проверка требований и решения для Claude

Дата:2026-10-07. Это проверка текстового ТЗ, рабочего desktop-каркаса и исходников ядра; не утверждение готовности всего приложения.

## 1. Уже принятые решения — повторно не спрашивать

- PyQt5 + Qt Widgets, функциональный дизайн на QSS + Qt layouts + models/signals.
- Дизайн проектирует Claude; источник бренда — извлечённая система сайта SEOHEAD. Собственная desktop-адаптация; Material Symbols отдельно, без обязательной копии всего MD3.
- macOS, Windows, Linux обязательны; web после desktop. Electron/QtWebEngine UI не используются.
- Продукт — консоль SEO-инженера рядом с агентом, а не только копия SF. Проектная работа и URL-inspector входят в общий объём.
- Единое ядро/проекты/сканы/checklist/inbox/ledger; второй crawler, analyzer и registry задач не создавать.
- UI-поток не читает БД/сеть/большие файлы. Работники, ограниченные очереди и модели; реальные исходные данные сохраняются.
- Заметка, её прочтение, triage, ack, принятие цели, выполнение задачи и подтверждение исправления — разные состояния.
- Сортировка/фильтр только загруженной страницы не считается операцией по всему скану. Неизмеренное/неподключённое не превращается в ноль или успешный результат.

## 2. Проверенное текущее состояние

Desktop source: Work/tools/seohead-desktop, commit3f939d4. Ядро: Work/tools/seotools, inspectedHEAD6252dc2e. DocsPR899 на момент проверки OPEN: не считать его уже merged.

| Возможность | Состояние сейчас | Что обязан сделать Claude |
|---|---|---|
| Нативный Mac-каркас, QSS/tokens/fonts/icons | Запуск и4smoke tests passed | Сохранить baseline, заменить неподключённые представления последовательно |
| Demo URL search/sort/detail/panels |5синтетических строк | Не выдавать demo и локальный proxy sort за production queries |
| Чтение проекта CLI в фоне | project-open metadata прошёл | Проверять schema/ok/error/version и очищать старые данные при переключении |
| Real URL query/detail | Не подключён; предлагаемые scan-url-query/detail отсутствуют в текущих registered routes | Реализовать общий bounded adapter/contract; CLI/MCP parity, потом Qt model |
| Работа/tasks/methods/inbox | Контейнеры/заглушки GUI, не полноценная интеграция | Подключить существующие contracts и реальные evidence/revisions |
| Новый скан | Только preview без dispatch | Реальное подтверждение→shared backend→jobID→progress/cancel/resume |
| Core-owned completion/coverage/recheck | Есть в ядре, не представлены целиком в GUI | Переиспользовать отдельные оси/состояния, не считать наличие окна интеграцией |
| Windows/Linux native/packaging | Не запускались | Сборки и реальные native interaction evidence на соответствующих ОС |
| Untitled.fig | Контейнер/basiclabels/thumbnail проверены, полного layer inspection нет | Не считать пустой/неразобранный экспорт утверждённым макетом; nativeprototype — основной результат |

## 3. Решённый порядок реализации

1. **Реальные данные.** Открыть существующий проект, выбрать собственный/конкурентный site/run и вывести bounded real URL table +detail. Capability/schema mismatch, отсутствующий/повреждённый/active artifact показываются явно. Не создавать проект автоматически при ошибке открытия.
2. **Проектная работа.** Реальные задачи/методы/покрытие, заметки/inbox и concurrency с отдельным агентом. Черновик заметки не теряется при переключении/ошибке сохранения. Read/ack/goal acceptance не запускает команды и не завершает checklist автоматически.
3. **Manual scan vertical.** Начать с существующего native HTML route: editconfig→exactpreview→explicitconfirm→durablejob→реальныйprogress/terminal→сохранённыйaudit. JS/SF/sitemap добавлять по их capabilities/licence/install state, без скрытой подмены источника.
4. **Reports/recheck/compare.** Исходные evidence, full/selected population, skipped/unavailable coverage и acceptance/recheck критерии. Engineering workbook с предложениями агента отличается от factual exporter.
5. **3OS release.** Упаковка, запуск без разработки пользователя, DPI/keyboard/accessibility/restart, resource/latency evidence. Веб-консоль полностью отменена; отдельной веб-дизайн-системы и веб-этапа нет.

Этапы — очередность, не отмена оставшихся требований. Demo-only вертикаль нельзя назвать готовым первым продуктовым релизом.

## 4. Конкретные обязательные исправления/границы

- Ограничить stdout/stderr при чтении из core во время получения, а не только после subprocess.capture_output: нынешний 1MiB check после полной materialization не является строгим memory bound.
- Проверять структурированную ошибку и ожидаемую схему даже при exit0. Совместимость определяется schema/capabilities/build, не одной надписью version3.0.0.
- Нужны cancel/coalescing stale-read generations и отсутствие overlapping polling. Ошибка нового проекта не оставляет старые реальные данные под новым именем.
- Существующая read_project seam не даёт реальной URL-таблицы. Никогда не показывать demo вместо данных подключённого проекта.
- Native source sorted/filter cursor — core-side и стабильный по source/query/revision. Изменившийся live scan не смешивает версии результатов.
- Лимит UI-cache и предел preview проверяются на чтении; longURL/cyrillic/empty/null/redacted отличаются. SQLite connections явно закрываются, транзакции сохраняют commit/rollback.
- Сохранённые виды для streamed audit.v2 и BI selected segments/coverage companion имеют известные незакрытые gaps; проверить current implementation перед обещанием full export. Не обходить их молчаливым materialization или потерей skipped reasons.
- Native crawl предел пока50k на inspectedsource. Viewer1Msynthetic storage ≠million live crawl/full audit. Полная цельмиллион остаётся отдельно в acceptance; GUI не повышает ceiling сам.

## 5. Как принимать результат

На каждом этапе требуются запуск и recordedsource/version/fixture. Минимальная functionalвертикаль: realproject→correctsite/run→realURL→metadata/bodyavailability/provenance→связаннаяnote→nextrealMCP notice→явныйtriage/ack→restart withsamecontext. Manualscan добавляет preview/submission equality, idempotentjob, interrupted/cancel/resume и retainedsource сохранность.

Любой state из empty/loading/ready/error/unavailable/partial/stale/disabled/conflict доступен в reproduciblefixtures. Table selection и draft editing не сбрасываются по backgroundupdate. Окно 1024/1440/1920, collapsedpanels, longstrings, OSshortcuts и разныхDPI проверяются. Budgettargets изТЗ — будущие gates, не уже измеренная скорость.

Mac evidence не закрывает Windows/Linux. Buildsuccess или offscreen widgettest не заменяет nativeplatform acceptance. Неподключённые кнопки не сообщают об успешном scan/export/goal execution.

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
