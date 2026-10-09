"""Money written the way people write it.

Currency detection is a parsing problem, not a pattern-matching one. The symbol
sits before the amount in some places and after it in others, thousands are
grouped with a space, a dot, a comma or an apostrophe depending on the country,
and the same comma means "thousands" in one convention and "decimal point" in
another. A regex that assumes one arrangement does not merely miss the others:
run across a page of prices it matches the tail of one and the head of the
next, and returns a number that appears nowhere on the page.

A wrong price is worse than no price. It becomes an ``Offer`` in a suggested
graph and ships to a client's structured data, so everything ambiguous here
resolves to None.
"""

# ruff: noqa: RUF001 - The characters this module parses are exactly the ones that
# look like ASCII and are not: no-break and narrow no-break spaces between
# thousands, Cyrillic currency words, a typographic apostrophe. Replacing them
# with their lookalikes would delete the thing being parsed.
from __future__ import annotations

import re
from typing import Any

# The twenty currencies this toolkit's audiences actually price in, plus the
# ISO code each marker resolves to. A marker that cannot name one currency —
# a bare "kr" is Swedish, Norwegian, Danish or Icelandic — is recognised as
# money without claiming which.
CURRENCY_MARKERS: tuple[tuple[str, str | None], ...] = (
    # Multi-character first: "R$" must win over "$", "Kč" over "K".
    ("руб.", "RUB"),
    ("руб", "RUB"),
    ("рублей", "RUB"),
    ("р.", "RUB"),
    ("у.е.", None),
    ("R$", "BRL"),
    ("CA$", "CAD"),
    ("AU$", "AUD"),
    ("HK$", "HKD"),
    ("CN¥", "CNY"),
    ("Kč", "CZK"),
    ("zł", "PLN"),
    ("CHF", "CHF"),
    ("Br", "BYN"),
    ("kr", None),
    ("Fr", None),
    ("₽", "RUB"),
    ("$", "USD"),
    ("€", "EUR"),
    ("£", "GBP"),
    ("¥", "JPY"),
    ("₴", "UAH"),
    ("₸", "KZT"),
    ("₺", "TRY"),
    ("₹", "INR"),
    ("₩", "KRW"),
    ("₫", "VND"),
    ("₪", "ILS"),
    ("USD", "USD"),
    ("EUR", "EUR"),
    ("GBP", "GBP"),
    ("RUB", "RUB"),
    ("BYN", "BYN"),
    ("UAH", "UAH"),
    ("KZT", "KZT"),
    ("PLN", "PLN"),
    ("CZK", "CZK"),
    ("SEK", "SEK"),
    ("NOK", "NOK"),
    ("DKK", "DKK"),
    ("TRY", "TRY"),
    ("JPY", "JPY"),
    ("CNY", "CNY"),
    ("INR", "INR"),
    ("BRL", "BRL"),
    ("CAD", "CAD"),
    ("AUD", "AUD"),
)

# Every space that appears between thousands in the wild: ordinary, no-break,
# narrow no-break and thin. Copy-pasted prices carry all four.
_GROUP_SPACES = "    "
_GROUP_SEPARATORS = _GROUP_SPACES + ".,'’"


def _marker_pattern(marker: str) -> str:
    """Escape one marker, blocking a bare $ or ¥ from matching inside a qualified symbol.

    "CA$", "AU$", "HK$" and "CN¥" are their own listed markers and win on their own, since
    ``finditer`` returns the leftmost successful match first. Without this guard, a country
    prefix this table does not recognise (any other "XX$") would still let the bare symbol
    match starting one character later, silently recoding an unknown currency as USD or JPY.
    """
    escaped = re.escape(marker)
    return rf"(?<![A-Za-z]){escaped}" if marker in ("$", "¥") else escaped


_MARKER_ALTERNATION = "|".join(
    _marker_pattern(marker) for marker, _ in sorted(CURRENCY_MARKERS, key=lambda m: -len(m[0]))
)
_AMOUNT = rf"\d{{1,3}}(?:[{re.escape(_GROUP_SEPARATORS)}]\d{{1,3}})*|\d+"

# The marker binds to the amount on one side or the other, and the pair must be
# adjacent: allowing anything between them is what let a match span two prices.
PRICE_RE = re.compile(
    rf"(?P<pre>{_MARKER_ALTERNATION})\s?(?P<pre_amount>{_AMOUNT})"
    rf"|(?P<post_amount>{_AMOUNT})\s?(?P<post>{_MARKER_ALTERNATION})",
    re.IGNORECASE,
)


def _resolve_currency(marker: str) -> str | None:
    folded = marker.casefold()
    for candidate, code in CURRENCY_MARKERS:
        if candidate.casefold() == folded:
            return code
    return None


def parse_amount(raw: str) -> float | None:
    """Turn a written amount into a number, or None when it is ambiguous.

    Separators are read as a group: the last one decides whether it introduced
    a decimal fraction or another thousand, and every earlier group has to be
    exactly three digits or the string is not a number anyone wrote on purpose.
    """
    runs = re.split(rf"[{re.escape(_GROUP_SEPARATORS)}]", raw)
    if not runs or any(not run.isdigit() for run in runs):
        return None
    if len(runs) == 1:
        return float(runs[0])
    if len(runs[0]) > 3:
        return None

    last_separator = raw[len(raw) - len(runs[-1]) - 1]
    tail = runs[-1]
    # A decimal fraction is one or two digits after a dot or a comma. Three
    # digits is a thousands group, whichever character precedes it, which is
    # what makes both 19,900 and 19.900 nineteen thousand nine hundred.
    is_decimal = last_separator in ".," and len(tail) <= 2
    groups = runs[:-1] if is_decimal else runs
    if any(len(run) != 3 for run in groups[1:]):
        return None
    whole = "".join(groups)
    return float(f"{whole}.{tail}") if is_decimal else float(whole)


def parse_price(text: str) -> dict[str, Any] | None:
    """Find one price in a single run of text.

    ``text`` must be one text node. Joined page text is what produced values
    stitched from two neighbouring prices, so the caller keeps them apart.
    """
    for match in PRICE_RE.finditer(text or ""):
        marker = match.group("pre") or match.group("post")
        amount = match.group("pre_amount") or match.group("post_amount")
        value = parse_amount(amount)
        if value is None:
            continue
        return {
            "value": value,
            "currency": _resolve_currency(marker),
            "raw": match.group(0).strip(),
        }
    return None
