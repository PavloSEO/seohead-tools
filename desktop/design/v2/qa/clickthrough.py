"""Click-through QA for the SEOHEAD canvas boards rendered with the canvas runtime."""
import http.server, json, socketserver, sys, threading, functools
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
SHOTS = SITE / "shots"
SHOTS.mkdir(exist_ok=True)
canvas = json.loads((CANVAS / "canvas.json").read_text())

class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def guess_type(self, p):
        if "/_blob/" in str(p): return "image/svg+xml" if Path(str(p)).name in ICONS else "text/css"
        return super().guess_type(p)

srv = socketserver.TCPServer(("127.0.0.1", 0), functools.partial(H, directory=str(SITE)))
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}/"

CLICKABLE = "button:not([disabled]), [role=button], .tr, .trow, .task, .msg, .fi, .hit, .m, .nrow, .itile, .dl, .sn, .proj, .src, .kpi"
report = {}
only = sys.argv[1:]
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, box in canvas["boards"].items():
        if only and name not in only: continue
        w, h = box["w"], box["h"]
        page = b.new_page(viewport={"width": w, "height": h})
        errs = []
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        page.goto(BASE + name)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(SHOTS / name.replace(".dc.html", ".png")))
        text = page.inner_text("body")
        holes = text.count("{{")
        n_click = page.locator(CLICKABLE).count()
        clicked, click_err = 0, []
        for i in range(min(n_click, 60)):
            loc = page.locator(CLICKABLE).nth(i)
            try:
                if not loc.is_visible(): continue
                if loc.evaluate("e => !!e.closest('[inert]')"): continue  # modal backdrop: not interactive by design
                before = len(errs)
                try:
                    loc.click(timeout=800, no_wait_after=True)
                except Exception:  # a popover opened by an earlier click may cover it: reset the board, retry once
                    page.goto(BASE + name); page.wait_for_timeout(600)
                    loc.click(timeout=800, no_wait_after=True)
                page.wait_for_timeout(60)
                if page.url != BASE + name:
                    page.goto(BASE + name); page.wait_for_timeout(600)
                clicked += 1
                if len(errs) > before: click_err.append(i)
            except Exception as e:
                click_err.append(f"{i}:{type(e).__name__}")
        links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
        dead = [l for l in links if l in ("#", "")]
        report[name] = {"errors": errs[:5], "n_err": len(errs), "unrendered_holes": holes, "clickables": n_click,
                        "clicked": clicked, "click_issues": click_err[:8], "links": len(links), "dead_links": len(dead),
                        "text_len": len(text)}
        page.close()
    b.close()
srv.shutdown()
print(json.dumps(report, ensure_ascii=False, indent=1))
