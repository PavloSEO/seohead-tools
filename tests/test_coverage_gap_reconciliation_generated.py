from scripts.generate_coverage_gap_reconciliation import OUT, render, rows


def test_reconciliation_covers_every_canonical_gap_row_and_is_current():
    assert len(rows()) == 100
    assert OUT.read_text(encoding="utf-8") == render()
