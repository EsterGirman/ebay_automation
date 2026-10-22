import random
from typing import Optional

import allure
from playwright.sync_api import Error as PlaywrightError, Locator, TimeoutError as PlaywrightTimeout

from .base_page import AccountInfoRequiredError, BasePage, SignInRequiredError
from .cart_page import CartPage
from .login_page import LoginPage


class ItemNotAddedError(Exception):
    pass


class ItemDetailsPage(BasePage):
    BAD_OPTION_WORDS = ("select", "out of stock", "sold out", "unavailable")

    VARIANT_SELECT = "//select[not(@disabled)][not(ancestor::*[@role='dialog'])]"  # skip popup selects
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

    # Is the option selectable?
    def _is_good_option(self, text: str) -> bool:
        t = text.strip().lower()
        return bool(t) and not any(w in t for w in self.BAD_OPTION_WORDS)

    # Pick random size/color
    def _select_random_variants(self) -> None:
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
            except PlaywrightError as e:
                self.logger.info(f"Select variant skipped: {str(e).splitlines()[0]}")

        for button in self.page.locator(self.VARIANT_LISTBOX_BUTTON).all():
            for _ in range(2):
                if self._pick_from_listbox(button):
                    break

    # Pick from one custom listbox
    def _pick_from_listbox(self, button: Locator) -> bool:
        try:
            button.scroll_into_view_if_needed()
            button.click()
            listbox_id = button.get_attribute("aria-controls")
            if listbox_id:
                listbox = self.page.locator(f"//*[@id='{listbox_id}']")
                listbox.wait_for(state="visible", timeout=self.QUICK_MS)  # opens with an animation
                candidates = listbox.locator("xpath=." + self.LISTBOX_OPTION)
            else:
                candidates = self.page.locator(self.LISTBOX_OPTION)
                candidates.first.wait_for(state="visible", timeout=self.QUICK_MS)
            options = [o for o in candidates.all()
                       if o.is_visible() and self._is_good_option(o.inner_text(timeout=self.QUICK_MS))]
            if not options:
                self.page.keyboard.press("Escape")
                return False
            random.choice(options).click(timeout=self.QUICK_MS)
            selected = button.locator("xpath=.//span[contains(@class, 'btn__text')]")
            shown = selected.first if selected.count() else button
            try:
                self.page.wait_for_function(  # until the button shows the chosen value
                    "el => { const t = el.textContent.trim().toLowerCase(); return t && !t.endsWith('select'); }",
                    arg=shown.element_handle(), timeout=self.QUICK_MS)
            except PlaywrightTimeout:
                return False
            self.logger.info("Selected variant (listbox): " + " ".join(button.inner_text().split()))
            return True
        except PlaywrightError as e:
            self.logger.info(f"Listbox variant skipped: {str(e).splitlines()[0]}")
            self.page.keyboard.press("Escape")
            return False

    # Wait for Add to cart to enable
    def _wait_until_enabled(self, button: Locator, timeout_ms: Optional[int] = None) -> None:
        try:
            self.page.wait_for_function(
                "b => !(b.disabled || b.getAttribute('aria-disabled') === 'true'"
                " || /disabled/i.test(String(b.className)))",
                arg=button.element_handle(), timeout=timeout_ms)
        except PlaywrightTimeout:
            raise ItemNotAddedError("Add to cart is disabled (item not purchasable here, "
                                    "selection required, or page not fully loaded)") from None

    # Wait for 'Added to cart'
    def _wait_for_added_confirmation(self) -> None:
        try:
            self.page.wait_for_selector(self.ADDED_CONFIRMATION, timeout=2 * self.QUICK_MS)
            return
        except PlaywrightTimeout:
            pass
        LoginPage(self.page).complete_sign_in_if_prompted()
        if self.is_account_upgrade_page() or self.page.locator(self.ACCOUNT_INFO_FORM).first.is_visible():
            self.take_screenshot("account_info_required")
            raise AccountInfoRequiredError(AccountInfoRequiredError.MESSAGE)
        if not self.is_cart_page():
            raise ItemNotAddedError(f"No 'Added to cart' confirmation (now at {self.page.url})")

    # Open item and add it to cart
    @allure.step("הוספת {label} לסל: {url}")
    def add_to_cart(self, url: str, label: str) -> bool:
        step = "navigate"
        try:
            self.navigate(url, retries=0)
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
            try:
                self._wait_for_added_confirmation()
            except ItemNotAddedError:
                step = "check_cart"
                if not CartPage(self.page).contains_item(url):  # slow confirmation != not added
                    raise
                self.logger.info(f"{label}: no confirmation shown, but the item is in the cart")
            self.take_screenshot(f"{label}_added_to_cart")
            return True
        except SignInRequiredError:
            raise
        except (PlaywrightError, ItemNotAddedError) as e:  # expected on a live site; real bugs still raise
            first_line = str(e).strip().splitlines()[0] if str(e).strip() else ""
            self.logger.error(f"{label} failed at step '{step}': "
                              f"{type(e).__name__}: {first_line} | url={url}")
            self.take_screenshot(f"{label}_add_failure")
            return False
