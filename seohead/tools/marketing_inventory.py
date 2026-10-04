"""Correlated CTA and embedded-form observations over supplied HTML (#875)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit

from bs4 import BeautifulSoup

MAX_DOCUMENTS = 500
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_OCCURRENCES = 10_000
DEFAULT_CTA_SELECTOR = "a.cta, [data-cta]"
DEFAULT_FORM_SELECTOR = "form, iframe"
DEFAULT_ID_ATTRIBUTES = ("id", "name", "data-form-id")
DEFAULT_ID_PARAMETERS = ("form_id", "formid", "id")


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _safe_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in {"=", "+", "-", "@"} else text


def _selector(value: Any, fallback: str, name: str) -> str:
    if value is None:
        return fallback
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValueError(f"{name} must be a non-empty CSS selector up to 500 characters")
    return value


def _document(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {
        "url",
        "html",
        "representation",
        "document_ref",
        "body_state",
    }:
        raise ValueError(
            "each document accepts url, html, representation, document_ref, and body_state"
        )
    url = value.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValueError("each document url must be absolute HTTP(S)")
    html = value.get("html")
    state = value.get("body_state", "complete")
    if state not in {"complete", "unavailable", "partial"}:
        raise ValueError("body_state must be complete, unavailable, or partial")
    if state == "complete" and (
        not isinstance(html, str) or len(html.encode()) > MAX_DOCUMENT_BYTES
    ):
        raise ValueError("complete document html must be UTF-8 text up to 2 MiB")
    return {
        "url": url,
        "html": html if isinstance(html, str) else None,
        "representation": value.get("representation")
        if value.get("representation") in {"raw", "rendered"}
        else "raw",
        "document_ref": value.get("document_ref")
        if isinstance(value.get("document_ref"), str)
        else None,
        "body_state": state,
    }


def _identifier(
    tag: Any, attributes: tuple[str, ...], parameters: tuple[str, ...]
) -> tuple[str | None, str | None]:
    for attribute in attributes:
        value = tag.get(attribute)
        if isinstance(value, str) and value.strip():
            return attribute, value.strip()
    raw = tag.get("src") if tag.name == "iframe" else tag.get("action")
    if isinstance(raw, str):
        for name, value in parse_qsl(urlsplit(raw).query, keep_blank_values=True):
            if name.lower() in parameters:
                return f"url_parameter:{name}", value
    return None, None


def _write(out_dir: str, result: dict[str, Any]) -> dict[str, str]:
    directory = Path(out_dir).absolute()
    if directory.exists():
        raise ValueError("out_dir must not already exist")
    directory.mkdir(mode=0o700, parents=True)
    json_path = directory / "marketing-inventory.json"
    csv_path = directory / "marketing-occurrences.csv"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    fields = [
        "kind",
        "source_url",
        "document_ref",
        "representation",
        "occurrence",
        "element",
        "label",
        "raw_target",
        "resolved_target",
        "target_state",
        "identifier_rule",
        "identifier",
    ]
    with csv_path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in result["occurrences"]:
            writer.writerow({field: _safe_cell(row.get(field)) for field in fields})
    return {"json": str(json_path), "csv": str(csv_path)}


def inventory(
    documents: list[dict[str, Any]],
    *,
    cta_selector: str | None = None,
    form_selector: str | None = None,
    id_attributes: list[str] | None = None,
    id_parameters: list[str] | None = None,
    out_dir: str | None = None,
) -> dict[str, Any]:
    """Inspect supplied DOMs without requests, form submission, or iframe traversal."""
    if not isinstance(documents, list) or not documents or len(documents) > MAX_DOCUMENTS:
        raise ValueError(f"documents must contain 1..{MAX_DOCUMENTS} entries")
    cta_selector = _selector(cta_selector, DEFAULT_CTA_SELECTOR, "cta_selector")
    form_selector = _selector(form_selector, DEFAULT_FORM_SELECTOR, "form_selector")
    attributes = tuple(id_attributes or DEFAULT_ID_ATTRIBUTES)
    parameters = tuple(name.lower() for name in (id_parameters or DEFAULT_ID_PARAMETERS))
    if not all(
        isinstance(name, str) and name and len(name) <= 80 for name in attributes + parameters
    ):
        raise ValueError("identifier rules must be short non-empty strings")
    occurrences: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    for original in documents:
        doc = _document(original)
        if doc["body_state"] != "complete":
            unavailable.append(
                {key: doc[key] for key in ("url", "document_ref", "representation", "body_state")}
            )
            continue
        soup = BeautifulSoup(doc["html"], features="lxml")
        try:
            selected = [("cta", tag) for tag in soup.select(cta_selector)] + [
                ("form", tag) for tag in soup.select(form_selector)
            ]
        except Exception as exc:
            raise ValueError("invalid CSS selector") from exc
        for kind, tag in selected:
            if len(occurrences) >= MAX_OCCURRENCES:
                raise ValueError(f"inventory exceeds {MAX_OCCURRENCES} matched elements")
            raw_target = (
                tag.get("href")
                if kind == "cta"
                else tag.get("src")
                if tag.name == "iframe"
                else tag.get("action")
            )
            raw_target = (
                raw_target.strip() if isinstance(raw_target, str) and raw_target.strip() else None
            )
            rule, identifier = (
                _identifier(tag, attributes, parameters) if kind == "form" else (None, None)
            )
            occurrences.append(
                {
                    "kind": "cta"
                    if kind == "cta"
                    else "iframe"
                    if tag.name == "iframe"
                    else "form",
                    "source_url": doc["url"],
                    "document_ref": doc["document_ref"],
                    "representation": doc["representation"],
                    "occurrence": len(occurrences) + 1,
                    "element": tag.name,
                    "label": _text(tag.get_text(" ")) if kind == "cta" else None,
                    "raw_target": raw_target,
                    "resolved_target": urljoin(doc["url"], raw_target) if raw_target else None,
                    "target_state": "observed" if raw_target else "missing",
                    "identifier_rule": rule,
                    "identifier": identifier,
                }
            )
    cta_groups: dict[str, dict[str, Any]] = {}
    form_groups: dict[str, dict[str, Any]] = {}
    for row in occurrences:
        if row["kind"] == "cta" and row["resolved_target"]:
            group = cta_groups.setdefault(
                row["resolved_target"],
                {
                    "destination": row["resolved_target"],
                    "labels": set(),
                    "pages": set(),
                    "occurrences": 0,
                },
            )
            group["labels"].add(_text(row["label"]).lower())
            group["pages"].add(row["source_url"])
            group["occurrences"] += 1
        elif row["kind"] in {"form", "iframe"} and row["identifier"] is not None:
            group = form_groups.setdefault(
                row["identifier"],
                {
                    "identifier": row["identifier"],
                    "identifier_rule": row["identifier_rule"],
                    "pages": set(),
                    "occurrences": 0,
                },
            )
            group["pages"].add(row["source_url"])
            group["occurrences"] += 1
    result = {
        "format": "seohead.marketing-inventory.v1",
        "occurrences": occurrences,
        "cta_groups": [
            {**group, "labels": sorted(group["labels"]), "pages": sorted(group["pages"])}
            for group in cta_groups.values()
        ],
        "form_groups": [
            {**group, "pages": sorted(group["pages"])} for group in form_groups.values()
        ],
        "label_normalization": "lowercase_collapse_whitespace",
        "coverage": {
            "documents_supplied": len(documents),
            "documents_unavailable": unavailable,
            "complete": not unavailable,
            "limits": {"documents": MAX_DOCUMENTS, "occurrences": MAX_OCCURRENCES},
        },
        "notes": [
            "Rows are correlated to one matched DOM element; related fields are never zipped from independent page-level lists.",
            "Cross-origin iframe src values are observed only; iframe contents are never fetched or inspected.",
        ],
    }
    if out_dir:
        result["artifacts"] = _write(out_dir, result)
    return result
