"""relevance: free red flag from the collected SERP.

If no domain from ``vendors.txt`` (sites of the project's own kind) appears in a phrase's
top results, the topic is probably off-scope or too weak. The phrase goes to review with
reason "no vendor in SERP"; nothing is deleted. Phrases without collected SERP are skipped.
"""

from __future__ import annotations

from pathlib import Path


def _load_vendors(cfg):
    p = Path(cfg["_dir"]) / cfg.get("vendor_file", "vendors.txt")
    if not p.exists():
        return None
    return set(
        d.strip().lower().replace("www.", "")
        for d in p.read_text(encoding="utf-8").splitlines()
        if d.strip()
    )


def _is_vendor(domain, vendors):
    d = (domain or "").lower().replace("www.", "")
    return any(d == v or d.endswith("." + v) for v in vendors)


def run(store, cfg, top=10):
    vendors = _load_vendors(cfg)
    if not vendors:
        print(f"relevance: {cfg.get('vendor_file', 'vendors.txt')} is missing; list peer domains")
        return {"skipped": True}
    # Kept phrases that have collected SERP.
    have_serp = set(
        r["phrase_norm"] for r in store.db.execute("SELECT DISTINCT phrase_norm FROM serp")
    )
    flagged = checked = 0
    for r in store.phrases(status="kept"):
        p = r["norm"]
        if p not in have_serp:
            continue
        checked += 1
        docs = store.serp_for(p)[:top]
        if not any(_is_vendor(d["domain"], vendors) for d in docs):
            store.set_status(p, "review", reason="no vendor in SERP", stage="relevance")
            flagged += 1
    store.commit()
    store.add_run("relevance", note=f"checked={checked}, no-vendor->review={flagged}")
    print(
        f"relevance: checked={checked} | no peer in top -> review={flagged} | "
        f"kept with a peer={checked - flagged}"
    )
    return {"checked": checked, "flagged": flagged}
