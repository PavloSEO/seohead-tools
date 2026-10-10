"""open_no_redirect must turn every redirect into an HTTP error without following it.

Other tests stub open_no_redirect out, so the real _RefuseRedirects handler never runs there.
This test serves a real 302 from a local server and checks that the redirect target is not requested.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from seohead.data_sources.http import open_no_redirect


class _Handler(BaseHTTPRequestHandler):
    hits: ClassVar[list[str]] = []

    def do_GET(self):
        type(self).hits.append(self.path)
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/target")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"target reached")

    def log_message(self, *_args):  # Keep pytest output clean.
        pass


@pytest.fixture()
def server():
    _Handler.hits = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_redirect_is_raised_as_http_error_and_not_followed(server):
    request = urllib.request.Request(f"{server}/start", headers={"Authorization": "Bearer t"})

    with pytest.raises(urllib.error.HTTPError) as exc:
        open_no_redirect(request, timeout=5)

    assert exc.value.code == 302
    assert exc.value.headers["Location"] == "/target"
    assert _Handler.hits == ["/start"]


def test_plain_response_still_opens(server):
    with open_no_redirect(urllib.request.Request(f"{server}/target"), timeout=5) as response:
        assert response.read() == b"target reached"
    assert _Handler.hits == ["/target"]
