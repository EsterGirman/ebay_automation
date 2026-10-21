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
    # Pages used by the flow
    def __init__(self, page: Page, currency: str = "USD"):
        self.page = page
        self.currency = currency
        self.search_page = SearchResultsPage(page)
        self.cart_page = CartPage(page)
        self.search_report: List[dict] = []
        self.spare_urls: List[str] = []

    # 1. Sign in or continue as guest
    @allure.step("הזדהות")
    def authenticate(self, credentials: Optional[Credentials]) -> bool:
        HomePage(self.page).open()
        if credentials is None:
            logger.info("No EBAY_USER/EBAY_PASS - running as guest")
            allure.dynamic.label("auth", "guest")
            return False
        try:
            LoginPage(self.page, credentials).login()
            self.cart_page.open()  # account without address -> fall back
        except SignInRequiredError as e:
            if env_flag("EBAY_REQUIRE_LOGIN", False):
                raise
            self._fall_back_to_guest(str(e))
            return False
        allure.dynamic.label("auth", "signed-in")
        return True

    # Reset session, continue as guest
    def _fall_back_to_guest(self, reason: str) -> None:
        logger.warning(f"Sign-in not completed - continuing as guest ({reason})")
        allure.dynamic.label("auth", "guest (sign-in fallback)")
        allure.attach(reason, name="sign_in_fallback", attachment_type=allure.attachment_type.TEXT)
        self.page.context.clear_cookies()
        AUTH_STATE_FILE.unlink(missing_ok=True)
        HomePage(self.page).open()

    # Empty the cart
    @allure.step("ריקון הסל")
    def clear_cart(self) -> None:
        self.cart_page.clear()

    # 2. Search under price, with paging
    @allure.step("חיפוש '{query}' עד {max_price} (עד {limit} פריטים)")
    def search_items_by_name_under_price(self, query: str, max_price: float, limit: int = 5,
                                         max_pages: int = 5) -> List[str]:
        self.search_report = []
        self.search_page.search(query, max_price)
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
        allure.attach(json.dumps(self.search_report, ensure_ascii=False, indent=2),
                      name="search_report", attachment_type=allure.attachment_type.JSON)
        return result

    # 3. Add items, replace failed ones
    @allure.step("הוספת פריטים לסל")
    def add_items_to_cart(self, urls: List[str], spare_urls: Sequence[str] = ()) -> List[str]:
        target = len(urls)
        added: List[str] = []
        for index, url in enumerate([*urls, *spare_urls], start=1):
            if len(added) >= target:
                break
            if index > target:
                logger.info(f"Replacing a failed item with spare candidate: {url}")
            if index > 1:
                self.page.wait_for_timeout(random.randint(2000, 5000))  # human pace vs bot detection
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

    # Assert items are in the cart
    @allure.step("אימות שהפריטים נמצאים בסל")
    def assert_items_in_cart(self, urls: List[str]) -> None:
        self.cart_page.assert_contains_items(urls)

    # 4. Assert cart total within budget
    @allure.step("אימות סכום הסל")
    def assert_cart_total_not_exceeds(self, budget_per_item: float, items_count: int) -> None:
        self.cart_page.assert_total_not_exceeds(budget_per_item, items_count, self.currency)
