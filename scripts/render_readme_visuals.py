"""Render README visuals from committed examples and product registries.

The images are documentation artifacts, not a product UI. Audit/report values come
from ``examples/``; interface counts come from the code registries.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import tempfile
from pathlib import Path
from textwrap import dedent

from PIL import Image
from playwright.sync_api import Browser, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / ".github" / "assets"
LOGO_SVG = (ROOT / "docs" / "assets" / "logo.svg").read_text(encoding="utf-8")

PRIMARY = "#1565C0"
ON_PRIMARY = "#FFFFFF"
PRIMARY_CONTAINER = "#D7E3FF"
ON_PRIMARY_CONTAINER = "#001B3F"
SURFACE = "#FDFCFF"
SURFACE_LOW = "#F4F6F8"
SURFACE_HIGH = "#E9EEF4"
OUTLINE = "#C4C7C5"
TEXT = "#1A1C1E"
MUTED = "#44474E"
CRITICAL = "#BA1A1A"
CRITICAL_CONTAINER = "#FFDAD6"
WARNING = "#8A4F00"
WARNING_CONTAINER = "#FFE0B2"
SUCCESS = "#176B3A"
SUCCESS_CONTAINER = "#B8F2C8"


def load_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def base_css(width: int, height: int) -> str:
    return f"""
      * {{ box-sizing: border-box; }}
      html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden; }}
      body {{
        background: {SURFACE}; color: {TEXT};
        font-family: Roboto, Arial, Helvetica, sans-serif;
        text-rendering: geometricPrecision;
      }}
      .canvas {{ width: {width}px; height: {height}px; padding: 56px 64px; position: relative; }}
      .eyebrow {{ color: {PRIMARY}; font-size: 17px; font-weight: 700; letter-spacing: .02em; }}
      h1 {{ margin: 10px 0 12px; font-size: 46px; line-height: 1.08; letter-spacing: -.025em; }}
      .lead {{ margin: 0; color: {MUTED}; font-size: 21px; line-height: 1.45; }}
      .card {{ background: #fff; border: 1px solid {OUTLINE}; border-radius: 20px; }}
      .soft {{ background: {SURFACE_LOW}; border: 1px solid {OUTLINE}; border-radius: 16px; }}
      .chip {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 8px 14px;
               background: {PRIMARY_CONTAINER}; color: {ON_PRIMARY_CONTAINER}; font-size: 15px; font-weight: 700; }}
      .chip--critical {{ background: {CRITICAL_CONTAINER}; color: {CRITICAL}; }}
      .chip--warning {{ background: {WARNING_CONTAINER}; color: {WARNING}; }}
      .chip--success {{ background: {SUCCESS_CONTAINER}; color: {SUCCESS}; }}
      .mono {{ font-family: "SFMono-Regular", Menlo, Consolas, monospace; }}
      .muted {{ color: {MUTED}; }}
      .arrow {{ color: {PRIMARY}; font-size: 44px; font-weight: 400; text-align: center; }}
      .footer {{ position: absolute; left: 64px; right: 64px; bottom: 34px; color: {MUTED};
                 font-size: 15px; display: flex; justify-content: space-between; gap: 24px; }}
    """


def document(body: str, width: int, height: int, extra_css: str = "") -> str:
    return dedent(
        f"""\
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8">
            <style>{base_css(width, height)}{extra_css}</style>
          </head>
          <body><main class="canvas">{body}</main></body>
        </html>
        """
    )


def render(browser: Browser, markup: str, output: Path, width: int, height: int) -> None:
    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
    page.set_content(markup, wait_until="load")
    page.locator(".canvas").screenshot(path=str(output), animations="disabled")
    page.close()


def product_counts() -> tuple[int, int, int]:
    from seohead.cli import COMMANDS
    from seohead.sf.core.registry import CHECKS

    sf_source = (ROOT / "seohead" / "servers" / "sf_mcp.py").read_text(encoding="utf-8")
    sf_tools = set(re.findall(r"def (sf_[a-z0-9_]+)\(", sf_source))
    return len(COMMANDS), len(sf_tools), len(CHECKS)


def social_preview(tool_count: int, check_count: int) -> str:
    body = f"""
      <div class="brand">{LOGO_SVG}</div>
      <div class="social-copy">
        <div class="eyebrow">Local-first · open source · one toolkit</div>
        <h1>SEOHEAD Tools</h1>
        <p class="lead">Technical SEO evidence, orchestrated.</p>
        <div class="social-flow mono">Screaming Frog exports + live checks → audits, tasks, reports</div>
      </div>
      <div class="social-stats">
        <div><strong>{tool_count}</strong><span>tools</span></div>
        <div><strong>{check_count}</strong><span>check registry</span></div>
        <div><strong>CLI</strong><span>+ local MCP</span></div>
      </div>
    """
    css = f"""
      .canvas {{ padding: 62px 74px; border-left: 14px solid {PRIMARY}; }}
      .brand {{ position: absolute; right: 76px; top: 58px; width: 86px; height: 86px;
                border-radius: 20px; overflow: hidden; }}
      .brand svg {{ display: block; width: 100%; height: 100%; }}
      .social-copy {{ max-width: 990px; padding-top: 42px; }}
      .social-copy h1 {{ font-size: 76px; margin-top: 14px; margin-bottom: 8px; }}
      .social-copy .lead {{ font-size: 31px; }}
      .social-flow {{ margin-top: 30px; border-radius: 14px; background: {SURFACE_HIGH};
                      border: 1px solid {OUTLINE}; padding: 17px 20px; font-size: 19px; color: {TEXT}; }}
      .social-stats {{ position: absolute; left: 74px; right: 74px; bottom: 52px;
                       display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
      .social-stats div {{ border-top: 3px solid {PRIMARY}; padding-top: 14px; display: flex;
                           align-items: baseline; gap: 10px; }}
      .social-stats strong {{ color: {PRIMARY}; font-size: 30px; }}
      .social-stats span {{ color: {MUTED}; font-size: 17px; }}
    """
    return document(body, 1280, 640, css)


def audit_workflow(audit: dict, tasks: dict) -> str:
    summary = audit["summary"]
    totals = summary["totals"]
    severity = summary["by_severity"]
    task_summary = tasks["summary"]
    body = f"""
      <div class="eyebrow">Real output from the committed synthetic fixture</div>
      <h1>From Screaming Frog exports to prioritized work</h1>
      <p class="lead">Existing crawl evidence becomes a traceable audit and an engineering backlog.</p>
      <div class="fixture chip">Synthetic repository fixture · no client data</div>
      <section class="audit-flow">
        <article class="card stage input-card">
          <div class="step">01</div><h2>Inputs</h2>
          <div class="file mono">internal_all.csv</div>
          <div class="file mono">4xx inlinks export</div>
          <p>Screaming Frog CSV exports</p>
        </article>
        <div class="arrow">→</div>
        <article class="card stage command-card">
          <div class="step">02</div><h2>Analyze</h2>
          <pre class="mono"><span>$</span> seohead sf run<br>  --exports-dir ./exports<br>  --out ./report --tasks</pre>
          <p>Applicable checks from a 104-check registry</p>
        </article>
        <div class="arrow">→</div>
        <article class="card stage audit-card">
          <div class="step">03</div><h2>Audit evidence</h2>
          <div class="health"><strong>{esc(summary["health_score"])}</strong><span>/100 health</span></div>
          <div class="mini-grid">
            <div><strong>{esc(totals["urls_crawled"])}</strong><span>URLs</span></div>
            <div><strong>{esc(totals["issues_total"])}</strong><span>issues</span></div>
          </div>
          <div class="severity">
            <span class="chip chip--critical">{esc(severity["critical"])} critical</span>
            <span class="chip chip--warning">{esc(severity["warning"])} warning</span>
            <span class="chip">{esc(severity["notice"])} notice</span>
          </div>
        </article>
        <div class="arrow">→</div>
        <article class="card stage tasks-card">
          <div class="step">04</div><h2>Task backlog</h2>
          <div class="health"><strong>{esc(task_summary["tasks_total"])}</strong><span>prioritized tasks</span></div>
          <div class="priorities">
            <span>P1 · {esc(task_summary["by_priority"]["P1"])}</span>
            <span>P2 · {esc(task_summary["by_priority"]["P2"])}</span>
            <span>P3 · {esc(task_summary["by_priority"]["P3"])}</span>
          </div>
          <div class="finding mono">BROKEN_INTERNAL_LINK · 404</div>
        </article>
      </section>
      <div class="footer"><span>Source: examples/exports → examples/audit.* → examples/tasks.*</span><span>Missing inputs remain explicit skipped checks.</span></div>
    """
    css = f"""
      .fixture {{ position: absolute; top: 64px; right: 64px; }}
      .audit-flow {{ margin-top: 42px; display: grid; grid-template-columns: 1fr 56px 1.15fr 56px 1fr 56px 1fr;
                     align-items: stretch; gap: 0; }}
      .stage {{ min-height: 465px; padding: 26px; position: relative; }}
      .stage h2 {{ font-size: 25px; margin: 48px 0 22px; }}
      .stage p {{ color: {MUTED}; font-size: 16px; line-height: 1.5; margin: 18px 0 0; }}
      .step {{ position: absolute; left: 24px; top: 22px; color: {PRIMARY}; font-weight: 800; font-size: 17px; }}
      .arrow {{ align-self: center; }}
      .file {{ background: {SURFACE_LOW}; border: 1px solid {OUTLINE}; border-radius: 12px;
               padding: 15px; margin: 12px 0; font-size: 15px; overflow: hidden; }}
      pre {{ margin: 0; padding: 20px; border-radius: 14px; background: {SURFACE_LOW};
             border: 1px solid {OUTLINE}; font-size: 15px; line-height: 1.75; white-space: pre-wrap; }}
      pre span {{ color: {PRIMARY}; font-weight: 800; }}
      .health {{ display: flex; gap: 8px; align-items: baseline; margin-bottom: 18px; }}
      .health strong {{ color: {PRIMARY}; font-size: 46px; }} .health span {{ color: {MUTED}; font-size: 16px; }}
      .mini-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
      .mini-grid div {{ background: {SURFACE_LOW}; border-radius: 12px; padding: 14px; }}
      .mini-grid strong, .mini-grid span {{ display: block; }}
      .mini-grid strong {{ font-size: 27px; color: {PRIMARY}; }} .mini-grid span {{ color: {MUTED}; font-size: 14px; }}
      .severity {{ margin-top: 18px; display: flex; flex-wrap: wrap; gap: 8px; }}
      .severity .chip {{ font-size: 13px; padding: 7px 10px; }}
      .priorities {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }}
      .priorities span {{ text-align: center; padding: 11px 4px; border-radius: 10px;
                          background: {SURFACE_HIGH}; font-size: 14px; font-weight: 700; }}
      .finding {{ margin-top: 20px; padding: 14px 12px; border-left: 4px solid {CRITICAL};
                  background: {CRITICAL_CONTAINER}; color: {CRITICAL}; font-size: 13px; }}
    """
    return document(body, 1600, 900, css)


def interfaces_visual(core_count: int, sf_count: int) -> str:
    body = f"""
      <div class="eyebrow">One Python core · two local interfaces</div>
      <h1>Specialists and agents use the same evidence core</h1>
      <p class="lead">{core_count} seo_* tools share tested handlers; {sf_count} sf_* tools expose the crawl workflow.</p>
      <section class="interfaces">
        <article class="card interface-card">
          <span class="chip">For specialists and automation</span>
          <h2>seohead CLI</h2>
          <pre class="mono"><span>$</span> seohead sf run<br>  --exports-dir ./exports<br>  --out ./report --tasks</pre>
          <p>Repeatable batch work, CI, and local terminal workflows.</p>
        </article>
        <div class="flow-arrow">→</div>
        <article class="core-card">
          <div class="core-mark">{LOGO_SVG}</div>
          <h2>SEOHEAD Python core</h2>
          <div class="core-lines">
            <span>Crawl export analysis</span>
            <span>Live &amp; infrastructure evidence</span>
            <span>Structured artifacts</span>
          </div>
        </article>
        <div class="flow-arrow">←</div>
        <article class="card interface-card">
          <span class="chip">For tool-calling AI agents</span>
          <h2>Local stdio MCP</h2>
          <pre class="mono"><span>$</span> seohead mcp<br><br><span>#</span> {core_count} seo_* + {sf_count} sf_* tools</pre>
          <p>Bounded calls and artifact paths instead of an improvised crawler.</p>
        </article>
      </section>
      <div class="trust-row">
        <span>Local stdio</span><span>No inbound port</span><span>No hosted account</span><span>No telemetry</span>
      </div>
      <div class="footer"><span>{core_count} shared seo_* handlers plus {sf_count} sf_* workflow tools.</span><span>{core_count + sf_count} tools in one process.</span></div>
    """
    css = f"""
      .interfaces {{ margin-top: 48px; display: grid; grid-template-columns: 1fr 70px 1.05fr 70px 1fr;
                     align-items: center; }}
      .interface-card {{ min-height: 390px; padding: 28px; }}
      .interface-card h2, .core-card h2 {{ font-size: 28px; margin: 24px 0 18px; }}
      .interface-card pre {{ margin: 0; padding: 20px; border: 1px solid {OUTLINE}; border-radius: 14px;
                             background: {SURFACE_LOW}; font-size: 16px; line-height: 1.7; }}
      .interface-card pre span {{ color: {PRIMARY}; font-weight: 800; }}
      .interface-card p {{ color: {MUTED}; font-size: 16px; line-height: 1.5; margin-top: 22px; }}
      .flow-arrow {{ color: {PRIMARY}; text-align: center; font-size: 44px; }}
      .core-card {{ min-height: 440px; border: 2px solid {PRIMARY}; border-radius: 24px;
                    background: {PRIMARY_CONTAINER}; padding: 32px; text-align: center; }}
      .core-mark {{ width: 76px; height: 76px; border-radius: 18px; overflow: hidden; margin: 0 auto 10px; }}
      .core-mark svg {{ display: block; width: 100%; height: 100%; }}
      .core-lines {{ display: grid; gap: 10px; margin-top: 26px; }}
      .core-lines span {{ background: rgba(255,255,255,.72); border-radius: 12px; padding: 14px;
                          color: {ON_PRIMARY_CONTAINER}; font-weight: 700; font-size: 16px; }}
      .trust-row {{ margin: 30px auto 0; max-width: 1120px; display: grid; grid-template-columns: repeat(4,1fr);
                    border: 1px solid {OUTLINE}; border-radius: 16px; overflow: hidden; }}
      .trust-row span {{ text-align: center; padding: 14px; background: #fff; color: {MUTED};
                         font-size: 15px; font-weight: 700; border-right: 1px solid {OUTLINE}; }}
      .trust-row span:last-child {{ border-right: 0; }}
    """
    return document(body, 1600, 900, css)


def reports_visual(report: dict) -> str:
    summary = report["summary"]
    failed = summary["tools_failed"][0]
    body = f"""
      <div class="eyebrow">One reviewable evidence contract</div>
      <h1>One audit document, five deliverables</h1>
      <p class="lead">Renderers preserve the same evidence; they do not invent new findings.</p>
      <section class="report-flow">
        <article class="document-card">
          <div class="doc-icon">{{ }}</div>
          <div>
            <span class="mono schema">seohead.site-audit/1</span>
            <h2>Structured audit document</h2>
            <div class="doc-stats"><span><strong>{esc(summary["findings_total"])}</strong> findings</span><span><strong>{esc(summary["pages_checked"])}</strong> pages</span></div>
            <div class="not-covered"><strong>Not covered:</strong> {esc(failed["tool"])} — {esc(failed["error"])}</div>
          </div>
        </article>
        <div class="down-arrow">↓</div>
        <div class="formats">
          <article class="format card"><strong>XLSX</strong><span>Working file</span><small>4 sheets</small></article>
          <article class="format card"><strong>DOCX</strong><span>Client deliverable</span><small>Grouped findings</small></article>
          <article class="format card"><strong>CSV</strong><span>Import</span><small>Findings + pages</small></article>
          <article class="format card"><strong>MD</strong><span>Git and reading</span><small>Complete report</small></article>
          <article class="format card"><strong>JSON</strong><span>Data exchange</span><small>Same document</small></article>
        </div>
      </section>
      <div class="rule">Report renderers add no findings and make no network requests.</div>
      <div class="footer"><span>Source: examples/reports/full.json</span><span>Unavailable measurements stay visible.</span></div>
    """
    css = f"""
      .report-flow {{ margin-top: 44px; }}
      .document-card {{ width: 860px; min-height: 250px; margin: 0 auto; border-radius: 24px;
                        border: 2px solid {PRIMARY}; background: {PRIMARY_CONTAINER}; padding: 30px;
                        display: grid; grid-template-columns: 100px 1fr; gap: 24px; align-items: start; }}
      .doc-icon {{ width: 82px; height: 82px; border-radius: 20px; background: {PRIMARY}; color: white;
                   display: grid; place-items: center; font: 700 28px/1 "SFMono-Regular", Menlo, monospace; }}
      .schema {{ color: {PRIMARY}; font-weight: 800; font-size: 15px; }}
      .document-card h2 {{ margin: 8px 0 14px; font-size: 27px; }}
      .doc-stats {{ display: flex; gap: 12px; }}
      .doc-stats span {{ background: rgba(255,255,255,.75); border-radius: 12px; padding: 11px 14px;
                         color: {MUTED}; }} .doc-stats strong {{ color: {PRIMARY}; font-size: 22px; }}
      .not-covered {{ margin-top: 14px; background: {WARNING_CONTAINER}; color: {WARNING};
                      border-radius: 10px; padding: 11px 13px; font-size: 14px; }}
      .down-arrow {{ color: {PRIMARY}; text-align: center; font-size: 42px; line-height: 1; margin: 12px 0; }}
      .formats {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; }}
      .format {{ min-height: 160px; padding: 24px; text-align: center; display: flex; flex-direction: column;
                 align-items: center; justify-content: center; }}
      .format strong {{ color: {PRIMARY}; font-size: 30px; }} .format span {{ margin-top: 10px; font-weight: 700; font-size: 16px; }}
      .format small {{ color: {MUTED}; font-size: 13px; margin-top: 7px; }}
      .rule {{ width: 920px; margin: 24px auto 0; border-radius: 999px; background: {SUCCESS_CONTAINER};
               color: {SUCCESS}; padding: 13px 20px; text-align: center; font-size: 15px; font-weight: 700; }}
    """
    return document(body, 1600, 900, css)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    audit = load_json("examples/audit.json")
    tasks = load_json("examples/tasks.json")
    report = load_json("examples/reports/full.json")
    core_count, sf_count, check_count = product_counts()
    tool_count = core_count + sf_count

    with tempfile.TemporaryDirectory(prefix="seohead-readme-") as tmp_name:
        tmp = Path(tmp_name)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            social_png = tmp / "social-preview.png"
            render(browser, social_preview(tool_count, check_count), social_png, 1280, 640)
            render(browser, audit_workflow(audit, tasks), ASSETS / "audit-workflow.png", 1600, 900)
            render(
                browser,
                interfaces_visual(core_count, sf_count),
                ASSETS / "cli-mcp.png",
                1600,
                900,
            )
            render(browser, reports_visual(report), ASSETS / "report-formats.png", 1600, 900)
            browser.close()

        with Image.open(social_png) as image:
            image.convert("RGB").save(
                ASSETS / "social-preview.jpg",
                format="JPEG",
                quality=88,
                optimize=True,
                progressive=True,
            )

    for name in ("social-preview.jpg", "audit-workflow.png", "cli-mcp.png", "report-formats.png"):
        path = ASSETS / name
        print(f"{name}: {path.stat().st_size:,} bytes · sha256 {digest(path)}")


if __name__ == "__main__":
    main()
