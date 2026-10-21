from typing import Dict, List, Set

import allure
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from utils.parsing import Money, item_id, parse_cart_item_count, parse_cart_summary
from .base_page import AccountInfoRequiredError, BasePage
from .login_page import LoginPage


class CartPage(BasePage):
    CART_URL = "https://cart.ebay.com/"

    ORDER_SUMMARY = "//*[normalize-space()='Order summary']"
    REMOVE_BUTTON = ("//button[@data-test-id='cart-remove-item'] | "
                     "//button[normalize-space()='Remove']")
    CART_ITEM_LINK = "//a[contains(@href, '/itm/')]"
    EMPTY_CART_MESSAGE = "//*[contains(text(), \"You don't have any items in your cart\")]"

    @allure.step("פתיחת סל הקניות")
    def open(self, refresh: bool = True) -> None:
        """refresh=False: אם כבר בעמוד הסל (למשל אחרי אימות הפריטים) - לא טוענים שוב."""
        if not refresh and self.is_cart_page():
            return
        self.navigate(self.CART_URL, wait_until="commit", timeout=45000)
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=20000)
        except PlaywrightTimeout:
            self.logger.warning("Cart page is slow to load, continuing")
        LoginPage(self.page).complete_sign_in_if_prompted()
        if not self.is_cart_page():                 # אחרי התחברות eBay לא תמיד חוזרת לסל
            self.navigate(self.CART_URL)
        if self.is_account_upgrade_page():
            self.take_screenshot("account_info_required")
            raise AccountInfoRequiredError(AccountInfoRequiredError.MESSAGE)

    @allure.step("ריקון הסל לפני התרחיש")
    def clear(self, max_items: int = 30) -> int:
        """פריטים מהרצות קודמות מנפחים את הסכום ומפילים את האימות - מתחילים מסל ריק."""
        self.open()
        self._wait_until_loaded()
        removed = 0
        buttons = self.page.locator(self.REMOVE_BUTTON)
        while removed < max_items and self.is_visible_within(self.REMOVE_BUTTON, timeout=15000):
            before = buttons.count()
            buttons.first.click()
            try:
                self.page.wait_for_function(
                    "([xp, n]) => document.evaluate(`count(${xp})`, document, null, "
                    "XPathResult.NUMBER_TYPE, null).numberValue < n",
                    arg=[self.REMOVE_BUTTON, before], timeout=15000)
            except PlaywrightTimeout:
                self.logger.warning("Cart item removal not reflected yet, reloading")
                self.page.reload(wait_until="domcontentloaded")
            removed += 1
        self.logger.info(f"Cart cleared ({removed} items removed)")
        return removed

    def _wait_until_loaded(self) -> None:
        """הפריטים בסל מוצגים אחרי טעינת העמוד. בלי ההמתנה הזו, ברשת איטית סל מלא נראה ריק
        (לא נמצא כפתור Remove) - והניקוי מדלג על כל הפריטים."""
        try:
            self.wait_for_element_visible(f"{self.ORDER_SUMMARY} | {self.EMPTY_CART_MESSAGE}")
        except PlaywrightTimeout:
            self.logger.warning("Cart content did not finish loading")

    def _read_summary(self) -> Dict[str, Money]:
        try:
            self.wait_for_element_visible(self.ORDER_SUMMARY, 20000)
        except PlaywrightTimeout:
            self.logger.info("Order summary block not found, parsing the whole page text")
        summary = parse_cart_summary(self.page.inner_text("body"))
        if "items" not in summary and "subtotal" not in summary:
            raise AssertionError("Could not read cart totals (empty cart or changed layout).")
        return summary

    def get_item_ids(self) -> Set[str]:
        hrefs = self.page.locator(self.CART_ITEM_LINK).evaluate_all("els => els.map(a => a.href)")
        return {i for i in map(item_id, hrefs) if i}

    @allure.step("אימות שכל הפריטים שנוספו נמצאים בסל")
    def assert_contains_items(self, urls: List[str]) -> None:
        """אישור 'Added to cart' בדף הפריט לא מספיק: בודקים בסל עצמו את מספר הפריטים ואת מזהי
        הפריטים, כדי לתפוס הוספה שלא נשמרה (סשן שהתחלף, פריט שהוסר, הוספה כפולה)."""
        self.open()
        self._read_summary()                         # ממתין שהסל ייטען
        expected = {item_id(u) for u in urls}
        in_cart = self.get_item_ids()
        count = parse_cart_item_count(self.page.inner_text("body"))
        report = [f"expected ids: {sorted(expected)}", f"ids in cart: {sorted(in_cart)}",
                  f"summary count: {count}"]
        allure.attach("\n".join(report), name="cart_items_check",
                      attachment_type=allure.attachment_type.TEXT)
        missing = expected - in_cart
        assert not missing, f"Items added but not found in the cart: {sorted(missing)}"
        assert count == len(urls), f"Cart shows {count} items, expected {len(urls)}"
        self.logger.info(f"All {len(urls)} added items are in the cart")

    @allure.step("שליפת סכומי הסל")
    def get_summary(self) -> Dict[str, Money]:
        self.open(refresh=False)
        summary = self._read_summary()
        self.take_screenshot("cart_page")
        self.logger.info("Cart summary: " + ", ".join(
            f"{k}={v.amount} {v.currency}" for k, v in summary.items()))
        return summary

    @allure.step("אימות שמחיר הפריטים בסל ≤ {budget_per_item} × {items_count}")
    def assert_total_not_exceeds(self, budget_per_item: float, items_count: int,
                                 currency: str = "USD") -> None:
        """משווים את מחיר הפריטים (Items) ולא את Subtotal שכולל משלוח: הסינון בחיפוש הוא לפי
        מחיר הפריט, ובדיקה כולל משלוח הייתה נכשלת גם כשכל פריט עומד בתנאי."""
        threshold = budget_per_item * items_count
        summary = self.get_summary()
        items_total = summary.get("items") or summary["subtotal"]
        lines = [f"{k}={v.amount} {v.currency}" for k, v in summary.items()]
        lines.append(f"threshold={budget_per_item} x {items_count} = {threshold} {currency}")
        allure.attach("\n".join(lines),
                      name="cart_check", attachment_type=allure.attachment_type.TEXT)
        assert items_total.currency == currency.upper(), (
            f"Cart is shown in {items_total.currency} but the budget is in {currency}; "
            "set 'currency' in config/test_data.json to match the site")
        assert items_total.amount <= threshold, (
            f"Cart items total {items_total.amount} {currency} exceeds threshold {threshold} {currency}")
