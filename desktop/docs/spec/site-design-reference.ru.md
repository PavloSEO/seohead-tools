# Дизайн-система сайта SEOHEAD: извлечённый референс для приложения

Источник: локальный Next.js-репозиторий `~/Work/projects/my-own/seohead.tech`, commit `3bbd32d9d5d3a0f7c419e8d1901e13c76adec7df`. Сайт не менялся. Этот документ описывает прочитанные исходники; это не новый дизайн, макет или проверка текущего production CSS в браузере.

## 1. Что считать основой

**Рекомендация для Claude: собственная система SEOHEAD Desktop.** Сохранить фирменные роли цвета, Roboto, Material Symbols и спокойный характер компонентов. Организацию рабочего приложения проектировать на Qt Widgets: плотные таблицы, инспекторы, компактные toolbar, splitters и keyboard focus. Полное следование всем мобильным/маркетинговым MD3-компонентам не требуется; сторонний Fluent/MUI/shadcn-kit не нужен только ради оформления.

Это рекомендация адаптации, а не утверждение, что desktop-система уже реализована. Визуальный дизайн составляет Claude на основе ТЗ и этого референса. Иконки Material Symbols можно использовать отдельно от всего MD3.

Приоритет источников: действующий `frontend/src/app/globals.css` → фактические компоненты/frontend layout → `design/colors_and_type.css` как расширенный reference-kit → старые prose-документы. В `design/README.md` есть устаревшие сведения о Tailwind/локалях; фактический проект явно описывает CSS custom properties, отсутствие Tailwind/shadcn/CSS-in-JS и en-only сайт. Язык сайта не задаёт язык desktop: приложение по текущему ТЗ русскоязычное.

## 2. Реальные цветовые роли frontend

Источник `frontend/src/app/globals.css:2235`, блок `:root` (извлечено 68 токенов). Роли ниже — из действующего CSS, а не новая палитра.

| Роль `--md-sys-color-*` | Значение |
|---|---|
| `primary` | `#1565C0` |
| `on-primary` | `#FFFFFF` |
| `primary-container` | `#D3E4FF` |
| `on-primary-container` | `#001D36` |
| `secondary` | `#525F7A` |
| `secondary-container` | `#D9E3F9` |
| `tertiary` | `#64549B` |
| `error` | `#B3261E` |
| `error-container` | `#FFDAD6` |
| `surface` | `#FDFCFF` |
| `on-surface` | `#1C1B1F` |
| `on-surface-variant` | `#49454F` |
| `surface-container-lowest` | `#FFFFFF` |
| `surface-container-low` | `#F2EFF6` |
| `surface-container` | `#EDEAF2` |
| `surface-container-high` | `#E7E4EE` |
| `surface-container-highest` | `#E1DEE9` |
| `outline` | `#79747E` |
| `outline-variant` | `#CAC4D0` |
| `success` | `#2E7D32` |
| `success-container` | `#B7F0CC` |
| `warning` | `#F9A825` |
| `warning-container` | `#FFF0C2` |

Сохранять роли, а не подбирать произвольные похожие цвета. Числа/статусы нельзя передавать только цветом: нужны текст и доступные имена.

В badge-компонентах имеются отдельные компонентные пары: success `#C8F0D8 / #002114`, warning `#FFE2B7 / #281900`, info `#DAE3FF / #001A41` (globals.css:8288–8297). Они отличаются от общих success/warning-container: выбирать осознанно по виду элемента, не объявлять все значения одним токеном.

## 3. Шрифты и типографика

Frontend `[locale]/layout.tsx:16` загружает Roboto через next/font: latin+cyrillic, веса **400/500/700**. JetBrains Mono подключён отдельно для технических code-blocks, без preload. Desktop должен локально упаковать выбранные лицензированные font-assets, а не зависеть от next/font/CDN.

Расширенная типошкала ниже извлечена из `design/colors_and_type.css` (237 reference-токенов). Это reference-kit: не все эти custom properties объявлены в активном frontend. Особенно web prose line-height 26/22/19 не нужно механически переносить в строки плотной таблицы.

| Роль | Размер | Line-height reference | Вес |
|---|---|---|---|
| display-large | 57px | 64px | 400 |
| headline-large | 32px | 40px | 400 |
| headline-medium | 28px | 36px | 400 |
| headline-small | 24px | 32px | 400 |
| title-large | 22px | 28px | 400 |
| title-medium | 16px | 24px | 500 |
| title-small | 14px | 20px | 500 |
| label-large | 14px | 20px | 500 |
| label-medium | 12px | 16px | 500 |
| label-small | 11px | 16px | 500 |
| body-large | 16px | 26px | 400 |
| body-medium | 14px | 22px | 400 |
| body-small | 12px | 19px | 400 |

Для приложения: Roboto остаётся основным UI-шрифтом; размеры таблиц, toolbar и detail-pane подбирает Claude по плотности/читаемости и проверяет на всех DPI. Крупные display-стили предназначены для сайта и не должны занять рабочий экран. Моноширинный шрифт уместен для исходного HTML, JSON, headers и технического лога.

## 4. Отступы, формы и поверхности

Действующий frontend spacing: **4, 8, 12, 16, 20, 24, 28, 32, 40, 48, 64** px. Reference-kit содержит более короткую базовую шкалу 4/8/12/16/24/32/48/64. Форма: **0/4/8/12/16/28/9999**. Цвета поверхностей в таблице выше задают иерархию панелей.

В сайте app/prose max-width **1280/720 px**. Эти ограничения относятся к сайту; desktop-workbench должен использовать ширину окна, а не растягивать пустые поля вокруг центральной колонки 720 px.

У outlined card — 1 px outline-variant, radius 12, без тени. Elevated/filled варианты есть как reference. Для приложения преимущественно плоские поверхности, разделители и компактные рамки; отдельный widget/shadow на каждую ячейку запрещён требованиями памяти/скорости.

## 5. Фактические компоненты и состояния

| Референс сайта | Проверенный источник | Перенос в приложение |
|---|---|---|
| Filled/Tonal/Outlined/Text buttons, 14/500, gap 8, padding 10×24, pill radius | globals.css:3661–3725 | Сохранить роли; toolbar-кнопкам/плотным действиям дать компактные размеры и умеренные радиусы |
| Hover 8% black overlay, pressed 12%, transition 150ms | globals.css:3682–3695 | Qt state styles / paint; это не требование CSS transition в QSS |
| Focus-visible primary ring 3px +offset2 | globals.css:3393 | Видимый keyboard focus; реализовать доступным Qt-способом, проверить DPI |
| Filter/Input/Assist chips, selected/check и remove/close | ui/Chip.tsx + globals.css:8210–8270 | Быстрые фильтры и выбранные условия; состояние всегда связано с реальным query |
| Neutral/Info/Success/Warning/Error/Accent badges, Count/Dot/Difficulty | ui/Badge.tsx + globals.css:8277–8334 | Статусы, счётчики, приоритеты; не смешивать HTTP/code/coverage/repair |
| Сравнение `.comp-table`, KPI, timeline | globals.css:8867–9060 | Использовать как семантический ориентир сравнения и истории, не копировать маркетинговую раскладку |
| Reduce-motion правила | globals.css:2101 и другие scoped blocks | Отключать необязательную анимацию; прогресс остаётся данными |

У активного CSS встречаются route-specific градиенты и поздние component overrides, хотя brand skill запрещает градиенты для базового chrome. Поэтому не копировать весь globals.css в Qt и не трактовать любой поздний маркетинговый блок как обязательный дизайн-token.

## 6. Иконки

Сайт реально использует **Material Symbols Outlined**: базовый размер 24, dense helper 20/16; в layout подключён `icon-font.css`, сформированный `scripts/subset-icons.mjs` из используемых glyphs. Старый kit описывает CDN/ligatures, но production-source уже использует локальный build subset.

Для desktop сохранить смысл иконок, outline-характер и аккуратные размеры. Claude выбирает нужный subset для приложения и упаковывает SVG/другие поддержанные Qt-assets с upstream license/NOTICE. Qt WebEngine, браузерная оболочка и загрузка шрифтов с CDN для этого не требуются. Готовые новые иконки здесь не генерируются.

## 7. Граница переноса

**Сохранять:** фирменную палитру и semantic roles, основной Roboto, язык форм/состояний, спокойные кнопки/рамки, Material Symbols, доступный focus и читаемые числа.

**Адаптировать:** плотность и высоту строк, размеры toolbar, splitter/panel hierarchy, desktop navigation, DPI/keyboard, virtual model/cache, тени и анимацию. CSS box-shadow/transform/transition не являются переносимыми свойствами QSS. React-компоненты — справочные примеры; GUI реализуется через PyQt5/Qt Widgets.

**Не создавать автоматически:** новый дизайн, макеты, графику и полную новую палитру. Эти решения выполняет Claude в рамках задания.

## 8. Файлы примера

`Референс_сайта/site-globals.css` и `site-kit-colors-and-type.css` — неизменённые копии исходников. `Badge.tsx`, `Chip.tsx` — фактические примеры компонентов. `site-tokens.json` — извлечённые значения с разделением active frontend/reference kit. `source-manifest.json` фиксирует commit, пути и SHA256. Исходники сайта не изменены и не публиковались.
