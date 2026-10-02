"""Build bilingual, fully synthetic 23-page SEO report examples."""

from __future__ import annotations

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from seohead.reports.chromium_pdf import print_to_pdf

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "seo-audit-sample"
OUT.mkdir(exist_ok=True)

BLUE, ORANGE, GREEN, VIOLET, TEAL = "#2867d8", "#ed812e", "#16a57a", "#8462d9", "#18a1a8"
INK, MUTED, PALE, BORDER = "#14233f", "#68758a", "#f4f7fb", "#dce4ef"
SERIES = [126, 144, 158, 171, 189, 202, 218, 240, 265, 288, 315, 348]
ALL_VISITS = [302, 324, 351, 388, 412, 456, 493, 528, 577, 635, 701, 782]
LEADS = [12, 14, 15, 17, 19, 20, 22, 24, 24, 27, 30, 34]
MONTHS = {
    "en": ["Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct"],
    "ru": ["Ноя", "Дек", "Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт"],
}
T = {
    "en": {
        "sample": "SYNTHETIC SAMPLE · FICTIONAL DATA",
        "title": "SEO performance report",
        "subtitle": "Demo Company · October 2026",
        "comparison": "Compared with September 2026 and October 2025",
        "scope": "Illustrative 12-month series · no live sources connected",
        "cover_tags": ["Organic search", "Technical SEO", "Leads", "Next steps"],
        "source": "Synthetic example data",
        "dashboard": "October at a glance: search visits rose 10% month over month",
        "kpis": ["Organic visits", "Visits from Google", "Qualified contacts", "Search clicks", "Indexed sample URLs", "All site visits"],
        "chapter_seo": "Organic visibility",
        "chapter_seo_sub": "Visits, search engines, index coverage, landing pages and queries",
        "seo_visits": "Organic visits: 348, up 100% year over year",
        "year_total": "12-month total",
        "same_month": "same month last year",
        "month_avg": "monthly average",
        "visits_trend": "Organic search visits · 12 months",
        "day_title": "Search activity is stronger on weekdays",
        "weekday": "Weekday",
        "weekend": "Weekend",
        "per_day": "visits per day",
        "daily": "Average organic visits by day type",
        "engines_title": "Both search engines contributed to growth",
        "engine": "Search engine",
        "direct": "Direct",
        "referral": "Referral",
        "prev": "September",
        "current": "October",
        "impressions_title": "Search visibility expanded across both engines",
        "impressions": "Impressions",
        "clicks": "Clicks",
        "position_title": "Average ranking positions improved in the sample",
        "position": "Average position",
        "index_title": "Most submitted sample URLs are indexed",
        "indexed": "Indexed",
        "not_indexed": "Not indexed / unknown",
        "sample_urls": "Sample URLs",
        "section_title": "Equipment and resource sections bring organic visits",
        "organic_visits": "Organic visits",
        "landing_title": "Top landing pages in the sample",
        "landing": "Landing page",
        "visits": "Visits",
        "change": "Change",
        "queries_title": "Queries with the most sample clicks",
        "query": "Search query",
        "audience_title": "The sample audience uses desktop and mobile",
        "desktop": "Desktop",
        "mobile": "Mobile",
        "tablet": "Tablet",
        "audience": "Audience",
        "all_title": "All-site visits grew steadily in the sample",
        "all_visits": "All visits",
        "chapter_leads": "Contact activity",
        "chapter_leads_sub": "How often visitors took a contact action, where they started and what they viewed",
        "lead_title": "34 visits included a contact action",
        "lead_visit": "Visits with a contact action",
        "lead_rate": "Contact-visit rate",
        "contact_actions": "Contact actions",
        "calls": "Phone clicks",
        "email": "Email clicks",
        "messenger": "Messenger clicks",
        "form": "Form starts",
        "lead_engine_title": "Organic search brought most contact visits",
        "lead_sources": "Contact visits by source",
        "lead_trend_title": "Contact visits increased over the year",
        "three_month": "Latest 3 months",
        "prior_three": "Previous 3 months",
        "lead_entry_title": "Visitors contacted the demo company from key sections",
        "entry": "Entry section",
        "actions": "Actions",
        "interest_title": "Product and resource pages generated sample interest",
        "group": "Page group",
        "region_title": "Sample contact visits by region",
        "region": "Region",
        "work_divider": "Work completed",
        "work_sub": "Illustrative examples of work items · not a client record",
        "work_title": "Example work delivered in October",
        "work": [
            ("Content structure", "Created 12 synthetic category and guide pages to make the site easier to navigate."),
            ("Technical cleanup", "Reviewed sample canonicals, sitemap coverage and page titles across 40 example URLs."),
            ("Measurement", "Separated search visits from contact actions in the example reporting model."),
            ("Landing pages", "Improved internal paths between product groups and educational resources."),
            ("Quality review", "Checked desktop and mobile templates using synthetic test cases."),
            ("Reporting", "Saved a repeatable baseline for comparison with the next sample period."),
        ],
        "next_divider": "Next steps",
        "next_sub": "Suggested checks for the following reporting period",
        "next_title": "Plan and information needed",
        "plan": "Next-period plan",
        "needs": "Needed from the site owner",
        "plan_items": ["Review the sample contact form on mobile.", "Expand the technical guide section.", "Recheck indexing for the 8 sample URLs that remain unknown.", "Compare the next monthly sample with this baseline."],
        "need_items": ["Confirm the priority product groups.", "Share any planned site changes that could affect URLs.", "Review the sample contact actions and their definitions."],
        "disclaimer": "All names, dates, URLs, metrics, tables and charts in this file are invented for demonstration. They are not client data, measurements, forecasts or SEO claims.",
        "no_data": "No live data",
        "geo": ["Central", "North", "West", "South", "East"],
        "engines": ["Yandex", "Google", "Other"],
        "sections": ["Products", "Guides", "Industries", "About", "News"],
        "pages": ["/products/model-a/", "/guides/selection/", "/industries/water/", "/products/model-b/", "/about/"],
        "queries": ["industrial pump guide", "select a water pump", "pump model alpha", "equipment maintenance", "water system supplier"],
        "regions": ["Central", "North", "West", "South", "East"],
        "groups": ["Model A", "Model B", "Water systems", "Guides", "Accessories"],
    },
    "ru": {
        "sample": "СИНТЕТИЧЕСКИЙ ПРИМЕР · ВЫМЫШЛЕННЫЕ ДАННЫЕ",
        "title": "Отчёт о результатах SEO",
        "subtitle": "Демо-компания · октябрь 2026",
        "comparison": "Сравнение с сентябрём 2026 и октябрём 2025",
        "scope": "Условный ряд за 12 месяцев · реальные источники не подключены",
        "cover_tags": ["Поиск", "Техническое SEO", "Обращения", "Следующие шаги"],
        "source": "Синтетические данные для примера",
        "dashboard": "Октябрь в цифрах: поисковые визиты выросли на 10% за месяц",
        "kpis": ["Визиты из поиска", "Визиты из Google", "Обращения", "Клики из поиска", "В индексе, примеры URL", "Все визиты на сайт"],
        "chapter_seo": "Видимость в поиске",
        "chapter_seo_sub": "Визиты, поисковые системы, индексация, посадочные страницы и запросы",
        "seo_visits": "348 поисковых визитов: вдвое больше, чем год назад",
        "year_total": "За 12 месяцев",
        "same_month": "в том же месяце год назад",
        "month_avg": "в среднем за месяц",
        "visits_trend": "Визиты из поиска · 12 месяцев",
        "day_title": "В будни из поиска приходят чаще",
        "weekday": "Будни",
        "weekend": "Выходные",
        "per_day": "визитов в день",
        "daily": "Среднее число поисковых визитов по типу дня",
        "engines_title": "Обе поисковые системы дали прирост",
        "engine": "Поисковая система",
        "direct": "Прямые визиты",
        "referral": "Переходы по ссылкам",
        "prev": "Сентябрь",
        "current": "Октябрь",
        "impressions_title": "Показы выросли в обеих поисковых системах",
        "impressions": "Показы",
        "clicks": "Клики",
        "position_title": "Средняя позиция улучшилась в примере",
        "position": "Средняя позиция",
        "index_title": "Большинство тестовых страниц найдено в поиске",
        "indexed": "В индексе",
        "not_indexed": "Нет в индексе / статус неизвестен",
        "sample_urls": "Тестовых URL",
        "section_title": "Каталог и полезные материалы получают поисковые визиты",
        "organic_visits": "Визиты из поиска",
        "landing_title": "Популярные посадочные страницы в примере",
        "landing": "Посадочная страница",
        "visits": "Визиты",
        "change": "Изменение",
        "queries_title": "Запросы с наибольшим числом кликов в примере",
        "query": "Поисковый запрос",
        "audience_title": "В примере есть аудитория с компьютеров и телефонов",
        "desktop": "Компьютер",
        "mobile": "Телефон",
        "tablet": "Планшет",
        "audience": "Аудитория",
        "all_title": "Число визитов на сайт росло весь год",
        "all_visits": "Все визиты",
        "chapter_leads": "Контактные действия",
        "chapter_leads_sub": "Как часто посетители связывались с компанией, откуда начинали и что смотрели",
        "lead_title": "В 34 визитах было контактное действие",
        "lead_visit": "Визиты с контактом",
        "lead_rate": "Доля визитов с контактом",
        "contact_actions": "Контактные действия",
        "calls": "Клики по телефону",
        "email": "Клики по email",
        "messenger": "Переходы в мессенджер",
        "form": "Начатые формы",
        "lead_engine_title": "Большинство контактных визитов пришло из поиска",
        "lead_sources": "Контактные визиты по источникам",
        "lead_trend_title": "Контактных визитов стало больше за год",
        "three_month": "Последние 3 месяца",
        "prior_three": "Предыдущие 3 месяца",
        "lead_entry_title": "Посетители связывались с демо-компанией с важных разделов",
        "entry": "Начальный раздел",
        "actions": "Действия",
        "interest_title": "Страницы товаров и материалов вызвали интерес в примере",
        "group": "Раздел сайта",
        "region_title": "Контактные визиты по условным регионам",
        "region": "Регион",
        "work_divider": "Что сделано",
        "work_sub": "Условные примеры выполненных задач · это не история клиента",
        "work_title": "Примеры работ за октябрь",
        "work": [
            ("Структура контента", "Подготовлено 12 вымышленных страниц категорий и руководств для удобной навигации."),
            ("Технические улучшения", "Проверены канонические URL, покрытие карты сайта и заголовки на 40 тестовых страницах."),
            ("Измерение результата", "Поисковые визиты отделены от контактных действий в модели примера."),
            ("Посадочные страницы", "Добавлены внутренние переходы между группами товаров и полезными материалами."),
            ("Контроль качества", "Шаблоны проверены на компьютере и телефоне на синтетических данных."),
            ("Отчётность", "Сохранена повторяемая исходная точка для сравнения со следующим месяцем."),
        ],
        "next_divider": "Следующие шаги",
        "next_sub": "Рекомендуемые проверки в следующем отчётном периоде",
        "next_title": "План и что потребуется",
        "plan": "План на следующий период",
        "needs": "Нужно от владельца сайта",
        "plan_items": ["Проверить форму обращения на телефоне.", "Расширить раздел технических руководств.", "Повторно проверить индексацию 8 тестовых URL с неизвестным статусом.", "Сравнить следующий отчётный месяц с этой базой."],
        "need_items": ["Подтвердить приоритетные группы товаров.", "Сообщить о планируемых изменениях сайта и URL.", "Проверить определения контактных действий в примере."],
        "disclaimer": "Все названия, даты, URL, показатели, таблицы и графики в файле вымышлены для демонстрации. Это не данные клиента, измерения, прогнозы или SEO-утверждения.",
        "no_data": "Нет реальных данных",
        "geo": ["Центральный", "Северный", "Западный", "Южный", "Восточный"],
        "engines": ["Яндекс", "Google", "Другие"],
        "sections": ["Каталог", "Руководства", "Отрасли", "О компании", "Новости"],
        "pages": ["/products/model-a/", "/guides/selection/", "/industries/water/", "/products/model-b/", "/about/"],
        "queries": ["руководство по промышленным насосам", "как выбрать водяной насос", "насос модель альфа", "обслуживание оборудования", "поставщик систем водоснабжения"],
        "regions": ["Центральный", "Северный", "Западный", "Южный", "Восточный"],
        "groups": ["Модель A", "Модель B", "Системы воды", "Руководства", "Аксессуары"],
    },
}


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def svg_bars(values: list[int], labels: list[str], color: str = BLUE, previous: list[int] | None = None) -> str:
    w, h, left, top, bottom = 980, 300, 55, 18, 48
    maxv = max(values + (previous or [0])) * 1.15
    plot_h, plot_w = h - top - bottom, w - left - 10
    slot, bw = plot_w / len(values), min(38, plot_w / len(values) * .58)
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Sample bar chart">']
    for tick in range(4):
        y = top + plot_h * tick / 3
        val = maxv * (3 - tick) / 3
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{w-8}" y2="{y:.1f}" stroke="#e7edf5"/><text x="{left-8}" y="{y+4:.1f}" text-anchor="end">{val:.0f}</text>')
    for i, value in enumerate(values):
        x = left + i * slot + (slot - bw) / 2
        bh = value / maxv * plot_h
        if previous:
            ph = previous[i] / maxv * plot_h
            parts.append(f'<rect x="{x:.1f}" y="{top+plot_h-ph:.1f}" width="{bw:.1f}" height="{ph:.1f}" rx="4" fill="#c8d2e1"/>')
        parts.append(f'<rect x="{x:.1f}" y="{top+plot_h-bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="5" fill="{color}"/>')
        parts.append(f'<text x="{x+bw/2:.1f}" y="{h-16}" text-anchor="middle">{e(labels[i])}</text>')
    return ''.join(parts) + '</svg>'


def svg_lines(series: list[tuple[str, list[int], str]], labels: list[str]) -> str:
    w, h, left, top, bottom = 980, 300, 55, 18, 48
    vals = [x for _, v, _ in series for x in v]
    maxv = max(vals) * 1.15
    ph, pw = h-top-bottom, w-left-12
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img">']
    for tick in range(4):
        y=top+ph*tick/3;v=maxv*(3-tick)/3
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{w-8}" y2="{y:.1f}" stroke="#e7edf5"/><text x="{left-8}" y="{y+4:.1f}" text-anchor="end">{v:.0f}</text>')
    for name, values, color in series:
        points=[]
        for i,v in enumerate(values):
            x=left+i*pw/(len(values)-1);y=top+ph-v/maxv*ph;points.append((x,y))
        path=' '.join(f'{x:.1f},{y:.1f}' for x,y in points)
        parts.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>')
        for x,y in points: parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{color}"/>')
    for i,label in enumerate(labels):
        x=left+i*pw/(len(labels)-1);parts.append(f'<text x="{x:.1f}" y="{h-16}" text-anchor="middle">{e(label)}</text>')
    return ''.join(parts)+'</svg>'


def bars_html(values: list[int], labels: list[str], colors: list[str] | None = None) -> str:
    colors = colors or [BLUE] * len(values)
    peak=max(values) or 1
    return '<div class="barlist">'+''.join(
        f'<div class="barrow"><span>{e(label)}</span><span class="track"><i style="width:{v/peak*100:.1f}%;background:{colors[i]}"></i></span><b>{v}</b></div>'
        for i,(label,v) in enumerate(zip(labels,values)))+'</div>'


def cards(items: list[tuple[str,str,str]], cols: int = 3) -> str:
    return f'<div class="cards cols{cols}">'+''.join(f'<article class="card"><small>{e(k)}</small><strong>{e(v)}</strong><em>{e(s)}</em></article>' for k,v,s in items)+'</div>'


def table(headers: list[str], rows: list[list[str]]) -> str:
    return '<table><thead><tr>'+''.join(f'<th>{e(x)}</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join(f'<td>{e(x)}</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table>'


def bullets(items: list[str]) -> str:
    return '<ul class="bullets">'+''.join(f'<li>{e(x)}</li>' for x in items)+'</ul>'


def donut(labels: list[str], vals: list[int], colors: list[str]) -> str:
    total=sum(vals);pos=0;segs=[]
    for v,c in zip(vals,colors):
        frac=v/total;end=pos+frac
        segs.append(f'{c} {pos*100:.3f}% {end*100:.3f}%');pos=end
    legend=''.join(f'<div class="legendrow"><i style="background:{c}"></i><span>{e(label)}</span><b>{v}%</b></div>' for label,v,c in zip(labels,vals,colors))
    return '<div class="donutwrap"><div class="donut" style="background:conic-gradient('+','.join(segs)+')"></div><div class="legend">'+legend+'</div></div>'


def page(lang: str, number: int, title: str, content: str, chapter: str = 'SEO', lead: str = '') -> str:
    t=T[lang]
    return f'''<section class="page"><header><div class="brand"><span class="mark"><i></i><i></i><i></i></span><b>DEMO COMPANY</b><span class="sample">{e(t['sample'])}</span><span class="chapter">{e(chapter.upper())}</span></div><div class="period">{e(t['subtitle'])}</div><h1>{e(title)}</h1>{f'<p class="lead">{e(lead)}</p>' if lead else ''}</header><main>{content}</main><footer><span>{e(t['source'])} · {e(t['disclaimer'])}</span><b>{number:02d} / 23</b></footer></section>'''


def divider(lang: str, number: int, num: str, heading: str, sub: str) -> str:
    t=T[lang]
    return f'''<section class="page divider"><div class="brand"><span class="mark"><i></i><i></i><i></i></span><b>DEMO COMPANY</b><span class="sample">{e(t['sample'])}</span></div><div class="division"><b>{num}</b><div><h1>{e(heading)}</h1><p>{e(sub)}</p></div></div><footer><span>{e(t['source'])} · {e(t['disclaimer'])}</span><b>{number:02d} / 23</b></footer></section>'''


def build(lang: str) -> str:
    t=T[lang];pages=[]
    # 1 · Cover. The geometric symbol is newly drawn and does not reproduce a client logo.
    pages.append(f'''<section class="page cover"><div class="cover-art"><span></span><span></span><span></span><span></span></div><div class="coverbrand"><span class="mark"><i></i><i></i><i></i></span><b>DEMO COMPANY</b><em>{e(t['sample'])}</em></div><div class="coverbody"><small>SEO · SEARCH VISIBILITY · CONTACT ACTIVITY · PLAN</small><h1>{e(t['title'])}</h1><h2>{e(t['subtitle'])}</h2><p>{e(t['comparison'])}</p><div class="tags">{''.join(f'<i>{e(x)}</i>' for x in t['cover_tags'])}</div><div class="disclaimer">{e(t['disclaimer'])}</div></div><div class="coverfoot"><span>REPORTING PERIOD<br><b>01–31 OCT 2026</b></span><span>COMPARISON<br><b>SEP 2026 · OCT 2025</b></span><span>SAMPLE DOMAIN<br><b>demo.example.invalid</b></span></div></section>''')
    # 2 · Executive dashboard.
    kpis=[(t['kpis'][0],'348','+10% MoM · +100% YoY'),(t['kpis'][1],'126','synthetic example'),(t['kpis'][2],'34','+13% MoM'),(t['kpis'][3],'412','sample clicks'),(t['kpis'][4],'142 / 150','8 unknown'),(t['kpis'][5],'782','+12% MoM')]
    pages.append(page(lang,2,t['dashboard'],cards(kpis)+f'<div class="callout">{e(t["scope"])}</div>',lead=t['comparison']))
    pages.append(divider(lang,3,'01',t['chapter_seo'],t['chapter_seo_sub']))
    pages.append(page(lang,4,t['seo_visits'],cards([(t['year_total'],'2,664','synthetic'),(t['same_month'],'174','synthetic'),(t['month_avg'],'222','synthetic')])+f'<div class="charttitle">{e(t["visits_trend"])}</div>'+svg_bars(SERIES,MONTHS[lang],BLUE),lead=t['scope']))
    pages.append(page(lang,5,t['day_title'],cards([(t['weekday'],'15.4',t['per_day']),(t['weekend'],'7.2',t['per_day'])],2)+f'<div class="charttitle">{e(t["daily"])}</div>'+svg_bars([15,16,14,17,13,8,7],(['Mon','Tue','Wed','Thu','Fri','Sat','Sun'] if lang=='en' else ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']),GREEN)))
    pages.append(page(lang,6,t['engines_title'],table([t['engine'],t['prev'],t['current'],t['change']],[[t['engines'][0],'190','210','+11%'],[t['engines'][1],'115','126','+10%'],[t['engines'][2],'10','12','+20%']])+svg_lines([(t['engines'][0],[78,86,92,104,117,125,139,151,165,177,190,210],BLUE),(t['engines'][1],[45,50,56,62,67,74,80,88,95,103,115,126],ORANGE)],MONTHS[lang])))
    pages.append(page(lang,7,t['impressions_title'],cards([(f'{t["impressions"]} · {t["engines"][0]}','18,420','+9% MoM'),(f'{t["impressions"]} · {t["engines"][1]}','11,280','+7% MoM'),(f'{t["clicks"]} · {t["engines"][0]}','245','+8% MoM'),(f'{t["clicks"]} · {t["engines"][1]}','167','+12% MoM')],2)+svg_lines([(t['engines'][0],[12000,12600,13200,13900,14500,15300,15800,16400,17000,17300,16900,18420],BLUE),(t['engines'][1],[7200,7600,7900,8200,8500,9000,9400,9800,10100,10400,10500,11280],ORANGE)],MONTHS[lang]),chapter=t['chapter_seo']))
    rank_labels=(['Ranks 1–3','Ranks 4–10','Ranks 11–20','Ranks 21–50','Beyond 50'] if lang=='en' else ['Позиции 1–3','Позиции 4–10','Позиции 11–20','Позиции 21–50','Ниже 50'])
    pages.append(page(lang,8,t['position_title'],cards([(f'{t["position"]} · {t["engines"][0]}','12.4','improved from 14.1'),(f'{t["position"]} · {t["engines"][1]}','14.9','improved from 16.2')],2)+bars_html([12,15,21,28,34],rank_labels,[VIOLET]*5),chapter=t['chapter_seo']))
    pages.append(page(lang,9,t['index_title'],cards([(f'{t["indexed"]} · {t["engines"][0]}','142 / 150','94.7%'),(f'{t["indexed"]} · {t["engines"][1]}','131 / 150','87.3%')],2)+donut([t['indexed'],t['not_indexed']],[91,9],[GREEN,ORANGE])+f'<div class="callout">300 engine-by-URL observations · 273 indexed · 27 not indexed or unknown · demo.example.invalid</div>',chapter=t['chapter_seo']))
    section_vals=[138,91,63,34,22]
    pages.append(page(lang,10,t['section_title'],bars_html(section_vals,t['sections'][:5],[BLUE,TEAL,GREEN,ORANGE,VIOLET]),chapter=t['chapter_seo']))
    pages.append(page(lang,11,t['landing_title'],table([t['landing'],t['visits'],t['change']],[[x,str(v),ch] for x,v,ch in zip(t['pages'],[92,68,53,41,32],['+18%','+12%','+9%','+7%','+5%'])]),chapter=t['chapter_seo']))
    pages.append(page(lang,12,t['queries_title'],table([t['query'],t['clicks'],t['position']],[[q,str(c),str(p)] for q,c,p in zip(t['queries'][:5],[54,43,37,28,21],[8.2,10.4,12.1,14.6,16.3])]),chapter=t['chapter_seo']))
    pages.append(page(lang,13,t['audience_title'],donut([t['desktop'],t['mobile'],t['tablet']],[58,37,5],[BLUE,ORANGE,GREEN])+cards([(t['audience'],'704','synthetic users'),('Returning' if lang=='en' else 'Вернувшиеся','22%','synthetic share')],2),chapter=t['chapter_seo']))
    pages.append(page(lang,14,t['all_title'],cards([(t['all_visits'],'782','+12% MoM'),('Organic' if lang=='en' else 'Из поиска','348','44.5%'),('Direct' if lang=='en' else 'Прямые','251','32.1%')],3)+svg_bars(ALL_VISITS,MONTHS[lang],BLUE),chapter=t['chapter_seo']))
    pages.append(divider(lang,15,'02',t['chapter_leads'],t['chapter_leads_sub']))
    pages.append(page(lang,16,t['lead_title'],cards([(t['lead_visit'],'34','+13% MoM'),(t['lead_rate'],'4.3%','synthetic rate'),(t['contact_actions'],'49','multiple actions'),(t['calls'],'21','synthetic'),(t['email'],'12','synthetic'),(t['messenger'],'9','synthetic'),(t['form'],'7','synthetic'),('Contact-page views' if lang=='en' else 'Просмотры контактов','18','synthetic')],3),chapter=t['chapter_leads']))
    pages.append(page(lang,17,t['lead_engine_title'],cards([(t['engines'][0],'16','47%'),(t['engines'][1],'10','29%'),(t['direct'],'6','18%')],3)+donut([t['engines'][0],t['engines'][1],t['direct'],t['referral']],[47,29,18,6],[BLUE,ORANGE,GREEN,VIOLET]),chapter=t['chapter_leads']))
    pages.append(page(lang,18,t['lead_trend_title'],cards([(t['three_month'],'91','latest period'),(t['prior_three'],'70','previous period'),('Change' if lang=='en' else 'Изменение','+30%','synthetic comparison')],3)+svg_bars(LEADS,MONTHS[lang],GREEN),chapter=t['chapter_leads']))
    pages.append(page(lang,19,t['lead_entry_title'],table([t['entry'],t['lead_visit'],t['actions']],[[t['sections'][0],'13','19'],[t['sections'][1],'7','11'],[t['sections'][2],'6','8'],[t['sections'][3],'4','6'],[t['sections'][4],'4','5']]),chapter=t['chapter_leads']))
    pages.append(page(lang,20,t['interest_title'],bars_html([25,19,16,12,8],t['groups'],[BLUE,TEAL,GREEN,ORANGE,VIOLET]),chapter=t['chapter_leads']))
    pages.append(page(lang,21,t['region_title'],table([t['region'],t['lead_visit'],t['change']],[[r,str(v),c] for r,v,c in zip(t['regions'],[10,7,5,4,2],['+11%','+8%','+6%','+4%','+3%'])]),chapter=t['chapter_leads']))
    pages.append(divider(lang,22,'03',t['work_divider'],t['work_sub']))
    work=''.join(f'<article class="work"><b>{e(title)}</b><p>{e(body)}</p></article>' for title,body in t['work'])
    pages.append(page(lang,23,t['work_title'],f'<div class="workgrid">{work}</div><div class="nextgrid"><section><h2>{e(t["plan"])}</h2>{bullets(t["plan_items"])}</section><section><h2>{e(t["needs"])}</h2>{bullets(t["need_items"])}</section></div>',chapter=t['next_divider'],lead=t['next_sub']))
    return '<!doctype html><html lang="'+lang+'"><meta charset="utf-8"><title>'+e(t['title'])+' · synthetic sample</title><style>'+CSS+'</style><body>'+''.join(pages)+'</body></html>'


CSS = f'''
@page {{ size: 13.333in 7.5in; margin: 0 }}
* {{ box-sizing:border-box }} html,body {{ margin:0; padding:0; font-family:Arial,"Noto Sans",sans-serif;color:{INK};-webkit-print-color-adjust:exact;print-color-adjust:exact }}
.page {{ width:1280px;height:720px;position:relative;overflow:hidden;padding:54px 70px 52px;background:#fff;page-break-after:always;break-after:page }}
.page:last-child {{page-break-after:auto;break-after:auto}}
header {{ min-height:112px }} .brand,.coverbrand {{display:flex;align-items:center;gap:12px;color:{INK};font-size:13px;letter-spacing:.04em}}
.mark {{width:32px;height:28px;display:flex;align-items:center;gap:3px;padding:3px;border:1px solid {BLUE};border-radius:8px;transform:skew(-12deg)}}.mark i {{display:block;width:6px;background:{BLUE};border-radius:4px}}.mark i:nth-child(1){{height:11px}}.mark i:nth-child(2){{height:18px}}.mark i:nth-child(3){{height:14px}}
.sample {{margin-left:5px;color:#8b5b12;background:#fff4d9;padding:6px 9px;border-radius:20px;font-size:9px;font-weight:700;letter-spacing:.06em}}
.chapter {{margin-left:auto;color:{BLUE};font-size:10px;font-weight:700;letter-spacing:.1em}}.period {{position:absolute;right:70px;top:64px;color:{MUTED};font-size:12px}}
h1 {{font-size:31px;line-height:1.16;letter-spacing:-.02em;margin:22px 0 7px;max-width:1050px}}.lead {{font-size:15px;color:{MUTED};margin:0;line-height:1.4}}
main {{margin-top:24px;height:485px;overflow:hidden}} footer {{position:absolute;bottom:17px;left:70px;right:70px;border-top:1px solid {BORDER};padding-top:8px;display:flex;justify-content:space-between;gap:20px;color:#78859a;font-size:8px}}footer span{{max-width:1070px}}footer b{{white-space:nowrap;color:{BLUE};font-size:10px}}
.cards {{display:grid;gap:14px;margin:6px 0 18px}}.cols3{{grid-template-columns:repeat(3,1fr)}}.cols2{{grid-template-columns:repeat(2,1fr)}}
.card {{border:1px solid {BORDER};border-top:4px solid {BLUE};border-radius:5px;background:{PALE};padding:15px 17px;min-height:105px;display:flex;flex-direction:column;gap:7px}}
.card small{{color:{MUTED};font-weight:700;font-size:12px}}.card strong{{font-size:27px;letter-spacing:-.02em}}.card em{{font-size:11px;color:{GREEN};font-style:normal;font-weight:700}}
.charttitle{{font-weight:700;font-size:15px;margin:4px 0 6px}}svg{{width:100%;height:260px}}svg text{{font-size:12px;fill:{MUTED};font-family:Arial,"Noto Sans",sans-serif}}
.barlist{{display:flex;flex-direction:column;gap:18px;padding:24px 10px}}.barrow{{display:grid;grid-template-columns:230px 1fr 50px;gap:14px;align-items:center;font-size:15px}}.barrow span:first-child{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.track{{height:20px;background:#edf1f7;border-radius:10px;overflow:hidden}}.track i{{display:block;height:100%;border-radius:10px}}
table{{border-collapse:collapse;width:100%;font-size:14px;margin-top:12px}}th{{text-align:left;background:#eaf0f8;color:{INK};padding:12px 13px;font-size:12px}}td{{border-bottom:1px solid #e9edf3;padding:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}tr:nth-child(even) td{{background:#fafbfd}}td:first-child{{width:58%}}
.donutwrap{{display:flex;align-items:center;justify-content:center;gap:80px;height:280px}}.donut{{position:relative;width:220px;height:220px;border-radius:50%}}.donut:after{{content:"";position:absolute;inset:52px;border-radius:50%;background:#fff}}.legend{{min-width:260px;display:flex;flex-direction:column;gap:18px}}.legendrow{{display:grid;grid-template-columns:14px 1fr 48px;gap:10px;align-items:center;font-size:14px}}.legendrow i{{width:12px;height:12px;border-radius:4px}}.legendrow b{{text-align:right}}
.callout{{padding:14px 17px;border-left:4px solid {ORANGE};background:#fff8eb;color:{MUTED};font-size:12px;margin-top:10px}}
.bullets{{margin:0;padding-left:20px;line-height:1.55;font-size:12px}}.bullets li{{margin:8px 0}}
.workgrid{{display:grid;grid-template-columns:repeat(3,1fr);gap:13px}}.work{{padding:14px 16px;border:1px solid {BORDER};border-radius:6px;background:{PALE};min-height:114px}}.work b{{font-size:14px}}.work p{{margin:8px 0 0;color:{MUTED};font-size:11px;line-height:1.4}}
.nextgrid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:13px}}.nextgrid section{{border-top:3px solid {GREEN};padding:10px 12px;background:#f8fbfa}}.nextgrid section+section{{border-color:{ORANGE};background:#fffaf4}}.nextgrid h2{{font-size:13px;margin:0 0 7px}}
.cover{{padding:0;background:linear-gradient(135deg,#123774 0%,#2867d8 65%,#638ce2 100%);color:#fff}}.coverbrand{{position:absolute;top:62px;left:75px;color:#fff}}.cover .mark{{border-color:#fff}}.cover .mark i{{background:#fff}}.coverbrand .sample{{margin-left:20px}}.cover-art span{{position:absolute;border:1px solid rgba(255,255,255,.16);border-radius:50%;width:500px;height:500px;right:-130px;top:-180px}}.cover-art span:nth-child(2){{width:390px;height:390px;right:-74px;top:-122px}}.cover-art span:nth-child(3){{width:275px;height:275px;right:-17px;top:-63px}}.cover-art span:nth-child(4){{width:8px;height:8px;background:#fff;right:210px;top:163px}}
.coverbody{{position:absolute;left:82px;top:192px;width:940px}}.coverbody small{{letter-spacing:.25em;font-size:11px;color:#dce8ff}}.coverbody h1{{font-size:56px;line-height:1.04;margin:18px 0 16px;color:#fff}}.coverbody h2{{font-size:27px;margin:0 0 11px;color:#e9f0ff}}.coverbody p{{font-size:16px;color:#dce8ff;margin:0}}.tags{{display:flex;gap:9px;margin-top:25px}}.tags i{{font-style:normal;border:1px solid rgba(255,255,255,.35);border-radius:20px;padding:8px 12px;font-size:11px}}.disclaimer{{margin-top:35px;max-width:810px;color:#e2eaff;font-size:10px;line-height:1.5}}
.coverfoot{{position:absolute;bottom:44px;left:82px;right:82px;display:flex;gap:90px;color:#dce8ff;font-size:9px;letter-spacing:.08em}}.coverfoot span{{line-height:1.7}}.coverfoot b{{color:#fff;font-size:11px}}
.divider{{background:linear-gradient(120deg,#f0f4fa,#fbfcfe)}}.divider .brand{{position:absolute;top:46px;left:70px;right:70px}}.division{{position:absolute;left:125px;top:250px;display:flex;align-items:center;gap:35px}}.division>b{{font-size:116px;color:{BLUE};line-height:1}}.division h1{{font-size:47px;margin:0 0 12px}}.division p{{font-size:17px;max-width:650px;line-height:1.45;color:{MUTED};margin:0}}
'''


def main() -> None:
    for lang in ("ru", "en"):
        source=OUT/f".seo-audit-sample-{lang}.html"
        dest=OUT/f"seo-audit-sample-{lang}.pdf"
        source.write_text(build(lang),encoding="utf-8")
        result=print_to_pdf(source,dest,timeout=90)
        if result["status"]!="ok":
            raise SystemExit(f"PDF render failed ({lang}): {result}")
        import fitz
        doc=fitz.open(dest)
        if len(doc)!=23:
            raise SystemExit(f"Expected 23 pages, got {len(doc)} ({lang})")
        doc.set_metadata({"title":f"SEO performance report · SYNTHETIC SAMPLE ({lang.upper()})","author":"SEOHEAD Tools sample","subject":"Fictional data for demonstration; not client measurements","keywords":"synthetic, sample, seo, report","creator":"SEOHEAD Tools synthetic example renderer","producer":"Chromium PDF renderer"})
        tmp=dest.with_suffix(".metadata.pdf")
        doc.save(tmp,garbage=4,deflate=True)
        doc.close();tmp.replace(dest)
        check=fitz.open(dest)
        if len(check)!=23 or "SYNTHETIC SAMPLE" not in " ".join(p.get_text() for p in check[:2]) and "СИНТЕТИЧЕСКИЙ ПРИМЕР" not in " ".join(p.get_text() for p in check[:2]):
            raise SystemExit(f"PDF validation failed ({lang})")
        check.close()
        source.unlink(missing_ok=True)
        print(f"Created {dest.name}: 23 pages, {dest.stat().st_size} bytes")


if __name__ == "__main__":
    main()
