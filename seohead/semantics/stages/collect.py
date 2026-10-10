"""Paid Wordstat collection: accumulates phrases, deduplicates the queue, survives interruption."""

from __future__ import annotations

import time
from collections import deque

from seohead.data_sources.yandex_cloud import NetworkAmbiguousError, Wordstat
from seohead.semantics.demand import has_observed_demand
from seohead.semantics.norm import Filters, normalize


def run(store, cfg, seeds=None, max_seeds=0, max_phrases=100000, resume=False):
    f = Filters.from_config(cfg)
    regions = cfg.get("regions") or ["225"]
    floor = cfg.get("reexpand_floor", 40)
    max_depth = cfg.get("max_depth", 3)
    store.db.execute(
        "CREATE TABLE IF NOT EXISTS collect_attempts ("
        "seed TEXT PRIMARY KEY, depth INTEGER, state TEXT, error TEXT)"
    )
    store.commit()
    expanded = {r[0] for r in store.db.execute("SELECT seed_norm FROM expansions")}
    blocked = {
        r[0]
        for r in store.db.execute(
            "SELECT seed FROM collect_attempts WHERE state IN ('submitting','unknown')"
        )
    }
    frontier, queued = deque(), set()

    def enqueue(phrase, depth):
        phrase = normalize(phrase)
        if phrase and phrase not in expanded and phrase not in queued and phrase not in blocked:
            frontier.append((phrase, depth))
            queued.add(phrase)

    for seed in seeds or []:
        enqueue(seed, 0)
    if resume:
        for row in store.db.execute(
            "SELECT seed,depth FROM collect_attempts WHERE state='retryable'"
        ):
            enqueue(row["seed"], row["depth"])
        for row in store.db.execute(
            "SELECT norm,depth FROM phrases WHERE base>=? AND status IN ('new','kept') "
            "ORDER BY base DESC",
            (floor,),
        ):
            n, depth = row["norm"], row["depth"] or 0
            if depth <= max_depth and not f.is_stop(n) and f.has_intent(n) and len(n.split()) >= 2:
                enqueue(n, depth)

    if not frontier:
        print(f"collect: no new seeds; awaiting reconciliation={len(blocked)}")
        return {"quota_hit": False, "expanded": 0, "blocked": len(blocked)}
    ws = Wordstat(rps=cfg.get("rps", 5))
    done = n_req = errors = 0
    total = store.counts()["_total"]
    t0 = time.monotonic()
    quota_hit = False
    while frontier:
        if total >= max_phrases or (max_seeds and done >= max_seeds):
            break
        phrase, depth = frontier.popleft()
        # The reservation survives interruption between submission and response persistence.
        total += int(store.upsert(phrase, depth=depth, src="seed", region=str(regions[0])))
        store.db.execute(
            "INSERT OR REPLACE INTO collect_attempts VALUES(?,?,?,NULL)",
            (phrase, depth, "submitting"),
        )
        store.commit()
        try:
            pool, meta = ws.expand(phrase, limit=cfg.get("num", 300), regions=regions)
        except Exception as exc:
            msg = str(exc)
            unknown = isinstance(exc, NetworkAmbiguousError)
            state = "unknown" if unknown else "retryable"
            store.db.execute(
                "UPDATE collect_attempts SET state=?,error=? WHERE seed=?", (state, msg, phrase)
            )
            store.commit()
            errors += 1
            if unknown:
                blocked.add(phrase)
            print(f"  ! {phrase[:40]}: {msg[:100]} ({state})")
            if any(q in msg.lower() for q in ("429", "503", "quota", "resource-exhausted")):
                quota_hit = True
                break
            continue
        # Preserve the receipt before processing data; accounting survives parser/write failures.
        n_req += 1
        store.add_run("collect", 1, note=f"seed={phrase}")
        origin = meta.get("origin") or meta.get("src") or {}
        new = 0
        for raw_phrase, base in pool.items():
            p = normalize(raw_phrase)
            if not p:
                continue
            source = origin.get(raw_phrase, origin.get(p, "?"))
            is_new = store.upsert(p, base=base, depth=depth + 1, src=source, region=str(regions[0]))
            if is_new:
                new += 1
                store.add_edge(phrase, p, "expand" if source == "results" else "assoc")
            if f.is_stop(p):
                # Keep every observed phrase, including rejected results, for later review.
                row = store.db.execute(
                    "SELECT impr,pos_y,pos_g FROM phrases WHERE norm=?", (p,)
                ).fetchone()
                if not has_observed_demand(*row):
                    store.set_status(p, "dropped", reason="stop-list match", stage="collect")
                continue
            if depth < max_depth and base is not None and base >= floor and f.has_intent(p):
                enqueue(p, depth + 1)
        store.mark_expanded(
            phrase, meta.get("results", 0), meta.get("associations", 0), str(regions[0])
        )
        store.db.execute(
            "UPDATE collect_attempts SET state='complete',error=NULL WHERE seed=?", (phrase,)
        )
        store.commit()
        expanded.add(phrase)
        total += new
        done += 1
        rate = done / max(time.monotonic() - t0, 1)
        print(
            f"[{done}] d{depth} '{phrase[:38]}' +{new} | pool={total} "
            f"frontier={len(frontier)} ({rate:.1f}/s)",
            flush=True,
        )
    print(
        f"collect: expanded={done}, errors={errors}, awaiting reconciliation={len(blocked)}, "
        f"Wordstat requests={n_req}"
    )
    return {"quota_hit": quota_hit, "expanded": done, "errors": errors, "blocked": len(blocked)}
