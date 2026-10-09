"""Public-release safety boundaries for URL and file tools."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
from PIL import Image

from seohead.checks import optimizer
from seohead.recon import net
from seohead.sf.core.auth_proxy import AuthProxy


def _image(
    path: Path,
    *,
    size: tuple[int, int] = (96, 64),
    color: str = "#1565c0",
) -> None:
    Image.new("RGB", size, color).save(path)


def test_private_networks_are_blocked_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(net.PRIVATE_NETWORK_ENV, raising=False)
    with pytest.raises(ValueError, match="private"):
        net.validate_url("http://127.0.0.1/admin")
    with pytest.raises(ValueError, match="private"):
        net.validate_url("http://169.254.169.254/latest/meta-data")


def test_private_networks_require_explicit_opt_in(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(net.PRIVATE_NETWORK_ENV, "1")
    assert net.validate_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000/"


def test_socket_resolution_requires_explicit_private_opt_in(monkeypatch: pytest.MonkeyPatch):
    records = [(net.socket.AF_INET, net.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(net.socket, "getaddrinfo", lambda *_args, **_kwargs: records)
    monkeypatch.delenv(net.PRIVATE_NETWORK_ENV, raising=False)
    with pytest.raises(ValueError, match="private or non-public"):
        net.resolve_socket_addresses("internal.example", 443)

    monkeypatch.setenv(net.PRIVATE_NETWORK_ENV, "1")
    assert net.resolve_socket_addresses("internal.example", 443) == [
        (net.socket.AF_INET, net.socket.SOCK_STREAM, 6, ("127.0.0.1", 443))
    ]


def test_resolve_socket_addresses_honours_the_named_host_allowlist(
    monkeypatch: pytest.MonkeyPatch,
):
    """The scoped opt-in works at the resolver, not only at validate_url."""
    records = [(net.socket.AF_INET, net.socket.SOCK_STREAM, 6, "", ("10.0.0.9", 443))]
    monkeypatch.setattr(net.socket, "getaddrinfo", lambda *_args, **_kwargs: records)
    monkeypatch.delenv(net.PRIVATE_NETWORK_ENV, raising=False)
    monkeypatch.delenv(net.PRIVATE_HOST_ALLOWLIST_ENV, raising=False)
    with pytest.raises(ValueError, match="private or non-public"):
        net.resolve_socket_addresses("staging.internal", 443)

    monkeypatch.setenv(net.PRIVATE_HOST_ALLOWLIST_ENV, "staging.internal")
    assert net.resolve_socket_addresses("staging.internal", 443) == [
        (net.socket.AF_INET, net.socket.SOCK_STREAM, 6, ("10.0.0.9", 443))
    ]
    # The obvious bypass: a *different* host must still be blocked, even
    # though it resolves to the exact same private address.
    with pytest.raises(ValueError, match="private or non-public"):
        net.resolve_socket_addresses("other-internal.example", 443)


def _private_records(address: str = "10.0.0.9") -> list:
    return [(net.socket.AF_INET, net.socket.SOCK_STREAM, 6, "", (address, 443))]


def test_allowed_private_hosts_permits_only_the_named_host(monkeypatch: pytest.MonkeyPatch):
    """The scoped opt-in authorizes one staging host, not every private range."""
    monkeypatch.delenv(net.PRIVATE_NETWORK_ENV, raising=False)
    monkeypatch.setenv(net.PRIVATE_HOST_ALLOWLIST_ENV, "staging.internal")
    monkeypatch.setattr(net.socket, "getaddrinfo", lambda *_a, **_k: _private_records())

    assert net.validate_url("https://staging.internal/") == "https://staging.internal/"


def test_allowed_private_hosts_does_not_permit_a_different_private_host(
    monkeypatch: pytest.MonkeyPatch,
):
    """The obvious bypass: try a second private host under the same opt-in."""
    monkeypatch.delenv(net.PRIVATE_NETWORK_ENV, raising=False)
    monkeypatch.setenv(net.PRIVATE_HOST_ALLOWLIST_ENV, "staging.internal")
    monkeypatch.setattr(net.socket, "getaddrinfo", lambda *_a, **_k: _private_records())

    with pytest.raises(ValueError, match="private"):
        net.validate_url("https://other-internal.example/")


def test_allowed_private_hosts_matching_is_exact_not_a_suffix(monkeypatch: pytest.MonkeyPatch):
    """A subdomain of the allowed host must not inherit the authorization."""
    monkeypatch.delenv(net.PRIVATE_NETWORK_ENV, raising=False)
    monkeypatch.setenv(net.PRIVATE_HOST_ALLOWLIST_ENV, "staging.internal")
    monkeypatch.setattr(net.socket, "getaddrinfo", lambda *_a, **_k: _private_records())

    with pytest.raises(ValueError, match="private"):
        net.validate_url("https://evil.staging.internal/")


def test_allowed_private_hosts_does_not_survive_a_redirect_to_a_different_private_host(
    monkeypatch: pytest.MonkeyPatch,
):
    """An allowlisted host redirecting elsewhere must not widen the opt-in."""
    monkeypatch.delenv(net.PRIVATE_NETWORK_ENV, raising=False)
    monkeypatch.setenv(net.PRIVATE_HOST_ALLOWLIST_ENV, "staging.internal")
    monkeypatch.setattr(net.socket, "getaddrinfo", lambda *_a, **_k: _private_records())

    class Request:
        url = "https://staging.internal/start"

    class Response:
        is_redirect = True
        # A real redirect response always carries one, and the guard now reports
        # it so a caller can tell a refused hop from a transport failure (#175).
        status_code = 302
        request = Request()
        headers: ClassVar[dict[str, str]] = {"location": "http://admin.internal/panel"}

    with pytest.raises(ValueError, match="private"):
        net.network_event_hooks()["response"][0](Response())


def test_allowed_private_hosts_is_empty_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(net.PRIVATE_HOST_ALLOWLIST_ENV, raising=False)
    assert net.allowed_private_hosts() == frozenset()


def test_url_credentials_are_always_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(net.PRIVATE_NETWORK_ENV, "1")
    with pytest.raises(ValueError, match="credentials"):
        net.validate_url("https://user:password@example.com/")


def test_redirect_hook_blocks_a_private_destination(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(net.PRIVATE_NETWORK_ENV, raising=False)

    class Request:
        url = "https://93.184.216.34/start"

    class Response:
        is_redirect = True
        # A real redirect response always carries one, and the guard now reports
        # it so a caller can tell a refused hop from a transport failure (#175).
        status_code = 302
        request = Request()
        headers: ClassVar[dict[str, str]] = {"location": "http://127.0.0.1/admin"}

    with pytest.raises(ValueError, match="private"):
        net.network_event_hooks()["response"][0](Response())


def test_basic_auth_proxy_validates_its_target(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(net.PRIVATE_NETWORK_ENV, raising=False)
    with pytest.raises(ValueError, match="private"):
        AuthProxy("http://127.0.0.1:8080/", "user", "password")
    with pytest.raises(ValueError, match="http"):
        AuthProxy("file:///etc/passwd", "user", "password")

    monkeypatch.setenv(net.PRIVATE_NETWORK_ENV, "1")
    proxy = AuthProxy("http://127.0.0.1:8080/", "user", "password")
    assert proxy.origin == "http://127.0.0.1:8080"


def test_optimizer_requires_an_explicit_destination(tmp_path: Path):
    source = tmp_path / "source.png"
    _image(source)
    result = optimizer.optimize_files([str(source)])
    assert result["ok"] is False
    assert "out_dir is required" in result["error"]
    assert source.exists()


def test_optimizer_keeps_source_when_converting(tmp_path: Path):
    source = tmp_path / "source.png"
    output = tmp_path / "optimized"
    _image(source)
    result = optimizer.optimize_files(
        [str(source)],
        {"out_dir": str(output), "format": "webp", "quality": 70},
    )
    assert result["ok"] is True
    assert source.exists()
    assert (output / "source.webp").exists()
    assert result["results"][0]["source_retained"] is True


def test_optimizer_in_place_creates_backup(tmp_path: Path):
    source = tmp_path / "source.jpeg"
    _image(source)
    before = source.read_bytes()
    result = optimizer.optimize_files(
        [str(source)],
        {"in_place": True, "format": "keep", "quality": 65},
    )
    assert result["ok"] is True
    assert result["results"][0]["out"] == str(source)
    backup = Path(result["results"][0]["backup"])
    assert backup.read_bytes() == before


def test_optimizer_preserves_duplicate_basenames(tmp_path: Path):
    first = tmp_path / "one" / "hero.png"
    second = tmp_path / "two" / "hero.png"
    output = tmp_path / "out"
    first.parent.mkdir()
    second.parent.mkdir()
    _image(first, color="#1565c0")
    _image(second, color="#151a25")
    result = optimizer.optimize_files(
        [str(first), str(second)],
        {"out_dir": str(output), "format": "webp"},
    )
    outputs = {Path(record["out"]).name for record in result["results"]}
    assert result["ok"] is True
    assert len(outputs) == 2


def test_optimizer_requires_overwrite_for_existing_destination(tmp_path: Path):
    source = tmp_path / "source.png"
    output = tmp_path / "out"
    output.mkdir()
    _image(source)
    _image(output / "source.png", color="#151a25")
    result = optimizer.optimize_files([str(source)], {"out_dir": str(output)})
    assert result["ok"] is False
    assert "overwrite=true" in result["results"][0]["error"]


def test_optimizer_rejects_animated_images(tmp_path: Path):
    source = tmp_path / "animated.gif"
    output = tmp_path / "out"
    frames = [Image.new("RGB", (16, 16), color) for color in ("red", "blue")]
    frames[0].save(source, save_all=True, append_images=frames[1:], duration=100, loop=0)
    result = optimizer.optimize_files([str(source)], {"out_dir": str(output)})
    assert result["ok"] is False
    assert "animated or multipage" in result["results"][0]["error"]
    assert source.exists()


def test_nested_output_directory_is_not_reprocessed(tmp_path: Path):
    source = tmp_path / "source.png"
    output = tmp_path / "optimized"
    output.mkdir()
    _image(source)
    _image(output / "old.png")
    result = optimizer.optimize_files([str(tmp_path)], {"out_dir": str(output)})
    assert result["count"] == 1
    assert result["results"][0]["file"] == str(source)


def test_svg_rejects_dtd_and_entities(tmp_path: Path):
    source = tmp_path / "unsafe.svg"
    output = tmp_path / "out"
    source.write_text(
        '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text></svg>',
        encoding="utf-8",
    )
    result = optimizer.optimize_files([str(source)], {"out_dir": str(output)})
    assert result["ok"] is False
    assert "DTD or entity" in result["results"][0]["error"]
