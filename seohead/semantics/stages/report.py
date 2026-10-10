"""report: free Markdown digest and cluster matrix.

The digest lists the pool by status, the top clusters (marker, size, summed exact frequency,
top competitor domains) and provider units used. The cluster matrix is a CSV file.
"""

from __future__ import annotations

import csv
from pathlib import Path


def run(store, cfg, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    c = store.counts()
    sp = store.spend()

    clusters = store.db.execute(
        "SELECT c.id, c.name, c.coherence, c.is_real, "
        " COUNT(p.id) n, COALESCE(SUM(p.exact),0) sum_exact, MAX(p.exact) top_exact "
        "FROM clusters c LEFT JOIN phrases p ON p.cluster_id=c.id "
        "GROUP BY c.id ORDER BY sum_exact DESC"
    ).fetchall()

    def domains(cid):
        rows = store.db.execute(
            "SELECT s.domain, COUNT(*) n FROM serp s JOIN phrases p ON p.norm=s.phrase_norm "
            "WHERE p.cluster_id=? AND s.domain IS NOT NULL GROUP BY s.domain "
            "ORDER BY n DESC LIMIT 5",
            (cid,),
        ).fetchall()
        return ", ".join(f"{r['domain']}({r['n']})" for r in rows)

    md = [
        f"# Semantic core report — {cfg['_project']}",
        "",
        f"Pool: **{c['_total']}** phrases | kept={c.get('kept', 0)} review={c.get('review', 0)} "
        f"dropped={c.get('dropped', 0)} dead={c.get('dead', 0)} | exact measured={c['_exact']} | "
        f"clusters={c['_clusters']}",
        f"Provider units: {sp['yandex_requests']} Yandex requests + {sp['arsenkin']} Arsenkin "
        "limits (money: `seohead spend-report`)",
        "",
        "## Top clusters (by summed exact !W)",
        "",
        "| # | Marker | Phrases | Sum !W | Top !W | Coherence | Competitors (top domains) |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for i, cl in enumerate([c for c in clusters if c["n"]][:40], 1):
        md.append(
            f"| {i} | {cl['name'][:40]} | {cl['n']} | {cl['sum_exact']} | "
            f"{cl['top_exact'] or 0} | {cl['coherence']} | {domains(cl['id'])} |"
        )

    # Kept phrases without a cluster: singletons that still have demand.
    solo = store.db.execute(
        "SELECT norm, exact, base FROM phrases WHERE status='kept' AND cluster_id IS NULL "
        "AND (exact>=? OR (exact IS NULL AND base>=?)) ORDER BY COALESCE(exact,base) DESC LIMIT 30",
        (cfg.get("exact_cut_exact", 10), cfg.get("min_base", 20)),
    ).fetchall()
    if solo:
        md += ["", "## Kept without a cluster (singleton demand)", ""]
        for r in solo:
            md.append(
                f"- {r['norm']} — !W {r['exact'] if r['exact'] is not None else '~' + str(r['base'])}"
            )

    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    with open(out / "clusters.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["marker", "phrases", "sum_exact", "top_exact", "coherence", "is_real"])
        for cl in clusters:
            if cl["n"]:
                w.writerow(
                    [
                        cl["name"],
                        cl["n"],
                        cl["sum_exact"],
                        cl["top_exact"] or 0,
                        cl["coherence"],
                        cl["is_real"],
                    ]
                )
    store.add_run("report", note=f"clusters in report={len([c for c in clusters if c['n']])}")
    print(f"report: {len([c for c in clusters if c['n']])} clusters -> report.md + clusters.csv")
    return {"clusters": len([c for c in clusters if c["n"]])}
