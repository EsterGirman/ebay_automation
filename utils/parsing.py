"""Pure logic (no browser): price + currency parsing and result-card filtering. Unit-tested."""
import re
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

# Currency symbols as eBay shows them -> ISO code. Order matters: "US $" before "$"
_CURRENCY_CODES = {
    "US $": "USD", "USD": "USD", "$": "USD",
    "ILS": "ILS", "NIS": "ILS", "₪": "ILS",
    "EUR": "EUR", "€": "EUR",
    "GBP": "GBP", "£": "GBP",
}
_CUR = r"(US\s?\$|USD|\$|ILS|NIS|₪|EUR|€|GBP|£)"
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
# "ILS 1,234.50 to ILS 2,000.00" is a range; the currency comes from the first value
_PRICE_RE = re.compile(_CUR + r"\s?" + _NUM + r"(?:\s*to\s*" + _CUR + r"?\s?" + _NUM + r")?",
                       re.IGNORECASE)

FAKE_ITEM_MARKER = "/itm/123456"   # fake "Shop on eBay" item shown at the top of the results


class Money(NamedTuple):
    amount: float
    currency: str


def _currency_code(symbol: str) -> str:
    return _CURRENCY_CODES[re.sub(r"\s+", " ", symbol.upper().replace("US$", "US $"))]


def parse_money(text: Optional[str]) -> Optional[Money]:
    """Return amount and currency from the text (for a range - the higher value), or None if there is no price."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    cur, low, _, high = m.groups()
    amount = max(float(v.replace(",", "")) for v in (low, high) if v)
    return Money(amount, _currency_code(cur))


def parse_price(text: Optional[str]) -> Optional[float]:
    money = parse_money(text)
    return money.amount if money else None


def parse_cart_summary(text: str) -> Dict[str, Money]:
    """Parse the cart's 'Order summary': items (items price), shipping, subtotal (incl. shipping).
    Search after the 'Order summary' heading so 'Shipping' isn't taken from the item cards."""
    start = text.lower().find("order summary")
    text = text[start:] if start >= 0 else text
    summary: Dict[str, Money] = {}
    for key, label in (("items", r"Items\s*\(\d+\)"), ("shipping", r"Shipping"),
                       ("subtotal", r"Subtotal")):
        m = re.search(label, text, re.IGNORECASE)
        # Value on the same line or the next one, so 'Free' doesn't "borrow" the next line's amount
        value = "\n".join(text[m.end():].split("\n")[:2]) if m else ""
        money = parse_money(value)
        if money:
            summary[key] = money
    return summary


def parse_cart_item_count(text: str) -> Optional[int]:
    """Item count from 'Items (n)' in the Order summary."""
    start = text.lower().find("order summary")
    m = re.search(r"Items\s*\((\d+)\)", text[start:] if start >= 0 else text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def item_id(url: str) -> Optional[str]:
    """eBay item ID from a URL (/itm/<id> or /itm/<slug>/<id>)."""
    m = re.search(r"/itm/(?:[^/?#]+/)?(\d{9,})", url or "")
    return m.group(1) if m else None


def filter_cards(cards: List[Dict], max_price: float, limit: int, currency: str = "USD",
                 already: Iterable[str] = ()) -> Tuple[List[str], Dict]:
    """Take cards [{href, price, text}] and return (up to limit URLs within budget, stats).
    A card in a currency other than `currency` is skipped: 100 ILS can't be compared with 100 USD."""
    seen = set(already)
    urls: List[str] = []
    stats = {"cards": len(cards), "no_link": 0, "no_price": 0, "other_currency": 0,
             "over_budget": 0, "duplicate": 0}
    for card in cards:
        if len(urls) >= limit:
            break
        href = (card.get("href") or "").strip()
        if not href or FAKE_ITEM_MARKER in href:
            stats["no_link"] += 1
            continue
        money = parse_money(card.get("price")) or parse_money(card.get("text"))
        if money is None:
            stats["no_price"] += 1
            continue
        if money.currency != currency.upper():
            stats["other_currency"] += 1
            continue
        if money.amount > max_price:
            stats["over_budget"] += 1
            continue
        url = href.split("?")[0]
        if url in seen:
            stats["duplicate"] += 1
            continue
        seen.add(url)
        urls.append(url)
    stats["collected"] = len(urls)
    return urls, stats
