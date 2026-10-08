"""Exercise public behavior through the implementation named by the inventory."""

import importlib

import pytest

from scripts.generate_headless_capability_matrix import document
from seohead.storage import ScanError
from tests.test_scan_native import _metadata


def _implementation(row):
    return importlib.import_module(row["implementation"].removesuffix(".py").replace("/", "."))


def test_run_isolation_reference_blocks_duplicate_writers_and_keeps_scans_independent(tmp_path):
    row = next(row for row in document()["workflows"] if row["capability"] == "run_isolation")
    scan_type = _implementation(row).NativeScan
    first, second = tmp_path / "first.sqlite", tmp_path / "second.sqlite"
    with (
        scan_type.create(first, **_metadata()) as left,
        scan_type.create(second, **_metadata()) as right,
    ):
        left.enqueue([("https://example.test/left", 1)])
        right.enqueue([("https://example.test/right", 1)])
        assert left.claim(1)[0].url == "https://example.test/left"
        assert right.claim(1)[0].url == "https://example.test/right"
        with pytest.raises((BlockingIOError, ScanError, OSError)):
            scan_type.open(first)


@pytest.mark.parametrize("mode", ["native", "list"])
def test_external_crawl_reference_preserves_public_refusals_before_network(
    mode, monkeypatch, tmp_path
):
    row = next(row for row in document()["settings"] if row["path"] == "discovery.external.crawl")

    def no_dns(*_args, **_kwargs):
        pytest.fail("a refused crawl must not resolve a network target")

    monkeypatch.setattr("socket.getaddrinfo", no_dns)
    target = (
        {"url": "https://example.test/", "scan_out": str(tmp_path / "scan.sqlite")}
        if mode == "native"
        else {"urls": ["https://example.test/page"], "out_dir": str(tmp_path / "legacy")}
    )
    with pytest.raises(ValueError, match=r"discovery\.external\.crawl"):
        _implementation(row).crawl_site(
            **target, overrides={"discovery.external.crawl": True}, producer_build="a" * 40
        )
    assert not list(tmp_path.iterdir())
