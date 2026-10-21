import re
from typing import Dict, List
from urllib.parse import quote_plus

import allure
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from utils.config import REPORTS_DIR
from utils.parsing import FAKE_ITEM_MARKER, filter_cards
from .base_page import BasePage, SiteBlockedError


class SearchResultsPage(BasePage):
    SEARCH_URL = "https://www.ebay.com/sch/i.html?_nkw={query}&LH_BIN=1&_ipg=60"

    ITEM_CONTAINER = "//li[.//a[contains(@href, '/itm/')]]"
    ITEM_PRICE = ".//*[contains(@class, 's-card__price')] | .//span[contains(@class, 'price')]"
    ITEM_LINK = ".//a[contains(@href, '/itm/')]"
    NEXT_PAGE = ("//a[@type='next'] | //a[@aria-label='Go to next search page'] | "
                 "//a[contains(@class, 'pagination__next')]")
    MAX_PRICE_INPUT = ("//div[contains(@class, 'textrange')]"  # hidden slider has the same label
                       "//input[@type='text' and starts-with(@placeholder, 'Max')]")
    PRICE_SUBMIT = "//button[@title='Submit price range' or @aria-label='Submit price range']"

    JS_EXTRACT = """(els, xp) => els.map(li => {
        const first = (path) => document.evaluate(path, li, null,
            XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
        const a = first(xp.link);
        const p = first(xp.price);
        return {href: a ? a.href : '', price: p ? p.innerText : '', text: li.innerText || ''};
    })"""

    # Init per-page stats
    def __init__(self, page: Page):
        super().__init__(page)
        self.last_stats: Dict = {}

    # Search and apply price filter
    @allure.step("חיפוש: {query} עד מחיר {max_price}")
    def search(self, query: str, max_price: float) -> None:
        url = self.SEARCH_URL.format(query=quote_plus(query))
        self.navigate(url)
        if not self._apply_price_filter_ui(max_price):
            self.logger.warning("Price filter UI not available - applying it via URL (_udhi)")
            self.navigate(f"{url}&_udhi={self._format_price(max_price)}")  # fallback: same filter via URL

    # 220.0 -> '220'
    @staticmethod
    def _format_price(price: float) -> str:
        return str(int(price)) if float(price).is_integer() else str(price)

    # Use the on-page price filter
    def _apply_price_filter_ui(self, max_price: float, attempts: int = 2) -> bool:
        for attempt in range(1, attempts + 1):
            if not self.is_visible_within(self.MAX_PRICE_INPUT, timeout=15000):
                return False
            try:
                if not self._type_max_price(max_price):
                    return False
                self.page.locator(self.PRICE_SUBMIT).first.click()
                if self._wait_for_filter_applied(timeout_ms=20000):
                    self.logger.info("Price filter applied via page UI")
                    return True
                self.logger.info(f"Price filter not applied (attempt {attempt}/{attempts})")
            except PlaywrightTimeout as e:
                self.logger.warning(f"Price filter UI did not respond: {str(e).splitlines()[0]}")
        return False

    # Type max price until accepted
    def _type_max_price(self, max_price: float) -> bool:
        max_input = self.page.locator(self.MAX_PRICE_INPUT).first
        submit = self.page.locator(self.PRICE_SUBMIT).first
        max_input.scroll_into_view_if_needed()
        for _ in range(3):
            max_input.click()
            max_input.press("Control+A")
            max_input.press("Backspace")
            max_input.press_sequentially(self._format_price(max_price), delay=100)  # component ignores fill()
            if self._wait_until_enabled(submit, timeout_ms=3000):
                return True
        return False

    # Wait for filter in URL
    def _wait_for_filter_applied(self, timeout_ms: int) -> bool:
        for _ in range(timeout_ms // 500):
            if re.search(r"[?&]_udhi=", self.page.url):
                self.page.wait_for_load_state("domcontentloaded")
                return True
            if self.confirm_ship_to_if_shown():
                return False
            self.page.wait_for_timeout(500)
        return False

    # Wait for button to enable
    def _wait_until_enabled(self, button, timeout_ms: int) -> bool:
        try:
            self.page.wait_for_function("b => !b.disabled", arg=button.element_handle(),
                                        timeout=timeout_ms)
            return True
        except PlaywrightTimeout:
            return False

    # Read all cards via XPath
    def _extract(self) -> List[Dict]:
        args = {"link": self.ITEM_LINK, "price": self.ITEM_PRICE}
        return self.page.locator(self.ITEM_CONTAINER).evaluate_all(self.JS_EXTRACT, args)

    # Count real product cards
    @staticmethod
    def _real_cards(cards: List[Dict]) -> int:
        return sum(1 for c in cards if c.get("href") and FAKE_ITEM_MARKER not in c["href"])

    # Read cards, reload if partial
    def _read_cards(self, min_cards: int = 5, max_reloads: int = 2) -> List[Dict]:
        cards: List[Dict] = []
        for attempt in range(max_reloads + 1):
            if self.is_blocked():
                self.take_screenshot("blocked_on_results")
                raise SiteBlockedError(
                    "eBay shows a block/error page instead of results. "
                    "Refreshing will not help - wait (minutes to hours) and run again.")
            try:
                self.page.wait_for_selector(self.ITEM_CONTAINER, state="attached", timeout=15000)
            except PlaywrightTimeout:
                pass
            for _ in range(10):
                self.page.mouse.wheel(0, 2500)
                self.page.wait_for_timeout(300)
                self.page.mouse.wheel(0, -20000)
                cards = self._extract()
                if self._real_cards(cards) >= min_cards:
                    return cards
                self.page.wait_for_timeout(1700)
            if attempt < max_reloads:
                self.logger.warning(f"Results page incomplete ({self._real_cards(cards)} real cards) "
                                    f"- reloading (attempt {attempt + 1}/{max_reloads})")
                self.page.wait_for_timeout(5000)
                try:
                    self.page.reload(wait_until="domcontentloaded", timeout=45000)
                except PlaywrightTimeout:
                    self.logger.warning("Reload exceeded timeout, continuing")
        return cards

    # Item URLs under max price
    @allure.step("איסוף פריטים עד מחיר {max_price} (עד {limit})")
    def get_items_under_price(self, max_price: float, limit: int, currency: str,
                              already: List[str] = ()) -> List[str]:
        cards = self._read_cards()
        urls, stats = filter_cards(cards, max_price, limit, currency, already)
        stats["page_url"] = self.page.url
        stats["sample"] = [(c.get("price") or c.get("text") or "")[:80].replace("\n", " | ")
                           for c in cards[:3]]
        self.last_stats = stats
        self.logger.info(f"Page stats: {stats}")
        if not urls:
            self._save_page_html("search_page_debug")
        self.take_screenshot("search_results" if cards else "search_no_results")
        return urls

    # Save page HTML for debugging
    def _save_page_html(self, name: str) -> None:
        try:
            REPORTS_DIR.mkdir(exist_ok=True)
            html = self.page.content()
            (REPORTS_DIR / f"{name}.html").write_text(html, encoding="utf-8")
            allure.attach(html, name=name, attachment_type=allure.attachment_type.HTML)
        except Exception as e:
            self.logger.warning(f"Could not save page HTML: {e}")

    # Go to next results page
    @allure.step("מעבר לעמוד הבא")
    def go_to_next_page(self) -> bool:
        next_btn = self.page.locator(self.NEXT_PAGE)
        if next_btn.count() == 0:
            return False
        try:
            if next_btn.first.get_attribute("aria-disabled") == "true":
                return False
            previous_url = self.page.url
            next_btn.first.click()
            self.page.wait_for_url(lambda url: url != previous_url, timeout=30000,
                                   wait_until="domcontentloaded")
            if self.is_blocked():
                self.take_screenshot("blocked_on_paging")
                return False
            return True
        except Exception as e:
            self.logger.warning(f"Paging failed: {e}")
            return False
