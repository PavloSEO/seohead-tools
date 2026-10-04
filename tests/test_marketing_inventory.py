import csv

from seohead.tools.marketing_inventory import inventory

HTML = """
<a class="cta" href="/demo">Demo</a>
<a class="cta">Call</a>
<a class="cta" href="/buy">Buy</a>
<form id="lead" action="/submit"><input name="email"></form>
<form id="lead"><input name="email"></form>
<iframe src="https://forms.example/embed?formid=external"></iframe>
"""


def test_occurrences_stay_correlated_to_their_matched_elements(tmp_path):
    result = inventory(
        [
            {"url": "https://example.com/landing", "html": HTML, "document_ref": "doc-1"},
            {"url": "https://example.com/unavailable", "body_state": "unavailable"},
        ],
        out_dir=str(tmp_path / "inventory"),
    )
    ctas = [row for row in result["occurrences"] if row["kind"] == "cta"]
    assert [
        (row["label"], row["raw_target"], row["resolved_target"], row["target_state"])
        for row in ctas
    ] == [
        ("Demo", "/demo", "https://example.com/demo", "observed"),
        ("Call", None, None, "missing"),
        ("Buy", "/buy", "https://example.com/buy", "observed"),
    ]
    forms = [row for row in result["occurrences"] if row["kind"] == "form"]
    assert [(row["identifier"], row["raw_target"], row["target_state"]) for row in forms] == [
        ("lead", "/submit", "observed"),
        ("lead", None, "missing"),
    ]
    assert result["form_groups"][0]["occurrences"] == 2
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["documents_unavailable"][0]["body_state"] == "unavailable"
    assert (tmp_path / "inventory" / "marketing-inventory.json").is_file()
    with (tmp_path / "inventory" / "marketing-occurrences.csv").open() as stream:
        assert len(list(csv.DictReader(stream))) == len(result["occurrences"])


def test_formula_like_labels_are_safe_in_csv_and_iframes_are_not_inspected(tmp_path):
    result = inventory(
        [{"url": "https://example.com/", "html": '<a class="cta" href="/x">=SUM(A1)</a>'}],
        out_dir=str(tmp_path / "inventory"),
    )
    with (tmp_path / "inventory" / "marketing-occurrences.csv").open() as stream:
        assert next(csv.DictReader(stream))["label"] == "'=SUM(A1)"
    assert "never fetched or inspected" in result["notes"][1]
