"""Bounded image downloader with deterministic, filesystem-safe paths.

Downloads a list of image URLs to disk, deriving each target filename from the
URL and the response ``Content-Type``. Supports two folder layouts:

* ``flat``        — every file goes directly into ``output_dir``.
* ``domain-path`` — files are nested under ``output_dir/<host>/<url path>/``.

The public entry point is :func:`download_images`. Filename derivation lives in
the pure, side-effect-free helper :func:`target_path` so it can be unit-tested
without touching the network or the filesystem.

Only the Python standard library plus ``httpx`` are used. Errors are never
raised out of :func:`download_images`; each URL yields a result dict carrying an
``ok`` flag and, on failure, an ``error`` message.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from urllib.parse import urlsplit

from seohead.recon.net import http_client

try:  # httpx is the only third-party dependency; degrade gracefully if absent.
    import httpx
except Exception:  # pragma: no cover - exercised only without httpx installed
    httpx = None  # type: ignore[assignment]

# Some image hosts reject requests without a browser-compatible user agent.
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Character classes disallowed in a safe path segment: the Windows-reserved set
# plus control characters (0x00-0x1F).
_UNSAFE_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_WHITESPACE_RE = re.compile(r"\s+")
_UNDERSCORES_RE = re.compile(r"_+")


def safe_segment(value: object) -> str:
    """Sanitize an arbitrary value into a single safe filename/path segment.

    Replace unsafe characters with ``_``, collapse whitespace runs into ``-`` and underscore runs
    into a single ``_``,
    truncate to 160 characters, and fall back to ``"file"`` when empty.
    """
    text = str(value or "")
    text = _UNSAFE_CHARS_RE.sub("_", text)
    text = _WHITESPACE_RE.sub("-", text)
    text = _UNDERSCORES_RE.sub("_", text)
    text = text[:160]
    return text or "file"


def pick_extension_from_content_type(content_type: object) -> str:
    """Return a file extension (with leading dot) inferred from a MIME type.

    Returns an empty string when the type is unknown.
    """
    ct = str(content_type or "").lower()
    if "image/jpeg" in ct or "image/jpg" in ct:
        return ".jpg"
    if "image/png" in ct:
        return ".png"
    if "image/webp" in ct:
        return ".webp"
    if "image/gif" in ct:
        return ".gif"
    if "image/svg" in ct:
        return ".svg"
    if "image/avif" in ct:
        return ".avif"
    if "image/tiff" in ct:
        return ".tiff"
    return ""


def _parse_url(url: str) -> tuple[str, str, str] | None:
    """Parse an absolute HTTP-style URL with strict scheme and host requirements.

    Returns ``(hostname, path, origin)`` or ``None`` when the string is not a
    valid absolute URL (no scheme or no host).
    """
    try:
        parts = urlsplit(url)
    except Exception:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    origin = f"{parts.scheme}://{parts.netloc}"
    return parts.hostname, parts.path or "", origin


def infer_base_name(hostname: str, pathname: str) -> str:
    """Derive a base filename from a URL host and path.

    Uses the last path segment when present, otherwise ``<host>-image``.
    """
    raw_name = os.path.basename(pathname or "")
    no_query_name = raw_name.split("?")[0]
    if no_query_name:
        return no_query_name
    return f"{safe_segment(hostname)}-image"


def target_path(
    url: str,
    output_dir: str,
    structure: str,
    content_type: object,
) -> str:
    """Pure helper: compute the destination path for a downloaded image.

    Deterministic and side-effect-free for valid URLs — it does not touch the
    filesystem and does not deduplicate existing files (the download loop layers
    uniqueness on top).

    * ``structure == "flat"``  -> ``output_dir/<base_name>``
    * otherwise (``domain-path``) -> ``output_dir/<host>/<path dirs>/<base_name>``
    """
    parsed = _parse_url(url)
    if parsed is None:
        # Invalid URL: fall back to a timestamped binary name, as the TS does.
        return os.path.join(output_dir, f"image_{int(time.time() * 1000)}.bin")

    hostname, pathname, _origin = parsed

    base_name = infer_base_name(hostname, pathname)
    # A fallback name such as ``example.com-image`` contains a dot but no file extension.
    # Only a real final URL path segment may contribute an existing extension.
    ext = os.path.splitext(base_name)[1] if os.path.basename(pathname or "") else ""
    if not ext:
        from_type = pick_extension_from_content_type(content_type)
        ext = from_type or ".bin"
        base_name = f"{base_name}{ext}"
    base_name = safe_segment(base_name)

    if structure == "flat":
        return os.path.join(output_dir, base_name)

    clean_parts = [safe_segment(p) for p in pathname.split("/") if p][:-1]
    dir_parts = [output_dir, safe_segment(hostname), *clean_parts]
    return os.path.join(*dir_parts, base_name)


def _unique_path(file_path: str) -> str:
    """Return ``file_path`` or, if it exists, a ``name-N.ext`` variant that does not."""
    if not os.path.exists(file_path):
        return file_path
    directory = os.path.dirname(file_path)
    base, ext = os.path.splitext(os.path.basename(file_path))
    i = 1
    candidate = file_path
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base}-{i}{ext}")
        i += 1
    return candidate


def _download_one(
    client: httpx.Client,
    url: str,
    output_dir: str,
    structure: str,
    max_bytes: int | None,
    user_agent: str,
) -> dict:
    """Fetch a single URL and write it to disk. Returns a result dict."""
    parsed = _parse_url(url)
    referer = parsed[2] if parsed else None
    # Use the caller's resolved user_agent, not a hard-coded constant: a fixed value
    # here would win over options.user_agent on every request regardless of what the
    # caller configured (#228).
    headers = {"User-Agent": user_agent}
    if referer:
        headers["Referer"] = referer

    with client.stream("GET", url, headers=headers) as response:
        status = response.status_code
        if status != 200:
            # Drain so the connection can be reused, then report the failure.
            response.close()
            return {
                "url": url,
                "path": None,
                "bytes": None,
                "content_type": None,
                "status": status,
                "ok": False,
                "error": f"HTTP {status}",
            }

        final_url = str(response.url)
        content_type = response.headers.get("content-type", "")
        dest = _unique_path(target_path(final_url, output_dir, structure, content_type))
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

        size = 0
        try:
            with open(dest, "wb") as fh:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise ValueError(f"Size limit exceeded ({max_bytes} bytes)")
                    fh.write(chunk)
        except Exception:
            # Clean up a partial file so it can't be mistaken for a good one.
            with contextlib.suppress(OSError):
                os.remove(dest)
            raise

    return {
        "url": url,
        "path": dest,
        "bytes": size,
        "content_type": content_type,
        "status": status,
        "ok": True,
    }


def _manifest_path(output_dir: str) -> str:
    return os.path.join(output_dir, "manifest.json")


def _load_manifest(output_dir: str) -> dict[str, dict[str, object]]:
    """Read a prior run's manifest, keyed by URL, dropping entries whose file is gone.

    A stale entry -- naming a file that no longer exists -- would make skip_existing
    falsely report a URL as already downloaded, and would carry a dangling reference
    forward into the next manifest write.
    """
    try:
        with open(_manifest_path(output_dir), encoding="utf-8") as handle:
            raw = json.loads(handle.read())
    except (OSError, ValueError):
        return {}
    entries = raw.get("images") if isinstance(raw, dict) else raw
    live: dict[str, dict[str, object]] = {}
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        rel_path = entry.get("path")
        if not url or not rel_path:
            continue
        if os.path.isfile(os.path.join(output_dir, str(rel_path))):
            live[str(url)] = {"path": str(rel_path), "bytes": entry.get("bytes")}
    return live


def _write_manifest(output_dir: str, entries: dict[str, dict[str, object]]) -> None:
    """Write the manifest ``log-scan --images-dir`` needs to map a file back to its URL.

    Without this, log-scan has no way to tell which URL a file on disk came from and
    silently compares nothing, no matter how wrong a crawl's recorded size is (#226).
    """
    images = [
        {"url": url, "path": data["path"], "bytes": data.get("bytes")}
        for url, data in sorted(entries.items())
    ]
    with open(_manifest_path(output_dir), "w", encoding="utf-8") as handle:
        json.dump({"images": images}, handle, indent=2)
        handle.write("\n")


def download_images(
    urls: list[str],
    output_dir: str,
    options: dict | None = None,
) -> list[dict]:
    """Download a list of image URLs into ``output_dir``.

    Options (all optional):

    * ``structure``     -- ``"flat"`` or ``"domain-path"`` (default).
    * ``skip_existing`` -- skip URLs already recorded in ``manifest.json``, or whose
      preview path already exists for a URL never recorded there (default True).
    * ``timeout``       -- per-request timeout in seconds (default 30).
    * ``max_bytes``     -- abort a download that grows past this size.
    * ``retries``       -- extra attempts per URL on failure (default 1).
    * ``user_agent``    -- override the default browser User-Agent.

    Every successful download (fresh or skipped) is recorded in
    ``output_dir/manifest.json``, mapping each URL to the file it produced, so a
    later ``log-scan --images-dir`` run can compare a crawl's claims against reality.

    Returns one result dict per input URL, each shaped like
    ``{url, path, bytes, content_type, status, ok, error?, skipped?}``. This
    function never raises: transport and I/O errors are captured per URL.
    """
    opts = options or {}
    structure = opts.get("structure", "domain-path")
    skip_existing = opts.get("skip_existing", True)
    timeout = opts.get("timeout", 30)
    max_bytes = opts.get("max_bytes")
    retries = max(0, int(opts.get("retries", 1) or 0))
    user_agent = opts.get("user_agent") or DEFAULT_USER_AGENT

    results: list[dict] = []

    if httpx is None:
        for url in urls:
            results.append(
                {
                    "url": url,
                    "path": None,
                    "bytes": None,
                    "content_type": None,
                    "status": None,
                    "ok": False,
                    "error": "httpx is unavailable",
                }
            )
        return results

    default_headers = {"User-Agent": user_agent}
    try:
        client, _http2_capable = http_client(
            timeout,
            follow_redirects=True,
            headers=default_headers,
        )
    except Exception as exc:  # pragma: no cover - defensive
        for url in urls:
            results.append(
                {
                    "url": url,
                    "path": None,
                    "bytes": None,
                    "content_type": None,
                    "status": None,
                    "ok": False,
                    "error": str(exc),
                }
            )
        return results

    manifest_entries = _load_manifest(output_dir)

    try:
        for url in urls:
            try:
                # The manifest, not a guessed filename, is authoritative once a URL has
                # been downloaded: for a URL with no extension, guessing a preview name
                # before the response is fetched cannot know the real Content-Type and
                # would miss the file the previous run actually wrote (#227).
                recorded = manifest_entries.get(url) if skip_existing else None
                recorded_path = (
                    os.path.join(output_dir, str(recorded["path"])) if recorded else None
                )
                if recorded_path and os.path.isfile(recorded_path):
                    results.append(
                        {
                            "url": url,
                            "path": recorded_path,
                            "bytes": os.path.getsize(recorded_path),
                            "content_type": None,
                            "status": None,
                            "ok": True,
                            "skipped": True,
                        }
                    )
                    continue

                # Fallback for a URL the manifest has never seen: a real extension in
                # the URL path makes this guess exact, and it also covers files placed
                # in output_dir by something other than this function.
                preview = target_path(url, output_dir, structure, "")
                if skip_existing and os.path.exists(preview):
                    manifest_entries[url] = {
                        "path": os.path.relpath(preview, output_dir),
                        "bytes": os.path.getsize(preview),
                    }
                    results.append(
                        {
                            "url": url,
                            "path": preview,
                            "bytes": os.path.getsize(preview),
                            "content_type": None,
                            "status": None,
                            "ok": True,
                            "skipped": True,
                        }
                    )
                    continue

                last_error: Exception | None = None
                item: dict | None = None
                for _ in range(retries + 1):
                    try:
                        item = _download_one(
                            client, url, output_dir, structure, max_bytes, user_agent
                        )
                        # A non-200 HTTP result is still a definitive answer;
                        # keep it rather than retrying blindly.
                        break
                    except Exception as exc:  # network / I/O / size-limit
                        last_error = exc
                        item = None

                if item is None:
                    results.append(
                        {
                            "url": url,
                            "path": None,
                            "bytes": None,
                            "content_type": None,
                            "status": None,
                            "ok": False,
                            "error": str(last_error) if last_error else "Failed to download file",
                        }
                    )
                else:
                    results.append(item)
                    # Only a genuinely finished file earns a manifest entry: a failed or
                    # non-200 item carries path=None, so this can never point at a partial
                    # download.
                    if item.get("ok") and item.get("path"):
                        manifest_entries[url] = {
                            "path": os.path.relpath(item["path"], output_dir),
                            "bytes": item.get("bytes"),
                        }
            except Exception as exc:  # pragma: no cover - last-resort guard
                results.append(
                    {
                        "url": url,
                        "path": None,
                        "bytes": None,
                        "content_type": None,
                        "status": None,
                        "ok": False,
                        "error": str(exc),
                    }
                )
    finally:
        client.close()

    if manifest_entries:
        with contextlib.suppress(OSError):
            _write_manifest(output_dir, manifest_entries)

    return results


if __name__ == "__main__":
    # Offline smoke test: exercise the pure helpers only (no network).
    assert safe_segment("a b/c") == "a-b_c"
    assert safe_segment("") == "file"
    assert safe_segment("  ") == "-"  # whitespace run collapses to a single dash
    assert pick_extension_from_content_type("image/jpeg") == ".jpg"
    assert pick_extension_from_content_type("image/svg+xml") == ".svg"
    assert pick_extension_from_content_type("text/html") == ""

    flat = target_path("https://example.com/pics/photo.png", "/out", "flat", "image/png")
    assert flat == os.path.join("/out", "photo.png"), flat

    nested = target_path("https://example.com/a/b/photo", "/out", "domain-path", "image/webp")
    assert nested == os.path.join("/out", "example.com", "a", "b", "photo.webp"), nested

    no_ext_no_type = target_path("https://example.com/img/pic", "/out", "flat", "")
    assert no_ext_no_type == os.path.join("/out", "pic.bin"), no_ext_no_type

    # A host-only URL still receives an extension inferred from Content-Type.
    root = target_path("https://example.com/", "/out", "domain-path", "image/gif")
    assert root == os.path.join("/out", "example.com", "example.com-image.gif"), root

    print("downloader.py self-check OK")
