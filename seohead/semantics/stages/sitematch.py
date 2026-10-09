"""sitematch: free check that a phrase matches something the site offers.

A phrase belongs in the core only when the site has a matching section or product.
``catalog.txt`` lists the site's section terms, one per line; a kept phrase without any catalog
term goes to review with reason "off catalog". Nothing is deleted.
"""

from __future__ import annotations

import re
from pathlib import Path

from seohead.semantics.norm import normalize


def _load_catalog(cfg):
    p = Path(cfg["_dir"]) / cfg.get("catalog_file", "catalog.txt")
    if not p.exists():
        return None, p
    terms = []
    for line in p.read_text(encoding="utf-8").splitlines():
        t = normalize(line)
        if t:
            terms.append(t)
    return terms, p


def run(store, cfg):
    terms, path = _load_catalog(cfg)
    if terms is None:
        print(f"sitematch: {path} is missing; list the site sections, one per line")
        return {"skipped": True}
    # Any catalog term as a substring; terms are already normalized.
    rx = re.compile("|".join(re.escape(t) for t in terms)) if terms else None
    off = 0
    for r in store.phrases(status="kept"):
        if rx and not rx.search(r["norm"]):
            store.set_status(r["norm"], "review", reason="off catalog", stage="sitematch")
            off += 1
    store.commit()
    store.add_run("sitematch", note=f"off-catalog->review={off}, terms={len(terms)}")
    kept_now = store.counts().get("kept", 0)
    print(
        f"sitematch: catalog terms={len(terms)} | off-catalog->review={off} | "
        f"kept and matching the site={kept_now}"
    )
    return {"off_catalog": off, "terms": len(terms)}
