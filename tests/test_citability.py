"""Offline tests for the content citability scorer."""

from seohead.checks import citability as C

# A highly citable passage with self-contained paragraphs, evidence, and structure.
GOOD = """## How caching works

Caching stores a copy of a server response and serves it again without querying
the database. According to a 2024 study, enabling caching reduces response time
by 60% and cuts database load by a factor of three.

TL;DR: caching makes a site faster and reduces server load.

## When caching causes problems

- Frequently updated data may leave users seeing stale content.
- According to a Cloudflare report, 12% of sites lose dynamic data because
  their TTL is too long.
"""

# Russian is intentional: this fixture verifies localized context-phrase detection.
WATER = """Однако, как упоминалось выше, это важно. Тем не менее стоит учесть.

Почему мы это обсуждаем? Это означает, что нужно подумать. Таким образом, выходит
определённый результат, который, однако, зависит от контекста, упомянутого ранее.
"""


def test_good_content_scores_high():
    r = C.score_citability(GOOD)
    assert r["ok"] is True
    assert r["score"] >= 55
    assert r["dimensions"]["answer_blocks"] > 0
    assert r["dimensions"]["statistical_density"] > 0
    assert r["dimensions"]["structure_quality"] >= 8  # Headings, lists, and TL;DR.


def test_empty_returns_error():
    assert C.score_citability("")["ok"] is False
    assert C.score_citability("   ")["ok"] is False


def test_water_has_low_self_containment_and_density():
    r = C.score_citability(WATER)
    assert r["ok"] is True
    # Numerous context-dependent phrases reduce self-containment.
    assert r["dimensions"]["self_containment"] < 15
    # Almost no numbers or evidence markers means low statistical density.
    assert r["dimensions"]["statistical_density"] < 10
    assert r["signals"]["context_phrase_hits"] >= 3


def test_dimensions_sum_equals_score():
    r = C.score_citability(GOOD)
    d = r["dimensions"]
    assert (
        abs(
            r["score"]
            - (
                d["answer_blocks"]
                + d["self_containment"]
                + d["statistical_density"]
                + d["structure_quality"]
            )
        )
        < 0.2
    )


def test_grade_thresholds():
    assert C.score_citability(GOOD)["grade"] in ("A", "B")
    assert C.score_citability(WATER)["grade"] in ("C", "D", "F")


def test_is_answer_block_filters_short_and_contextual():
    assert (
        C._is_answer_block(
            "Caching stores the server response and serves a copy for repeated requests "
            "without querying the database, reducing load several times according to tests."
        )
        is True
    )
    assert C._is_answer_block("too short") is False  # Fewer than 20 words.
    assert (
        C._is_answer_block(
            "However, as mentioned above, this matters for understanding the complete "
            "context of the article and the broader discussion around it."
        )
        is False
    )


def test_original_data_signal_detected():
    text = (
        "In our 2024 study, we analyzed 500 sites and found that caching improved "
        "response time by 60 percent according to the data we collected."
    )
    r = C.score_citability(text)
    assert r["signals"]["original_data"] is True


def test_superlative_claims_counted_as_negative():
    text = (
        "This is the best service on the market and the number one choice according "
        "to experts. It is the fastest solution, and everyone knows it."
    )
    r = C.score_citability(text)
    assert r["signals"]["superlative_claims"] >= 2


# --- M11: question heading followed by a direct 40-60-word answer ---


def _answer(n: int) -> str:
    """Return exactly ``n`` words so word-count assertions remain deterministic."""
    return " ".join(["answer"] * n)


def test_question_heading_with_direct_answer_in_target_band():
    text = (
        "## Which plan should I choose?\n\n"
        + _answer(50)
        + "\n\n## About the company\n\n"
        + _answer(10)
    )
    r = C.score_citability(text)
    sig = r["signals"]
    assert sig["question_headings"] == 1
    assert sig["questions_with_direct_answer"] == 1
    assert sig["answers_in_target_band"] == 1
    # HTML headings are recognized as well.
    r2 = C.score_citability("<h2>Where to host the app?</h2>\n\n" + _answer(45))
    assert r2["signals"]["question_headings"] == 1
    assert r2["signals"]["answers_in_target_band"] == 1


def test_declarative_howto_not_counted_as_question():
    # Russian is intentional: the localized infinitive rule distinguishes a how-to
    # heading from a question heading.
    ru = "## Как настроить кэш\n\n" + _answer(50)
    assert C.score_citability(ru)["signals"]["question_headings"] == 0
    # English "how to" and "what we/the" headings are declarative, not questions.
    en1 = "## How to configure cache\n\n" + _answer(50)
    en2 = "## What we offer\n\n" + _answer(50)
    assert C.score_citability(en1)["signals"]["question_headings"] == 0
    assert C.score_citability(en2)["signals"]["question_headings"] == 0
    # The localized heading means “How does caching work?” and is a question, not an infinitive.
    real_q = "## Как работает кэш\n\n" + _answer(50)
    assert C.score_citability(real_q)["signals"]["question_headings"] == 1


def test_question_without_answer_not_counted():
    # A second heading immediately after the question means there is no answer.
    text = "## Why does speed matter?\n\n## Another topic\n\n" + _answer(40)
    sig = C.score_citability(text)["signals"]
    assert sig["question_headings"] == 1
    assert sig["questions_with_direct_answer"] == 0
    assert sig["answers_in_target_band"] == 0
    # A 25-word direct answer exists but falls outside the target band.
    text2 = "## Why is SSL necessary?\n\n" + _answer(25)
    sig2 = C.score_citability(text2)["signals"]
    assert sig2["questions_with_direct_answer"] == 1
    assert sig2["answers_in_target_band"] == 0
