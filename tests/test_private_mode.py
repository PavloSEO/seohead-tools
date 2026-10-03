"""Secret-file privacy check: POSIX mode bits, skipped on Windows (no such bits)."""

from seohead.data_sources import credentials


def test_posix_mode_bits(monkeypatch):
    monkeypatch.setattr(credentials.os, "name", "posix")
    assert credentials.is_private_mode(0o100600)
    assert not credentials.is_private_mode(0o100644)
    assert not credentials.is_private_mode(0o100660)


def test_windows_skips_mode_bits(monkeypatch):
    monkeypatch.setattr(credentials.os, "name", "nt")
    # os.stat on Windows reports 0o666 for every writable file
    assert credentials.is_private_mode(0o100666)
