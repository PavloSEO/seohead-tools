"""Audit regional site structures built with subdomains, folders, or satellites.

Runet sites typically implement regional targeting in one of three ways, and the
models are not equivalent::

    msk.site.ru      subdomain  — separate host; can have its own Yandex region
    site.ru/msk/     folder     — same host; regional relevance comes from content
    site-msk.ru      satellite  — separate domain with independent history and trust

The audit answers four questions: which regional versions exist, which structural
model they use, whether they are reachable rather than redirected to the main site,
and whether they undermine one another through duplicate content, cross-host
canonicals, or a single nationwide phone number.

The network is a data source, not a precondition for the audit: an unreachable host
is reported as unavailable without aborting analysis of the remaining regions.
"""

# ruff: noqa: RUF001
# Cyrillic strings in this module are intentional regional names and matching data.

from __future__ import annotations

import re
from collections import Counter
from typing import Any
from urllib.parse import urljoin, urlparse

from seohead.checks.parser import document_base_url
from seohead.recon.net import http_client, normalize_url, registrable_domain

# ── Regional dictionary ──────────────────────────────────────────────────────
# Keys are URL tokens observed in practice; values are human-readable city names.
# Multiple spellings of the same city (msk / moskva / moscow) must collapse to one
# region. Otherwise msk.site.ru and moskva.site.ru would appear to be unrelated
# regions instead of two competing implementations of the same target market.
REGION_SLUGS: dict[str, str] = {}


def _region(name: str, *slugs: str) -> None:
    for slug in slugs:
        REGION_SLUGS[slug] = name


_region("Москва", "msk", "moskva", "moscow", "mo", "moskow")
_region(
    "Санкт-Петербург",
    "spb",
    "sankt-peterburg",
    "saint-petersburg",
    "peterburg",
    "piter",
    "petersburg",
    "sanktpeterburg",
)
_region("Новосибирск", "nsk", "novosibirsk", "novosib")
_region("Екатеринбург", "ekb", "ekaterinburg", "yekaterinburg", "ekat")
_region("Казань", "kzn", "kazan", "kazan-city")
_region(
    "Нижний Новгород",
    "nn",
    "nnov",
    "nizhniy-novgorod",
    "nizhny-novgorod",
    "nizhniynovgorod",
    "nnovgorod",
)
_region("Челябинск", "chel", "chelyabinsk", "chelyab")
_region("Красноярск", "krsk", "krasnoyarsk", "krasnojarsk")
_region("Самара", "samara", "smr")
_region("Уфа", "ufa")
_region("Ростов-на-Дону", "rostov", "rostov-na-donu", "rnd", "rostovnadonu")
_region("Краснодар", "krasnodar", "krd")
_region("Омск", "omsk")
_region("Воронеж", "voronezh", "vrn")
_region("Пермь", "perm", "prm")
_region("Волгоград", "volgograd", "vlg")
_region("Саратов", "saratov")
_region("Тюмень", "tyumen", "tumen", "tyumen-city")
_region("Тольятти", "tolyatti", "togliatti")
_region("Ижевск", "izhevsk", "izh")
_region("Барнаул", "barnaul")
_region("Ульяновск", "ulyanovsk", "ulianovsk")
_region("Иркутск", "irkutsk", "irk")
_region("Хабаровск", "khabarovsk", "habarovsk")
_region("Ярославль", "yaroslavl", "jaroslavl")
_region("Владивосток", "vladivostok", "vlad")
_region("Махачкала", "makhachkala", "mahachkala")
_region("Томск", "tomsk")
_region("Оренбург", "orenburg")
_region("Кемерово", "kemerovo")
_region("Новокузнецк", "novokuznetsk")
_region("Рязань", "ryazan", "riazan")
_region("Астрахань", "astrakhan", "astrahan")
_region("Набережные Челны", "naberezhnye-chelny", "chelny", "nab-chelny")
_region("Пенза", "penza")
_region("Липецк", "lipetsk")
_region("Киров", "kirov")
_region("Чебоксары", "cheboksary")
_region("Тула", "tula")
_region("Калининград", "kaliningrad", "kld")
_region("Балашиха", "balashikha")
_region("Курск", "kursk")
_region("Севастополь", "sevastopol")
_region("Сочи", "sochi")
_region("Ставрополь", "stavropol")
_region("Улан-Удэ", "ulan-ude", "ulanude")
_region("Тверь", "tver")
_region("Магнитогорск", "magnitogorsk")
_region("Иваново", "ivanovo")
_region("Брянск", "bryansk", "briansk")
_region("Белгород", "belgorod")
_region("Сургут", "surgut")
_region("Владимир", "vladimir")
_region("Нижний Тагил", "nizhniy-tagil", "nizhny-tagil", "tagil")
_region("Архангельск", "arkhangelsk", "arhangelsk")
_region("Чита", "chita")
_region("Симферополь", "simferopol")
_region("Калуга", "kaluga")
_region("Смоленск", "smolensk")
_region("Волжский", "volzhskiy", "volzhsky")
_region("Курган", "kurgan")
_region("Орёл", "orel", "oryol")
_region("Череповец", "cherepovets")
_region("Вологда", "vologda")
_region("Саранск", "saransk")
_region("Якутск", "yakutsk", "jakutsk")
_region("Владикавказ", "vladikavkaz")
_region("Подольск", "podolsk")
_region("Грозный", "grozny", "grozniy")
_region("Мурманск", "murmansk")
_region("Тамбов", "tambov")
_region("Петрозаводск", "petrozavodsk")
_region("Стерлитамак", "sterlitamak")
_region("Нижневартовск", "nizhnevartovsk")
_region("Кострома", "kostroma")
_region("Новороссийск", "novorossiysk", "novorossijsk")
_region("Йошкар-Ола", "yoshkar-ola", "joshkar-ola")
_region("Химки", "khimki", "himki")
_region("Таганрог", "taganrog")
_region("Сыктывкар", "syktyvkar")
_region("Нальчик", "nalchik")
_region("Шахты", "shakhty")
_region("Дзержинск", "dzerzhinsk")
_region("Орск", "orsk")
_region("Братск", "bratsk")
_region("Ангарск", "angarsk")
_region("Энгельс", "engels")
_region("Благовещенск", "blagoveshchensk", "blagoveshensk")
_region("Великий Новгород", "veliky-novgorod", "velikiy-novgorod", "novgorod")
_region("Старый Оскол", "stary-oskol", "staryy-oskol")
_region("Королёв", "korolev")
_region("Псков", "pskov")
_region("Бийск", "biysk", "bijsk")
_region("Люберцы", "lyubertsy", "liubertsy")
_region("Южно-Сахалинск", "yuzhno-sakhalinsk")
_region("Мытищи", "mytishchi", "mytishi")
_region("Прокопьевск", "prokopyevsk")
_region("Норильск", "norilsk")
_region("Армавир", "armavir")
_region("Абакан", "abakan")
_region("Сызрань", "syzran")
_region("Каменск-Уральский", "kamensk-uralskiy", "kamensk-uralsky")
_region("Красногорск", "krasnogorsk")
_region("Междуреченск", "mezhdurechensk")
_region("Ноябрьск", "noyabrsk")
_region("Новый Уренгой", "novy-urengoy", "noviy-urengoy")

# Infrastructure and product subdomains are never regions. Without this denylist,
# labels such as ``www`` and ``shop`` would leak into the report as unknown regions.
NON_REGION_HOSTS = frozenset(
    {
        "www",
        "m",
        "mobile",
        "amp",
        "api",
        "cdn",
        "static",
        "img",
        "images",
        "media",
        "mail",
        "smtp",
        "imap",
        "pop",
        "ftp",
        "ns1",
        "ns2",
        "dev",
        "test",
        "stage",
        "staging",
        "beta",
        "demo",
        "old",
        "new",
        "blog",
        "shop",
        "store",
        "forum",
        "help",
        "support",
        "docs",
        "admin",
        "lk",
        "my",
        "account",
        "cabinet",
        "webmail",
        "vpn",
        "git",
        "status",
        "video",
        "files",
        "download",
        "assets",
    }
)

# Common first path segments that must never be interpreted as regions.
NON_REGION_PATHS = frozenset(
    {
        "catalog",
        "product",
        "products",
        "category",
        "blog",
        "news",
        "articles",
        "about",
        "contacts",
        "contact",
        "services",
        "service",
        "price",
        "prices",
        "delivery",
        "payment",
        "cart",
        "search",
        "tag",
        "tags",
        "page",
        "user",
        "login",
        "account",
        "ru",
        "en",
        "de",
        "kz",
        "by",
        "upload",
        "uploads",
        "images",
        "img",
        "assets",
        "static",
        "media",
        "files",
        "wp-content",
    }
)

_TEL_RE = re.compile(r"(?:\+7|\b8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
_SLUG_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)


def _norm_ru(value: str) -> str:
    """Normalize Russian text to a lowercase, space-delimited comparison form."""
    return re.sub(r"[^а-я0-9]+", " ", value.lower().replace("ё", "е")).strip()


# Reverse index for Russian city names. City switchers commonly use Cyrillic anchor text rather
# than slugs; without this index, localized names would not be recognized at all.
REGION_NAMES: dict[str, str] = {_norm_ru(name): name for name in set(REGION_SLUGS.values())}
# Check longer multi-word names before a shorter city name that may be contained within them.
_NAMES_BY_LENGTH: list[tuple[str, str]] = sorted(REGION_NAMES.items(), key=lambda kv: -len(kv[0]))


def detect_region_name(text: str) -> str | None:
    """Return a region found in Russian anchor or heading text, otherwise ``None``."""
    if not text or not _CYRILLIC_RE.search(text):
        return None
    normalized = _norm_ru(text)
    if not normalized:
        return None
    exact = REGION_NAMES.get(normalized)
    if exact:
        return exact
    for key, name in _NAMES_BY_LENGTH:
        if re.search(rf"(?<![а-я]){re.escape(key)}(?![а-я])", normalized):
            return name
    return None


def _slugs_of(token: str) -> list[str]:
    """Return slug candidates consisting of a token and its components.

    ``site-msk`` yields both ``site-msk`` and ``msk``. A satellite domain is
    recognized by the component, not by its complete label.
    """
    token = token.strip().lower()
    if not token:
        return []
    parts = [p for p in _SLUG_SPLIT_RE.split(token) if p]
    return [token] + [p for p in parts if p != token]


def detect_region(token: str) -> str | None:
    """Return a region for a URL token, testing the full token before its parts."""
    for candidate in _slugs_of(token):
        name = REGION_SLUGS.get(candidate)
        if name:
            return name
    return None


def _registrable(host: str) -> str:
    """Approximate the registrable domain, accounting for compound Runet zones."""
    return registrable_domain(host)


def classify_url(url: str, main_host: str) -> dict[str, Any]:
    """Classify a URL relative to the main host's regional structure.

    Return ``scheme`` (``subdomain``/``folder``/``domain``/``main``), ``region``,
    and ``slug``. A ``None`` region means the URL is an ordinary internal page or
    an unrelated external domain and requires no regional assessment.
    """
    parsed = urlparse(normalize_url(url))
    host = (parsed.hostname or "").lower()
    main_host = (urlparse(normalize_url(main_host)).hostname or main_host).lower()
    main_base = _registrable(main_host)
    out: dict[str, Any] = {"url": url, "host": host, "scheme": None, "region": None, "slug": None}
    if not host:
        return out

    if _registrable(host) != main_base:
        # Separate domain: look for the region in its label (site-msk.ru, msk-site.ru).
        label = _registrable(host).split(".")[0]
        out["scheme"] = "domain"
        out["region"] = detect_region(label)
        out["slug"] = label
        return out

    sub = host[: -len(main_base)].strip(".") if host != main_base else ""
    sub_head = sub.split(".")[0] if sub else ""
    if sub_head and sub_head not in NON_REGION_HOSTS:
        region = detect_region(sub_head)
        if region:
            out.update(scheme="subdomain", region=region, slug=sub_head)
            return out

    segments = [s for s in parsed.path.split("/") if s]
    if segments:
        head = segments[0].lower()
        if head not in NON_REGION_PATHS:
            region = detect_region(head)
            if region:
                out.update(scheme="folder", region=region, slug=head)
                return out

    out["scheme"] = "main" if not sub_head or sub_head in NON_REGION_HOSTS else "subdomain"
    return out


def discover_regional_links(html: str, base_url: str) -> list[dict[str, Any]]:
    """Find regional links in a page, typically from a city switcher.

    This is a pure function: downloaded HTML is sufficient and no network is used.
    """
    from bs4 import BeautifulSoup

    main_host = urlparse(normalize_url(base_url)).hostname or ""
    soup = BeautifulSoup(html, features="lxml")
    resolve_from = document_base_url(soup, normalize_url(base_url))
    seen: dict[str, dict[str, Any]] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(resolve_from, href)
        info = classify_url(absolute, main_host)
        if not info["region"]:
            # A city in anchor text is still evidence when the URL has no region,
            # but it is weaker evidence because labels are less reliable than URLs.
            anchor = a.get_text(" ", strip=True)
            text_region = detect_region_name(anchor) or detect_region(anchor.lower())
            if not text_region:
                continue
            info["region"] = text_region
            info["from_anchor_text"] = True
        key = f"{info['region']}|{info['host']}|{urlparse(absolute).path}"
        if key not in seen:
            info["url"] = absolute.split("#")[0]
            seen[key] = info
    return list(seen.values())


def _phones(html: str) -> list[str]:
    """Return normalized phone numbers for cross-region contact comparison."""
    found = {re.sub(r"\D", "", m) for m in _TEL_RE.findall(html)}
    return sorted(p[-10:] for p in found if len(p) >= 10)


def _fetch(client, url: str) -> dict[str, Any]:
    """Fetch one regional URL and collect status, redirects, page facts, and phones."""
    from seohead.checks.page_facts import extract

    out: dict[str, Any] = {"url": url}
    try:
        resp = client.get(url)
    # Network failures are result data and must not abort the remaining regions.
    except Exception as exc:
        out.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        return out
    final = str(resp.url)
    html = resp.text if "html" in resp.headers.get("content-type", "").lower() else ""
    facts = extract(html, final) if html else {}
    out.update(
        ok=True,
        status=resp.status_code,
        final_url=final,
        redirected=final.split("#")[0].rstrip("/") != url.split("#")[0].rstrip("/"),
        title=facts.get("title") or "",
        h1=facts.get("h1") or "",
        canonical=facts.get("canonical") or "",
        word_count=facts.get("word_count") or 0,
        phones=_phones(html),
        noindex="noindex" in (resp.headers.get("x-robots-tag", "").lower() + _meta_robots(html)),
        html=html,
    )
    return out


def _meta_robots(html: str) -> str:
    m = re.search(
        r'<meta[^>]+name=["\']robots["\'][^>]+content=["\']([^"\']+)', html, re.IGNORECASE
    )
    return (m.group(1) or "").lower() if m else ""


def _findings(main: dict[str, Any], pages: list[dict[str, Any]], schemes: Counter) -> list[str]:
    """Build findings from measured facts without inferring operator intent."""
    from seohead.checks.duplicate import simhash, similarity

    out: list[str] = []
    live = [p for p in pages if p.get("ok") and p.get("status") == 200]
    if not pages:
        # Zero checked pages is not a clean result. A city switcher is often rendered
        # by JavaScript and therefore absent from the raw HTML.
        return [
            "No regional URLs were found in the HTML: the site may have no regional "
            "versions, or its city switcher may be rendered by JavaScript. Satellites "
            "on separate domains cannot be discovered from this page; provide them "
            "explicitly through extra."
        ]
    main_host = urlparse(main.get("final_url") or main.get("url", "")).hostname or ""

    dead = [p for p in pages if not p.get("ok") or p.get("status", 0) >= 400]
    if dead:
        out.append(
            f"{len(dead)} regional URLs are unavailable or return errors: "
            + ", ".join(p["url"] for p in dead[:5])
            + (" and others" if len(dead) > 5 else "")
        )

    to_main = [
        p
        for p in live
        if p.get("redirected")
        and (urlparse(p["final_url"]).hostname or "") == main_host
        and (urlparse(p["url"]).hostname or "") != main_host
    ]
    if to_main:
        out.append(
            f"{len(to_main)} regional hosts redirect to the main site; these hosts "
            "do not provide distinct regional pages and cannot rank as such: "
            + ", ".join(p["url"] for p in to_main[:5])
        )

    canon_out = []
    for p in live:
        canon = p.get("canonical") or ""
        if not canon:
            continue
        c_host = urlparse(urljoin(p["final_url"], canon)).hostname or ""
        if c_host and c_host != (urlparse(p["final_url"]).hostname or ""):
            canon_out.append(f"{p['url']} → {canon}")
    if canon_out:
        out.append(
            "Regional pages canonicalize to another host, effectively excluding "
            "themselves from search results: " + "; ".join(canon_out[:5])
        )

    noindexed = [p["url"] for p in live if p.get("noindex")]
    if noindexed:
        out.append("Regional pages marked noindex: " + ", ".join(noindexed[:5]))

    # Identical copy across regions is a classic source of regional cannibalization.
    hashed = [(p, simhash(p.get("html", ""))) for p in live if p.get("word_count", 0) > 50]
    twins: list[str] = []
    for i in range(len(hashed)):
        for j in range(i + 1, len(hashed)):
            sim = similarity(hashed[i][1], hashed[j][1])
            if sim >= 0.95:
                twins.append(f"{hashed[i][0]['url']} ≈ {hashed[j][0]['url']} ({sim:.0%})")
    if twins:
        out.append(
            f"Content matches across {len(twins)} region pairs; the regional "
            "versions differ only by URL: " + "; ".join(twins[:5])
        )

    same_title = Counter(p.get("title", "") for p in live if p.get("title"))
    dupes = [t for t, n in same_title.items() if n > 1]
    if dupes:
        out.append(
            f"The same title is used across multiple regions ({len(dupes)} "
            "duplicates); the city is not inserted into the title."
        )

    no_region_in_title = [
        p["url"]
        for p in live
        if p.get("region")
        and p.get("title")
        and p["region"].split("-")[0].lower() not in p["title"].lower()
    ]
    if no_region_in_title:
        out.append(
            f"The city is missing from the title on {len(no_region_in_title)} "
            "pages, leaving search engines with no title-level regional signal: "
            + ", ".join(no_region_in_title[:5])
        )

    phone_sets = {tuple(p.get("phones", [])) for p in live if p.get("phones")}
    if len(phone_sets) == 1 and len(live) > 1:
        out.append(
            "The phone number is identical across all regions. Yandex may treat "
            "this as evidence that the branches are not distinct and the pages "
            "are templated."
        )

    if schemes.get("subdomain") and schemes.get("folder"):
        out.append(
            "Regional versions use both subdomains and folders. Choose one model "
            "to prevent the two structures from competing with each other."
        )

    satellites = schemes.get("domain", 0)
    if satellites:
        out.append(
            f"{satellites} regional URLs use separate domains. Check for affiliate "
            "site signals: Yandex may cluster domains with identical content and "
            "contact details and retain only one in search results."
        )

    if not out:
        out.append(f"Checked regions: {len(live)}; no major structural errors were found.")
    return out


def analyze_regions(
    url: str,
    extra: list[str] | None = None,
    limit: int = 12,
    timeout: float = 20.0,
    render: bool = False,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Analyze a site's regional structure.

    ``url`` is any page on the site, usually the homepage, from which the city
    switcher is discovered. ``extra`` supplies known URLs such as satellites on
    separate domains, which cannot reliably be discovered from the page itself.
    ``limit`` caps the number of regional pages fetched.

    With ``render=True``, discovery uses the rendered DOM because large sites often
    omit the switcher from raw HTML. This requires Playwright; when rendering is
    unavailable, the audit degrades gracefully and records that only raw HTML was used.
    """
    if max_pages is not None:  # backward-compatible alias used by early callers
        limit = max_pages
    if not url or not str(url).strip():
        return {"ok": False, "error": "URL is required"}
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        return {"ok": False, "error": "limit must be a number"}

    start = normalize_url(url)
    main_host = urlparse(start).hostname or ""
    if not main_host:
        return {"ok": False, "error": f"Unable to parse URL: {url!r}"}

    client, _ = http_client(timeout)
    try:
        main = _fetch(client, start)
        if not main.get("ok"):
            return {
                "ok": False,
                "error": main.get("error", "Homepage is unavailable"),
                "url": start,
            }

        switcher_html = main.get("html", "")
        render_note = None
        if render:
            from seohead.checks.render import rendered_html

            shot = rendered_html(start, timeout=max(timeout, 30.0))
            if shot.get("ok"):
                switcher_html = shot["html"]
                render_note = "City switcher was extracted from the rendered DOM"
            else:
                render_note = f"Rendering failed ({shot.get('error')}); only raw HTML was analyzed"
        candidates = discover_regional_links(switcher_html, start)
        for raw in extra or []:
            absolute = normalize_url(str(raw).strip())
            if not absolute:
                continue
            info = classify_url(absolute, main_host)
            info["url"] = absolute
            info["from_input"] = True
            candidates.append(info)

        # Deduplicate by URL because city switchers often appear in both header and
        # footer; without this step each regional page would be fetched twice.
        by_url: dict[str, dict[str, Any]] = {}
        for c in candidates:
            by_url.setdefault(c["url"].rstrip("/"), c)
        regional = [c for c in by_url.values() if c.get("region")]
        regional.sort(key=lambda c: (c["region"], c["url"]))
        truncated = max(0, len(regional) - limit)

        pages: list[dict[str, Any]] = []
        for c in regional[:limit]:
            page = _fetch(client, c["url"])
            page.update(region=c["region"], scheme=c["scheme"], slug=c["slug"])
            pages.append(page)
    finally:
        client.close()

    schemes = Counter(p["scheme"] for p in pages if p.get("scheme"))
    findings = _findings(main, pages, schemes)
    scheme = schemes.most_common(1)[0][0] if len(schemes) == 1 else "mixed" if schemes else "none"

    for p in pages:  # HTML is retained only until content comparison
        p.pop("html", None)
    main.pop("html", None)

    return {
        "ok": True,
        "url": start,
        "main_host": main_host,
        "scheme": scheme,
        "schemes": dict(schemes),
        "regions_found": sorted({p["region"] for p in pages if p.get("region")}),
        "regions_total": len(regional),
        "checked": len(pages),
        "truncated": truncated,
        "pages": pages,
        "rendered": bool(render),
        "render_note": render_note,
        "findings": findings,
    }
