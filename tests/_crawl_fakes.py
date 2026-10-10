"""Shared fakes for crawl tests: a synthetic site, responses and a fetcher. No network."""

ROBOTS_OK = "User-agent: *\nDisallow: /private/\n"


class FakeResponse:
    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}


def page(*links: str, title: str = "t") -> FakeResponse:
    """An HTML response linking to each href, in document order."""
    body = "".join(f'<a href="{href}">{href}</a>' for href in links)
    return FakeResponse(
        f"<html><head><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>"
    )


SITE = {
    "https://example.com/robots.txt": FakeResponse(
        ROBOTS_OK, headers={"content-type": "text/plain"}
    ),
    "https://example.com/": page("/a", "/b", "https://other.com/x"),
    "https://example.com/a": page("/c"),
    "https://example.com/b": page("/c"),
    "https://example.com/c": page(),
    "https://example.com/private/secret": page(),
}


def _fetcher(mapping):
    def fetch(url):
        value = mapping.get(url)
        if value is None:
            return FakeResponse("", status_code=404)
        if isinstance(value, Exception):
            raise value
        return value

    return fetch
