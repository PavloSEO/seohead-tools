"""Score content citability for generative and answer-engine optimization.

The model estimates how readily an AI system can extract a self-contained answer
and cite its source. Four dimensions contribute up to 25 points each:

1. **Answer Blocks (25)** — 20-200 word answer paragraphs that do not begin with
   a pronoun, question, or context-dependent transition. These paragraphs can be
   quoted without requiring surrounding copy.
2. **Self-Containment (25)** — absence of phrases such as "as mentioned above,"
   "however," or "this means," which make an isolated excerpt ambiguous.
3. **Statistical Density (25)** — numbers, percentages, dates, and evidence markers
   per 100 words. Specific supported facts are more citable than generic prose.
4. **Structure Quality (25)** — headings, lists, TL;DR markers, and readable
   paragraph lengths that help a model locate and summarize the main point.

The scorer is a pure function with no network access. Callers may provide text
from live ``parse`` output, a Screaming Frog crawl, or a prepared excerpt.
"""

from __future__ import annotations

import re
from typing import Any

# Russian and English phrases that make a paragraph depend on surrounding context.
_CONTEXT_PHRASES = (
    "как упоминалось",
    "как сказано",
    "как отмечалось",
    "как говорилось",
    "см. выше",
    "смотри выше",
    "выше мы",
    "ранее мы",
    "как мы уже",
    "as mentioned",
    "as noted",
    "as discussed",
    "see above",
    "see below",
    "as we",
    "earlier we",
    "previously",
    "however",
    "thus",
    "therefore",
    "однако",
    "тем не менее",
    "следовательно",
    "таким образом",
    "итак",
)
# Pronoun-led or question-led openings indicate a non-self-contained answer.
_BAD_START_RE = re.compile(
    r"^(?:я|мы|ты|вы|он|она|оно|они|это|этот|эта|эти|тот|та|те|такой|здесь|там"
    r"|i|we|you|they|it|this|that|these|those|here|there|therefore|thus|so)\b",
    re.IGNORECASE | re.UNICODE,
)
_QUESTION_START_RE = re.compile(
    r"^(?:почему|зачем|как|что|кто|где|когда|какой|"
    r"why|how|what|who|where|when|which)\b.*\?\s*$",
    re.IGNORECASE | re.UNICODE,
)
# Numbers, percentages, years, dates, and versions.
_STAT_RE = re.compile(r"\d+(?:[.,]\d+)?\s?%|\b\d{4}\b|\b\d+(?:[.,]\d+)?\b", re.UNICODE)
# Russian and English evidence markers.
_EVIDENCE_RE = re.compile(
    r"согласно|исследован|исследованию|по данным|статистик|опрос|доказыва|"
    r"according to|research|study|studies|survey|data shows|evidence|report",
    re.IGNORECASE | re.UNICODE,
)
# First-party research is a strong positive signal.
_ORIGINAL_DATA_RE = re.compile(
    r"наш(?:его|е|и|а)? (?:исследован|опрос|анализ|тест|эксперимент|датасет|данные)|"  # noqa: RUF001 - Russian evidence pattern
    r"мы (?:проанализиров|опраш|провер|измер|собрал|выяснил)|"
    r"собственные данные|первичные данные|"
    r"our (?:study|survey|data|research|analysis|test|experiment|benchmark|dataset)|"
    r"we (?:analyz|surveyed|tested|measured|polled|found that|collected)|"
    r"internal data|proprietary data|first-party data",
    re.IGNORECASE | re.UNICODE,
)
# Unverifiable superlatives are a negative signal.
_SUPERLATIVE_RE = re.compile(
    r"(?:самый|наилучш|крупнейш|ведущ|номер один|№\s?1|эксперты утверждают|все знают|"
    r"studies show|research shows|experts agree|everyone knows)|"
    r"\b(?:the )?(?:best|fastest|most|largest|leading|number one|#1|cheapest)\b",
    re.IGNORECASE | re.UNICODE,
)
# Structural markers.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S|^\s{0,3}<h[1-6]\b", re.IGNORECASE)
_LIST_RE = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+\S", re.MULTILINE)
_TLDR_RE = re.compile(r"\btl;?dr\b|кратко|основное|главное|итог", re.IGNORECASE)

# --- M11: question heading followed by a direct 40-60 word answer ------------
# Russian and English question words at the beginning of a heading.
_Q_HEADING_WORD_RE = re.compile(
    r"^(?:как|что|почему|зачем|кто|где|когда|какой|сколько"
    r"|how|what|why|when|where|who|which|can|is)\b",
    re.IGNORECASE | re.UNICODE,
)
# Exclude declarative headings that start with question words but are not
# questions: Russian how-to forms with language-specific infinitive endings, English ``how to
# ...``, and ``what we/you/the ...`` constructions.
_DECLARATIVE_HEADING_RE = re.compile(
    r"^(?:как\s+\w*(?:ть|ти|чь)\b"
    r"|how\s+to\b"
    r"|what\s+(?:we|you|i|our|they|this|that|the)\b)",
    re.IGNORECASE | re.UNICODE,
)
# Markdown or HTML heading lines used to split text into heading-scoped blocks.
_HEADING_LINE_RE = re.compile(
    r"^[ \t]{0,3}(?:#{1,6}\s+\S|<h[1-6]\b)",
    re.MULTILINE | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_TARGET_BAND_MIN = 40
_TARGET_BAND_MAX = 60


def _paragraphs(text: str) -> list[str]:
    """Split text into non-empty paragraphs."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _word_count(s: str) -> int:
    return len(re.findall(r"\w+", s, re.UNICODE))


def _is_answer_block(para: str) -> bool:
    """Return whether a paragraph is a self-contained 20-200 word answer.

    Context-dependent transitions, question openings, and pronoun-led openings
    disqualify a paragraph because an isolated excerpt would be ambiguous.
    """
    wc = _word_count(para)
    if wc < 20 or wc > 200:
        return False
    low = para.lower()
    if any(phrase in low for phrase in _CONTEXT_PHRASES):
        return False
    first_line = para.split("\n", 1)[0]
    if _BAD_START_RE.match(first_line):
        return False
    return not _QUESTION_START_RE.match(first_line)


# --- M11 helpers: question heading followed by a direct answer ---------------


def _strip_tags(s: str) -> str:
    """Replace HTML tags with spaces."""
    return _TAG_RE.sub(" ", s)


def _split_heading_body(chunk: str) -> tuple[str | None, str]:
    """Separate a leading heading line from the rest of a text block.

    Returns ``(None, chunk)`` when the first line is not a Markdown or HTML
    heading. The body extends only to the next heading boundary established by
    the caller.
    """
    first_line, _, rest = chunk.partition("\n")
    line = first_line.strip()
    m = re.match(r"#{1,6}\s+(.+)", line)
    if m:
        heading = m.group(1).strip().rstrip("#").strip()
        return heading, rest
    m = re.match(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", line, re.IGNORECASE | re.DOTALL)
    if m:
        heading = _strip_tags(m.group(1)).strip()
        return heading, rest
    return None, chunk


def _heading_blocks(text: str) -> list[tuple[str | None, str]]:
    """Split text into ``(heading | None, body)`` blocks.

    Every Markdown or HTML heading begins a block whose body extends to the next
    heading.
    """
    starts = [m.start() for m in _HEADING_LINE_RE.finditer(text)]
    if not starts:
        return [(None, text)]
    blocks: list[tuple[str | None, str]] = []
    if starts[0] > 0:
        blocks.append((None, text[: starts[0]]))
    bounds = [*starts, len(text)]
    for i in range(len(starts)):
        chunk = text[bounds[i] : bounds[i + 1]]
        blocks.append(_split_heading_body(chunk))
    return blocks


def _is_question_heading(heading: str) -> bool:
    """Return whether a heading is a question rather than a declarative how-to.

    A question ends in ``?`` or begins with a recognized question word, but must
    not match the explicit Russian or English declarative exclusions.
    """
    h = heading.strip()
    if not h:
        return False
    if not (h.endswith("?") or _Q_HEADING_WORD_RE.match(h)):
        return False
    return not _DECLARATIVE_HEADING_RE.match(h)


def _first_answer_words(body: str) -> int:
    """Count words in the first non-empty paragraph after a heading.

    List items count as answer text, and HTML tags are removed before counting.
    """
    for para in _paragraphs(body):
        clean = _strip_tags(para).strip()
        if clean:
            return _word_count(clean)
    return 0


def _count_question_answers(text: str) -> dict[str, int]:
    """Measure question headings and their immediate answer paragraphs.

    ``questions_with_direct_answer`` counts headings followed by a non-empty
    answer paragraph. ``answers_in_target_band`` counts answers in the preferred
    40-60 word range.
    """
    question_headings = 0
    questions_with_direct_answer = 0
    answers_in_target_band = 0
    for heading, body in _heading_blocks(text):
        if heading is None or not _is_question_heading(heading):
            continue
        question_headings += 1
        answer_words = _first_answer_words(body)
        if answer_words > 0:
            questions_with_direct_answer += 1
            if _TARGET_BAND_MIN <= answer_words <= _TARGET_BAND_MAX:
                answers_in_target_band += 1
    return {
        "question_headings": question_headings,
        "questions_with_direct_answer": questions_with_direct_answer,
        "answers_in_target_band": answers_in_target_band,
    }


def score_citability(text: str) -> dict[str, Any]:
    """Score text citability from 0 to 100 without network access."""
    if not text or not text.strip():
        return {"ok": False, "error": "Text is empty"}

    paragraphs = _paragraphs(text)
    words = _word_count(text)
    if words == 0:
        return {"ok": False, "error": "No words are available for scoring"}

    qa = _count_question_answers(text)

    # 1. Answer Blocks: proportion of self-contained paragraphs.
    good = [p for p in paragraphs if _is_answer_block(p)]
    answer_blocks = round(25 * (len(good) / len(paragraphs)) if paragraphs else 0, 1)

    # 2. Self-Containment: penalize context-dependent phrases per paragraph.
    context_hits = sum(text.lower().count(p) for p in _CONTEXT_PHRASES)
    penalty = context_hits / max(len(paragraphs), 1)
    self_containment = round(25 * max(0.0, 1.0 - penalty), 1)

    # 3. Statistical Density: numeric and evidence markers per 100 words.
    stats = len(_STAT_RE.findall(text))
    evidence = len(_EVIDENCE_RE.findall(text))
    per_100 = (stats + evidence) / words * 100
    # Eight fact markers per 100 words is already dense; cap values above it.
    statistical_density = round(min(25.0, per_100 / 8 * 25), 1)
    original_data = bool(_ORIGINAL_DATA_RE.search(text))
    # First-party data strengthens numeric claims, so add a capped density bonus.
    if original_data and statistical_density > 0:
        statistical_density = round(min(25.0, statistical_density + 3.0), 1)

    # 4. Structure Quality: headings, lists, TL;DR markers, and paragraph length.
    structure = 0.0
    if _HEADING_RE.search(text):
        structure += 8
    if _LIST_RE.search(text):
        structure += 7
    if _TLDR_RE.search(text):
        structure += 5
    # A median paragraph length of 30-150 words earns five additional points.
    if paragraphs:
        wcs = sorted(_word_count(p) for p in paragraphs)
        median = wcs[len(wcs) // 2]
        if 30 <= median <= 150:
            structure += 5
    # M11: reward at least one question followed by a direct 40-60 word answer.
    if qa["answers_in_target_band"] >= 1:
        structure += 5
    structure_quality = round(min(25.0, structure), 1)

    total = round(answer_blocks + self_containment + statistical_density + structure_quality, 1)
    grade = (
        "A"
        if total >= 75
        else "B"
        if total >= 55
        else "C"
        if total >= 35
        else "D"
        if total >= 15
        else "F"
    )

    return {
        "ok": True,
        "score": total,
        "grade": grade,
        "word_count": words,
        "paragraphs": len(paragraphs),
        "dimensions": {
            "answer_blocks": answer_blocks,
            "self_containment": self_containment,
            "statistical_density": statistical_density,
            "structure_quality": structure_quality,
        },
        "signals": {
            "answer_block_paragraphs": len(good),
            "context_phrase_hits": context_hits,
            "statistical_markers": stats,
            "evidence_markers": evidence,
            "original_data": original_data,  # First-party research or measurement.
            "superlative_claims": len(_SUPERLATIVE_RE.findall(text)),  # Unsupported claims.
            "has_headings": bool(_HEADING_RE.search(text)),
            "has_lists": bool(_LIST_RE.search(text)),
            "has_tldr": bool(_TLDR_RE.search(text)),
            # M11: question heading followed by a direct 40-60 word answer.
            "question_headings": qa["question_headings"],
            "questions_with_direct_answer": qa["questions_with_direct_answer"],
            "answers_in_target_band": qa["answers_in_target_band"],
        },
    }
