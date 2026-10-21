import random

import allure
from playwright.sync_api import Locator, TimeoutError as PlaywrightTimeout

from .base_page import AccountInfoRequiredError, BasePage, SignInRequiredError
from .login_page import LoginPage


class ItemNotAddedError(Exception):
    """The item was not added (not purchasable / selection required / button disabled)."""


class ItemDetailsPage(BasePage):
    """Item page: random variant selection (size/color), click 'Add to cart' and verify it was added."""
    BAD_OPTION_WORDS = ("select", "out of stock", "sold out", "unavailable")

    # Skip dropdowns inside popups (e.g. the shipping-country select in the location dialog)
    VARIANT_SELECT = "//select[not(@disabled)][not(ancestor::*[@role='dialog'])]"
    VARIANT_LISTBOX_BUTTON = ("//*[@data-testid='x-msku-evo']//button[@aria-haspopup='listbox'] | "
                              "//div[contains(@class, 'x-msku')]//button[@aria-haspopup='listbox']")
    LISTBOX_OPTION = "//*[@role='option' and not(@aria-disabled='true')]"
    ADD_TO_CART_BUTTON = (
        "//a[contains(@id, 'atcBtn')] | "
        "//*[self::a or self::button][.//span[normalize-space()='Add to cart']] | "
        "//*[self::a or self::button][normalize-space()='Add to cart']"
    )
    ACCOUNT_INFO_FORM = ("//*[self::h1 or self::h2]"
                         "[contains(normalize-space(), 'Complete your account info')]")
    ADDED_CONFIRMATION = (
        "//*[contains(normalize-space(text()), 'Added to cart')] | "
        "//a[contains(@href, 'cart.ebay.com')][contains(., 'See in cart') "
        "or contains(., 'Go to cart') or contains(., 'View cart')]"
    )

    def _is_good_option(self, text: str) -> bool:
        t = text.strip().lower()
        return bool(t) and not any(w in t for w in self.BAD_OPTION_WORDS)

    def _select_random_variants(self) -> None:
        """Pick a random available value in every selector (size/color). Quantity stays 1 to keep the budget."""
        # 1. Native <select> dropdowns
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

        # 2. eBay's custom dropdowns (button + role=option)
        for button in self.page.locator(self.VARIANT_LISTBOX_BUTTON).all():
            for _ in range(2):                       # retry if the choice didn't register
                if self._pick_from_listbox(button):
                    break

    def _pick_from_listbox(self, button: Locator) -> bool:
        """Open a listbox and pick a random option from the visible options of this listbox only.
        Collecting options page-wide also picked hidden options of the other listbox (color/size);
        clicking one timed out and the choice stayed "Select"."""
        try:
            button.scroll_into_view_if_needed()
            button.click()
            listbox_id = button.get_attribute("aria-controls")
            if listbox_id:
                listbox = self.page.locator(f"//*[@id='{listbox_id}']")
                # The listbox opens with an animation; checking visibility right after the click found 0 options
                listbox.wait_for(state="visible", timeout=5000)
                candidates = listbox.locator("xpath=." + self.LISTBOX_OPTION)   # a relative XPath needs the xpath= prefix
            else:
                self.page.wait_for_timeout(1000)
                candidates = self.page.locator(self.LISTBOX_OPTION)
            options = [o for o in candidates.all()
                       if o.is_visible() and self._is_good_option(o.inner_text(timeout=2000))]
            if not options:
                self.page.keyboard.press("Escape")
                return False
            random.choice(options).click(timeout=5000)
            # Wait until the button shows the chosen value instead of "Select"
            selected = button.locator("xpath=.//span[contains(@class, 'btn__text')]")
            for _ in range(10):
                text = (selected.first.inner_text(timeout=2000) if selected.count()
                        else button.inner_text(timeout=2000)).strip()
                if text and text.lower() != "select":
                    break
                self.page.wait_for_timeout(300)
            else:
                return False                         # still "Select" - the choice didn't register
            self.logger.info("Selected variant (listbox): " + " ".join(button.inner_text().split()))
            return True
        except Exception as e:
            self.logger.info(f"Listbox variant skipped: {str(e).splitlines()[0]}")
            self.page.keyboard.press("Escape")
            return False

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
        # eBay sometimes asks for the password again on 'Add to cart', then redirects to the cart
        LoginPage(self.page).complete_sign_in_if_prompted()
        if self.is_account_upgrade_page() or self.page.locator(self.ACCOUNT_INFO_FORM).first.is_visible():
            # An account problem, not an item problem: every item gets the same form, so stop trying
            self.take_screenshot("account_info_required")
            raise AccountInfoRequiredError(AccountInfoRequiredError.MESSAGE)
        if not self.is_cart_page():
            raise ItemNotAddedError(f"No 'Added to cart' confirmation (now at {self.page.url})")

    @allure.step("הוספת {label} לסל: {url}")
    def add_to_cart(self, url: str, label: str) -> bool:
        """Return True if added. A single item failure is logged and doesn't fail the scenario; a sign-in failure does."""
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
            raise                                    # no point trying more items without sign-in
        except Exception as e:
            first_line = str(e).strip().splitlines()[0] if str(e).strip() else ""
            self.logger.error(f"{label} failed at step '{step}': "
                              f"{type(e).__name__}: {first_line} | url={url}")
            self.take_screenshot(f"{label}_add_failure")
            return False
