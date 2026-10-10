"""Negative cases for the retained external-check validators read back on resume."""

import sqlite3

import pytest

from seohead.storage import ScanError
from seohead.storage.external_checks import (
    MAX_EXTERNAL_CHECKS,
    check_item,
    ensure_schema,
    validate_check,
    validate_summary,
)


def _payload(**overrides):
    payload = {
        "url": "https://example.test/out",
        "depth": 1,
        "sources": ["https://example.test/"],
        "source_count": 1,
        "outcome": "fetched",
        "reason": "",
        "status_code": 200,
        "content_type": "text/html",
        "redirect_url": "",
        "redirect_chain": [],
        "final_url": "https://example.test/out",
        "error": "",
        "error_kind": "",
    }
    payload.update(overrides)
    return payload


def _item(key="0", **overrides):
    item = check_item(int(key), _payload())
    item.update(overrides)
    return item


def _summary(**overrides):
    summary = {
        "enabled": True,
        "ran": True,
        "policy": {},
        "robots_policy": "not_evaluated",
        "destinations": 1,
        "records_carried": 0,
        "resumed": 0,
        "fetched": 1,
        "failed": 0,
        "blocked": 0,
        "skipped": 0,
        "skipped_reasons": {},
        "hosts_contacted": 1,
        "requests": 1,
        "finish_reason": "complete",
    }
    summary.update(overrides)
    return summary


def test_valid_check_and_summary_pass():
    validate_check(_item(), _payload())
    validate_summary({"item_key": "run", "completeness": "complete", "reason": ""}, _summary())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(outcome="unknown"),
        lambda p: p.update(sources=[f"https://example.test/{i}" for i in range(9)], source_count=9),
        lambda p: p.update(source_count=0),
        lambda p: p.update(redirect_chain=["x"] * 11),
        lambda p: p.update(depth=-1),
        lambda p: p.update(status_code="200"),
        lambda p: p.update(extra="field"),
        lambda p: p.pop("error_kind"),
    ],
)
def test_check_payload_rejects_invalid_shape(mutate):
    payload = _payload()
    mutate(payload)
    with pytest.raises(ScanError, match="invalid"):
        validate_check(_item(), payload)


def test_check_rejects_ordinal_beyond_bound_and_non_dict_payload():
    with pytest.raises(ScanError, match="retained bound"):
        check_item(MAX_EXTERNAL_CHECKS, _payload())
    with pytest.raises(ScanError, match="invalid"):
        validate_check(_item(), ["not", "a", "dict"])
    over = dict(_item(), item_key=str(MAX_EXTERNAL_CHECKS))
    with pytest.raises(ScanError, match="invalid"):
        validate_check(over, _payload())


def test_check_rejects_partial_or_reasoned_item():
    with pytest.raises(ScanError, match="invalid"):
        validate_check(_item(completeness="partial"), _payload())
    with pytest.raises(ScanError, match="invalid"):
        validate_check(_item(reason="why"), _payload())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.update(enabled=False),
        lambda s: s.update(robots_policy="evaluated"),
        lambda s: s.update(fetched=-1),
        lambda s: s.update(skipped_reasons=[]),
        lambda s: s.update(finish_reason=None),
        lambda s: s.pop("requests"),
    ],
)
def test_summary_payload_rejects_invalid_shape(mutate):
    summary = _summary()
    mutate(summary)
    with pytest.raises(ScanError, match="invalid"):
        validate_summary({"item_key": "run", "completeness": "complete", "reason": ""}, summary)


def test_summary_rejects_wrong_key_or_partial_completeness():
    with pytest.raises(ScanError, match="invalid"):
        validate_summary(
            {"item_key": "other", "completeness": "complete", "reason": ""}, _summary()
        )
    with pytest.raises(ScanError, match="invalid"):
        validate_summary({"item_key": "run", "completeness": "partial", "reason": ""}, _summary())


def test_ensure_schema_is_idempotent_and_enforces_outcome_check(tmp_path):
    con = sqlite3.connect(tmp_path / "x.sqlite")
    ensure_schema(con)
    ensure_schema(con)
    con.execute(
        "INSERT INTO external_checks(ordinal,outcome,payload_json) VALUES(0,'fetched','{}')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO external_checks(ordinal,outcome,payload_json) VALUES(1,'bogus','{}')"
        )
    con.close()
