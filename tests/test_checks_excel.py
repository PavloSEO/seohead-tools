"""Coverage for seohead/checks/excel.py: redirects file reading and cluster writing.

No network. Files are created under tmp_path.
"""

from __future__ import annotations

from openpyxl import Workbook, load_workbook

from seohead.checks import excel


def _write_xlsx(path, rows, sheet="Sheet1"):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_parse_xlsx_with_header_skips_header_and_blank_rows(tmp_path):
    path = tmp_path / "redirects.xlsx"
    _write_xlsx(path, [["Old URL", "New URL"], ["/a", "/b"], [None, None], ["/c", None]])

    result = excel.parse_redirects_workbook(str(path))

    assert result == [
        {"old_url": "/a", "new_url": "/b"},
        {"old_url": "/c", "new_url": ""},
    ]


def test_parse_xlsx_without_header_keeps_first_row(tmp_path):
    path = tmp_path / "plain.xlsx"
    _write_xlsx(path, [["/x", "/y"]])

    assert excel.parse_redirects_workbook(str(path)) == [{"old_url": "/x", "new_url": "/y"}]


def test_parse_csv_sniffs_semicolon_and_cyrillic_header(tmp_path):
    path = tmp_path / "redirects.csv"
    path.write_text("старый;новый\n/one;/two\n", encoding="utf-8")

    assert excel.parse_redirects_workbook(str(path)) == [{"old_url": "/one", "new_url": "/two"}]


def test_parse_tsv_falls_back_to_tab_by_extension(tmp_path):
    path = tmp_path / "redirects.tsv"
    path.write_text("from\tto\n/p\t/q\n", encoding="utf-8")

    assert excel.parse_redirects_workbook(str(path)) == [{"old_url": "/p", "new_url": "/q"}]


def test_empty_file_is_error_dict(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("\n\n", encoding="utf-8")

    assert excel.parse_redirects_workbook(str(path)) == {"ok": False, "error": "File is empty"}


def test_rows_without_old_url_are_error_dict(tmp_path):
    path = tmp_path / "no_old.csv"
    path.write_text(",/only-new\n", encoding="utf-8")

    assert excel.parse_redirects_workbook(str(path)) == {
        "ok": False,
        "error": "The file contains no valid URLs",
    }


def test_unreadable_file_is_error_dict_not_exception(tmp_path):
    result = excel.parse_redirects_workbook(str(tmp_path / "missing.xlsx"))

    assert isinstance(result, dict)
    assert result["ok"] is False
    assert result["error"].startswith("Failed to read worksheet")


def test_corrupt_xlsx_is_error_dict(tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"not a zip at all")

    result = excel.parse_redirects_workbook(str(path))

    assert result["ok"] is False
    assert "Failed to read worksheet" in result["error"]


def test_safe_sheet_name_replaces_forbidden_and_truncates():
    assert excel.safe_sheet_name("a/b:c*d?e[f]") == "a_b_c_d_e_f_"
    assert excel.safe_sheet_name("x" * 40) == "x" * 30
    assert excel.safe_sheet_name("") == "Sheet"


def test_csv_round_trip_has_bom_header_and_escaped_quotes(tmp_path):
    clusters = [
        {"label": "насосы", "keywords": ['насос "CNS"', "купить насос"]},
        {"id": 7, "label": "фильтры", "count": 1, "keywords": ["фильтр"]},
    ]
    path = tmp_path / "clusters.csv"

    assert excel.write_clusters_csv(clusters, str(path)) is None

    text = path.read_bytes().decode("utf-8")
    assert text.startswith("﻿Keyword,Cluster ID,Cluster name,Cluster size")
    assert '"насос ""CNS"""' in text
    assert '"фильтр",7,"фильтры",1' in text
    assert '"купить насос",1,"насосы",2' in text


def test_write_csv_to_bad_path_returns_error_dict(tmp_path):
    bad = tmp_path / "no_such_dir" / "out.csv"

    result = excel.write_clusters_csv([{"label": "x", "keywords": ["y"]}], str(bad))

    assert result["ok"] is False
    assert result["error"]


def test_build_csv_derives_id_and_count_when_missing():
    text = excel.build_clusters_csv([{"label": "a", "keywords": ["k1", "k2"]}])

    lines = text.split("\n")
    assert lines[1] == '"k1",1,"a",2'
    assert lines[2] == '"k2",1,"a",2'


def test_xlsx_writes_sheet_per_cluster_plus_summary(tmp_path):
    clusters = [
        {"label": "pumps/CNS", "keywords": ["насос"]},
        {"label": "pumps_CNS", "keywords": ["насос cns"]},
    ]
    path = tmp_path / "clusters.xlsx"

    assert excel.write_clusters_xlsx(clusters, str(path)) is None

    wb = load_workbook(path)
    names = wb.sheetnames
    assert "_All" in names
    # Two clusters whose sanitised names collide must get distinct sheet titles.
    cluster_sheets = [n for n in names if n != "_All"]
    assert len(cluster_sheets) == 2
    assert len({n.lower() for n in cluster_sheets}) == 2

    summary = list(wb["_All"].iter_rows(values_only=True))
    assert summary[0] == ("Keyword", "Cluster ID", "Cluster name")
    assert ("насос", 1, "pumps/CNS") in summary
    assert ("насос cns", 2, "pumps_CNS") in summary


def test_xlsx_sheet_title_is_truncated_to_excel_limit_on_collision(tmp_path):
    long_name = "н" * 30
    clusters = [{"label": long_name, "keywords": ["a"]}, {"label": long_name, "keywords": ["b"]}]
    path = tmp_path / "long.xlsx"

    assert excel.write_clusters_xlsx(clusters, str(path)) is None

    titles = [n for n in load_workbook(path).sheetnames if n != "_All"]
    assert len(titles) == 2
    assert all(len(t) <= 30 for t in titles)
