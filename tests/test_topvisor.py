import io
import json
import urllib.error

import pytest

from seohead.data_sources import topvisor
from seohead.mcp import handlers


def _credentials(monkeypatch, token="test-secret", user_id="123"):
    monkeypatch.setattr(
        topvisor,
        "read",
        lambda path, env: user_id if "user_id" in path else token,
    )


def _opener(payload, captured=None):
    """Mocked transport answering one canned JSON envelope per open()."""

    class Opener:
        def open(self, request, timeout):
            if captured is not None:
                captured.update(
                    url=request.full_url,
                    headers=dict(request.header_items()),
                    body=json.loads(request.data),
                    timeout=timeout,
                )
            return io.BytesIO(json.dumps(payload).encode())

    return Opener()


def _stub_transport(monkeypatch, payload, captured=None):
    calls = []
    opener = _opener(payload, captured)

    class CountingOpener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return opener.open(request, timeout)

    monkeypatch.setattr(topvisor.urllib.request, "build_opener", lambda *args: CountingOpener())
    return calls


def test_read_projects_uses_fixed_get_endpoint_and_central_credentials(monkeypatch):
    _credentials(monkeypatch)
    captured = {}
    calls = _stub_transport(monkeypatch, {"result": [{"id": 1, "url": "example.com"}]}, captured)
    result = topvisor.fetch()
    assert result["ok"] and result["page_only"]
    assert calls == ["https://api.topvisor.com/v2/json/get/projects_2/projects"]
    assert captured["url"] == calls[0]
    assert captured["headers"]["User-id"] == "123"
    assert captured["body"] == {"limit": 100, "offset": 0}


@pytest.mark.parametrize(
    "operation,params",
    [
        ("edit/checker/go", {}),
        ("positions_2/checker/go", {"project_id": 1}),
        ("add", {"project_id": 1}),
        ("del", {"project_id": 1}),
        ("projects", {"limit": 0}),
        ("projects", {"limit": True}),
        ("projects", {"limit": 1001}),
        ("projects", {"offset": -1}),
        ("history", {}),
    ],
)
def test_rejects_mutations_and_unbounded_inputs_before_auth(operation, params, monkeypatch):
    monkeypatch.setattr(topvisor, "read", lambda *a: pytest.fail("must validate before auth"))
    assert not handlers.topvisor_read(operation, params)["ok"]


def test_api_error_never_echoes_secret(monkeypatch):
    _credentials(monkeypatch, token="secret")
    _stub_transport(monkeypatch, {"errors": [{"message": "secret"}]})
    result = handlers.topvisor_read()
    assert not result["ok"] and "secret" not in result["error"]


def test_redirect_does_not_forward_credentials():
    with pytest.raises(topvisor.TopvisorError, match="redirect refused"):
        topvisor._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")


def test_nonfinal_page_preserves_provider_paging_metadata(monkeypatch):
    """A nonfinal page keeps nextOffset/total/limitedBy as provider metadata,
    distinct from the echoed requested limit/offset."""
    _credentials(monkeypatch)
    _stub_transport(
        monkeypatch,
        {
            "result": [{"id": 11, "name": "Example", "url": "example.com"}],
            "nextOffset": 1,
            "total": 2,
            "limitedBy": 1,
        },
    )
    page = handlers.topvisor_read("projects", {"limit": 1, "offset": 0})
    assert page["ok"]
    assert page["limit"] == 1 and page["offset"] == 0  # the request, not provider state
    assert page["nextOffset"] == 1
    assert page["total"] == 2
    assert page["limitedBy"] == 1
    assert page["result"] == [{"id": 11, "name": "Example", "url": "example.com"}]


def test_final_page_omits_next_offset_and_empty_page_is_valid(monkeypatch):
    """The final page carries total without nextOffset; an empty result list is
    a valid zero-row page, not an error and not an invented continuation."""
    _credentials(monkeypatch)
    _stub_transport(monkeypatch, {"result": [], "total": 0})
    page = handlers.topvisor_read("projects", {"limit": 100})
    assert page["ok"] and page["result"] == [] and page["total"] == 0
    assert "nextOffset" not in page

    _stub_transport(monkeypatch, {"result": [{"id": 9}], "total": 1})
    page = handlers.topvisor_read("projects", {"limit": 100, "offset": 0})
    assert page["ok"] and page["total"] == 1 and "nextOffset" not in page


def test_groups_pages_follow_provider_next_offset(monkeypatch):
    """A caller can follow nextOffset across pages and collect distinct IDs."""
    _credentials(monkeypatch)
    pages = iter(
        [
            {"result": [{"id": 5}], "nextOffset": 1, "total": 2},
            {"result": [{"id": 6}], "total": 2},
        ]
    )
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps(next(pages)).encode())

    monkeypatch.setattr(topvisor.urllib.request, "build_opener", lambda *args: Opener())
    first = topvisor.fetch("groups", {"project_id": 7, "limit": 1, "offset": 0})
    second = topvisor.fetch("groups", {"project_id": 7, "limit": 1, "offset": first["nextOffset"]})
    assert requests[0]["offset"] == 0 and requests[1]["offset"] == 1
    ids = [row["id"] for row in first["result"] + second["result"]]
    assert ids == [5, 6] and len(set(ids)) == 2
    assert "nextOffset" not in second


def test_keywords_page_exposes_continuation_signal(monkeypatch):
    _credentials(monkeypatch)
    calls = _stub_transport(
        monkeypatch,
        {"result": [{"id": 101, "name": "fixture query"}], "nextOffset": 1, "total": 2},
    )
    page = handlers.topvisor_read("keywords", {"project_id": 7, "limit": 1})
    assert page["ok"] and page["nextOffset"] == 1 and page["total"] == 2
    assert calls == ["https://api.topvisor.com/v2/json/get/keywords_2/keywords"]


def test_competitors_result_is_an_array(monkeypatch):
    _credentials(monkeypatch)
    _stub_transport(monkeypatch, {"result": [{"id": 3, "url": "rival.example"}]})
    page = handlers.topvisor_read("competitors", {"project_id": 7})
    assert page["ok"] and page["result"] == [{"id": 3, "url": "rival.example"}]


def test_history_keeps_object_shape_and_sibling_metadata(monkeypatch):
    """History rows live at result.keywords; headers/existsDates/visitors/
    topsByDepth are siblings inside the result object, while nextOffset/total
    stay top-level provider metadata."""
    _credentials(monkeypatch)
    payload = {
        "result": {
            "keywords": [{"id": 101, "positionsData": {"2026-09-20:11:3": {"position": 7}}}],
            "headers": {"dates": ["2026-09-20"]},
            "existsDates": ["2026-09-19", "2026-09-20"],
        },
        "nextOffset": 1,
        "total": 2,
    }
    _stub_transport(monkeypatch, payload)
    page = handlers.topvisor_read(
        "history",
        {
            "project_id": 11,
            "regions_indexes": [3],
            "date1": "2026-09-01",
            "date2": "2026-09-21",
        },
    )
    assert page["ok"] and page["result"] == payload["result"]
    assert page["result"]["keywords"][0]["id"] == 101
    assert page["result"]["headers"]["dates"] == ["2026-09-20"]
    assert page["nextOffset"] == 1 and page["total"] == 2


def test_history_preserves_unavailable_and_missing_positions_verbatim(monkeypatch):
    """Three distinct states must survive untouched: an integer rank, the
    provider's "--" unavailable marker, and a requested date with no entry."""
    _credentials(monkeypatch)
    payload = {
        "result": {
            "keywords": [
                {
                    "id": 101,
                    "positionsData": {
                        "2026-09-20:11:3": {"position": 7},
                        "2026-09-21:11:3": {"position": "--"},
                    },
                }
            ],
            "headers": {"dates": ["2026-09-20", "2026-09-21", "2026-09-22"]},
        },
        "total": 1,
    }
    _stub_transport(monkeypatch, payload)
    page = handlers.topvisor_read(
        "history",
        {
            "project_id": 11,
            "regions_indexes": [3],
            "dates": ["2026-09-20", "2026-09-21", "2026-09-22"],
        },
    )
    positions = page["result"]["keywords"][0]["positionsData"]
    assert positions["2026-09-20:11:3"]["position"] == 7
    assert positions["2026-09-21:11:3"]["position"] == "--"
    assert "2026-09-22:11:3" not in positions  # missing observation, not rank 0


def test_empty_history_page_is_valid(monkeypatch):
    _credentials(monkeypatch)
    _stub_transport(
        monkeypatch,
        {"result": {"keywords": [], "headers": {"dates": []}}, "total": 0},
    )
    page = handlers.topvisor_read(
        "history", {"project_id": 11, "regions_indexes": [3], "dates": ["2026-09-20"]}
    )
    assert page["ok"] and page["result"]["keywords"] == [] and page["total"] == 0


def test_summary_result_is_an_object_not_a_row_page(monkeypatch):
    """Summary covers the two requested dates; its object result is not a page
    of rows and claims no continuation without provider nextOffset."""
    _credentials(monkeypatch)
    payload = {
        "result": {
            "dates": ["2026-09-01", "2026-09-21"],
            "dynamics": {"moved_up": 4, "moved_down": 1},
            "tops": {"1_3": 5},
            "avgs": {"avg": 12},
            "visibilities": {"visibility": 0.4},
        }
    }
    _stub_transport(monkeypatch, payload)
    page = handlers.topvisor_read("summary", {"project_id": 11, "region_index": 3})
    assert page["ok"] and page["result"] == payload["result"]
    assert "nextOffset" not in page


@pytest.mark.parametrize("payload", [{"result": None, "errors": []}, {"result": None}])
def test_null_result_is_not_a_clean_page(monkeypatch, payload):
    """result:null is unavailable or malformed even when errors is empty or absent."""
    _credentials(monkeypatch)
    _stub_transport(monkeypatch, payload)
    result = handlers.topvisor_read("projects")
    assert result["ok"] is False and result["error"]


@pytest.mark.parametrize(
    "operation,params",
    [
        ("projects", {}),
        ("competitors", {"project_id": 7}),
        ("groups", {"project_id": 7}),
        ("keywords", {"project_id": 7}),
    ],
)
def test_list_operations_reject_non_array_results(monkeypatch, operation, params):
    _credentials(monkeypatch)
    for bad in ("unexpected", {"id": 1}, 5):
        _stub_transport(monkeypatch, {"result": bad})
        assert not handlers.topvisor_read(operation, params)["ok"], (operation, bad)


@pytest.mark.parametrize("operation", ["history", "summary"])
def test_object_operations_reject_non_object_results(monkeypatch, operation):
    _credentials(monkeypatch)
    for bad in ("unexpected", [{"id": 1}], 5):
        _stub_transport(monkeypatch, {"result": bad})
        assert not handlers.topvisor_read(operation, {"project_id": 7})["ok"], (operation, bad)


def test_permission_denied_envelope_is_safe_error(monkeypatch):
    _credentials(monkeypatch)
    _stub_transport(
        monkeypatch,
        {"result": None, "errors": [{"code": 403, "string": "Access denied"}]},
    )
    result = handlers.topvisor_read("projects")
    assert result["ok"] is False
    assert "403" in result["error"] or "denied" in result["error"].lower()
    assert "test-secret" not in result["error"] and "123" not in result["error"].split("403")[0]


@pytest.mark.parametrize("code", [401, 403])
def test_http_auth_failures_are_not_followed_or_echoed(monkeypatch, code):
    _credentials(monkeypatch)

    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, code, "denied", {}, None)

    monkeypatch.setattr(topvisor.urllib.request, "build_opener", lambda *args: Opener())
    result = handlers.topvisor_read("projects")
    assert result["ok"] is False and str(code) in result["error"]
    assert "test-secret" not in result["error"]
