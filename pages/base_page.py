import logging
import re
import time
from urllib.parse import urlparse

import allure
from playwright.sync_api import Error as PlaywrightError, Locator, Page, TimeoutError as PlaywrightTimeout

from utils.config import REPORTS_DIR

SCREENSHOTS_DIR = REPORTS_DIR / "screenshots"


class SiteBlockedError(Exception):
    """האתר הציג עמוד חסימה / שגיאה כללית במקום התוכן המבוקש."""


class SignInRequiredError(Exception):
    """eBay דורשת התחברות וההתחברות לא הושלמה."""


class AccountInfoRequiredError(SignInRequiredError):
    """החשבון מחובר, אבל eBay מעבירה לטופס 'Complete your account info' (אין כתובת) במקום לסל."""

    MESSAGE = ("eBay redirects to 'Complete your account info' (the test account has no address), "
               "so the cart can't be used. Complete it once in a regular browser, or run as guest.")


class BasePage:
    """בסיס לכל ה-Page Objects: ניווט עמיד (ניסיון חוזר, זיהוי חסימה ובדיקת בוטים), המתנות,
    צילומי מסך וטיפול בחלונות קופצים. כל עמוד יורש מכאן ומוסיף רק את הלוקייטורים והפעולות שלו."""
    # טקסטים שמעידים על עמוד חסימה / שגיאה כללית של eBay
    BLOCK_MARKERS = (
        "something went wrong on our end",
        "pardon our interruption",
        "please verify yourself",
    )

    # עמוד בדיקת דפדפן של eBay - עובר לבד אחרי כמה שניות, לא חסימה
    BROWSER_CHECK_MARKER = "checking your browser before you access ebay"
    BROWSER_CHECK_WAIT_SEC = 180      # בפועל עוברת בין שניות לכ-3 דקות, לפי עומס ורשת

    # חלון "Are you shipping to ...?" קופץ מעל העמוד ובולע לחיצות והקלדות. לוקייטורי טקסט/role
    # ולא XPath: החלון מוזרק דינמית ו-XPath לא חודר Shadow DOM
    SHIP_TO_TEXT = re.compile(r"Are you shipping to", re.IGNORECASE)

    def __init__(self, page: Page):
        self.page = page
        self.logger = logging.getLogger(self.__class__.__name__)

    @classmethod
    def install_popup_handlers(cls, page: Page) -> None:
        """Playwright מפעיל את ה-handler אוטומטית לפני כל פעולה, בכל פעם שהחלון מופיע."""
        page.add_locator_handler(page.get_by_text(cls.SHIP_TO_TEXT).first,
                                 lambda: cls(page).confirm_ship_to_if_shown())

    def confirm_ship_to_if_shown(self) -> bool:
        """מאשר את חלון המיקום אם הוא מוצג. האישור טוען את העמוד מחדש; זמן לחיצה קצר כדי שחלון
        שנעלם בינתיים לא יתקע את הטסט."""
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
        """ניווט עם זיהוי חסימה. timeout בטעינה לא מפיל: ממשיכים עם מה שנטען ומחכים לאלמנט ספציפי."""
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
        """timeout לא מפיל (ממשיכים עם מה שנטען). שגיאת רשת (net::ERR_ABORTED וכד' - ניווט שנקטע
        ע"י הפניה של האתר או סינון ברשת) מקבלת ניסיון חוזר אחרי המתנה קצרה."""
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
        """'Checking your browser before you access eBay' מפנה לבד לתוכן; ממתינים במקום להיכשל.
        בדיקה בלולאה מצד Python: בזמן ההפניה העמוד ריק לרגע וקוד שרץ בדפדפן נשבר."""
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
        """עמוד הבדיקה זעיר ומחזיר טקסט מיד. עמוד שלא החזיר טקסט בזמן הוא עמוד כבד (למשל דף הבית
        ברשת איטית) - כלומר לא עמוד הבדיקה. בעבר timeout כזה נחשב בטעות "עדיין בבדיקה"."""
        try:
            return self.BROWSER_CHECK_MARKER in self.page.inner_text("body", timeout=10000).lower()
        except PlaywrightTimeout:
            return False
        except PlaywrightError:
            return True                 # העמוד באמצע הפניה (ההקשר הוחלף) - עדיין בתוך הבדיקה

    def is_blocked(self) -> bool:
        """True אם eBay הציגה עמוד חסימה / שגיאה במקום התוכן ('Something went wrong' וכד')."""
        try:
            text = self.page.inner_text("body", timeout=10000).lower()
        except PlaywrightError:
            return False
        return any(marker in text for marker in self.BLOCK_MARKERS)

    # בודקים דומיין ולא מחרוזת בכתובת: כתובת ההתחברות מכילה ru=https://cart.ebay.com/...
    def _host(self) -> str:
        return urlparse(self.page.url).netloc

    def is_signin_page(self) -> bool:
        return self._host().startswith("signin.")

    def is_cart_page(self) -> bool:
        return self._host().startswith("cart.")

    def is_account_upgrade_page(self) -> bool:
        """הטופס 'Complete your account info' (signup.ebay.com/fpa/upgrade)."""
        return "/fpa/upgrade" in self.page.url or self._host().startswith("signup.")

    def wait_for_element_visible(self, selector: str, timeout: int = None) -> Locator:
        """ממתין שהאלמנט הראשון שמתאים יוצג ומחזיר אותו. בלי timeout - ברירת המחדל של הפרופיל."""
        locator = self.page.locator(selector).first
        locator.wait_for(state="visible", timeout=timeout)   # None = ברירת המחדל של הפרופיל
        return locator

    def is_visible_within(self, selector: str, timeout: int) -> bool:
        """כמו wait_for_element_visible, אבל מחזיר True/False במקום לזרוק שגיאה."""
        try:
            self.wait_for_element_visible(selector, timeout)
            return True
        except PlaywrightTimeout:
            return False

    @allure.step("לכידת צילום מסך: {name}")
    def take_screenshot(self, name: str) -> str:
        """צילום מסך מלא ל-reports/screenshots ולדוח Allure. כישלון בצילום לא מפיל את הטסט."""
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
