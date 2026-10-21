import logging
import re
import time
from urllib.parse import urlparse

import allure
from playwright.sync_api import Error as PlaywrightError, Locator, Page, TimeoutError as PlaywrightTimeout

from utils.config import REPORTS_DIR

SCREENSHOTS_DIR = REPORTS_DIR / "screenshots"


class SiteBlockedError(Exception):
    """eBay showed a block / generic error page instead of the requested content."""


class SignInRequiredError(Exception):
    """eBay requires signing in and sign-in was not completed."""


class AccountInfoRequiredError(SignInRequiredError):
    """Signed in, but eBay redirects to 'Complete your account info' (no address) instead of the cart."""

    MESSAGE = ("eBay redirects to 'Complete your account info' (the test account has no address), "
               "so the cart can't be used. Complete it once in a regular browser, or run as guest.")


class BasePage:
    """Base for all Page Objects: resilient navigation (retries, block and bot-check detection),
    waits, screenshots and popup handling. Each page adds only its own locators and actions."""
    # Texts that identify an eBay block / generic error page
    BLOCK_MARKERS = (
        "something went wrong on our end",
        "pardon our interruption",
        "please verify yourself",
    )

    # eBay's browser-check page: not a block, it redirects to the content by itself
    BROWSER_CHECK_MARKER = "checking your browser before you access ebay"
    BROWSER_CHECK_WAIT_SEC = 180      # observed: seconds up to ~3 minutes, depending on load/network

    # "Are you shipping to ...?" popup covers the page and swallows clicks/typing. Text/role
    # locator, not XPath: the popup is injected dynamically and XPath doesn't pierce Shadow DOM
    SHIP_TO_TEXT = re.compile(r"Are you shipping to", re.IGNORECASE)

    def __init__(self, page: Page):
        self.page = page
        self.logger = logging.getLogger(self.__class__.__name__)

    @classmethod
    def install_popup_handlers(cls, page: Page) -> None:
        """Playwright runs the handler automatically before any action whenever the popup appears."""
        page.add_locator_handler(page.get_by_text(cls.SHIP_TO_TEXT).first,
                                 lambda: cls(page).confirm_ship_to_if_shown())

    def confirm_ship_to_if_shown(self) -> bool:
        """Confirm the ship-to popup if shown (this reloads the page). Short click timeout so a
        popup that already disappeared can't hang the test."""
        if not self.page.get_by_text(self.SHIP_TO_TEXT).first.is_visible():
            return False
        self.logger.info("Ship-to location popup shown - confirming")
        try:
            self.page.get_by_role("button", name="Confirm", exact=True).first.click(timeout=5000)
            self.page.wait_for_load_state("domcontentloaded")
        except PlaywrightError as e:
            self.logger.warning(f"Could not confirm ship-to popup: {str(e).splitlines()[0]}")
        return True

    @allure.step("ניווט לכתובת: {url}")
    def navigate(self, url: str, retries: int = 1, retry_wait_ms: int = 20_000,
                 wait_until: str = "domcontentloaded", timeout: int = 30_000) -> None:
        """Navigate with block detection. A load timeout is not fatal: continue and wait for a specific element."""
        self.logger.info(f"Navigating to URL: {url}")
        for attempt in range(retries + 1):
            self._goto(url, wait_until, timeout)
            self._wait_for_browser_check()
            if not self.is_blocked():
                return
            self.logger.warning(f"Block page detected (attempt {attempt + 1})")
            self.take_screenshot(f"blocked_attempt_{attempt + 1}")
            if attempt < retries:
                self.page.wait_for_timeout(retry_wait_ms)
        raise SiteBlockedError(
            f"eBay returned a block/error page for {url}. "
            "Wait a few hours / change network, or run again later."
        )

    def _goto(self, url: str, wait_until: str, timeout: int, network_retries: int = 2) -> None:
        """A timeout is not fatal (continue with what loaded). A network error (net::ERR_ABORTED etc.,
        navigation interrupted by a site redirect or network filtering) is retried after a short wait."""
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
                self.page.wait_for_timeout(3000 * (attempt + 1))

    def _wait_for_browser_check(self) -> None:
        """Wait for eBay's browser check to redirect to the content instead of failing. Polled from
        Python: during the redirect the page is briefly empty and in-page JS would break."""
        if not self._on_browser_check():
            return
        self.logger.warning("eBay browser check page shown - waiting for it to pass")
        deadline = time.monotonic() + self.BROWSER_CHECK_WAIT_SEC
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(1000)
            if not self._on_browser_check():
                try:
                    self.page.wait_for_load_state("domcontentloaded")
                except PlaywrightError:
                    pass
                return
        self.take_screenshot("browser_check_stuck")
        raise SiteBlockedError("eBay browser check did not pass. Too many requests in a short "
                               "time - wait 15-30 minutes and run again.")

    def _on_browser_check(self) -> bool:
        """The check page is tiny and returns text instantly; a page whose text times out is a heavy
        page (e.g. the homepage on a slow network), so it is not the check page."""
        try:
            return self.BROWSER_CHECK_MARKER in self.page.inner_text("body", timeout=10000).lower()
        except PlaywrightTimeout:
            return False
        except PlaywrightError:
            return True                 # mid-redirect (context replaced) - still in the check

    def is_blocked(self) -> bool:
        """True if eBay shows a block / error page instead of the content."""
        try:
            text = self.page.inner_text("body", timeout=10000).lower()
        except PlaywrightError:
            return False
        return any(marker in text for marker in self.BLOCK_MARKERS)

    # Check the host, not a URL substring: the sign-in URL contains ru=https://cart.ebay.com/...
    def _host(self) -> str:
        return urlparse(self.page.url).netloc

    def is_signin_page(self) -> bool:
        return self._host().startswith("signin.")

    def is_cart_page(self) -> bool:
        return self._host().startswith("cart.")

    def is_account_upgrade_page(self) -> bool:
        """The 'Complete your account info' form (signup.ebay.com/fpa/upgrade)."""
        return "/fpa/upgrade" in self.page.url or self._host().startswith("signup.")

    def wait_for_element_visible(self, selector: str, timeout: int = None) -> Locator:
        """Wait until the first matching element is visible and return it (default timeout: profile)."""
        locator = self.page.locator(selector).first
        locator.wait_for(state="visible", timeout=timeout)
        return locator

    def is_visible_within(self, selector: str, timeout: int) -> bool:
        """Like wait_for_element_visible, but returns True/False instead of raising."""
        try:
            self.wait_for_element_visible(selector, timeout)
            return True
        except PlaywrightTimeout:
            return False

    @allure.step("לכידת צילום מסך: {name}")
    def take_screenshot(self, name: str) -> str:
        """Full-page screenshot to reports/screenshots and Allure. A failed screenshot never fails the test."""
        safe_name = re.sub(r"[^\w\-]+", "_", name)
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOTS_DIR / f"{safe_name}.png"
        try:
            data = self.page.screenshot(path=str(path), full_page=True)
            allure.attach(data, name=safe_name, attachment_type=allure.attachment_type.PNG)
            self.logger.info(f"Screenshot saved: {path}")
        except Exception as e:
            self.logger.error(f"Failed to capture screenshot {name}: {e}")
        return str(path)
