"""Money written the way people outside the US write it.

The old pattern assumed the symbol precedes the amount and that amounts have no
thousands separator. Both are false across ru/by/eu pages, and the failure was
not a clean miss: scanned across a page of prices it matched the tail of one and
the head of the next and returned a number printed nowhere on the page. That
number then became an Offer in a suggested graph.
"""

# ruff: noqa: RUF001 - The test data is real localized text, including no-break and
# narrow no-break spaces and non-Latin currency words. Swapping in the ASCII
# lookalikes would test something else.
from __future__ import annotations

import pytest

from seohead.checks.page_facts import extract
from seohead.checks.page_type import classify
from seohead.checks.price import parse_amount, parse_price

# The table from the issue, plus the separators that arrive with copied text.
WRITTEN = [
    ("Стол 19 900 ₽", 19900.0, "RUB"),
    ("Стол 19900 ₽", 19900.0, "RUB"),
    ("Стол 19 900 руб.", 19900.0, "RUB"),
    ("Кухня 1 500 000 ₽", 1500000.0, "RUB"),
    ("Table 19 900 €", 19900.0, "EUR"),
    ("Table $19,900", 19900.0, "USD"),
    ("Стол 19 900 ₽", 19900.0, "RUB"),  # no-break space
    ("Стол 19 900 ₽", 19900.0, "RUB"),  # narrow no-break space
    ("Cena 1 200 zł", 1200.0, "PLN"),
    ("Cena 500 Kč", 500.0, "CZK"),
    ("12'500 CHF", 12500.0, "CHF"),
    ("1.234.567 ₽", 1234567.0, "RUB"),
]

# US format, which worked before and must keep working.
US_FORMAT = [("$19.99", 19.99), ("USD 19.99", 19.99), ("$1,299.50", 1299.5)]


@pytest.mark.parametrize(("text", "value", "currency"), WRITTEN)
def test_the_amount_and_currency_are_read_correctly(text, value, currency):
    found = parse_price(text)
    assert found is not None, f"no price found in {text!r}"
    assert found["value"] == value
    assert found["currency"] == currency


@pytest.mark.parametrize(("text", "value"), US_FORMAT)
def test_us_format_is_unchanged(text, value):
    assert parse_price(text)["value"] == value


def test_a_marker_that_names_no_single_currency_is_still_money():
    # "kr" is Swedish, Norwegian, Danish or Icelandic: an amount, no claim.
    found = parse_price("99 kr")
    assert found["value"] == 99.0
    assert found["currency"] is None


def test_text_without_a_currency_is_not_a_price():
    assert parse_price("Артикул 19900") is None
    assert parse_price("Цена по запросу") is None


@pytest.mark.parametrize("raw", ["1234,5678", "12,34,56", "1234 5678"])
def test_an_amount_nobody_wrote_on_purpose_is_rejected(raw):
    assert parse_amount(raw) is None


# --- the stitching failure --------------------------------------------------
LISTING = (
    "<html><body><h1>Столы</h1>"
    '<div class="price">19 900 ₽</div>'
    '<div class="price">23 000 ₽</div>'
    '<div class="price">14 800 ₽</div>'
    "</body></html>"
)


def test_a_listing_never_returns_a_price_stitched_from_two():
    price = extract(LISTING, "https://example.com/stoly/")["price"]
    # The old regex returned "₽ 23" here: the tail of one price, the head of
    # the next, presented as a finding.
    assert price["value"] == 19900.0
    assert price["raw"] == "19 900 ₽"


def test_the_price_is_normalized_not_the_matched_string():
    price = extract(LISTING, "https://example.com/stoly/")["price"]
    assert isinstance(price["value"], float)
    assert price["currency"] == "RUB"
    assert price["heuristic"] is True


def test_a_declared_price_is_normalized_too():
    html = (
        '<html><body><div itemscope><span itemprop="price">19900</span>'
        '<span itemprop="priceCurrency">RUB</span></div></body></html>'
    )
    price = extract(html, "https://example.com/p")["price"]
    assert price["value"] == 19900.0
    assert price["currency"] == "RUB"
    assert price["heuristic"] is False


def test_a_declared_price_that_does_not_parse_is_kept_verbatim():
    # A stated fact stands even when it is not a number.
    html = '<html><body><div itemscope><span itemprop="price">по запросу</span></div></body></html>'
    assert extract(html, "https://example.com/p")["price"]["value"] == "по запросу"


# --- classification no longer hinges on the URL slug ------------------------
def _catalogue(items: int = 6) -> str:
    cards = "".join(
        f'<div class="card"><a href="/stol-{i}">Стол {i}</a>'
        f'<img src="/i{i}.jpg"><div class="price">1{i} 900 ₽</div>'
        f"<button>В корзину</button></div>"
        for i in range(1, items + 1)
    )
    return f"<html><body><h1>Столы под старину</h1>{cards}</body></html>"


def test_a_listing_with_no_markup_and_no_latin_slug_is_a_collection():
    url = "https://example.com/stolyi-pod-starinu/"
    result = classify(url, extract(_catalogue(), url))
    assert result["inferred_type"] == "CollectionPage"
    assert result["confidence"] in ("mid", "high")


def test_the_listing_signal_names_what_it_counted():
    url = "https://example.com/stolyi-pod-starinu/"
    result = classify(url, extract(_catalogue(), url))
    assert any("6 linked, priced items" in s["reason"] for s in result["signals"])


def test_a_single_item_with_a_buy_button_is_a_product():
    html = (
        "<html><body><h1>Стол дубовый</h1><img src=/1.jpg><img src=/2.jpg>"
        "<img src=/3.jpg><img src=/4.jpg>"
        "<table><tr><td>Материал</td><td>Дуб</td></tr>"
        "<tr><td>Ширина</td><td>120</td></tr><tr><td>Высота</td><td>75</td></tr></table>"
        '<div class="price">39 900 ₽</div><button>Купить</button></body></html>'
    )
    url = "https://example.com/stol-dubovyi/"
    result = classify(url, extract(html, url))
    assert result["inferred_type"] == "Product"


def test_structure_is_measured_even_without_prices():
    html = '<html><body><table><tr><td>a</td></tr></table><img src="/x.jpg"></body></html>'
    structure = extract(html, "https://example.com/")["structure"]
    assert structure["priced_items"] == 0
    assert structure["images"] == 1
    assert structure["spec_rows"] == 1


# --- qualified symbols must not be recoded to a bare-symbol currency -------
QUALIFIED = [
    ("CA$ 1,299.50", 1299.5, "CAD"),
    ("AU$ 1,299.50", 1299.5, "AUD"),
    ("HK$ 1,299.50", 1299.5, "HKD"),
    ("CN¥ 1,299.50", 1299.5, "CNY"),
]


@pytest.mark.parametrize(("text", "value", "currency"), QUALIFIED)
def test_a_qualified_symbol_resolves_to_its_own_currency_not_the_bare_symbols(
    text, value, currency
):
    # Before the fix, "CA$"/"AU$"/"HK$" matched on their inner bare "$" and
    # came back USD, and "CN¥" matched on its inner "¥" and came back JPY.
    found = parse_price(text)
    assert found is not None, f"no price found in {text!r}"
    assert found["value"] == value
    assert found["currency"] == currency


def test_schema_build_publishes_the_qualified_currency_not_a_recoded_one():
    from seohead.checks.schema_build import build_schema

    url = "https://shop.example.com/product/widget"
    html = "<html><body><h1>Widget</h1><p>CA$ 1,299.50</p></body></html>"
    result = build_schema(url=url, html=html)
    webpage = next(
        node for node in result["suggested_graph"]["@graph"] if node.get("@id") == "#webpage"
    )
    assert webpage["offers"]["priceCurrency"] == "CAD"


def test_an_unrecognised_qualified_symbol_is_no_price_not_a_guessed_one():
    # "SG$" names no currency this table resolves unambiguously; guessing USD
    # from the inner bare "$" would be a wrong finding, which is worse than none.
    assert parse_price("SG$100") is None


def test_bare_dollar_and_yen_are_unaffected_by_the_qualifier_guard():
    assert parse_price("$19.99")["currency"] == "USD"
    assert parse_price("¥100")["currency"] == "JPY"
    assert parse_price("R$ 100")["currency"] == "BRL"
