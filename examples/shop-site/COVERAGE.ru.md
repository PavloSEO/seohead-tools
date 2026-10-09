# Покрытие экранов приложения тестовым сайтом магазина (локальный стенд)

Тестовый сайт магазина (локальный стенд) — `shop_site.py`, версии v1 → v2 → v3. Проект в
ядре: «Мебельный магазин», хост `shop.example.test:18431` (нужна строка
`127.0.0.1 shop.example.test` в `/etc/hosts`; без неё — запасной хост `shop.localhost`,
он резолвится в 127.0.0.1 штатно). Три скана реального краулера:

| Скан | Версия | URL (`urls_crawled`) | Находок | Задач (`sf tasks`) |
|---|---|---|---|---|
| №1 | v1 | 1 248 | 4 056 | 51 |
| №2 | v2 | 1 314 | 4 272 | 34 |
| №3 | v3 | 1 330 | 4 294 | 20 |

Приложение читает эти данные только через ядро (CLI/MCP), как клиентский проект. Ниже —
какие URL, дефекты и сканы наполняют каждый экран. Id дефектов `Dnn` — из `DEFECTS` в
`shop_site.py` и `ground_truth.json`.

## Витринные URL (стабильны во всех версиях)

| # | URL | История по сканам №1 → №2 → №3 | Что показывает |
|---|---|---|---|
| В1 | `/catalog/stoly/stol-007/` | 200 → **404** (D02, ссылка из старой статьи, в sitemap) → **301 → 301** через `/tovar/stol-007/` (D55, D03) | удалённый товар: 404, потом цепочка редиректов |
| В2 | `/blog/kuhnya-v-stile-loft/` | 155 → 436 → 676 слов; входящих ссылок 4 → 4 → 7 | рост слов и перелинковки в блоге |
| В3 | `/catalog/divany/divan-020/` | `STRUCTURED_DATA_PARSE_ERROR` (D48) → исправлено → чисто | JSON-LD с ошибкой, исправлен, перепроверка `resolved` |
| В4 | `/garantiya/` | `NOINDEX` (D16) → исправлено → **снова `NOINDEX`** | регрессия |
| В5 | `/catalog/kuhni/kuhnya-001/` | чисто → чисто → **`STRUCTURED_DATA_PARSE_ERROR`** (D49) | новая регрессия в №3 |
| В6 | `/aktsii/` | 500 → 500 (перепроверка `persisting`) → 200 (перепроверка `resolved`) | задача закрывается только перепроверкой |
| В7 | `/kontakty/` | маркер `GTM-K7OLD12` на 1 238 страницах → 1 странице (`/kontakty/`) → 0 | Поиск в HTML |
| В8 | `/skidki/` | цепочка `/skidki/ → /rasprodazha/ → /sale/ → /aktsii/` (D07) → убрана | вкладка «Редиректы» |
| В9 | `/catalog/stulya/stul-001/` | чисто → чисто → **`CANONICALISED`** на категорию (D19) | регрессия canonical |
| В10 | `/catalog/krovati/krovat-127/` | нет → новый товар с lorem ipsum (D33) → исправлен | URL, появившийся в №2 |
| В11 | `/catalog/shkafy/?page=2` | ответ 2 с (D50, `SLOW_RESPONSE`) → быстро | тайминги |
| В12 | `/en/catalog/kuhni/` | нет обратной hreflang-ссылки (D42) → исправлено | hreflang |
| В13 | `/landing/kuhni-na-zakaz/` | только в sitemap (D37, `SITEMAP_ORPHAN`) в №1 и №2 → удалён | сирота |
| В14 | `/blog/arhiv/2018/` | глубина > 4 (D38) в №1, №2 → архив убран | глубина |
| В15 | `/catalog/divany/divan-003/` | 200 → 301 на категорию, остаётся в sitemap (D04) → вне sitemap | удалённый товар → 301 |

## Экраны

| Экран (доска) | Чем наполняется | Сценарий |
|---|---|---|
| Main / MainB / Project | проект «Мебельный магазин», 3 скана, сводка находок и задач | открыть проект, переключить сканы №1–№3 |
| Url — Заголовки | В6 (500), `/oplata/` (`X-Robots-Tag: noindex`, D17), В1 (301 `Location`) | открыть URL в №1, №2, №3 |
| Url — Ссылки | В1 (входящая из `/blog/kak-vybrat-divan-dlya-gostinoy/`), В2 (рост входящих), `/spasibo/` (D35, нет исходящих) | сравнить входящие В2 по сканам |
| Url — HTML | В7 (маркер в `<head>`), `/catalog/stoly/ne-naydeno/` (мягкая 404, D52) | открыть тело страницы |
| Url — Ресурсы | `/static/css/main.css`, `/static/js/app.js` (D12: js закрыт в robots в №1) | `resources.fetch=true` |
| Url — Редиректы | В8 (цепочка 3 хопа), `/staryy-katalog/` (петля, D08), `/dostavka-i-oplata/` (302, D09), В1 в №3 | — |
| Url — История | В1, В4, В5, В6, В9 | одна строка на скан: статус, находки |
| Url — Schema | В3, В5 (ошибка), любой товар (Product + BreadcrumbList), главная (Organization) | — |
| Issues / StatesIssues | 4 056 / 4 272 / 4 294 находок; группы `TITLE_DUPLICATE` (D20), `DESC_DUPLICATE` (D21), `H1_DUPLICATE` (D27) | фильтр по проверке, группа → участники |
| Search / StatesSearch | `GTM-K7OLD12`: 1 238 → 1 → 0; `GTM-M4NEW58` — обратный маркер | `scan-content-search` по трём сканам |
| Compare / StatesCompare | №1→№2: появилось 336, исчезло 17, вошло 17, ушло 120; №2→№3: 126 / 56 / 20 / 68, среди вошедших — регрессии В4, В5, В9 | `compare-crawls` |
| CompareUrl | В1 (200→404→301), В4, В2 (слова) | сравнение одного URL |
| Tasks / TaskDetail | 51 → 34 → 20 задач (`sf tasks`); ledger: 7 случаев «исправлено» → перепроверка | В3, В6 |
| Handoff | задачи №1 с URL и подсказкой исправления (`fix_hint`) | — |
| ReportPreview / ProjExport | `report-build` MD/XLSX по №1 и №3; `remediation-report` | — |
| Scans / ScanList / ScanRun / Log | 3 скана в `project/scans/`, лог краула | `scan-list --project` |
| Crawler / CrawlerResults | живой прогон v1/v2/v3 (~1 мин на скан на loopback) | `crawl-site --project` |
| LinkGraph / LinkGraphLocal | каталог → категории → пагинация → товары; архив блога цепочкой (D38) | — |
| Inbox | не наполняется сайтом: нужны сообщения агента/владельца | — |
| Settings*, Set*, Sc* (настройки скана) | не зависят от сайта; конфиг краула стенда — `crawl-config.json` в README | — |

## Что ядро пока не умеет (на данных стенда)

Подробно — в `ОТЧЁТ.md` прогона. Коротко: `REDIRECT_CHAIN`/`REDIRECT_LOOP` не срабатывают на
нативном скане (#930); проверки по инлинкам (`BROKEN_INTERNAL_LINK`, `LINK_TO_5XX`,
`INTERNAL_LINK_TO_REDIRECT`) и по sitemap (`SITEMAP_URL_3XX`, `SITEMAP_URL_4XX_5XX`,
`SITEMAP_URL_NON_INDEXABLE`) пропущены; изображения не скачиваются (`IMG_OVER_KB`, битая
картинка — #935); ledger создаётся только из Python API (#926); мягкая 404 не входит в аудит
скана.
