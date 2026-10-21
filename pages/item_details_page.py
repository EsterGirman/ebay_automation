import random

import allure
from playwright.sync_api import Locator, TimeoutError as PlaywrightTimeout

from .base_page import BasePage, SignInRequiredError
from .login_page import LoginPage


class ItemNotAddedError(Exception):
    """הפריט לא נוסף לסל (לא ניתן לרכישה / נדרשת בחירה / הכפתור לא פעיל)."""


class ItemDetailsPage(BasePage):
    BAD_OPTION_WORDS = ("select", "out of stock", "sold out", "unavailable")

    # לא בוחרים בתיבות שבתוך חלונות קופצים (למשל בחירת מדינת משלוח בחלון המיקום)
    VARIANT_SELECT = "//select[not(@disabled)][not(ancestor::*[@role='dialog'])]"
    VARIANT_LISTBOX_BUTTON = "//div[contains(@class, 'x-msku')]//button[@aria-haspopup='listbox']"
    LISTBOX_OPTION = "//*[@role='option' and not(@aria-disabled='true')]"
    ADD_TO_CART_BUTTON = (
        "//a[contains(@id, 'atcBtn')] | "
        "//*[self::a or self::button][.//span[normalize-space()='Add to cart']] | "
        "//*[self::a or self::button][normalize-space()='Add to cart']"
    )
    # טופס "Complete your account info" שמופיע לחשבון רשום בלי כתובת
    ACCOUNT_INFO_FORM = ("//*[self::h1 or self::h2]"
                         "[contains(normalize-space(), 'Complete your account info')]")
    ADDED_CONFIRMATION = (
        "//*[contains(normalize-space(text()), 'Added to cart')] | "
        "//a[contains(@href, 'cart.ebay.com')][contains(., 'See in cart') "
        "or contains(., 'Go to cart') or contains(., 'View cart')]"
    )

    # ---------- וריאנטים ----------
    def _is_good_option(self, text: str) -> bool:
        t = text.strip().lower()
        return bool(t) and not any(w in t for w in self.BAD_OPTION_WORDS)

    def _select_random_variants(self) -> None:
        """בוחר ערך אקראי מבין הזמינים בכל בורר (מידה/צבע). הכמות נשארת 1 כדי לא לחרוג מהתקציב."""
        # 1. תיבות <select> רגילות
        for dropdown in self.page.locator(self.VARIANT_SELECT).all():
            try:
                valid = [opt.get_attribute("value") for opt in dropdown.locator("option").all()
                         if opt.get_attribute("disabled") is None
                         and opt.get_attribute("value") not in (None, "", "-1")
                         and self._is_good_option(opt.inner_text())]
                if valid:
                    chosen = random.choice(valid)
                    dropdown.select_option(value=chosen)
                    self.logger.info(f"Selected variant (select): {chosen}")
            except Exception as e:
                self.logger.info(f"Select variant skipped: {e}")

        # 2. רשימות נפתחות מותאמות של eBay (button + role=option)
        for button in self.page.locator(self.VARIANT_LISTBOX_BUTTON).all():
            try:
                button.click()
                options = [o for o in self.page.locator(self.LISTBOX_OPTION).all()
                           if self._is_good_option(o.inner_text())]
                if options:
                    random.choice(options).click()
                    self.logger.info("Selected variant (listbox)")
                else:
                    self.page.keyboard.press("Escape")
            except Exception as e:
                self.logger.info(f"Listbox variant skipped: {e}")
                self.page.keyboard.press("Escape")

    @staticmethod
    def _is_disabled(button: Locator) -> bool:
        try:
            cls = (button.get_attribute("class") or "").lower()
            return (not button.is_enabled() or button.get_attribute("aria-disabled") == "true"
                    or "disabled" in cls)
        except Exception:
            return False

    def _wait_until_enabled(self, button: Locator, timeout_ms: int = 15000) -> None:
        for _ in range(timeout_ms // 500):
            if not self._is_disabled(button):
                return
            self.page.wait_for_timeout(500)
        raise ItemNotAddedError("Add to cart is disabled (item not purchasable here, "
                                "selection required, or page not fully loaded)")

    def _wait_for_added_confirmation(self) -> None:
        try:
            self.page.wait_for_selector(self.ADDED_CONFIRMATION, timeout=10000)
            return
        except PlaywrightTimeout:
            pass
        # eBay דורשת לפעמים סיסמה מחדש בלחיצה על 'Add to cart' ואחרי ההתחברות מעבירה לסל
        LoginPage(self.page).complete_sign_in_if_prompted()
        if self.page.locator(self.ACCOUNT_INFO_FORM).first.is_visible():
            # בעיה של החשבון ולא של הפריט: כל פריט יקבל את אותו טופס, אין טעם להמשיך לנסות
            self.take_screenshot("account_info_required")
            raise SignInRequiredError(
                "eBay asks to complete the account's contact info (address) before adding to "
                "cart. Complete it once in a regular browser, or run as guest (no EBAY_USER).")
        if not self.is_cart_page():
            raise ItemNotAddedError(f"No 'Added to cart' confirmation (now at {self.page.url})")

    # ---------- הוספה לסל ----------
    @allure.step("הוספת {label} לסל: {url}")
    def add_to_cart(self, url: str, label: str) -> bool:
        """מחזיר True אם נוסף. כשל של פריט בודד נרשם ולא מפיל את התרחיש; חוסר התחברות כן מפיל."""
        step = "navigate"
        try:
            self.navigate(url, retries=0, timeout=45000)
            step = "select_variants"
            self._select_random_variants()
            step = "find_add_to_cart_button"
            button = self.wait_for_element_visible(self.ADD_TO_CART_BUTTON)
            step = "button_enabled"
            self._wait_until_enabled(button)
            step = "click_add_to_cart"
            button.scroll_into_view_if_needed()
            button.click()
            step = "wait_for_confirmation"
            self._wait_for_added_confirmation()
            self.take_screenshot(f"{label}_added_to_cart")
            return True
        except SignInRequiredError:
            raise                                    # אין טעם לנסות פריטים נוספים בלי התחברות
        except Exception as e:
            first_line = str(e).strip().splitlines()[0] if str(e).strip() else ""
            self.logger.error(f"{label} failed at step '{step}': "
                              f"{type(e).__name__}: {first_line} | url={url}")
            self.take_screenshot(f"{label}_add_failure")
            return False
