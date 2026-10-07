"""One persistent, local stdio MCP session for the Desktop presentation layer."""

from __future__ import annotations

import asyncio
import json
import random
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal


TOOL_ALLOWLIST = frozenset(
    {
        "seo_crawl_describe_settings",
        "seo_compare_crawls",
        "seo_verify_fixes",
        "seo_project_open",
        "seo_project_observe",
        "seo_project_checklist_page",
        "seo_project_task_detail",
        "seo_project_inbox_submit",
        "seo_project_inbox_list",
        "seo_project_scans",
        "seo_scan_inspect",
        "seo_scan_url_detail",
        "seo_scan_evidence",
        "seo_scan_extract",
        "seo_scan_link_inspect",
        "seo_scan_navigation",
        "seo_scan_status",
    }
)
_DIRECTORY_TOOLS = frozenset(
    {
        "seo_project_open",
        "seo_project_observe",
        "seo_project_checklist_page",
        "seo_project_task_detail",
        "seo_project_inbox_submit",
        "seo_project_inbox_list",
        "seo_project_scans",
    }
)
_GLOBAL_READ_TOOLS = frozenset({"seo_crawl_describe_settings"})
_SCAN_TOOLS = frozenset(
    {
        "seo_scan_inspect",
        "seo_scan_url_detail",
        "seo_scan_evidence",
        "seo_scan_extract",
        "seo_scan_link_inspect",
        "seo_scan_navigation",
        "seo_scan_status",
    }
)


class GatewaySignals(QObject):
    ready = pyqtSignal(tuple)
    result = pyqtSignal(str, dict, int)
    failed = pyqtSignal(str, str, int)
    transport_failed = pyqtSignal(str)
    stopped = pyqtSignal()


@dataclass(frozen=True)
class Request:
    request_id: str
    tool: str
    arguments: dict[str, Any]
    generation: int


def payload(response: Any) -> dict[str, Any]:
    """Decode the structured result shape published by the bundled MCP server."""
    if getattr(response, "isError", False):
        message = "MCP tool returned an error"
        content = getattr(response, "content", ())
        if content and isinstance(getattr(content[0], "text", None), str):
            message = content[0].text
        raise ValueError(message)
    structured = getattr(response, "structuredContent", None)
    if isinstance(structured, dict):
        result = structured.get("result", structured)
        if isinstance(result, dict):
            if result.get("ok") is False:
                raise ValueError(str(result.get("error") or result.get("reason") or "core refused request"))
            return result
    content = getattr(response, "content", ())
    if content and isinstance(getattr(content[0], "text", None), str):
        result = json.loads(content[0].text)
        if isinstance(result, dict):
            if result.get("ok") is False:
                raise ValueError(str(result.get("error") or result.get("reason") or "core refused request"))
            return result
    raise TypeError("MCP tool returned no object result")


class PersistentMcpGateway(QRunnable):
    """Serialize bounded desktop calls over one `seohead mcp` subprocess.

    The core owns its tools and retained evidence. The desktop only retains the
    most recent request per panel, which prevents refresh bursts from queuing.
    """

    def __init__(self, executable: str):
        super().__init__()
        self.executable = executable
        self.signals = GatewaySignals()
        self._pending: OrderedDict[str, Request] = OrderedDict()
        self._condition = threading.Condition()
        self._stopped = False
        self._scope: Path | None = None

    def set_project_scope(self, directory: str) -> None:
        root = Path(directory).resolve()
        if not (root / "project.json").is_file():
            raise ValueError("project scope must contain project.json")
        with self._condition:
            self._scope = root
            self._pending.clear()

    def submit(self, request_id: str, tool: str, arguments: dict[str, Any], generation: int) -> None:
        self._validate(tool, arguments)
        request = Request(request_id, tool, dict(arguments), generation)
        with self._condition:
            if self._stopped:
                raise RuntimeError("MCP gateway is stopped")
            self._pending[request_id] = request
            self._pending.move_to_end(request_id)
            self._condition.notify()

    def cancel_generation(self, generation: int) -> None:
        with self._condition:
            self._pending = OrderedDict(
                (key, request)
                for key, request in self._pending.items()
                if request.generation != generation
            )

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._pending.clear()
            self._condition.notify_all()

    def _validate(self, tool: str, arguments: dict[str, Any]) -> None:
        if tool not in TOOL_ALLOWLIST or not isinstance(arguments, dict):
            raise ValueError("desktop request is not declared by the local MCP adapter")
        scope = self._scope
        if tool in _GLOBAL_READ_TOOLS:
            return
        if scope is None:
            raise ValueError("open a local project before making MCP requests")
        if tool in _DIRECTORY_TOOLS:
            directory = arguments.get("directory")
            if not isinstance(directory, str) or Path(directory).resolve() != scope:
                raise ValueError("MCP request is outside the selected local project")
        if tool in _SCAN_TOOLS:
            scan = arguments.get("input_path")
            if not isinstance(scan, str):
                raise ValueError("retained scan path is required")
            scans = (scope / "scans").resolve()
            if not Path(scan).resolve().is_relative_to(scans):
                raise ValueError("MCP scan request is outside the selected local project")

        if tool in {"seo_compare_crawls", "seo_verify_fixes"}:
            before = arguments.get("before" if tool == "seo_compare_crawls" else "baseline")
            after = arguments.get("after")
            if any(not isinstance(value, str) for value in (before, after)):
                raise ValueError("comparison requires explicit saved before and after paths; recrawling is unavailable")
            paths = [Path(value).resolve() for value in (before, after)]
            if paths[0] == paths[1] or any(
                not path.is_relative_to(scope / "scans") or not path.is_file() for path in paths
            ):
                raise ValueError("comparison requires two retained scans inside the selected project")
            output = arguments.get("out_dir")
            if not isinstance(output, str) or not Path(output).resolve().is_relative_to(scope / "reports"):
                raise ValueError("comparison output must remain inside the selected project's reports")
            if Path(output).exists() or arguments.get("force"):
                raise ValueError("comparison cannot overwrite an existing report or force incompatible settings")
            allowed = ({"before", "after", "force", "out_dir"} if tool == "seo_compare_crawls"
                       else {"baseline", "after", "finding_ids", "out_dir"})
            if set(arguments) - allowed:
                raise ValueError("comparison arguments exceed the offline Desktop contract")
            if tool == "seo_verify_fixes":
                ids = arguments.get("finding_ids")
                if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(not isinstance(item, str) or not item for item in ids):
                    raise ValueError("offline verification is bounded to 100 selected finding IDs")

    def _next_request(self) -> Request | None:
        with self._condition:
            while not self._stopped and not self._pending:
                self._condition.wait(timeout=0.25)
            if self._stopped:
                return None
            _key, request = self._pending.popitem(last=False)
            return request

    async def _serve(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        failures = 0
        active: Request | None = None
        while not self._stopped:
            try:
                parameters = StdioServerParameters(command=self.executable, args=["mcp"])
                async with (
                    stdio_client(parameters) as (reader, writer),
                    ClientSession(reader, writer) as session,
                ):
                    await session.initialize()
                    advertised = {tool.name for tool in (await session.list_tools()).tools}
                    missing = TOOL_ALLOWLIST - advertised
                    if missing:
                        raise RuntimeError("bundled core misses desktop tools: " + ", ".join(sorted(missing)))
                    self.signals.ready.emit(tuple(sorted(TOOL_ALLOWLIST)))
                    failures = 0
                    while True:
                        request = await asyncio.to_thread(self._next_request)
                        if request is None:
                            return
                        active = request
                        try:
                            response = await session.call_tool(request.tool, request.arguments)
                            self.signals.result.emit(
                                request.request_id, payload(response), request.generation
                            )
                        except Exception as exc:
                            self.signals.failed.emit(request.request_id, str(exc), request.generation)
                        finally:
                            active = None
            except Exception as exc:  # transport/bootstrap errors are retried with bounded backoff
                if self._stopped:
                    return
                if active is not None:
                    self.signals.failed.emit(active.request_id, str(exc), active.generation)
                    active = None
                self.signals.transport_failed.emit(str(exc))
                delay = min(4.0, 0.25 * (2**failures)) + random.uniform(0.0, 0.1)
                failures = min(failures + 1, 4)
                await asyncio.sleep(delay)
        self.signals.stopped.emit()

    def run(self) -> None:
        try:
            asyncio.run(self._serve())
        finally:
            try:
                self.signals.stopped.emit()
            except RuntimeError:
                # Qt may already have destroyed the window during test teardown.
                pass
