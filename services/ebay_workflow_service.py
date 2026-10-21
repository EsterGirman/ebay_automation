import json
import logging
import random
from typing import List, Optional, Sequence

import allure
from playwright.sync_api import Page

from pages.base_page import SignInRequiredError
from pages.cart_page import CartPage
from pages.home_page import HomePage
from pages.item_details_page import ItemDetailsPage
from pages.login_page import LoginPage
from pages.search_results_page import SearchResultsPage
from utils.config import AUTH_STATE_FILE, Credentials, env_flag

logger = logging.getLogger("EBayWorkflowService")


class EBayWorkflowService:
    """The four functions of the assignment, on top of the Page Objects. The test talks only to this layer."""

    def __init__(self, page: Page, currency: str = "USD"):
        self.page = page
        self.currency = currency
        self.search_page = SearchResultsPage(page)
        self.cart_page = CartPage(page)
        self.search_report: List[dict] = []
        self.spare_urls: List[str] = []          # extra candidates from the search, to replace an item that fails

    # 1. Authentication
    @allure.step("הזדהות")
    def authenticate(self, credentials: Optional[Credentials]) -> bool:
        """With EBAY_USER/EBAY_PASS - sign in (or reuse the saved auth state). Without them - guest.
        If sign-in isn't completed in time (CAPTCHA / verification code not entered within
        EBAY_LOGIN_WAIT_SEC), continue as guest with a warning in the log and report: eBay allows a guest cart.
        EBAY_REQUIRE_LOGIN=1 turns this into a failure."""
        HomePage(self.page).open()
        if credentials is None:
            logger.info("No EBAY_USER/EBAY_PASS - running as guest")
            allure.dynamic.label("auth", "guest")
            return False
        try:
            LoginPage(self.page, credentials).login()
            # An account without an address is redirected from the cart to 'Complete your account info';
            # check now, to switch to guest before starting rather than fail at the end
            self.cart_page.open()
        except SignInRequiredError as e:
            if env_flag("EBAY_REQUIRE_LOGIN", False):
                raise
            self._fall_back_to_guest(str(e))
            return False
        allure.dynamic.label("auth", "signed-in")
        return True

    def _fall_back_to_guest(self, reason: str) -> None:
        """Continue as guest: clear the partial session (cookies and .auth), log a warning, mark the fallback in the report."""
        logger.warning(f"Sign-in not completed - continuing as guest ({reason})")
        allure.dynamic.label("auth", "guest (sign-in fallback)")
        allure.attach(reason, name="sign_in_fallback", attachment_type=allure.attachment_type.TEXT)
        # A half-signed-in session made eBay ask for the password again in the cart; start as a clean guest
        self.page.context.clear_cookies()
        AUTH_STATE_FILE.unlink(missing_ok=True)
        HomePage(self.page).open()

    @allure.step("ריקון הסל")
    def clear_cart(self) -> None:
        """Empty the cart - at the start (leftovers inflate the total) and at the end (the ebay fixture)."""
        self.cart_page.clear()

    # 2. Search with a price condition (incl. paging). Returns up to limit URLs; fewer (even 0) is valid.
    @allure.step("חיפוש '{query}' עד {max_price} (עד {limit} פריטים)")
    def search_items_by_name_under_price(self, query: str, max_price: float, limit: int = 5,
                                         max_pages: int = 5) -> List[str]:
        """The search function of the assignment (4.1): search by query, price filter, collect up to limit
        items with paging. Returns what was found - fewer than limit or 0 is valid. Extra candidates go to spare_urls."""
        self.search_report = []                 # per-page diagnostics, attached to the report
        self.search_page.search(query, max_price)
        # Collect up to limit spares from the same pages; page further only while the result itself is short
        wanted = limit * 2
        collected: List[str] = []
        for page_no in range(1, max_pages + 1):
            collected += self.search_page.get_items_under_price(
                max_price, wanted - len(collected), self.currency, already=collected)
            self.search_report.append({"page": page_no, **self.search_page.last_stats})
            if len(collected) >= limit or not self.search_page.go_to_next_page():
                break
        result, self.spare_urls = collected[:limit], collected[limit:]
        logger.info(f"Total collected: {len(result)}/{limit} (+{len(self.spare_urls)} spare)")
        # Per-page diagnostics (cards, how many filtered and why) - explains an empty search from the report
        allure.attach(json.dumps(self.search_report, ensure_ascii=False, indent=2),
                      name="search_report", attachment_type=allure.attachment_type.JSON)
        return result

    # 3. Add to cart - each item in its own tab; return to the search tab after each item
    @allure.step("הוספת פריטים לסל")
    def add_items_to_cart(self, urls: List[str], spare_urls: Sequence[str] = ()) -> List[str]:
        """Add len(urls) items. An item that can't be added (sold out / button disabled) is logged with a
        screenshot and replaced by the next one from spare_urls. Returns the URLs actually added."""
        target = len(urls)
        added: List[str] = []
        for index, url in enumerate([*urls, *spare_urls], start=1):
            if len(added) >= target:
                break
            if index > target:
                logger.info(f"Replacing a failed item with spare candidate: {url}")
            if index > 1:
                # "Human" pace between items - a fast burst of requests is the usual bot-detection trigger
                self.page.wait_for_timeout(random.randint(2000, 5000))
            item_tab = self.page.context.new_page()
            try:
                label = f"item_{index}" if index <= target else f"spare_{index - target}"
                if ItemDetailsPage(item_tab).add_to_cart(url, label):
                    added.append(url)
            finally:
                item_tab.close()
                self.page.bring_to_front()
        logger.info(f"Added {len(added)}/{target} items to cart")
        return added

    @allure.step("אימות שהפריטים נמצאים בסל")
    def assert_items_in_cart(self, urls: List[str]) -> None:
        """Verify in the cart itself that every added item is there and the item count matches (beyond the assignment)."""
        self.cart_page.assert_contains_items(urls)

    # 4. Cart total check
    @allure.step("אימות סכום הסל")
    def assert_cart_total_not_exceeds(self, budget_per_item: float, items_count: int) -> None:
        """The cart check of the assignment (4.3): items total in the cart <= budget_per_item * items_count."""
        self.cart_page.assert_total_not_exceeds(budget_per_item, items_count, self.currency)
