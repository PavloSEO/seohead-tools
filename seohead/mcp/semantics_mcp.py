"""MCP tools of the semantic core (``seohead semantics``) on the unified ``seohead`` server.

They are registered separately, like the ``sf_*`` tools, because a semantic stage is not a
1:1 ``seohead`` command: ``seohead semantics <stage> --project DIR`` dispatches by stage.
Paid stages share one tool that refuses to run without ``confirm_paid=true``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def register(mcp, _checked: Callable[[Any], Any]):  # pragma: no cover - needs the SDK
    """Register the ``seo_semantics_*`` tools on an existing FastMCP server."""
    from mcp.types import ToolAnnotations

    read_files = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    create_files = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
    create_files_from_web = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
    )
    paid = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
    )

    @mcp.tool(annotations=read_files, structured_output=True)
    def seo_semantics_status(project: str) -> dict[str, Any]:
        """Read a semantic core's phrase counters, provider units used and the next stage,
        without migrating or writing its database. project is the semantic project directory,
        or a SEOHEAD project workspace whose semantics/ subdirectory holds the core."""
        from seohead.semantics.runner import run_quiet

        return _checked(run_quiet("status", project, allowed=("status",)))

    @mcp.tool(annotations=create_files, structured_output=True)
    def seo_semantics_run(
        stage: str,
        project: str,
        out: str | None = None,
        file: str | None = None,
    ) -> dict[str, Any]:
        """Run one free, offline semantic-core stage: init, import (file = CSV with a
        norm/phrase/query column), clean, graph, competitors, report, excel, sitematch,
        relevance or export. Phrases and evidence are retained; statuses change reversibly.
        No network access; paid stages are refused here (use seo_semantics_paid)."""
        from seohead.semantics import LOCAL_STAGES
        from seohead.semantics.runner import run_quiet

        return _checked(
            run_quiet(
                stage,
                project,
                allowed=tuple(s for s in LOCAL_STAGES if s != "status"),
                out=out,
                file=file,
            )
        )

    @mcp.tool(annotations=create_files_from_web, structured_output=True)
    def seo_semantics_mine(project: str, out: str | None = None) -> dict[str, Any]:
        """Fetch the most frequent competitor pages from the core's cached SERP and append
        title/heading 2-3-grams to synonyms.txt as candidate phrases. Reads public pages; spends
        no provider quota. Run seo_semantics_paid stage=synonyms afterwards to check them."""
        from seohead.semantics.runner import run_quiet

        return _checked(run_quiet("mine", project, allowed=("mine",), out=out))

    @mcp.tool(annotations=paid, structured_output=True)
    def seo_semantics_paid(
        stage: str,
        project: str,
        confirm_paid: bool = False,
        resume: bool = False,
        max_seeds: int = 0,
        limit: int = 0,
        method: str = "serp",
        yes: bool = False,
    ) -> dict[str, Any]:
        """Run one paid semantic-core stage: collect (Wordstat expansion of seeds.txt),
        synonyms (Wordstat check of synonyms.txt), cluster (Yandex Search SERP, cached per
        phrase; method=louvain needs the semantics extra) or exact (Arsenkin !W, once, at the
        end). Paid: without confirm_paid=true nothing is sent. Every submission is reserved
        before the call, so an interrupted or ambiguous request is never paid for twice; charges
        land in the shared spend journal (seo_spend_report)."""
        from seohead.semantics import PAID_STAGES
        from seohead.semantics.runner import run_quiet

        return _checked(
            run_quiet(
                stage,
                project,
                allowed=PAID_STAGES,
                confirm_paid=confirm_paid,
                resume=resume,
                max_seeds=max_seeds,
                limit=limit,
                method=method,
                yes=yes,
            )
        )
