# Scenario 54 — AI visibility: will an assistant cite this site

## The question

> People ask ChatGPT instead of Google now. Are we even readable to it?

## Covers

Nothing in the published catalogue. Visibility to AI assistants is not one of the
issues that catalogue enumerates, which is itself worth knowing: the shared checklist this
field uses predates the question.

## The chain

**1. Which AI crawlers are allowed in at all.**

```bash
seohead ai-bots-check --url https://example.com
```

GPTBot, ClaudeBot, PerplexityBot, Google-Extended and the rest, read from the site's own
robots.txt. A blanket `Disallow: /` for an unfamiliar user-agent is usually nobody's decision —
it is a default that was never revisited.

**2. Whether there is anything written for a model to read.**

```bash
seohead llms-txt-check --url https://example.com --brand Example
```

Reports whether `/llms.txt` exists, whether it is well-formed, and whether it actually names
the brand and points at pages worth reading — an llms.txt that lists a sitemap and nothing else
is a file, not an answer.

**3. Whether the content is in a shape a model can quote.**

```bash
seohead citability-check --url https://example.com/page
```

A citable page answers a question in a paragraph a model can restate with a link. A page whose
answer is spread across a carousel, a table image and three collapsibles is not quotable, no
matter how correct it is.

**4. Whether the answer survives without JavaScript.** AI crawlers almost never render. What
the [rendering scenario](rendering.md) reports as "JS-dependent" is, for this audience, "absent".

## What comes out

```json
{
  "ai-bots-check": {"summary": {"blocked": "<n>", "allowed_explicit": "<n>", "allowed_default": "<n>"},
                    "bots": [{"token": "GPTBot", "status": "allowed_default", "blocked_root": false}]},
  "llms-txt-check": {"score": "<int>", "grade": "<letter>", "passed": "<int>", "total": "<int>",
                     "stats": {"links": "<int>", "mentions_brand": "<bool>"}},
  "citability-check": {"score": "<int>", "grade": "<letter>", "word_count": "<int>", "paragraphs": "<int>", "dimensions": "<object>"}
}
```

Keys are real. `ai-bots-check` status is `blocked`, `allowed_explicit` (named in robots.txt) or `allowed_default` (no rule against it). Values are placeholders.

## What it costs

Three requests. Nothing paid.

## What it cannot answer

- **Whether an assistant will actually cite you.** No public API reports that. This measures
  whether you are readable and quotable, which is the part you control.
- **What any model was trained on.** Nothing here can see a training corpus.
- **Whether being crawled is good for you.** Allowing `GPTBot` is a business decision about
  your content, not a technical default this tool should push you toward.
