"""Print a local HTML file to PDF with an installed headless Chromium (Chrome, Edge, Chromium).

No browser is bundled or downloaded. Discovery order: the ``SEOHEAD_CHROME`` environment
variable, the standard install locations for Windows, macOS and Linux, then ``PATH``. A missing
browser is a ``skipped`` result with the reason, never an exception and never a silent success.

The browser runs with its sandbox enabled (no ``--no-sandbox``), a throwaway profile directory,
extensions and first-run UI disabled, and a timeout. Some Chromium builds, notably Edge on
Windows, return from the command before the PDF is completely written, so the result is accepted
only after the file exists, starts with ``%PDF`` and its size has stopped changing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ENV_VAR = "SEOHEAD_CHROME"
DEFAULT_TIMEOUT = 120.0
STABLE_CHECKS = 3
POLL_SECONDS = 0.25

_PATH_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "msedge",
    "chrome",
)


def _standard_locations(env: Mapping[str, str], platform: str) -> list[Path]:
    if platform.startswith("win"):
        roots = [
            env.get("PROGRAMFILES"),
            env.get("PROGRAMFILES(X86)"),
            env.get("LOCALAPPDATA"),
        ]
        relative = (
            ("Google", "Chrome", "Application", "chrome.exe"),
            ("Microsoft", "Edge", "Application", "msedge.exe"),
            ("Chromium", "Application", "chrome.exe"),
        )
        return [Path(root, *parts) for root in roots if root for parts in relative]
    if platform == "darwin":
        return [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    return [
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/usr/bin/microsoft-edge"),
        Path("/snap/bin/chromium"),
    ]


def find_browser(
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    is_file: Callable[[Path], bool] = Path.is_file,
) -> dict[str, Any]:
    """Locate a Chromium-family executable; returns ``{"path": ...}`` or ``{"path": None, ...}``."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    explicit = env.get(ENV_VAR)
    if explicit:
        candidate = Path(explicit)
        if is_file(candidate):
            return {"path": str(candidate), "source": ENV_VAR}
        return {"path": None, "reason": f"{ENV_VAR} points to a missing file: {explicit}"}
    for candidate in _standard_locations(env, platform):
        if is_file(candidate):
            return {"path": str(candidate), "source": "standard location"}
    for name in _PATH_NAMES:
        found = which(name)
        if found:
            return {"path": found, "source": "PATH"}
    return {
        "path": None,
        "reason": (
            "no Chrome, Edge or Chromium executable found; install one or set "
            f"{ENV_VAR} to its path"
        ),
    }


def browser_command(browser: str, html_path: Path, pdf_path: Path, profile_dir: Path) -> list[str]:
    return [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        f"--user-data-dir={profile_dir}",
        f"--print-to-pdf={pdf_path}",
        html_path.resolve().as_uri(),
    ]


def _wait_for_pdf(path: Path, deadline: float, sleep: Callable[[float], None]) -> str | None:
    """Wait until the PDF exists, has a PDF header, and its size is stable; return an error."""
    last_size = -1
    stable = 0
    while time.monotonic() < deadline:
        if path.is_file():
            size = path.stat().st_size
            if size > 0 and size == last_size:
                stable += 1
                if stable >= STABLE_CHECKS:
                    with path.open("rb") as handle:
                        if handle.read(5) != b"%PDF-":
                            return "the browser wrote a file that is not a PDF"
                    return None
            else:
                stable = 0
            last_size = size
        sleep(POLL_SECONDS)
    return "the browser did not finish writing the PDF before the timeout"


def print_to_pdf(
    html_path: str | Path,
    pdf_path: str | Path,
    *,
    browser: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    runner: Callable[..., Any] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Render ``html_path`` to ``pdf_path``; returns ``status`` ok, skipped or failed."""
    html_path = Path(html_path)
    pdf_path = Path(pdf_path)
    if browser is None:
        found = find_browser()
        if not found["path"]:
            return {"status": "skipped", "reason": found["reason"]}
        browser = found["path"]
    deadline = time.monotonic() + timeout
    # The browser writes beside the destination under a temporary name; only a verified file
    # is moved into place, so a timeout never leaves a truncated report under the final name.
    partial = pdf_path.with_name(f".{pdf_path.stem}.partial.pdf")
    partial.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="seohead-chromium-") as profile:
        command = browser_command(browser, html_path, partial, Path(profile))
        try:
            completed = runner(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            partial.unlink(missing_ok=True)
            return {"status": "failed", "reason": f"the browser timed out after {timeout:.0f} s"}
        except OSError as exc:
            return {"status": "failed", "reason": f"the browser could not start: {exc}"}
        error = _wait_for_pdf(partial, deadline, sleep)
        if error:
            code = getattr(completed, "returncode", None)
            partial.unlink(missing_ok=True)
            return {"status": "failed", "reason": f"{error} (exit code {code})"}
    os.replace(partial, pdf_path)
    return {"status": "ok", "path": str(pdf_path), "browser": browser}
