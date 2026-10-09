"""Audit site access for AI crawlers from a GEO/AEO perspective.

AI answers increasingly compete with conventional search results while traffic
from crawlers such as GPTBot, ClaudeBot, PerplexityBot, and Google-Extended grows.
The SEO question is which crawlers a site permits in ``robots.txt``, which it
blocks, and whether that policy is intentional. A broad ``Disallow: /`` may
accidentally exclude retrieval bots and reduce visibility in AI answers. The
opposite mistake is unintentionally allowing model-training crawlers.

This module performs no network requests. It accepts existing ``robots.txt``
content and determines the status of each known AI crawler, delegating parsing
to the pure functions in :mod:`seohead.checks.robots`.

Bot roles matter when defining an access policy:

* **training** — collects data for model training (Google-Extended, CCBot, Bytespider);
* **retrieval** — finds sources for real-time answers (PerplexityBot, Claude-Web);
* **user** — fetches on behalf of a specific user (ChatGPT-User, Perplexity-User).

Site owners often permit retrieval for answer visibility while restricting
training crawlers.
"""

from __future__ import annotations

from typing import Any

from seohead.checks.robots import is_allowed, parse_robots

# Known AI crawlers. ``token`` is the User-agent value used both in requests and
# in robots.txt matching; ``type`` describes the crawler's model/answer role.
AI_BOTS: list[dict[str, str]] = [
    {"token": "GPTBot", "vendor": "OpenAI", "type": "training", "purpose": "OpenAI model training"},
    {
        "token": "OAI-SearchBot",
        "vendor": "OpenAI",
        "type": "retrieval",
        "purpose": "retrieval for SearchGPT answers",
    },
    {
        "token": "ChatGPT-User",
        "vendor": "OpenAI",
        "type": "user",
        "purpose": "fetches requested by a ChatGPT user",
    },
    {
        "token": "ClaudeBot",
        "vendor": "Anthropic",
        "type": "training",
        "purpose": "Anthropic model training",
    },
    {
        "token": "Claude-Web",
        "vendor": "Anthropic",
        "type": "retrieval",
        "purpose": "source retrieval for Claude",
    },
    {
        "token": "anthropic-ai",
        "vendor": "Anthropic",
        "type": "training",
        "purpose": "Anthropic model training and research",
    },
    {
        "token": "PerplexityBot",
        "vendor": "Perplexity",
        "type": "retrieval",
        "purpose": "retrieval for Perplexity answers",
    },
    {
        "token": "Perplexity-User",
        "vendor": "Perplexity",
        "type": "user",
        "purpose": "fetches requested by a Perplexity user",
    },
    {
        "token": "Google-Extended",
        "vendor": "Google (Gemini)",
        "type": "training",
        "purpose": "Gemini and Vertex AI model training",
    },
    {
        "token": "Applebot-Extended",
        "vendor": "Apple",
        "type": "training",
        "purpose": "Apple model training",
    },
    {
        "token": "Bytespider",
        "vendor": "ByteDance",
        "type": "training",
        "purpose": "ByteDance and TikTok model training",
    },
    {
        "token": "CCBot",
        "vendor": "Common Crawl",
        "type": "training",
        "purpose": "public web dataset used by many models",
    },
    {
        "token": "Meta-ExternalAgent",
        "vendor": "Meta",
        "type": "training",
        "purpose": "Meta AI training and indexing",
    },
    {
        "token": "Meta-ExternalFetcher",
        "vendor": "Meta",
        "type": "retrieval",
        "purpose": "page retrieval for Meta products",
    },
    {
        "token": "Amazonbot",
        "vendor": "Amazon",
        "type": "training",
        "purpose": "Amazon model training",
    },
    {
        "token": "cohere-ai",
        "vendor": "Cohere",
        "type": "training",
        "purpose": "Cohere model training",
    },
    {
        "token": "YouBot",
        "vendor": "You.com",
        "type": "retrieval",
        "purpose": "retrieval for You.com answers",
    },
    {
        "token": "Diffbot",
        "vendor": "Diffbot",
        "type": "training",
        "purpose": "data extraction for a knowledge graph",
    },
]


def check_ai_access(robots_text: str) -> dict[str, Any]:
    """Determine each AI crawler's status from ``robots.txt`` content.

    Each bot result includes ``declared_in_robots`` for an explicit user-agent
    group, ``blocked_root`` for access to ``/``, and the resulting ``status``.
    This is a network-free pure function built on ``parse_robots``.
    """
    parsed = parse_robots(robots_text or "")
    declared_uas = {ua.lower() for g in parsed["groups"] for ua in g["user_agents"]}

    bots: list[dict[str, Any]] = []
    summary = {
        "total": len(AI_BOTS),
        "blocked": 0,
        "allowed_explicit": 0,
        "allowed_default": 0,
        "by_type": {
            "training": {"blocked": 0, "allowed": 0},
            "retrieval": {"blocked": 0, "allowed": 0},
            "user": {"blocked": 0, "allowed": 0},
        },
    }
    for bot in AI_BOTS:
        token = bot["token"]
        token_l = token.lower()
        declared = token_l in declared_uas
        allowed_root = is_allowed(parsed, "/", token)
        blocked_root = not allowed_root

        if blocked_root:
            status = "blocked"
            summary["blocked"] += 1
            summary["by_type"][bot["type"]]["blocked"] += 1
        elif declared:
            status = "allowed_explicit"
            summary["allowed_explicit"] += 1
            summary["by_type"][bot["type"]]["allowed"] += 1
        else:
            status = "allowed_default"
            summary["allowed_default"] += 1
            summary["by_type"][bot["type"]]["allowed"] += 1

        bots.append(
            {
                "token": token,
                "vendor": bot["vendor"],
                "type": bot["type"],
                "purpose": bot["purpose"],
                "declared_in_robots": declared,
                "blocked_root": blocked_root,
                "status": status,
            }
        )

    return {
        "ok": True,
        "declared_groups": len(parsed["groups"]),
        "summary": summary,
        "bots": bots,
    }
