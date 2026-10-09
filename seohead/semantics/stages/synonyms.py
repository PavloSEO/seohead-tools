"""Paid Wordstat check of candidate synonyms; every answer and request is retained."""

from __future__ import annotations

from pathlib import Path

from seohead.data_sources.yandex_cloud import NetworkAmbiguousError, Wordstat
from seohead.semantics.norm import Filters, normalize


def run(store, cfg):
    f = Filters.from_config(cfg)
    syn_path = Path(cfg["_dir"]) / "synonyms.txt"
    if not syn_path.exists():
        print("synonyms: synonyms.txt is missing")
        return {"added": 0, "checked": 0}
    store.db.execute(
        "CREATE TABLE IF NOT EXISTS synonym_attempts ("
        "phrase TEXT PRIMARY KEY, state TEXT, error TEXT)"
    )
    store.commit()
    have = {r[0] for r in store.db.execute("SELECT norm FROM phrases")}
    blocked = {
        r[0]
        for r in store.db.execute(
            "SELECT phrase FROM synonym_attempts WHERE state IN ('submitting','unknown')"
        )
    }
    cands = sorted({normalize(line) for line in syn_path.read_text(encoding="utf-8").splitlines()})
    cands = [
        n
        for n in cands
        if n and n not in have and n not in blocked and f.has_intent(n) and not f.is_stop(n)
    ]
    if not cands:
        return {"added": 0, "checked": 0, "blocked": len(blocked)}
    ws = Wordstat(rps=cfg.get("rps", 5))
    min_base = cfg.get("min_base", 20)
    regions = cfg.get("regions") or ["225"]
    added = n_req = rejected = errors = 0
    for p in cands:
        store.db.execute(
            "INSERT OR REPLACE INTO synonym_attempts VALUES(?,'submitting',NULL)", (p,)
        )
        store.commit()
        try:
            j = ws.top(p, limit=1, regions=regions)
        except Exception as exc:
            state = "unknown" if isinstance(exc, NetworkAmbiguousError) else "retryable"
            store.db.execute(
                "UPDATE synonym_attempts SET state=?,error=? WHERE phrase=?", (state, str(exc), p)
            )
            store.commit()
            errors += 1
            if state == "unknown":
                blocked.add(p)
            print(f"  ! synonyms {p}: {exc} ({state})")
            if isinstance(exc, (TypeError, AttributeError)):
                raise
            if any(q in str(exc).lower() for q in ("429", "503", "quota")):
                break
            continue
        n_req += 1
        store.add_run("synonyms", 1, note=f"phrase={p}")
        base = int(j["totalCount"]) if j.get("totalCount") is not None else None
        store.upsert(p, base=base, depth=0, src="synonym", region=str(regions[0]))
        if base is None:
            store.set_status(p, "review", reason="Wordstat totalCount missing", stage="synonyms")
        elif base < min_base:
            store.set_status(
                p, "dropped", reason=f"base={base} < min_base={min_base}", stage="synonyms"
            )
            rejected += 1
        else:
            store.add_edge("synonym", p, "synonym")
            added += 1
        store.db.execute(
            "UPDATE synonym_attempts SET state='complete',error=NULL WHERE phrase=?", (p,)
        )
        store.commit()
    print(f"synonyms: checked={n_req}, added={added}, rejected={rejected}, errors={errors}")
    return {"added": added, "checked": n_req, "errors": errors, "blocked": len(blocked)}
