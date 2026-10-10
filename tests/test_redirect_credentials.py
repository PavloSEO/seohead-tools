"""A credential-bearing request must not be re-sent to another host on a 30x.

Uses two loopback servers on different hostnames (127.0.0.1 and localhost); no network.
"""

import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from seohead.data_sources.http import open_no_redirect


class _Recorder:
    def __init__(self):
        self.auth_seen = []


def _serve(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture
def cross_host(request):
    """Target server records Authorization headers; origin answers 302 to it on another host."""
    recorder = _Recorder()

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            recorder.auth_seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{target.server_port}/landed")
            self.end_headers()

        def log_message(self, *args):
            pass

    target = _serve(Target)
    origin = _serve(Origin)
    request.addfinalizer(target.shutdown)
    request.addfinalizer(origin.shutdown)
    return recorder, f"http://127.0.0.1:{origin.server_port}/start"


def test_shared_no_redirect_opener_refuses_cross_host_302(cross_host):
    """Mechanism: the shared opener turns the 302 into HTTPError, so no second request exists."""
    recorder, url = cross_host
    request = urllib.request.Request(url, headers={"Authorization": "Bearer secret-token"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        open_no_redirect(request, timeout=5)
    assert caught.value.code == 302
    assert recorder.auth_seen == []


def test_default_urllib_opener_would_forward_authorization(cross_host):
    """Control: the default opener follows the 302 and re-sends Authorization, which is the bug."""
    recorder, url = cross_host
    request = urllib.request.Request(url, headers={"Authorization": "Bearer secret-token"})
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
    assert recorder.auth_seen == ["Bearer secret-token"]
