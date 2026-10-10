"""Fixed-provider HTTP helpers that never forward credentials across redirects."""

from __future__ import annotations

import ssl
import urllib.request


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirects())


def open_no_redirect(
    request: urllib.request.Request, *, timeout: float, context: ssl.SSLContext | None = None
):
    """Open one fixed HTTPS request while turning every redirect into an HTTP error.

    urllib copies the Authorization header to a redirect target, so credential-bearing
    requests must never follow one: a 3xx surfaces as HTTPError and nothing is re-sent.
    """
    opener = _OPENER
    if context is not None:
        opener = urllib.request.build_opener(
            _RefuseRedirects(), urllib.request.HTTPSHandler(context=context)
        )
    return opener.open(request, timeout=timeout)
