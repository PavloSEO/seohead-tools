import io
import json

import pytest

from seohead.data_sources import topvisor
from seohead.servers import handlers


def test_read_projects_uses_fixed_get_endpoint_and_central_credentials(monkeypatch):
    monkeypatch.setattr(
        topvisor, "read", lambda path, env: "123" if "user_id" in path else "test-secret"
    )
    captured = {}

    class Opener:
        def open(self, request, timeout):
            captured.update(
                url=request.full_url,
                headers=dict(request.header_items()),
                body=json.loads(request.data),
                timeout=timeout,
            )
            return io.BytesIO(b'{"result":[{"id":1,"url":"example.com"}]}')

    monkeypatch.setattr(topvisor.urllib.request, "build_opener", lambda *args: Opener())
    result = topvisor.fetch()
    assert result["ok"] and result["page_only"]
    assert captured["url"] == "https://api.topvisor.com/v2/json/get/projects_2/projects"
    assert captured["headers"]["User-id"] == "123"
    assert captured["body"] == {"limit": 100, "offset": 0}


@pytest.mark.parametrize(
    "operation,params",
    [
        ("edit/checker/go", {}),
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
    monkeypatch.setattr(
        topvisor, "read", lambda path, env: "123" if "user_id" in path else "secret"
    )

    class Opener:
        def open(self, *args, **kwargs):
            return io.BytesIO(b'{"errors":[{"message":"secret"}]}')

    monkeypatch.setattr(topvisor.urllib.request, "build_opener", lambda *args: Opener())
    result = handlers.topvisor_read()
    assert not result["ok"] and "secret" not in result["error"]


def test_redirect_does_not_forward_credentials():
    with pytest.raises(topvisor.TopvisorError, match="redirect refused"):
        topvisor._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")
