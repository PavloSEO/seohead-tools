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


def test_accepted_result_reduces_keyword_and_density_tables():
    result = miratext.analyze(
        hash="job-1",
        api_key="canary",
        top=1,
        transport=lambda *_: json.dumps(
            {
                "result": "ok",
                "status": "accepted",
                "data": {
                    "tz": {
                        "keywordsAll": [
                            {
                                "word": "pump",
                                "sites": 4,
                                "density": 1.2,
                                "mine": 0,
                                "recommended": 2,
                            },
                            {"word": "valve", "sites": 2},
                        ],
                        "densityDeviation": [
                            {"word": "pump", "mine": 0, "median": 1.2, "delta": -1.2}
                        ],
                        "stopwords": ["and"],
                    }
                },
            }
        ),
    )
    assert result["author_tables"] == {
        "state": "complete",
        "reason": None,
        "words": [
            {
                "word": "pump",
                "sites": 4,
                "median_density": 1.2,
                "mine": 0,
                "recommended": 2,
                "unit": "provider_reported",
            }
        ],
        "density_deviation": [
            {
                "word": "pump",
                "mine": 0,
                "median_density": 1.2,
                "deviation": -1.2,
                "unit": "provider_reported",
            }
        ],
        "stopwords": ["and"],
        "filters": "provider_not_reported",
    }


def test_sources_doctor_reports_miratext_without_printing_the_key(monkeypatch, tmp_path):
    from seohead.data_sources import credentials
    from seohead.servers import handlers

    monkeypatch.setattr(credentials, "CONFIG_ROOT", tmp_path)
    monkeypatch.setenv("MIRATEXT_API_KEY", "synthetic-canary")
    source = handlers.sources_doctor()["sources"]["miratext"]
    assert source["ready"] and source["env"] == "MIRATEXT_API_KEY"
    assert "synthetic-canary" not in json.dumps(source)
