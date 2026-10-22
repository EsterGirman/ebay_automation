import logging
import re
from typing import Optional
from urllib.parse import urlparse

import allure
from playwright.sync_api import Error as PlaywrightError, Locator, Page, TimeoutError as PlaywrightTimeout

from utils.config import REPORTS_DIR

SCREENSHOTS_DIR = REPORTS_DIR / "screenshots"


class SiteBlockedError(Exception):
    pass


class SignInRequiredError(Exception):
    pass


class AccountInfoRequiredError(SignInRequiredError):
    MESSAGE = ("eBay redirects to 'Complete your account info' (the test account has no address), "
               "so the cart can't be used. Complete it once in a regular browser, or run as guest.")


class BasePage:
    BLOCK_MARKERS = (
        "something went wrong on our end",
        "pardon our interruption",
        "please verify yourself",
    )

    BROWSER_CHECK_MARKER = "checking your browser before you access ebay"  # not a block - redirects by itself
    NAVIGATION_MS = 45_000       # page loads on a slow / filtered network
    QUICK_MS = 5_000             # UI reactions: popup, listbox, chosen value
    TEXT_CHECK_MS = 3_000        # block / check pages are tiny and return text at once
    BLOCK_BACKOFF_MS = 20_000

    BROWSER_CHECK_TEXT = re.compile(r"Checking your browser before you access eBay", re.IGNORECASE)
    BROWSER_CHECK_WAIT_SEC = 180

    SHIP_TO_TEXT = re.compile(r"Are you shipping to", re.IGNORECASE)  # injected dynamically - not reachable by XPath

    # Keep page and logger
    def __init__(self, page: Page):
        self.page = page
        self.logger = logging.getLogger(self.__class__.__name__)

    # Auto-confirm the ship-to popup
    @classmethod
    def install_popup_handlers(cls, page: Page) -> None:
        page.add_locator_handler(page.get_by_text(cls.SHIP_TO_TEXT).first,
                                 lambda: cls(page).confirm_ship_to_if_shown())

    # Confirm ship-to popup if shown
    def confirm_ship_to_if_shown(self) -> bool:
        if not self.page.get_by_text(self.SHIP_TO_TEXT).first.is_visible():
            return False
        self.logger.info("Ship-to location popup shown - confirming")
        try:
            self.page.get_by_role("button", name="Confirm", exact=True).first.click(timeout=self.QUICK_MS)
            self.page.wait_for_load_state("domcontentloaded")
        except PlaywrightError as e:
            self.logger.warning(f"Could not confirm ship-to popup: {str(e).splitlines()[0]}")
        return True

    # Open URL, detect block pages
    @allure.step("ניווט לכתובת: {url}")
    def navigate(self, url: str, retries: int = 1, retry_wait_ms: int = BLOCK_BACKOFF_MS,
                 wait_until: str = "domcontentloaded", timeout: int = NAVIGATION_MS) -> None:
        self.logger.info(f"Navigating to URL: {url}")
        for attempt in range(retries + 1):
            self._goto(url, wait_until, timeout)
            self._wait_for_browser_check()
            if not self.is_blocked():
                return
            self.logger.warning(f"Block page detected (attempt {attempt + 1})")
            self.take_screenshot(f"blocked_attempt_{attempt + 1}")
            if attempt < retries:
                self.page.wait_for_timeout(retry_wait_ms)  # deliberate backoff after a block, not a load wait
        raise SiteBlockedError(
            f"eBay returned a block/error page for {url}. "
            "Wait a few hours / change network, or run again later."
        )

    # goto with network-error retry
    def _goto(self, url: str, wait_until: str, timeout: int, network_retries: int = 2) -> None:
        for attempt in range(network_retries + 1):
            try:
                self.page.goto(url, timeout=timeout, wait_until=wait_until)
                return
            except PlaywrightTimeout:
                self.logger.warning(f"Page load exceeded {timeout} ms, continuing: {self.page.url}")
                return
            except PlaywrightError as e:
                if "net::" not in str(e) or attempt == network_retries:
                    raise
                self.logger.warning(f"Navigation interrupted ({str(e).splitlines()[0]}), retrying")
                self.page.wait_for_timeout(3000 * (attempt + 1))  # deliberate backoff before retry

    # Wait out eBay's browser check
    def _wait_for_browser_check(self) -> None:
        if not self._on_browser_check():
            return
        self.logger.warning("eBay browser check page shown - waiting for it to pass")
        try:
            # until the check text is gone (survives the redirect)
            self.page.get_by_text(self.BROWSER_CHECK_TEXT).first.wait_for(
                state="hidden", timeout=self.BROWSER_CHECK_WAIT_SEC * 1000)
            self.page.wait_for_load_state("domcontentloaded")
        except PlaywrightTimeout:
            self.take_screenshot("browser_check_stuck")
            raise SiteBlockedError("eBay browser check did not pass. Too many requests in a short "
                                   "time - wait 15-30 minutes and run again.") from None

    # Is the browser check shown?
    def _on_browser_check(self) -> bool:
        try:
            return self.BROWSER_CHECK_MARKER in self.page.inner_text("body", timeout=self.TEXT_CHECK_MS).lower()
        except PlaywrightTimeout:
            return False
        except PlaywrightError:
            return True

    # Is a block page shown?
    def is_blocked(self) -> bool:
        try:
            text = self.page.inner_text("body", timeout=self.TEXT_CHECK_MS).lower()
        except PlaywrightError:
            return False
        return any(marker in text for marker in self.BLOCK_MARKERS)

    # Current URL host
    def _host(self) -> str:
        return urlparse(self.page.url).netloc  # host, not substring: sign-in URL contains ru=cart...

    # On the sign-in page?
    def is_signin_page(self) -> bool:
        return self._host().startswith("signin.")

    # On the cart page?
    def is_cart_page(self) -> bool:
        return self._host().startswith("cart.")

    # On 'Complete your account info'?
    def is_account_upgrade_page(self) -> bool:
        return "/fpa/upgrade" in self.page.url or self._host().startswith("signup.")

    # Wait until element is visible
    def wait_for_element_visible(self, selector: str, timeout: Optional[int] = None) -> Locator:
        locator = self.page.locator(selector).first
        locator.wait_for(state="visible", timeout=timeout)
        return locator

    # Visible within timeout? (no raise)
    def is_visible_within(self, selector: str, timeout: Optional[int] = None) -> bool:
        try:
            self.wait_for_element_visible(selector, timeout)
            return True
        except PlaywrightTimeout:
            return False

    # Screenshot to file and report
    @allure.step("לכידת צילום מסך: {name}")
    def take_screenshot(self, name: str) -> str:
        safe_name = re.sub(r"[^\w\-]+", "_", name)
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOTS_DIR / f"{safe_name}.png"
        try:
            data = self.page.screenshot(path=str(path), full_page=True)
            allure.attach(data, name=safe_name, attachment_type=allure.attachment_type.PNG)
            self.logger.info(f"Screenshot saved: {path}")
        except (PlaywrightError, OSError) as e:
            self.logger.error(f"Failed to capture screenshot {name}: {e}")
        return str(path)
