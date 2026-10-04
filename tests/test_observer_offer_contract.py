"""The audit router keeps the observer opt-in rather than a hidden side effect."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_audit_routes_keep_a_single_explicit_observer_offer_contract():
    required = (
        "seohead watch --project ./project-directory",
        "explicit yes",
        "accepted or declined",
        "same session and project",
        "do not start a shell",
        "seo_project_observe",
        "does not inject a chat message",
    )
    for relative in (
        ".claude/skills/control/SKILL.md",
        ".claude/skills/full-audit-v1/SKILL.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        for phrase in required:
            assert phrase in text, f"{relative} lost {phrase!r}"


def test_public_observer_guidance_matches_the_opt_in_contract():
    projects = (ROOT / "docs/PROJECTS.md").read_text(encoding="utf-8")
    profiles = (ROOT / "docs/MCP_PROFILES.md").read_text(encoding="utf-8")
    assert "does not open a terminal or start a process before yes" in projects
    assert "does not substitute for consent, launch\na terminal, start a scan" in profiles
