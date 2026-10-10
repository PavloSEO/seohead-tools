"""Paid exact frequency (!W) via Arsenkin with durable task receipts.

An interrupted or timed-out task is recovered by its task_id without paying again; the
provider field ``overal`` is the exact wordform frequency (!W).
"""

from __future__ import annotations

import json

from seohead.data_sources.arsenkin import ArsenkinClient, parse_wordstat, wordstat_payload
from seohead.semantics.demand import has_observed_demand
from seohead.semantics.norm import normalize

DEFAULT_REGION = 225

# A submission is reserved before the paid call. Unknown outcomes require reconciliation;
# task IDs and raw responses are committed before parsing or changing phrase frequencies.
TASK_SCHEMA = """CREATE TABLE IF NOT EXISTS provider_tasks (
    id INTEGER PRIMARY KEY, provider TEXT NOT NULL, stage TEXT NOT NULL,
    region INTEGER NOT NULL, payload_json TEXT NOT NULL, state TEXT NOT NULL,
    task_id INTEGER, cost REAL, result_json TEXT, error TEXT
)"""


def _frequency(value):
    """Zero is measured; missing, malformed and negative values are not measurements."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _apply_result(store, task, by_group, cut_b, cut_e):
    raw = json.loads(task["result_json"])
    parsed = parse_wordstat(raw, task["region"])
    warnings = list(parsed["warnings"])
    # quoted is deliberately separate: the toolkit parser promises base/overal only.
    node = raw.get("result", raw) if isinstance(raw, dict) else {}
    data = node.get("data", {}) if isinstance(node, dict) else {}
    raw_rows = data.get("result", {}) if isinstance(data, dict) else {}
    wanted = set(json.loads(task["payload_json"])["queries"])
    for missing in sorted(wanted - parsed["frequencies"].keys()):
        warnings.append(f"{missing}: frequency row missing")
    snapped = dead = 0
    for raw_phrase, values in parsed["frequencies"].items():
        if raw_phrase not in wanted:
            continue
        p = normalize(raw_phrase)
        row = store.db.execute("SELECT * FROM phrases WHERE norm=?", (p,)).fetchone()
        if row is None:
            continue
        key = row["lemma_group"] if row["lemma_group"] is not None else f"solo:{p}"
        cell = (raw_rows.get(raw_phrase) or {}).get(str(task["region"])) or {}
        quoted = _frequency(cell.get("quoted"))
        frequency = _frequency(values.get("overal"))
        if frequency is None:
            warnings.append(f"{p}: !W missing or invalid")
        for member in by_group.get(key, []):
            # Read current values so a second recovered task cannot overwrite the first.
            m = store.db.execute("SELECT * FROM phrases WHERE norm=?", (member,)).fetchone()
            fields = {}
            if m["quoted"] is None and quoted is not None:
                fields["quoted"] = quoted
            if m["exact"] is None and frequency is not None:
                fields["exact"] = frequency
                snapped += 1
            if fields:
                store.set_fields(member, **fields)
            real_demand = has_observed_demand(m["impr"], m["pos_y"], m["pos_g"])
            effective_exact = m["exact"] if m["exact"] is not None else frequency
            if (
                not real_demand
                and m["base"] is not None
                and m["base"] < cut_b
                and effective_exact is not None
                and effective_exact < cut_e
            ):
                store.set_status(
                    member, "dead", reason=f"base<{cut_b} and exact<{cut_e}", stage="exact"
                )
                dead += 1
    store.db.execute(
        "UPDATE provider_tasks SET state=?,error=? WHERE id=?",
        ("incomplete" if warnings else "complete", "; ".join(warnings) or None, task["id"]),
    )
    store.commit()
    for warning in warnings:
        print(f"  ! exact task {task['task_id']}: {warning}")
    return snapped, dead


def run(store, cfg, yes=False, limit=0):
    region = int(str((cfg.get("regions") or [DEFAULT_REGION])[0]))
    cut_b, cut_e = cfg.get("exact_cut_base", 20), cfg.get("exact_cut_exact", 10)
    store.db.execute(TASK_SCHEMA)
    store.commit()
    kept = store.phrases(status="kept")
    by_group, reps = {}, {}
    for row in kept:
        key = row["lemma_group"] if row["lemma_group"] is not None else f"solo:{row['norm']}"
        by_group.setdefault(key, []).append(row["norm"])
        if row["exact"] is None and (key not in reps or (row["base"] or 0) > reps[key][1]):
            reps[key] = (row["norm"], row["base"] or 0)
    tasks = store.db.execute(
        "SELECT * FROM provider_tasks WHERE provider='arsenkin' AND stage='exact' AND region=? ORDER BY id",
        (region,),
    ).fetchall()
    claimed = {normalize(p) for task in tasks for p in json.loads(task["payload_json"])["queries"]}
    # An existing paid representative also covers new members of its lemma group.
    covered = set(claimed)
    for members in by_group.values():
        if claimed.intersection(members):
            covered.update(members)
    todo = [p for p, _ in sorted(reps.values(), key=lambda item: -item[1]) if p not in covered]
    if limit:
        todo = todo[:limit]
    if len(todo) > cfg.get("budget_arsenkin_gate", 2000) and not yes:
        print(f"[GATE] exact: {len(todo)} new phrases exceed budget_arsenkin_gate; pass --yes")
        return {"gated": True}

    # Reserve each batch just before submission, so unsubmitted later batches stay eligible.
    client = None
    snapped = dead = pending = blocked = 0
    for task in tasks + [None for _ in range(0, len(todo), 100)]:
        if task is None:
            batch, todo = todo[:100], todo[100:]
            payload = wordstat_payload(batch, region)
            payload["ws"] = list(dict.fromkeys(payload["ws"] + ["quoted"]))
            if client is None:
                client = ArsenkinClient()
            cur = store.db.execute(
                "INSERT INTO provider_tasks(provider,stage,region,payload_json,state) VALUES(?,?,?,?,?)",
                (
                    "arsenkin",
                    "exact",
                    region,
                    json.dumps(payload, ensure_ascii=False),
                    "submitting",
                ),
            )
            task_row_id = cur.lastrowid
            store.commit()
            try:
                receipt = client.set_task("wordstat", payload)
            except Exception as exc:
                error_receipt = getattr(exc, "payload", None) or {}
                charged = error_receipt.get("cost") if isinstance(error_receipt, dict) else None
                store.db.execute(
                    "UPDATE provider_tasks SET state='unknown',cost=?,error=? WHERE id=?",
                    (charged, str(exc), task_row_id),
                )
                if charged is not None:
                    store.add_run(
                        "exact",
                        len(batch),
                        cost_arsenkin=charged,
                        note=f"unknown task, submission={task_row_id}",
                    )
                else:
                    store.commit()
                blocked += 1
                print(f"  ! exact submission {task_row_id}: {exc}; automatic resubmission blocked")
                continue
            task_id = receipt.get("task_id")
            # The toolkit validates task IDs; retain uncertainty if an adapter violates that contract.
            if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id <= 0:
                store.db.execute(
                    "UPDATE provider_tasks SET state='unknown',cost=?,error=? WHERE id=?",
                    (receipt.get("cost"), "provider returned no usable task_id", task_row_id),
                )
                store.add_run(
                    "exact",
                    len(batch),
                    cost_arsenkin=receipt.get("cost") or 0,
                    note=f"unknown task, submission={task_row_id}",
                )
                blocked += 1
                continue
            store.db.execute(
                "UPDATE provider_tasks SET task_id=?,cost=?,state='pending' WHERE id=?",
                (task_id, receipt.get("cost"), task_row_id),
            )
            # Same commit stores the accepted ID and its actual charge, not a balance delta.
            store.add_run(
                "exact",
                len(batch),
                cost_arsenkin=receipt.get("cost") or 0,
                note=f"task_id={task_id}",
            )
            task = store.db.execute(
                "SELECT * FROM provider_tasks WHERE id=?", (task_row_id,)
            ).fetchone()
        if task["state"] in ("submitting", "unknown"):
            blocked += 1
            continue
        if task["result_json"] is None:
            if client is None:
                client = ArsenkinClient()
            try:
                raw = client.wait(task["task_id"])
            except Exception as exc:
                store.db.execute(
                    "UPDATE provider_tasks SET state='pending',error=? WHERE id=?",
                    (str(exc), task["id"]),
                )
                store.commit()
                pending += 1
                print(f"  ! exact task {task['task_id']}: {exc}; the result can be fetched later")
                continue
            store.db.execute(
                "UPDATE provider_tasks SET result_json=?,state='received',error=NULL WHERE id=?",
                (json.dumps(raw, ensure_ascii=False), task["id"]),
            )
            store.commit()
            task = store.db.execute(
                "SELECT * FROM provider_tasks WHERE id=?", (task["id"],)
            ).fetchone()
        try:
            n, d = _apply_result(store, task, by_group, cut_b, cut_e)
        except Exception:
            store.db.rollback()  # Receipt/raw response/cost already committed; retry parsing safely.
            raise
        snapped += n
        dead += d
    incomplete = store.db.execute(
        "SELECT COUNT(*) FROM provider_tasks WHERE provider='arsenkin' AND stage='exact' "
        "AND region=? AND state='incomplete'",
        (region,),
    ).fetchone()[0]
    print(
        f"exact: snapped={snapped}, dead={dead}, pending={pending}, blocked={blocked}, incomplete={incomplete}"
    )
    return {
        "snapped": snapped,
        "dead": dead,
        "pending": pending,
        "blocked": blocked,
        "incomplete": incomplete,
    }
