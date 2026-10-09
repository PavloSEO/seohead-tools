"""Offline publication and branded-search cohort projections.

These projections consume saved, normalized evidence.  They do not collect a
provider response, decide why traffic changed, or turn an absent query into a
zero.  Both outputs are deliberately small, inspectable CSV/JSON packages so
the later BI/Looker consumer can read a stable local artifact.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from seohead.checks.external_join import normalize_join_key
from seohead.data_sources.evidence_import import NORMALIZED_FORMAT
from seohead.reports import neutralize_formula

PUBLICATION_FORMAT = "seohead.publication-cohort-input.v1"
GSC_PROGRESS_FORMAT = "seohead.gsc-progress-input.v1"
PUBLICATION_RESULT_FORMAT = "seohead.publication-cohort-result.v1"
GSC_PROGRESS_RESULT_FORMAT = "seohead.gsc-progress-result.v1"
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_ROWS = 100_000


class CohortProjectionError(ValueError):
    """A saved evidence package cannot support the requested projection."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_input(document: Any, file: str | None, expected: str) -> dict[str, Any]:
    if (document is None) == (file is None):
        raise CohortProjectionError("supply exactly one of document or file")
    if file is not None:
        path = Path(file)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
            raise CohortProjectionError("file must be a bounded regular JSON file")
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CohortProjectionError(f"cannot read JSON input: {exc}") from exc
    if not isinstance(document, dict) or document.get("format") != expected:
        raise CohortProjectionError(f"document must use {expected}")
    return document


def _date_entry(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {"raw": None, "value": None, "state": "unknown", "source": None}
    if not isinstance(value, dict) or set(value) - {"raw", "value", "state", "source", "reason"}:
        raise CohortProjectionError(f"{name} must be a date evidence object")
    state = value.get("state", "measured")
    if state not in {"measured", "unknown", "ambiguous", "conflicting", "missing"}:
        raise CohortProjectionError(f"{name}.state is unsupported")
    parsed = value.get("value")
    if parsed is not None:
        if not isinstance(parsed, str):
            raise CohortProjectionError(f"{name}.value must be an ISO calendar date")
        try:
            date.fromisoformat(parsed)
        except ValueError as exc:
            raise CohortProjectionError(f"{name}.value must be an ISO calendar date") from exc
    if state == "measured" and parsed is None:
        raise CohortProjectionError(f"{name} cannot be measured without value")
    return {
        "raw": value.get("raw"),
        "value": parsed,
        "state": state,
        "source": value.get("source"),
        "reason": value.get("reason"),
    }


def _metric(entry: Any, name: str) -> tuple[float | None, str, str | None]:
    if not isinstance(entry, dict):
        return None, "unknown", "metric was not supplied"
    state = entry.get("state", "unknown")
    value = entry.get("value")
    if state != "measured":
        return None, state, entry.get("reason") or "metric is not measured"
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CohortProjectionError(f"{name} measured value must be numeric")
    return float(value), state, entry.get("reason")


def _normalized_documents(value: Any) -> list[dict[str, Any]]:
    documents = value if isinstance(value, list) else [value]
    if not documents or len(documents) > 32:
        raise CohortProjectionError("evidence must contain 1 to 32 normalized documents")
    if any(
        not isinstance(item, dict) or item.get("format") != NORMALIZED_FORMAT for item in documents
    ):
        raise CohortProjectionError(f"evidence entries must use {NORMALIZED_FORMAT}")
    return documents


def _source(document: dict[str, Any], ordinal: int) -> dict[str, Any]:
    mapping = document.get("mapping") or {}
    source = mapping.get("source") or {}
    collection = mapping.get("collection") or {}
    period = mapping.get("period") or {}
    if not isinstance(period, dict):
        period = {}
    return {
        "source_id": f"evidence-{ordinal}",
        "provider": source.get("provider") or "unknown",
        "operation": source.get("operation"),
        "reporting_identity": source.get("reporting_identity"),
        "timezone": source.get("timezone"),
        "attribution": source.get("attribution"),
        "search_type": source.get("search_type"),
        "period_start": period.get("start_date"),
        "period_end": period.get("end_date"),
        "collection": collection,
        "dimensions": mapping.get("dimensions") or [],
    }


def _atomic_package(
    out_dir: str | None, files: dict[str, list[dict[str, Any]]], manifest: dict[str, Any]
) -> dict[str, Any]:
    if not out_dir:
        raise CohortProjectionError("out_dir is required")
    destination = Path(out_dir)
    if destination.exists() or destination.is_symlink():
        raise CohortProjectionError("out_dir must not already exist")
    if not destination.parent.is_dir():
        raise CohortProjectionError("out_dir parent must be an existing regular directory")
    with tempfile.TemporaryDirectory(prefix=".seohead-cohort-", dir=destination.parent) as temp:
        stage = Path(temp)
        output = {}
        for name, rows in files.items():
            columns = sorted({key for row in rows for key in row})
            path = stage / name
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="raise")
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {
                            key: neutralize_formula(_canonical(value))
                            if isinstance(value, dict | list)
                            else neutralize_formula(value)
                            for key, value in row.items()
                        }
                    )
            raw = path.read_bytes()
            output[name] = {
                "rows": len(rows),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        manifest["files"] = output
        payload = (
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode()
        (stage / "manifest.json").write_bytes(payload)
        os.chmod(stage, 0o700)
        os.replace(stage, destination)
    return {
        "output_directory": str(destination),
        "manifest": str(destination / "manifest.json"),
        "files": output,
    }


def _publication_contents(document: dict[str, Any]) -> list[dict[str, Any]]:
    content = document.get("content")
    if not isinstance(content, list) or len(content) > MAX_ROWS:
        raise CohortProjectionError("content must be a bounded list")
    rows = []
    for ordinal, item in enumerate(content):
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise CohortProjectionError("every content row needs a URL")
        publication = _date_entry(item.get("publication"), "publication")
        modified = _date_entry(item.get("modified"), "modified")
        first_seen = _date_entry(item.get("first_observed"), "first_observed")
        authors = item.get("authors")
        if authors is None:
            authors, author_state = [], "missing"
        elif isinstance(authors, list) and all(
            isinstance(author, str) and author for author in authors
        ):
            author_state = "measured"
        else:
            raise CohortProjectionError("authors must be a list of non-empty strings when supplied")
        word_count, word_state, word_reason = _metric(item.get("word_count"), "word_count")
        if word_count is not None and word_count < 0:
            raise CohortProjectionError("word_count cannot be negative")
        key = normalize_join_key(item["url"])
        rows.append(
            {
                "content_id": f"content-{ordinal}",
                "url": item["url"],
                "url_key": key,
                "url_key_state": "keyed" if key else "unkeyable",
                "publication": publication,
                "modified": modified,
                "first_observed": first_seen,
                "authors": authors,
                "author_state": author_state,
                "word_count": word_count,
                "word_count_state": word_state,
                "word_count_reason": word_reason,
            }
        )
    return rows


def _age_windows(document: dict[str, Any]) -> tuple[list[dict[str, Any]], date | None]:
    value = document.get("age_windows", [])
    if not isinstance(value, list):
        raise CohortProjectionError("age_windows must be a list when supplied")
    windows = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "start_day", "end_day"}:
            raise CohortProjectionError("age windows require id, start_day and end_day")
        if (
            not isinstance(item["id"], str)
            or isinstance(item["start_day"], bool)
            or isinstance(item["end_day"], bool)
            or not isinstance(item["start_day"], int)
            or not isinstance(item["end_day"], int)
            or item["start_day"] < 0
            or item["start_day"] > item["end_day"]
        ):
            raise CohortProjectionError("age windows require ordered non-negative integer days")
        windows.append(item)
    if len({item["id"] for item in windows}) != len(windows):
        raise CohortProjectionError("age window ids must be unique")
    as_of = document.get("as_of_date")
    if as_of is None:
        return windows, None
    try:
        return windows, date.fromisoformat(as_of)
    except (TypeError, ValueError) as exc:
        raise CohortProjectionError("as_of_date must be an ISO calendar date") from exc


def publication_cohorts(
    document: Any = None, file: str | None = None, out_dir: str | None = None
) -> dict[str, Any]:
    """Project publication inventory and separately labelled provider observations."""
    input_document = _read_input(document, file, PUBLICATION_FORMAT)
    content = _publication_contents(input_document)
    age_windows, as_of = _age_windows(input_document)
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in content:
        if row["url_key"]:
            by_key[row["url_key"]].append(row)
    collisions = {key for key, rows in by_key.items() if len(rows) > 1}
    inventory, authors, observations, age_observations = [], [], [], []
    summary = defaultdict(lambda: {"content": 0, "authors": 0})
    for row in content:
        publication = row["publication"]
        cohort = publication["value"][:7] if publication["state"] == "measured" else "unknown"
        join_state = "collision" if row["url_key"] in collisions else row["url_key_state"]
        inventory.append(
            {
                "content_id": row["content_id"],
                "url": row["url"],
                "url_key": row["url_key"],
                "join_state": join_state,
                "publication_cohort": cohort,
                "publication_raw": row["publication"]["raw"],
                "publication_date": publication["value"],
                "publication_state": publication["state"],
                "publication_source": publication["source"],
                "modified_raw": row["modified"]["raw"],
                "modified_date": row["modified"]["value"],
                "modified_state": row["modified"]["state"],
                "first_observed_raw": row["first_observed"]["raw"],
                "first_observed_date": row["first_observed"]["value"],
                "first_observed_state": row["first_observed"]["state"],
                "authors_json": row["authors"],
                "author_state": row["author_state"],
                "word_count": row["word_count"],
                "word_count_state": row["word_count_state"],
                "word_count_reason": row["word_count_reason"],
            }
        )
        summary[cohort]["content"] += 1
        for author in row["authors"]:
            authors.append(
                {"content_id": row["content_id"], "publication_cohort": cohort, "author": author}
            )
            summary[cohort]["authors"] += 1
    for doc_ordinal, evidence in enumerate(_normalized_documents(input_document.get("evidence"))):
        source = _source(evidence, doc_ordinal)
        for row in evidence.get("rows") or []:
            url = (row.get("url") or {}).get("normalized")
            key = normalize_join_key(url) if isinstance(url, str) else None
            matched = by_key.get(key, []) if key and key not in collisions else []
            observed_date = (row.get("dimensions") or {}).get("date")
            try:
                observed_day = (
                    date.fromisoformat(observed_date) if isinstance(observed_date, str) else None
                )
            except ValueError:
                observed_day = None
            for metric_name, metric in (row.get("metrics") or {}).items():
                value, state, reason = _metric(metric, metric_name)
                observations.append(
                    {
                        "source_id": source["source_id"],
                        "provider": source["provider"],
                        "metric": metric_name,
                        "url": url,
                        "url_key": key,
                        "content_ids_json": [item["content_id"] for item in matched],
                        "publication_cohorts_json": sorted(
                            {
                                item["publication"]["value"][:7]
                                if item["publication"]["state"] == "measured"
                                else "unknown"
                                for item in matched
                            }
                        ),
                        "join_state": "matched"
                        if matched
                        else "collision"
                        if key in collisions
                        else "unmatched",
                        "period_start": source["period_start"],
                        "period_end": source["period_end"],
                        "timezone": source["timezone"],
                        "attribution": source["attribution"],
                        "collection_state": source["collection"].get("state"),
                        "sampled": source["collection"].get("sampled"),
                        "thresholded": source["collection"].get("thresholded"),
                        "truncated": source["collection"].get("truncated"),
                        "metric_value": value,
                        "metric_state": state,
                        "metric_reason": reason,
                        "dimensions_json": row.get("dimensions") or {},
                        "ambiguous_row": bool(row.get("ambiguous")),
                    }
                )
                for item in matched:
                    publication = item["publication"]
                    if publication["state"] != "measured" or observed_day is None:
                        continue
                    published_day = date.fromisoformat(publication["value"])
                    age_day = (observed_day - published_day).days
                    for window in age_windows:
                        if not window["start_day"] <= age_day <= window["end_day"]:
                            continue
                        mature = (
                            None
                            if as_of is None
                            else as_of >= published_day + timedelta(days=window["end_day"])
                        )
                        age_observations.append(
                            {
                                "content_id": item["content_id"],
                                "source_id": source["source_id"],
                                "provider": source["provider"],
                                "metric": metric_name,
                                "window_id": window["id"],
                                "window_start_day": window["start_day"],
                                "window_end_day": window["end_day"],
                                "expected_days": window["end_day"] - window["start_day"] + 1,
                                "age_day": age_day,
                                "observation_date": observed_date,
                                "metric_value": value,
                                "metric_state": state,
                                "mature": mature,
                                "history_state": "partial"
                                if source["collection"].get("state") != "complete"
                                else "observed_day_only",
                            }
                        )
    cohort_rows = [
        {
            "publication_cohort": key,
            "content_count": value["content"],
            "author_contributions": value["authors"],
        }
        for key, value in sorted(summary.items())
    ]
    manifest = {
        "format": PUBLICATION_RESULT_FORMAT,
        "source_format": PUBLICATION_FORMAT,
        "policy": {
            "publication_precedence": "publication, modification and first observation remain separate evidence fields",
            "aggregation": "GSC and analytics observations remain separately labelled and are never combined",
            "absence": "an absent or suppressed provider row is not a measured zero",
            "causality": "publication timing is descriptive and does not establish traffic causation",
        },
        "colliding_url_keys": sorted(collisions),
        "input_content_rows": len(content),
        "input_evidence_documents": len(_normalized_documents(input_document.get("evidence"))),
        "age_windows": age_windows,
        "as_of_date": as_of.isoformat() if as_of else None,
    }
    package = _atomic_package(
        out_dir,
        {
            "publication_inventory.csv": inventory,
            "publication_authors.csv": authors,
            "provider_observations.csv": observations,
            "publication_cohorts.csv": cohort_rows,
            "age_window_observations.csv": age_observations,
        },
        manifest,
    )
    return {
        "format": PUBLICATION_RESULT_FORMAT,
        **package,
        "content_rows": len(content),
        "observation_rows": len(observations),
    }


def _query_text(value: Any) -> str | None:
    return (
        unicodedata.normalize("NFKC", value).casefold().strip()
        if isinstance(value, str) and value.strip()
        else None
    )


def _brand_rules(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise CohortProjectionError("brand_rules must be a non-empty list")
    rules = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"id", "match", "value"}:
            raise CohortProjectionError("brand rules may contain id, match and value")
        if item.get("match") not in {"exact", "tokens"} or not isinstance(item.get("id"), str):
            raise CohortProjectionError("brand rules require id and exact/tokens match")
        text = _query_text(item.get("value"))
        if not text:
            raise CohortProjectionError("brand rule values must be non-empty text")
        rules.append({"id": item["id"], "match": item["match"], "value": text})
    return rules


def _classify(query: Any, rules: list[dict[str, str]]) -> tuple[str, str | None, str | None]:
    text = _query_text(query)
    if text is None:
        return "unknown", None, "query dimension is missing"
    hits = []
    for rule in rules:
        exact = rule["match"] == "exact" and text == rule["value"]
        tokens = rule["match"] == "tokens" and re.search(
            r"(?<!\w)" + re.escape(rule["value"]) + r"(?!\w)", text
        )
        if exact or tokens:
            hits.append(rule["id"])
    if len(hits) > 1:
        return "ambiguous", None, "multiple brand rules matched"
    if hits:
        return "branded", hits[0], None
    return "non_branded", None, None


def _overrides(value: Any) -> dict[str, dict[str, str | None]]:
    if value is None:
        return {}
    if not isinstance(value, list):
        raise CohortProjectionError("overrides must be a list when supplied")
    output = {}
    for item in value:
        if not isinstance(item, dict) or set(item) - {
            "query",
            "classification",
            "rule_id",
            "reason",
        }:
            raise CohortProjectionError(
                "overrides may contain query, classification, rule_id and reason"
            )
        query = _query_text(item.get("query"))
        classification = item.get("classification")
        if not query or classification not in {"branded", "non_branded", "unclassified"}:
            raise CohortProjectionError("overrides need a query and a recorded classification")
        if query in output:
            raise CohortProjectionError("overrides cannot classify one normalized query twice")
        output[query] = {
            "classification": classification,
            "rule_id": item.get("rule_id"),
            "reason": item.get("reason") or "operator override",
        }
    return output


def _periods(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) < 2:
        raise CohortProjectionError("periods must contain at least two comparable windows")
    output = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "start_date", "end_date"}:
            raise CohortProjectionError("periods require id, start_date and end_date")
        try:
            start, end = (
                date.fromisoformat(item["start_date"]),
                date.fromisoformat(item["end_date"]),
            )
        except (TypeError, ValueError) as exc:
            raise CohortProjectionError("period dates must be ISO calendar dates") from exc
        if start > end or not isinstance(item["id"], str):
            raise CohortProjectionError("periods must have ordered dates and string ids")
        output.append({**item, "start": start, "end": end})
    if len({item["id"] for item in output}) != len(output):
        raise CohortProjectionError("period ids must be unique")
    return output


def _position_bands(document: dict[str, Any]) -> list[dict[str, float | str | None]]:
    value = document.get(
        "position_bands",
        [
            {"id": "1-3", "min": 1, "max": 3},
            {"id": "4-10", "min": 4, "max": 10},
            {"id": "11-20", "min": 11, "max": 20},
            {"id": "21+", "min": 21, "max": None},
        ],
    )
    if not isinstance(value, list) or not value:
        raise CohortProjectionError("position_bands must be a non-empty list")
    bands = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "min", "max"}:
            raise CohortProjectionError("position bands require id, min and max")
        lower, upper = item["min"], item["max"]
        if (
            not isinstance(item["id"], str)
            or isinstance(lower, bool)
            or not isinstance(lower, int | float)
            or lower < 0
            or (
                upper is not None
                and (isinstance(upper, bool) or not isinstance(upper, int | float) or upper < lower)
            )
        ):
            raise CohortProjectionError("position bands need ordered numeric boundaries")
        bands.append(
            {"id": item["id"], "min": float(lower), "max": None if upper is None else float(upper)}
        )
    if len({item["id"] for item in bands}) != len(bands):
        raise CohortProjectionError("position band ids must be unique")
    return bands


def _band(value: float | None, bands: list[dict[str, float | str | None]]) -> str | None:
    if value is None:
        return None
    for item in bands:
        upper = item["max"]
        if value >= item["min"] and (upper is None or value <= upper):
            return str(item["id"])
    return None


def gsc_progress(
    document: Any = None, file: str | None = None, out_dir: str | None = None
) -> dict[str, Any]:
    """Project saved GSC query rows into explicit branded/non-branded trends."""
    input_document = _read_input(document, file, GSC_PROGRESS_FORMAT)
    rules = _brand_rules(input_document.get("brand_rules"))
    periods, bands = _periods(input_document.get("periods")), _position_bands(input_document)
    overrides = _overrides(input_document.get("overrides"))
    queries, groups = (
        [],
        defaultdict(
            lambda: {
                "clicks": 0.0,
                "impressions": 0.0,
                "ctr_clicks": 0.0,
                "ctr_impressions": 0.0,
                "rows": 0,
                "queries": set(),
                "unmeasured": 0,
                "collection_states": set(),
            }
        ),
    )
    for doc_ordinal, evidence in enumerate(_normalized_documents(input_document.get("evidence"))):
        source = _source(evidence, doc_ordinal)
        if source["provider"] != "gsc":
            raise CohortProjectionError("gsc progress accepts only normalized GSC evidence")
        for row in evidence.get("rows") or []:
            dimensions = row.get("dimensions") or {}
            query = dimensions.get("query")
            query_normalized = _query_text(query)
            override = overrides.get(query_normalized) if query_normalized else None
            if override:
                classification, rule_id, reason, classification_source = (
                    str(override["classification"]),
                    override["rule_id"],
                    override["reason"],
                    "operator_override",
                )
            else:
                classification, rule_id, reason = _classify(query, rules)
                classification_source = "rule"
            row_date = dimensions.get("date")
            selected = []
            if isinstance(row_date, str):
                try:
                    day = date.fromisoformat(row_date)
                except ValueError:
                    day = None
                if day:
                    selected = [
                        period for period in periods if period["start"] <= day <= period["end"]
                    ]
            elif source["period_start"] and source["period_end"]:
                selected = [
                    period
                    for period in periods
                    if period["start_date"] == source["period_start"]
                    and period["end_date"] == source["period_end"]
                ]
            if len(selected) != 1:
                continue
            period = selected[0]
            scope = {
                key: value
                for key, value in dimensions.items()
                if key not in {"query", "page", "date"}
            }
            scope.update(
                {
                    "timezone": source["timezone"],
                    "search_type": source["search_type"],
                    "source_id": source["source_id"],
                }
            )
            scope_id = hashlib.sha256(_canonical(scope).encode()).hexdigest()[:16]
            clicks, clicks_state, clicks_reason = _metric(
                (row.get("metrics") or {}).get("clicks"), "clicks"
            )
            impressions, impressions_state, impressions_reason = _metric(
                (row.get("metrics") or {}).get("impressions"), "impressions"
            )
            position, position_state, position_reason = _metric(
                (row.get("metrics") or {}).get("position"), "position"
            )
            query_row = {
                "source_id": source["source_id"],
                "scope_id": scope_id,
                "scope_json": scope,
                "period_id": period["id"],
                "query_raw": query,
                "query_normalized": query_normalized,
                "classification": classification,
                "matched_rule_id": rule_id,
                "classification_reason": reason,
                "classification_source": classification_source,
                "clicks": clicks,
                "clicks_state": clicks_state,
                "clicks_reason": clicks_reason,
                "impressions": impressions,
                "impressions_state": impressions_state,
                "impressions_reason": impressions_reason,
                "average_position": position,
                "position_state": position_state,
                "position_reason": position_reason,
                "collection_state": source["collection"].get("state"),
                "sampled": source["collection"].get("sampled"),
                "thresholded": source["collection"].get("thresholded"),
                "truncated": source["collection"].get("truncated"),
                "ambiguous_row": bool(row.get("ambiguous")),
            }
            query_row["position_band"] = (
                _band(position, bands) if position_state == "measured" else None
            )
            queries.append(query_row)
            key = (scope_id, period["id"], classification)
            bucket = groups[key]
            bucket["rows"] += 1
            bucket["collection_states"].add(source["collection"].get("state") or "unknown")
            if clicks is not None:
                bucket["clicks"] += clicks
            else:
                bucket["unmeasured"] += 1
            if impressions is not None:
                bucket["impressions"] += impressions
            else:
                bucket["unmeasured"] += 1
            if clicks is not None and impressions is not None:
                bucket["ctr_clicks"] += clicks
                bucket["ctr_impressions"] += impressions
            if query_row["query_normalized"]:
                bucket["queries"].add(query_row["query_normalized"])
    summaries = []
    for (scope_id, period_id, classification), bucket in sorted(groups.items()):
        ctr = (
            bucket["ctr_clicks"] / bucket["ctr_impressions"] if bucket["ctr_impressions"] else None
        )
        summaries.append(
            {
                "scope_id": scope_id,
                "period_id": period_id,
                "classification": classification,
                "clicks": bucket["clicks"],
                "impressions": bucket["impressions"],
                "ctr": ctr,
                "contributing_query_count": len(bucket["queries"]),
                "source_rows": bucket["rows"],
                "unmeasured_metric_cells": bucket["unmeasured"],
                "collection_states_json": sorted(bucket["collection_states"]),
            }
        )
    order = {item["id"]: index for index, item in enumerate(periods)}
    changes = []
    by_summary = defaultdict(list)
    for item in summaries:
        by_summary[(item["scope_id"], item["classification"])].append(item)
    for (scope_id, classification), entries in sorted(by_summary.items()):
        prior = None
        for current in sorted(entries, key=lambda item: order[item["period_id"]]):
            if prior is not None:
                if prior["clicks"] == 0:
                    percent, state = (
                        (0.0, "unchanged_zero")
                        if current["clicks"] == 0
                        else (None, "new_from_zero")
                    )
                else:
                    percent, state = (
                        (current["clicks"] - prior["clicks"]) / prior["clicks"],
                        "defined",
                    )
                changes.append(
                    {
                        "scope_id": scope_id,
                        "classification": classification,
                        "from_period_id": prior["period_id"],
                        "to_period_id": current["period_id"],
                        "click_change": current["clicks"] - prior["clicks"],
                        "click_change_percent": percent,
                        "percent_change_state": state,
                    }
                )
            prior = current
    position_rows = []
    by_query = defaultdict(list)
    for row in queries:
        if (
            row["position_state"] == "measured"
            and not row["ambiguous_row"]
            and row["query_normalized"]
        ):
            by_query[(row["scope_id"], row["classification"], row["query_normalized"])].append(row)
    for identity, entries in sorted(by_query.items()):
        prior = None
        for row in sorted(entries, key=lambda item: order[item["period_id"]]):
            movement = None
            if prior is not None:
                movement = (
                    "improved"
                    if row["average_position"] < prior["average_position"]
                    else "worsened"
                    if row["average_position"] > prior["average_position"]
                    else "unchanged"
                )
            position_rows.append(
                {
                    "scope_id": identity[0],
                    "classification": identity[1],
                    "query_normalized": identity[2],
                    "period_id": row["period_id"],
                    "average_position": row["average_position"],
                    "position_band": row["position_band"],
                    "previous_period_id": prior["period_id"] if prior else None,
                    "previous_average_position": prior["average_position"] if prior else None,
                    "movement": movement,
                    "interpretation": "average-position band; not a tracked rank or page-placement claim",
                }
            )
            prior = row
    manifest = {
        "format": GSC_PROGRESS_RESULT_FORMAT,
        "source_format": GSC_PROGRESS_FORMAT,
        "brand_rules": rules,
        "overrides": list(overrides.values()),
        "periods": [
            {key: item[key] for key in ("id", "start_date", "end_date")} for item in periods
        ],
        "position_bands": bands,
        "policy": {
            "ctr": "CTR is clicks divided by impressions for rows where both are measured; row CTR values are never averaged",
            "absence": "query rows absent from a supplied, limited or anonymized export remain unknown",
            "scope": "country/device/date/source scope is retained per scope_id and never silently combined",
            "position": "average-position values are not rank snapshots or page-one placement claims",
            "causality": "the projection describes observations and makes no SEO diagnosis or growth claim",
        },
        "source_rows": len(queries),
    }
    package = _atomic_package(
        out_dir,
        {
            "query_contributions.csv": queries,
            "period_summaries.csv": summaries,
            "period_changes.csv": changes,
            "position_bands.csv": position_rows,
        },
        manifest,
    )
    return {
        "format": GSC_PROGRESS_RESULT_FORMAT,
        **package,
        "query_rows": len(queries),
        "summary_rows": len(summaries),
    }
