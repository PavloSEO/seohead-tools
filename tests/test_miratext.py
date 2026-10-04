import json

from seohead.data_sources import miratext


def test_free_request_is_form_encoded_and_resumable_without_key_echo():
    seen = []

    def send(encoded, _):
        seen.append(encoded)
        return json.dumps({"result": "ok", "hash": "job-1", "status": "draft"})

    result = miratext.analyze(
        urls=["https://example.test/a"],
        my="https://example.test/me",
        api_key="canary",
        transport=send,
    )
    assert result == {
        "ok": True,
        "state": "draft",
        "hash": "job-1",
        "paid": False,
        "resumable": True,
    }
    assert "url%5B%5D=https%3A%2F%2Fexample.test%2Fa" in seen[0]


def test_paid_and_keywords_are_refused_before_provider_call():
    result = miratext.analyze(
        urls=["https://example.test/a"],
        my="x",
        paid=True,
        api_key="canary",
        transport=lambda *_: (_ for _ in ()).throw(AssertionError()),
    )
    assert result["state"] == "confirmation_required"


def test_transport_failure_redacts_key():
    result = miratext.analyze(urls=["x"], my="y", api_key="canary", transport=lambda *_: "not json")
    assert result["ok"] is False and "canary" not in json.dumps(result)
