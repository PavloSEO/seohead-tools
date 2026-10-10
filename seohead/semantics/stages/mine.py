"""mine: candidate phrases from competitor pages (public web fetch, no provider quota).

Takes the most frequent top-5 URLs from the collected SERP, reads their title, description,
H1 and first H2 headings with the toolkit parser, and appends topical 2-3-grams to
``synonyms.txt``. The paid ``synonyms`` stage then checks them before they join the core.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

from seohead.checks.parser import parse_url
from seohead.semantics.norm import Filters, normalize

BRAND_TAIL = re.compile(r"\s*[|—•·\-–:]\s*[^|—•·:]+$")


def _fetch(url, timeout=12):
    result = parse_url(
        url,
        options={
            "timeout": timeout,
            "meta": True,
            "headings": True,
            "jsonld": False,
            "links": False,
            "forms": False,
            "text": False,
        },
    )
    if not result.get("ok"):
        print(f"  ! mine: {url}: {result.get('error') or result.get('status_code')}")
        return ""
    headings = result.get("headings") or {}
    parts = [
        result.get("title") or "",
        result.get("meta_description") or "",
        *headings.get("h1", []),
        *headings.get("h2", [])[:6],
    ]
    return " || ".join(parts)


def _ngrams(text, f):
    out = []
    for chunk in text.split("||"):
        t = normalize(BRAND_TAIL.sub("", chunk))
        toks = t.split()
        for n in (2, 3):
            for i in range(len(toks) - n + 1):
                g = " ".join(toks[i : i + n])
                if 6 <= len(g) <= 45 and f.has_intent(g) and not f.is_stop(g):
                    out.append(g)
    return out


def run(store, cfg, out_dir, top_urls=60):
    f = Filters.from_config(cfg)
    # Competitor URLs from the core's SERP, by appearance count.
    urls = collections.Counter()
    for r in store.db.execute(
        "SELECT s.url, COUNT(*) n FROM serp s JOIN phrases p ON p.norm=s.phrase_norm "
        "WHERE p.status='kept' AND s.url IS NOT NULL AND s.pos<=5 GROUP BY s.url ORDER BY n DESC"
    ):
        urls[r["url"]] = r["n"]
    top = [u for u, _ in urls.most_common(top_urls)]
    if not top:
        print("mine: no collected SERP (run cluster first)")
        return {}

    have = set(r["norm"] for r in store.phrases())
    cand = collections.Counter()
    fetched = 0
    for u in top:
        text = _fetch(u)
        if not text:
            continue
        fetched += 1
        for g in _ngrams(text, f):
            if g not in have:
                cand[g] += 1

    new = [g for g, _ in cand.most_common(400)]
    syn = Path(cfg["_dir"]) / "synonyms.txt"
    existing = (
        set(line.strip() for line in syn.read_text(encoding="utf-8").splitlines())
        if syn.exists()
        else set()
    )
    with open(syn, "a", encoding="utf-8") as fh:
        for g in new:
            if g not in existing:
                fh.write(g + "\n")

    md = [
        f"# Competitor mining (title and headings) — {cfg['_project']}",
        "",
        f"Pages parsed: {fetched} of top {len(top)}. New candidate phrases: {len(new)}",
        "",
        "Appended to synonyms.txt; the `synonyms` stage checks them in Wordstat.",
        "",
    ]
    for g, n in cand.most_common(60):
        md.append(f"- {g} (×{n})")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "competitor_mining.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    store.add_run("mine", note=f"pages={fetched}, candidates={len(new)}")
    print(
        f"mine: parsed {fetched} competitor pages, new candidate phrases: {len(new)} "
        f"-> synonyms.txt (run synonyms next)"
    )
    return {"fetched": fetched, "candidates": len(new)}
