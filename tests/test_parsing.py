import pytest

from utils.parsing import (Money, filter_cards, item_id, parse_cart_item_count, parse_cart_summary,
                           parse_money, parse_price)


# Amount and currency parsing
@pytest.mark.parametrize("text, expected", [
    ("ILS 257.15\nor Best Offer\n+ILS 333.21 delivery", Money(257.15, "ILS")),
    ("ILS 1,234.50 to ILS 2,000.00", Money(2000.0, "ILS")),
    ("US $45.99", Money(45.99, "USD")),
    ("US$45.99", Money(45.99, "USD")),
    ("$10.00 to $20.00", Money(20.0, "USD")),
    ("₪ 99.90", Money(99.9, "ILS")),
    ("EUR 12.00", Money(12.0, "EUR")),
    ("Subtotal (3 items)\nUS $123.45", Money(123.45, "USD")),
    ("Free delivery", None),
    ("", None),
    (None, None),
])
def test_parse_money(text, expected):
    assert parse_money(text) == expected


# Amount only
def test_parse_price_returns_amount_only():
    assert parse_price("US $45.99") == 45.99 and parse_price("no price") is None


# Build a fake result card
def _card(i, price):
    return {"href": f"https://www.ebay.com/itm/{i}?hash=x", "price": price, "text": ""}


# Budget and limit
def test_filter_respects_budget_and_limit():
    cards = [_card(1, "$100.00"), _card(2, "$5,000.00"), _card(3, "$200.00"), _card(4, "$150.00")]
    urls, _ = filter_cards(cards, max_price=220, limit=2)
    assert urls == ["https://www.ebay.com/itm/1", "https://www.ebay.com/itm/3"]


# Price == max is included
def test_filter_price_equal_to_max_is_included():
    urls, _ = filter_cards([_card(1, "$220.00")], max_price=220, limit=5)
    assert urls == ["https://www.ebay.com/itm/1"]


# Other currency skipped
def test_filter_skips_other_currency():
    cards = [_card(1, "ILS 50.00"), _card(2, "$50.00")]
    urls, stats = filter_cards(cards, max_price=220, limit=5, currency="USD")
    assert urls == ["https://www.ebay.com/itm/2"] and stats["other_currency"] == 1


# Fewer than limit is valid
def test_filter_returns_fewer_when_not_enough_items():
    urls, stats = filter_cards([_card(1, "$100.00"), _card(2, "$9,999.00")],
                               max_price=220, limit=5)
    assert len(urls) == 1 and stats["over_budget"] == 1


# Empty input
def test_filter_empty_input_is_valid():
    urls, stats = filter_cards([], max_price=220, limit=5)
    assert urls == [] and stats["cards"] == 0


# Fake item and duplicates
def test_filter_skips_fake_item_and_duplicates():
    cards = [_card(123456, "$20.00"), _card(7, "$50.00"), _card(7, "$50.00")]
    urls, stats = filter_cards(cards, max_price=220, limit=5)
    assert urls == ["https://www.ebay.com/itm/7"] and stats["duplicate"] == 1


# Items from earlier pages
def test_filter_skips_items_already_collected_on_previous_pages():
    urls, stats = filter_cards([_card(7, "$50.00")], max_price=220, limit=5,
                               already=["https://www.ebay.com/itm/7"])
    assert urls == [] and stats["duplicate"] == 1


# Price from card text
def test_filter_uses_card_text_when_price_field_empty():
    cards = [{"href": "https://www.ebay.com/itm/9", "price": "", "text": "Shoes\n$80.00"}]
    urls, _ = filter_cards(cards, max_price=100, limit=5)
    assert urls == ["https://www.ebay.com/itm/9"]


CART_TEXT = ("Standard International Shipping\nRemove\nOrder summary\nItems (5)\nILS 605.30\n"
             "Shipping\nILS 381.92\nSubtotal\nILS 987.22\nGo to checkout")


# Items / shipping / subtotal
def test_cart_summary_separates_items_shipping_and_subtotal():
    summary = parse_cart_summary(CART_TEXT)
    assert summary == {"items": Money(605.30, "ILS"), "shipping": Money(381.92, "ILS"),
                       "subtotal": Money(987.22, "ILS")}


# Free shipping
def test_cart_summary_free_shipping_is_not_mistaken_for_subtotal():
    summary = parse_cart_summary("Order summary\nItems (1)\nUS $50.00\nShipping\nFree\n"
                                 "Subtotal\nUS $50.00")
    assert "shipping" not in summary
    assert summary["items"] == summary["subtotal"] == Money(50.0, "USD")


# Item ID from URL
@pytest.mark.parametrize("url, expected", [
    ("https://www.ebay.com/itm/315635216467", "315635216467"),
    ("https://www.ebay.com/itm/315635216467?var=0&hash=x", "315635216467"),
    ("https://www.ebay.com/itm/Nike-Air-Max-90/315635216467", "315635216467"),
    ("https://www.ebay.com/sch/i.html?_nkw=shoes", None),
    ("", None),
])
def test_item_id_from_url(url, expected):
    assert item_id(url) == expected


# Item count from summary
def test_cart_item_count_reads_order_summary_only():
    assert parse_cart_item_count(CART_TEXT) == 5
    assert parse_cart_item_count("Recommended Items (12)\nOrder summary\nItems (3)\nILS 9.00") == 3
    assert parse_cart_item_count("Your cart is empty") is None
