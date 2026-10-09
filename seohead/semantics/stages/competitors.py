"""competitors: free reuse of the SERP collected by ``cluster``.

Top domains are the competitors; 2-3-grams cut from their titles become candidate phrases
appended to ``synonyms.txt`` for the paid ``synonyms`` check.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

from seohead.semantics.norm import Filters, normalize

BRAND_TAIL = re.compile(r"\s*[|—•·\-–]\s*[^|—•·]+$")  # tail after a separator is a brand


def _ngrams(title, f):
    t = normalize(BRAND_TAIL.sub("", title or ""))
    toks = t.split()
    out = []
    for n in (2, 3):
        for i in range(len(toks) - n + 1):
            g = " ".join(toks[i : i + n])
            if len(g) >= 6 and f.has_intent(g) and not f.is_stop(g):
                out.append(g)
    return out


def run(store, cfg, out_dir):
    f = Filters.from_config(cfg)
    dom = collections.Counter()
    titles = []
    for r in store.db.execute("SELECT domain, title FROM serp"):
        if r["domain"]:
            dom[r["domain"]] += 1
        if r["title"]:
            titles.append(r["title"])
    if not dom:
        print("competitors: no collected SERP (run cluster first)")
        return {"domains": 0}

    # Candidate phrases from competitor titles that the store does not have yet.
    have = set(r["norm"] for r in store.phrases())
    cand = collections.Counter()
    for t in titles:
        for g in _ngrams(t, f):
            if g not in have:
                cand[g] += 1
    new_cands = [g for g, _ in cand.most_common(200)]

    # synonyms.txt is the input of the synonyms stage; an analyst may add more lines.
    syn_path = Path(cfg["_dir"]) / "synonyms.txt"
    existing = set()
    if syn_path.exists():
        existing = set(line.strip() for line in syn_path.read_text(encoding="utf-8").splitlines())
    with open(syn_path, "a", encoding="utf-8") as fh:
        for g in new_cands:
            if g not in existing:
                fh.write(g + "\n")

    md = [
        f"# Semantic core competitors — {cfg['_project']}",
        "",
        f"SERP positions collected: {sum(dom.values())}. Competitor domains:",
        "",
        "| # | Domain | Top appearances |",
        "|---|---|---:|",
    ]
    for i, (d, n) in enumerate(dom.most_common(30), 1):
        md.append(f"| {i} | {d} | {n} |")
    md += [
        "",
        f"## Candidate phrases from competitor titles ({len(new_cands)})",
        "",
        "Appended to `synonyms.txt`; the `synonyms` stage checks them in Wordstat:",
        "",
    ]
    for g, n in cand.most_common(40):
        md.append(f"- {g} (×{n})")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "competitors.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    store.add_run("competitors", note=f"domains={len(dom)}, candidates={len(new_cands)}")
    print(
        f"competitors: domains={len(dom)}, candidate phrases={len(new_cands)} "
        f"-> competitors.md + synonyms.txt"
    )
    return {"domains": len(dom), "candidates": len(new_cands)}
