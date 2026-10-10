"""graph: free word graph and anomaly report (standard library only).

Signals:
  * homonym detector: a token whose phrases split into two or more unrelated components
    (few shared other tokens) is a homonym candidate; the off-topic component goes to review;
  * candidate clusters: connected components of the phrase-token graph after removing the most
    frequent anchor tokens.
Writes ``anomalies.md`` before any paid exact frequency is bought.
"""

from __future__ import annotations

import collections
import threading
from pathlib import Path

from seohead.semantics.demand import has_observed_demand
from seohead.semantics.norm import Filters

# Grammatical stop words only (language, not topic). Topic anchors come from the project's
# anchor_words in project.yaml; run() adds them to _STOP.
GRAMMATICAL = {
    "в",
    "во",
    "на",
    "из",
    "по",
    "для",
    "и",
    "с",
    "со",
    "от",
    "до",
    "за",
    "к",
    "о",
    "об",
    "у",
    "а",
    "но",
    "или",
    "год",
    "года",
    "годы",
    "цена",
    "цены",
    "купить",
    "недорого",
    "2023",
    "2024",
    "2025",
    "2026",
    "2027",
}
_STOP = set(GRAMMATICAL)


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def _tokens(norm):
    return [t for t in norm.split() if t not in _STOP and len(t) > 2]


def detect_homonyms(phrase_tokens, min_mass=0.15, max_overlap=0.10):
    """token -> components of its phrases linked by other tokens; two or more = homonym."""
    tok_phrases = collections.defaultdict(list)
    for ph, toks in phrase_tokens.items():
        for t in toks:
            tok_phrases[t].append(ph)
    homonyms = {}
    for t, phrases in tok_phrases.items():
        if len(phrases) < 4:
            continue
        uf = UF()
        for ph in phrases:
            uf.union(("p", ph), ("p", ph))
            for other in _tokens(ph):
                if other != t:
                    uf.union(("p", ph), ("t", other))
        comps = collections.defaultdict(list)
        for ph in phrases:
            comps[uf.find(("p", ph))].append(ph)
        big = [c for c in comps.values() if len(c) >= max(2, min_mass * len(phrases))]
        if len(big) >= 2:
            vocs = [set(w for ph in c for w in _tokens(ph) if w != t) for c in big]
            inter = set.intersection(*vocs) if vocs else set()
            union = set().union(*vocs) if vocs else set()
            overlap = len(inter) / max(len(union), 1)
            if overlap < max_overlap:
                homonyms[t] = big
    return homonyms


def candidate_clusters(phrase_tokens, top_df_drop=8):
    """Phrase-token connected components after removing the most frequent anchor tokens."""
    df = collections.Counter()
    for toks in phrase_tokens.values():
        for t in set(toks):
            df[t] += 1
    anchors = {t for t, _ in df.most_common(top_df_drop)}
    uf = UF()
    for ph, toks in phrase_tokens.items():
        uf.union(("p", ph), ("p", ph))
        for t in toks:
            if t not in anchors:
                uf.union(("p", ph), ("t", t))
    comps = collections.defaultdict(list)
    for ph in phrase_tokens:
        comps[uf.find(("p", ph))].append(ph)
    return sorted([c for c in comps.values() if len(c) >= 2], key=len, reverse=True)


_RUN_LOCK = threading.Lock()  # _STOP is module state; one graph run at a time


def run(store, cfg, out_dir):
    with _RUN_LOCK:
        return _run(store, cfg, out_dir)


def _run(store, cfg, out_dir):
    global _STOP
    _STOP = set(GRAMMATICAL) | set(cfg.get("anchor_words") or [])
    f = Filters.from_config(cfg)
    rows = (
        store.phrases(status="kept") + store.phrases(status="review") + store.phrases(status="new")
    )
    observed = {r["norm"] for r in rows if has_observed_demand(r["impr"], r["pos_y"], r["pos_g"])}
    phrase_tokens = {r["norm"]: _tokens(r["norm"]) for r in rows}
    base = {r["norm"]: (r["base"] or 0) for r in rows}
    if not phrase_tokens:
        print("graph: the pool is empty")
        return {}

    homonyms = detect_homonyms(phrase_tokens)
    # Phrases of a homonym component without topic intent go to review.
    flagged = 0
    for _token, comps in homonyms.items():
        for comp in comps:
            if not any(f.has_intent(ph) for ph in comp):
                for ph in comp:
                    if ph in observed:
                        continue
                    store.set_status(ph, "review", reason="ambiguous graph context", stage="graph")
                    flagged += 1
    store.commit()

    clusters = candidate_clusters(phrase_tokens)

    md = [
        "# Anomalies and candidate clusters (semantics graph)",
        "",
        f"Pool: {len(phrase_tokens)} phrases. Homonym tokens: {len(homonyms)}. "
        f"Sent to review: {flagged}.",
        "",
        "## Homonyms (a token splits the pool into different meanings)",
        "",
    ]
    if homonyms:
        for t, comps in sorted(homonyms.items(), key=lambda kv: -len(kv[1])):
            md.append(f"### `{t}` — {len(comps)} meanings")
            for i, comp in enumerate(sorted(comps, key=len, reverse=True)[:4], 1):
                ex = sorted(comp, key=lambda p: -base.get(p, 0))[:5]
                mark = "on topic" if any(f.has_intent(p) for p in comp) else "off topic -> review"
                md.append(f"- meaning {i} ({len(comp)}, {mark}): " + "; ".join(ex))
            md.append("")
    else:
        md.append("_no homonyms found_\n")
    md += [
        "## Candidate clusters (before paid exact frequency)",
        "",
        f"Top {min(30, len(clusters))} connected components:",
        "",
    ]
    for i, comp in enumerate(clusters[:30], 1):
        head = sorted(comp, key=lambda p: -base.get(p, 0))[:6]
        md.append(f"{i}. **{len(comp)} phrases** — " + "; ".join(head))

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "anomalies.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    store.add_run(
        "graph", note=f"homonyms={len(homonyms)}, review+={flagged}, candidates={len(clusters)}"
    )
    print(
        f"graph: homonyms={len(homonyms)}, to review={flagged}, candidate clusters={len(clusters)} "
        f"-> anomalies.md"
    )
    return {"homonyms": len(homonyms), "flagged": flagged, "clusters": len(clusters)}
