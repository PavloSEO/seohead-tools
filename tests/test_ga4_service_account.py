"""GA4 landing pages: service-account fallback and empty reports."""

from seohead.data_sources import credentials, ga4, gsc


def test_empty_report_is_zero_rows():
    r = ga4.landing_pages("1", "2026-08-01", "2026-08-31", token="t",
                          transport=lambda url, payload, token: "{}")
    assert r["ok"] is True


def test_falls_back_to_service_account(monkeypatch):
    def no_bearer():
        raise credentials.MissingCredential("no ga4 bearer")

    seen = {}
    monkeypatch.setattr(credentials, "ga4_access_token", no_bearer)
    monkeypatch.setattr(credentials, "gsc_service_account_available", lambda: True)
    monkeypatch.setattr(gsc, "service_account_access_token", lambda scope: seen.setdefault("scope", scope) and "sa")

    def transport(url, payload, token):
        seen["token"] = token
        return '{"rows": []}'

    r = ga4.landing_pages("1", "2026-08-01", "2026-08-31", transport=transport)
    assert r["ok"] is True
    assert seen["scope"] == ga4.READONLY_SCOPE
    assert seen["token"] == "sa"


def test_no_credentials_at_all(monkeypatch):
    def no_bearer():
        raise credentials.MissingCredential("no ga4 bearer")

    monkeypatch.setattr(credentials, "ga4_access_token", no_bearer)
    monkeypatch.setattr(credentials, "gsc_service_account_available", lambda: False)
    r = ga4.landing_pages("1", "2026-08-01", "2026-08-31")
    assert r["state"] == "not_configured"
