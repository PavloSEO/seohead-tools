"""Shared local selected-BI delivery adapter for CLI and MCP registration."""

from __future__ import annotations

from typing import Any


def bi_filter(
    package: str,
    dataset: str,
    out_dir: str,
    where: dict[str, list[str]] | None = None,
    columns: list[str] | None = None,
    max_rows_per_file: int = 250_000,
    max_bytes_per_file: int = 8 * 1024 * 1024,
    max_output_bytes: int = 4 * 1024 * 1024 * 1024,
    xlsx_out: str | None = None,
    xlsx_max_rows_per_sheet: int = 1_048_575,
) -> dict[str, Any]:
    """Filter declared BI fields to a bounded local CSV package and optional indexed XLSX."""
    from seohead.reports.bi_destinations import export_bi_xlsx, filter_package

    result = filter_package(
        package,
        dataset=dataset,
        out_dir=out_dir,
        where=where,
        columns=columns,
        max_rows_per_file=max_rows_per_file,
        max_bytes_per_file=max_bytes_per_file,
        max_output_bytes=max_output_bytes,
    )
    if xlsx_out is not None:
        result["xlsx"] = export_bi_xlsx(
            out_dir,
            dataset=dataset,
            out=xlsx_out,
            max_rows_per_sheet=xlsx_max_rows_per_sheet,
            max_output_bytes=max_output_bytes,
        )
    return result
