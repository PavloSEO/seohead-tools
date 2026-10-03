"""Explicit local launch or operator-supplied Playwright browser connection."""

from __future__ import annotations

import ipaddress
import os
import re
from contextlib import suppress
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import urlsplit

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_VERSION = re.compile(r"(\d+)\.(\d+)\.\d+(?:[a-zA-Z0-9.+-]*)?\Z")
DEFAULTS = {
    "transport": "local",
    "remote_protocol": "playwright",
    "remote_endpoint_env": "",
    "remote_playwright_version": "",
}


class BrowserTransportError(RuntimeError):
    """Safe, actionable failure that never includes an endpoint or token."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def validate_config(config: dict[str, Any], *, embedded: bool = False) -> None:
    """Validate the recorded selection without resolving an endpoint secret."""
    if not isinstance(config, dict) or (not embedded and set(config) - set(DEFAULTS)):
        raise ValueError("browser transport config has unsupported fields")
    kind = config.get("transport", "local")
    protocol = config.get("remote_protocol", "playwright")
    name = config.get("remote_endpoint_env", "")
    expected = config.get("remote_playwright_version", "")
    if type(kind) is not str or kind not in {"local", "remote"}:
        raise ValueError("browser transport must be local or remote")
    if protocol != "playwright":
        raise ValueError("remote browser protocol must be playwright; CDP is unsupported")
    if type(name) is not str or type(expected) is not str:
        raise ValueError("remote browser endpoint env and version must be strings")
    if kind == "local":
        if name or expected:
            raise ValueError("remote browser settings require transport=remote")
        return
    if not _ENV_NAME.fullmatch(name):
        raise ValueError("remote browser requires a valid remote_endpoint_env variable name")
    if not _VERSION.fullmatch(expected):
        raise ValueError("remote browser requires a full remote_playwright_version")


def _local_version() -> str:
    try:
        return version("playwright")
    except PackageNotFoundError:
        raise BrowserTransportError(
            "playwright_unavailable",
            "Install the optional Playwright package before remote rendering",
        ) from None


def prepare(
    config: dict[str, Any] | None, *, embedded: bool = False
) -> tuple[str | None, dict[str, str]]:
    """Resolve an opt-in endpoint before any origin fetch or browser launch."""
    if config is not None and not isinstance(config, dict):
        raise BrowserTransportError(
            "invalid_browser_transport", "Browser transport config must be an object"
        )
    selected = {**DEFAULTS, **(config or {})}
    try:
        validate_config(selected, embedded=embedded)
    except ValueError as exc:
        raise BrowserTransportError("invalid_browser_transport", str(exc)) from exc
    if selected["transport"] == "local":
        return None, {"mode": "local"}
    local_version = _local_version()
    local_match = _VERSION.fullmatch(local_version)
    remote_match = _VERSION.fullmatch(selected["remote_playwright_version"])
    if (
        local_match is None
        or remote_match is None
        or local_match.groups()[:2] != remote_match.groups()[:2]
    ):
        raise BrowserTransportError(
            "remote_version_incompatible",
            "Remote Playwright major/minor version must match the installed client version",
        )
    endpoint = os.environ.get(selected["remote_endpoint_env"], "")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise BrowserTransportError(
            "invalid_remote_endpoint", "Remote Playwright endpoint is not a valid WebSocket URL"
        ) from None
    if (
        parsed.scheme not in {"ws", "wss"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port == 0
    ):
        raise BrowserTransportError(
            "invalid_remote_endpoint",
            "Remote Playwright endpoint must be a WebSocket URL without userinfo or fragment",
        )
    if parsed.scheme == "ws":
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname == "localhost"
        if not loopback:
            raise BrowserTransportError(
                "insecure_remote_endpoint", "Use wss for a non-loopback Playwright endpoint"
            )
    return endpoint, {
        "mode": "remote",
        "protocol": "playwright",
        "client_version": local_version,
        "expected_remote_version": selected["remote_playwright_version"],
    }


def open_browser(
    browser_type: Any,
    endpoint: str | None,
    *,
    timeout_seconds: float,
    local_launch_options: dict[str, Any] | None = None,
) -> Any:
    """Connect or launch once; a remote failure never launches a local browser."""
    if endpoint is None:
        return browser_type.launch(**(local_launch_options or {}))
    connect = getattr(browser_type, "connect", None)
    if not callable(connect):
        raise BrowserTransportError(
            "remote_protocol_unsupported", "This Playwright installation has no BrowserType.connect"
        )
    try:
        browser = connect(endpoint, timeout=int(timeout_seconds * 1000))
    except Exception:
        # Playwright exceptions may echo a token-bearing endpoint: never return them.
        raise BrowserTransportError(
            "remote_connection_failed",
            "Remote Playwright connection failed; check endpoint reachability and matching server version",
        ) from None
    if not callable(getattr(browser, "new_context", None)) or not callable(
        getattr(browser, "close", None)
    ):
        close = getattr(browser, "close", None)
        if callable(close):
            close()
        raise BrowserTransportError(
            "remote_protocol_unsupported",
            "Remote Playwright browser lacks required context lifecycle",
        )
    return browser


def open_context(browser: Any, endpoint: str | None, options: dict[str, Any]) -> Any:
    """Require the route controls before a remote page can issue requests."""
    try:
        context = browser.new_context(**options)
    except Exception:
        with suppress(Exception):
            browser.close()
        if endpoint is None:
            raise
        raise BrowserTransportError(
            "remote_context_unsupported",
            "Remote Playwright browser cannot create the requested isolated context",
        ) from None
    required = ("route", "route_web_socket", "new_page", "close")
    if any(not callable(getattr(context, name, None)) for name in required):
        close = getattr(context, "close", None)
        if callable(close):
            with suppress(Exception):
                close()
        with suppress(Exception):
            browser.close()
        raise BrowserTransportError(
            "remote_capability_unsupported",
            "Remote Playwright context lacks required HTTP/WebSocket route controls",
        )
    return context
