"""clean: deterministic, free cleanup.

Sets status and route from the project filters, flags frequency outliers and groups lemma
variants so exact frequency is paid once per group. Nothing is deleted: statuses are reversible.
"""

from __future__ import annotations

import re
import statistics

from seohead.semantics.demand import has_observed_demand
from seohead.semantics.norm import Filters

STEM = re.compile(r"(ый|ой|ая|ое|ые|ий|его|ого|ому|ыми|ами|ах|ов|ей|ю|я|и|е|а|у|ом|ем)$")


def _stem(tok):
    return STEM.sub("", tok) if len(tok) > 4 else tok


def _stemseq(norm):
    # An order-preserving stem sequence, not a set: otherwise "A to B" and "B to A" would
    # collapse into one lemma group and share an exact frequency despite different intent.
    return tuple(_stem(t) for t in norm.split())


def run(store, cfg):
    f = Filters.from_config(cfg)
    min_base = cfg.get("min_base", 20)
    rows = store.phrases(status="new")
    if not rows:
        print("clean: no new phrases")
        return {"changed": 0}

    bases = [r["base"] for r in rows if r["base"]]
    median = statistics.median(bases) if bases else 0
    outlier_hi = median * 50 if median else 10**12  # frequency outlier: > 50x the median

    changed = {"kept": 0, "review": 0, "dropped": 0}
    lemma = {}  # stem sequence -> lemma_group id
    next_lg = (
        (store.db.execute("SELECT COALESCE(MAX(lemma_group),0) FROM phrases").fetchone()[0]) + 1
    )

    for r in rows:
        p, base = r["norm"], r["base"]
        status, route = f.classify(p, base, min_base)
        # A frequency outlier goes to review: it usually means a homonym.
        if status == "kept" and base is not None and base > outlier_hi:
            status = "review"
        # Lemma group: identical stem sequence and identical base means one spelling variant,
        # so differing intent never collapses and exact frequency is never understated.
        key = (_stemseq(p), base)
        lg = lemma.get(key)
        if lg is None:
            lg = next_lg
            next_lg += 1
            lemma[key] = lg
        if has_observed_demand(r["impr"], r["pos_y"], r["pos_g"]):
            status = "kept"
            reason = "observed search demand"
        elif status == "dropped":
            reason = "stop phrase" if f.is_stop(p) else "invalid phrase length"
        elif status == "review":
            reason = (
                "no project intent"
                if not f.has_intent(p)
                else "frequency outlier"
                if base is not None and base > outlier_hi
                else f"base={base} below min_base={min_base}"
            )
        else:
            reason = "project intent and demand"
        store.set_fields(p, route=route, lemma_group=lg)
        store.set_status(p, status, reason=reason, stage="clean")
        changed[status] = changed.get(status, 0) + 1
    store.commit()
    store.add_run(
        "clean",
        note=f"kept={changed['kept']} review={changed['review']} dropped={changed['dropped']}",
    )
    n_groups = len(set(lemma.values()))
    print(
        f"clean: kept={changed['kept']} review={changed['review']} dropped={changed['dropped']} "
        f"| lemma groups={n_groups} (median base={median:.0f}, outlier>{outlier_hi:.0f})"
    )
    return {"changed": sum(changed.values()), "lemma_groups": n_groups}
