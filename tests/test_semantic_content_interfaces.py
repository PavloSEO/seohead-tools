"""Public CLI/MCP/retained-corpus contracts for #765 and #879."""

from __future__ import annotations

import asyncio
import json

from seohead import cli
from seohead.mcp import handlers
from tests.test_scan_corpus_inputs import _scan

HTML = """<html lang=en><head><title>Industrial pumps</title><meta name=description content=Old></head>
<body><nav>menu</nav><main><h1>Industrial pumps</h1><p>Water pump equipment for factories and maintenance teams.</p></main></body></html>"""


def _adapter():
    return {
        "kind": "local",
        "model_id": "fixture",
        "model_version": "1",
        "settings": {"dims": 2},
        "data_transfer": "none",
    }


def test_semantic_similarity_consumes_supplied_vectors_without_model_call_and_reuses_scan(tmp_path):
    scan = _scan(
        tmp_path,
        [
            ("https://example.test/a", HTML),
            ("https://example.test/b", HTML.replace("pumps", "equipment")),
        ],
    )
    result = handlers.semantic_similarity(
        scan=str(scan),
        adapter=_adapter(),
        embeddings=[
            {"url": "https://example.test/a", "vector": [1, 0]},
            {"url": "https://example.test/b", "vector": [1, 0]},
        ],
        cache_path=str(tmp_path / "semantic.sqlite"),
        threshold=0.9,
    )

    assert result["ok"] is True
    assert result["source"]["kind"] == "scan.v1"
    assert result["normalization"]["normalizer_version"] == "content_normalizer.v1"
    assert result["groups"][0]["kind"] == "semantic_similarity_candidate"
    assert (tmp_path / "semantic.sqlite").exists()


def test_meta_draft_plan_and_execute_reuse_retained_corpus_metadata(tmp_path):
    scan = _scan(tmp_path, [("https://example.test/a", HTML)])
    plan = handlers.meta_description_drafts(scan=str(scan), context={"min_chars": 1})
    page = plan["plan"]["batches"][0][0]
    result = handlers.meta_description_drafts(
        scan=str(scan),
        context={"min_chars": 1},
        drafts=[
            {
                "url": page["url"],
                "source_sha256": page["source_reference"]["normalized_sha256"],
                "proposed_description": "Industrial water pumps for factory maintenance and delivery planning.",
            }
        ],
        executor={
            "kind": "calling_agent",
            "contract_version": "meta_description_drafts.v1",
            "model_identity": None,
            "data_transfer": "caller_runtime",
        },
        checkpoint_path=str(tmp_path / "drafts.sqlite"),
        json_path=str(tmp_path / "review.json"),
        csv_path=str(tmp_path / "review.csv"),
    )

    assert plan["source"]["kind"] == "scan.v1"
    assert page["title"]  # parsed scan metadata reaches the draft contract
    assert result["result"]["rows"][0]["generation_state"] == "completed"
    assert (tmp_path / "review.csv").exists()


def test_cli_and_mcp_register_the_shared_content_routes(monkeypatch, capsys):
    class NeverRead:
        closed = False

        def isatty(self):
            return False

        def read(self):
            raise AssertionError("explicit source flags must not consume stdin")

    monkeypatch.setattr(cli.sys, "stdin", NeverRead())
    monkeypatch.setitem(
        handlers.HANDLERS, "semantic_similarity", lambda **kw: {"ok": True, "echo": kw}
    )
    assert (
        cli.main(["semantic-similarity", "--scan", "saved.sqlite", "--cache-path", "cache.sqlite"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["echo"]["scan"] == "saved.sqlite"

    from seohead.mcp.mcp_server import build_server

    tools = {tool.name: tool for tool in asyncio.run(build_server().list_tools())}
    assert {"seo_semantic_similarity", "seo_meta_description_drafts"} <= tools.keys()
    assert "cache_path" in tools["seo_semantic_similarity"].inputSchema["properties"]
    assert "checkpoint_path" in tools["seo_meta_description_drafts"].inputSchema["properties"]


def test_cli_runs_supplied_vectors_and_resumes_supplied_drafts(tmp_path, capsys):
    semantic_input = {
        "items": [
            {"url": "https://example.test/a", "html": HTML},
            {"url": "https://example.test/b", "html": HTML.replace("pumps", "equipment")},
        ],
        "adapter": _adapter(),
        "embeddings": [
            {"url": "https://example.test/a", "vector": [1, 0]},
            {"url": "https://example.test/b", "vector": [1, 0]},
        ],
    }
    assert (
        cli.main(
            [
                "semantic-similarity",
                "--cache-path",
                str(tmp_path / "semantic.sqlite"),
                "--input",
                json.dumps(semantic_input),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["groups"]

    draft_input = {
        "items": [{"url": "https://example.test/a", "html": HTML}],
        "context": {"min_chars": 1},
    }
    assert cli.main(["meta-description-drafts", "--input", json.dumps(draft_input)]) == 0
    page = json.loads(capsys.readouterr().out)["plan"]["batches"][0][0]
    execution = {
        **draft_input,
        "drafts": [
            {
                "url": page["url"],
                "source_sha256": page["source_reference"]["normalized_sha256"],
                "proposed_description": "Industrial water pumps for factory maintenance and delivery planning.",
            }
        ],
        "executor": {
            "kind": "calling_agent",
            "contract_version": "meta_description_drafts.v1",
            "model_identity": None,
            "data_transfer": "caller_runtime",
        },
    }
    command = [
        "meta-description-drafts",
        "--checkpoint-path",
        str(tmp_path / "drafts.sqlite"),
        "--input",
        json.dumps(execution),
    ]
    assert cli.main(command) == 0
    assert json.loads(capsys.readouterr().out)["result"]["rows"][0]["resumed"] is False
    assert cli.main(command) == 0
    assert json.loads(capsys.readouterr().out)["result"]["rows"][0]["resumed"] is True


def test_mcp_runs_the_same_synthetic_semantic_and_draft_cores(tmp_path):
    from seohead.mcp.mcp_server import build_server

    server = build_server()
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
    semantic = asyncio.run(
        tools["seo_semantic_similarity"].run(
            {
                "items": [{"url": "https://example.test/a", "html": HTML}],
                "adapter": _adapter(),
                "embeddings": [{"url": "https://example.test/a", "vector": [1, 0]}],
                "cache_path": str(tmp_path / "semantic.sqlite"),
            },
        )
    )
    draft = asyncio.run(
        tools["seo_meta_description_drafts"].run(
            {
                "items": [{"url": "https://example.test/a", "html": HTML}],
                "context": {"min_chars": 1},
            },
        )
    )

    assert semantic["ok"] is True
    assert draft["plan"]["coverage"]["eligible"] == 1
