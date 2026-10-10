import pytest

from seohead.semantics.stages.cluster import is_hub, is_hub_url, url_key

pytestmark = pytest.mark.usefixtures("semantics_offline")


def test_host_normalization_preserves_case_sensitive_pages():
    assert url_key("https://WWW.EXAMPLE.INVALID/Products/?x=1#part") == "example.invalid/Products"
    assert url_key("https://example.invalid/Products") != url_key(
        "https://example.invalid/products"
    )


def test_hub_names_in_paths_or_unrelated_hosts_are_not_hubs():
    assert is_hub_url("https://example.invalid/google.com") is False
    assert is_hub("wikipedia.org.example.invalid") is False
    assert is_hub_url("https://en.wikipedia.org/page") is True
    assert is_hub("portal.gov.ru") is True
