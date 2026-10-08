"""Behavioural checks: each click must change the screen the way the design promises."""
import functools, http.server, socketserver, threading
from pathlib import Path
from playwright.sync_api import sync_playwright

import os, shutil
CANVAS = Path(__file__).resolve().parents[1] / "canvas"
SITE = Path(os.environ.get("QA_SITE_DIR", "/tmp/seohead-canvas-qa"))
BLOBS = {"4a414078cb0b30a9f33cd7ff3f95b4b1": "app.css", "91b098e33df1d5d3a71b967b3280aee7": "ext.css"}
ICONS = {  # canvas asset id -> design/v2/brand file
    "9bbee7a83aa50baa041e02870d2402e4": "seohead-logo.svg", "d9315c4d275086b182d8bb919224809e": "seohead-logo.svg",
    "bce3e94b48603d5d8de3a11a27b69aae": "archive/icon-w1-widow-lens.svg", "cc4a78a69360a9a1e1e4328ccfac514a": "archive/icon-w2-widow-circle.svg",
    "a572d412378799e410d964ec07bc22ab": "archive/icon-c-web-lens.svg", "f548a497248c4a505018463bb5d7f783": "archive/icon-b-lens-body.svg",
    "a15a8fcad9ccfd0d1de09fe7968d08f3": "archive/icon-a-spider-in-lens.svg", "e390fd15cde18709247904bd3664f706": "archive/icon-c2-web-spider.svg",
    "65933da0c68d185dbbf8237390427db0": "archive/icon-b-lens-body.svg", "2cce9143dda83fd5095c0f1c7facdb9c": "archive/icon-c-web-lens.svg",
    "1df587c503f38ebcf8640574615fbdec": "archive/icon-d-grip.svg", "1fd8127680f28131e51c411820d47f57": "archive/icon-b-lens-body.svg"}

def prepare_site():
    """Build a static mirror of the canvas: boards + CSS under their /_blob/ ids + the canvas runtime.

    The runtime (dc-runtime.js) belongs to the Design canvas type and is not committed here;
    download it from the canvas and pass its path in DC_RUNTIME.
    """
    runtime = os.environ.get("DC_RUNTIME")
    if not runtime or not Path(runtime).is_file():
        raise SystemExit("Set DC_RUNTIME=/path/to/dc-runtime.js (artifact-type/dc-runtime.js of the canvas)")
    (SITE / "_blob").mkdir(parents=True, exist_ok=True)
    for f in CANVAS.glob("*.dc.html"):
        shutil.copy(f, SITE / f.name)
    for blob, css in BLOBS.items():
        shutil.copy(CANVAS / "assets" / css, SITE / "_blob" / blob)
    for blob, svg in ICONS.items():
        shutil.copy(CANVAS.parent / "brand" / svg, SITE / "_blob" / blob)
    shutil.copy(runtime, SITE / "support.js")

prepare_site()
class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def guess_type(self, p): return ("image/svg+xml" if Path(str(p)).name in ICONS else "text/css") if "/_blob/" in str(p) else super().guess_type(p)
srv = socketserver.TCPServer(("127.0.0.1", 0), functools.partial(H, directory=str(SITE)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}/"
results = []

def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))

with sync_playwright() as p:
    b = p.chromium.launch()
    def open_(board, w=1440, h=900):
        pg = b.new_page(viewport={"width": w, "height": h}); pg.goto(BASE + board); pg.wait_for_timeout(1200); return pg
    T = lambda pg: pg.inner_text("body")

    pg = open_("Main.dc.html")
    check("URL: фильтр HTTP показывает только 3xx–5xx", "Фильтр по всему запуску · 174" in T(pg))
    pg.get_by_label("Убрать фильтр HTTP").click(); pg.wait_for_timeout(150)
    check("URL: снять фильтр HTTP → 1 248 URL", "1 248 URL" in T(pg))
    pg.get_by_label("Убрать фильтр глубины").click(); pg.wait_for_timeout(150)
    check("URL: без фильтров → 1 314", "Без фильтров · 1 314 URL" in T(pg))
    pg.get_by_role("button", name="Входящие").first.click(); pg.wait_for_timeout(150)
    check("URL: вкладка «Входящие» показывает таблицу ссылок", "Клик по строке открывает источник" in T(pg))
    pg.get_by_role("button", name="Rendered DOM").click(); pg.wait_for_timeout(150)
    check("URL: Rendered DOM → честное «Нет измерения»", "Нет измерения в этом запуске" in T(pg))
    pg.get_by_label("Скрыть детали").click(); pg.wait_for_timeout(150)
    check("URL: скрыть детали → панель пропала", "Нет измерения в этом запуске" not in T(pg))
    pg.get_by_label("Колонки и их порядок").click(); pg.wait_for_timeout(150)
    check("URL: меню колонок открывается", "Колонки · порядок" in T(pg) or "КОЛОНКИ" in T(pg))
    pg.close()

    pg = open_("ScanDialog.dc.html")
    check("Скан: «Запустить» недоступна до подтверждения", pg.get_by_role("button", name="Запустить").is_disabled())
    pg.get_by_role("button", name="Список URL").first.click(); pg.wait_for_timeout(150)
    check("Скан: источник «Список URL» показывает поле списка", "1 чужой домен" in T(pg))
    pg.get_by_label("Больше").click(); pg.wait_for_timeout(100)
    check("Скан: 3 запроса/с → ошибка поля", "Исправьте «Запросов/с»" in T(pg))
    pg.get_by_label("Меньше").click(); pg.locator("input[type=checkbox]").last.check(); pg.wait_for_timeout(150)
    check("Скан: после подтверждения «Запустить» активна", pg.get_by_role("link", name="Запустить").count() == 1)
    pg.close()

    pg = open_("Compare.dc.html")
    pg.get_by_role("radio", name="Каталог").click(); pg.wait_for_timeout(150)
    check("Сравнение: сегмент «Каталог» меняет KPI", "Canonical на другой URL" in T(pg))
    pg.get_by_role("button", name="Ухудшилось").click(); pg.wait_for_timeout(150)
    check("Сравнение: вкладка «Ухудшилось»", "Новая ошибка 5xx" in T(pg))
    pg.close()

    pg = open_("Handoff.dc.html")
    pg.get_by_role("button", name="SEO").first.click(); pg.wait_for_timeout(150)
    check("Отчёт: вкладка SEO", "Перелинковка статей блога" in T(pg))
    check("Отчёт: «Передать» недоступна до проверки", pg.get_by_role("button", name="Передать…").is_disabled())
    pg.get_by_role("button", name="Отметить проверенным").click(); pg.wait_for_timeout(100)
    pg.get_by_role("button", name="Передать…").click(); pg.wait_for_timeout(100)
    check("Отчёт: стадия «Передано»", "Передано · файл в папке" in T(pg))
    pg.close()

    pg = open_("Scans.dc.html")
    pg.get_by_text("Импорт SF · каталог").click(); pg.wait_for_timeout(150)
    check("Сканы: SF-импорт → «Наблюдение устарело», без анимации", "Наблюдение устарело" in T(pg))
    pg.get_by_text("Скан №2", exact=True).click(); pg.wait_for_timeout(150)
    check("Сканы: частичный → «Продолжить с точки остановки»", "Продолжить с точки остановки" in T(pg))
    pg.close()

    pg = open_("Modals.dc.html", h=1000)
    names = pg.locator(".dl").all_inner_texts()
    seen = 0
    for i in range(len(names)):
        pg.locator(".dl").nth(i).click(); pg.wait_for_timeout(80)
        if pg.locator(".dialog h2").inner_text().strip(): seen += 1
    check(f"Модалки: все {len(names)} открываются", seen == len(names) == 21, f"{seen}/{len(names)}")
    pg.close()

    pg = open_("Settings.dc.html")
    secs = pg.locator(".sn"); n = secs.count(); ok = 0
    for i in range(n):
        secs.nth(i).click(); pg.wait_for_timeout(60)
        if pg.locator(".sc h2").count() == 1: ok += 1
    check(f"Настройки: все {n} разделов", ok == n == 11, f"{ok}/{n}")
    pg.close()

    pg = open_("Notify.dc.html", h=1200)
    for label in ["Успех", "Ошибка", "Агент", "Прогресс"]:
        pg.get_by_role("button", name=label).first.click(); pg.wait_for_timeout(80)
    check("Уведомления: стек тостов максимум 3", pg.locator("[aria-live=polite] .toast").count() == 3)
    pg.get_by_role("button", name="Прочитать все").click(); pg.wait_for_timeout(100)
    check("Уведомления: «Прочитать все» снимает точки", pg.locator(".nrow.unread").count() == 0)
    pg.close()

    pg = open_("Menus.dc.html", h=1000)
    pg.get_by_role("menuitem", name="Скан").click(); pg.wait_for_timeout(120)
    check("Меню: «Скан» открывается", "Скан по списку URL…" in T(pg))
    pg.get_by_role("menuitem", name="Вид").click(); pg.wait_for_timeout(120)
    pg.get_by_role("menuitem", name="Плотность таблиц").hover(); pg.wait_for_timeout(120)
    check("Меню: подменю «Плотность»", "Просторно · 40" in T(pg))
    pg.close()

    pg = open_("Palette.dc.html")
    pg.get_by_label("Команда или адрес").fill("сравн"); pg.wait_for_timeout(150)
    check("⌘K: поиск фильтрует команды", "Сравнить два скана" in T(pg) and "Заметка агенту" not in pg.locator(".dialog").inner_text())
    pg.close()

    pg = open_("TabSettings.dc.html")
    first = pg.locator(".dialog .li").nth(1).inner_text()
    pg.locator(".dialog").get_by_role("button", name="Ниже").click(); pg.wait_for_timeout(120)
    check("Вкладки: «Ниже» меняет порядок без перетаскивания", pg.locator(".dialog .li").nth(2).inner_text() == first)
    pg.close()

    pg = open_("Icons.dc.html", h=1200)
    pg.get_by_label("Найти иконку").fill("агент"); pg.wait_for_timeout(150)
    check("Иконки: поиск «агент»", pg.locator(".itile").count() <= 3)
    pg.close()

    pg = open_("Compact.dc.html", 800, 800)
    check("800×800: нет горизонтального переполнения", pg.evaluate("document.documentElement.scrollWidth <= 800"))
    pg.close()
    b.close()
srv.shutdown()
bad = [r for r in results if not r[1]]
for n, ok, d in results: print(("OK  " if ok else "FAIL"), n, d)
print(f"\n{len(results) - len(bad)}/{len(results)} проверок прошло")
