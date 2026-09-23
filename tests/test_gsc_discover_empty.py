"""discover_properties: a principal with no properties gets {} from Google — an empty list, not an error."""

from seohead.data_sources import gsc


def test_no_properties_is_empty_list():
    r = gsc.discover_properties(token="t", transport=lambda method, url, payload, token: "{}")
    assert r["ok"] is True
    assert r["properties"] == []


def test_non_object_is_still_malformed():
    r = gsc.discover_properties(token="t", transport=lambda method, url, payload, token: "[]")
    assert r["ok"] is False
