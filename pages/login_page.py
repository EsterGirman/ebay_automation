from typing import Optional
from urllib.parse import urlparse

import allure
from playwright.sync_api import Page

from utils.config import AUTH_STATE_FILE, Credentials, load_profile
from .base_page import BasePage, SignInRequiredError


class LoginPage(BasePage):
    """התחברות ל-eBay: מילוי הטופס, המתנה ל-CAPTCHA / קוד אימות, אימות ההצלחה ושמירת המצב."""
    SIGNIN_URL = "https://signin.ebay.com/"
    # עמוד שדורש התחברות מלאה. "Hi <name>" בכותרת לא מספיק: eBay מזהה סשן ישן
    # ובכל זאת דורשת סיסמה לפני פעולות סל
    PROTECTED_URL = "https://www.ebay.com/mye/myebay/summary"

    USERNAME_INPUT = "//input[@id='userid']"
    CONTINUE_BUTTON = "//button[@id='signin-continue-btn']"
    PASSWORD_INPUT = "//input[@id='pass']"
    SUBMIT_BUTTON = "//button[@id='sgnBt']"
    RETRY_SEC = 15          # אחרי כמה שניות בלי תגובה חוזרים על לחיצה שלא נקלטה

    def __init__(self, page: Page, credentials: Optional[Credentials] = None):
        super().__init__(page)
        self.credentials = credentials or Credentials.from_env()
        # זמן להשלמה ידנית בחלון (CAPTCHA / קוד אימות) - eBay לא מאפשרת לעקוף אותם וגם לא צריך
        self.manual_wait_ms = load_profile().login_wait_sec * 1000

    @allure.step("בדיקה האם המשתמש מחובר")
    def is_signed_in(self) -> bool:
        """נכנס לעמוד שדורש התחברות מלאה (My eBay). אם eBay מעבירה למסך כניסה או אימות - לא מחובר."""
        self.navigate(self.PROTECTED_URL)
        return not (self.is_signin_page() or self.is_verification_page())

    @allure.step("הזדהות למערכת")
    def login(self) -> None:
        """מתחבר אם צריך ושומר את מצב ההתחברות לקובץ, כדי שההרצות הבאות ידלגו על מסך ההתחברות."""
        if self.is_signed_in():
            self.logger.info("Already signed in (saved auth state)")
            return
        self.complete_sign_in_if_prompted()
        # אימות סופי מול עמוד שדורש התחברות מלאה, ורק אז שומרים את המצב לקובץ
        if not self.is_signed_in():
            self.take_screenshot("signin_not_verified")
            raise SignInRequiredError("Sign-in looked complete but My eBay still asks to sign in.")
        self._save_auth_state()
        self.logger.info("Signed in (verified on My eBay)")

    @allure.step("השלמת התחברות")
    def complete_sign_in_if_prompted(self) -> None:
        """מכונת מצבים פשוטה: בכל שנייה בודקים איזה מסך מוצג ופועלים בהתאם, עד שיוצאים מתהליך
        ההתחברות או שנגמר הזמן (EBAY_LOGIN_WAIT_SEC). מטפל ב:
        - טופס מלא (שם משתמש -> Continue -> סיסמה) וב-'Welcome back' (סיסמה בלבד);
        - לחיצה שלא נקלטה (העמוד נטען לאט והכפתור עוד לא פעיל) - חוזרים עליה אחרי RETRY_SEC;
        - CAPTCHA ואימות זהות (קוד למייל/SMS) - לא נעקפים: ממתינים להשלמה ידנית בחלון."""
        # גם CAPTCHA/אימות שקופצים באמצע התהליך (למשל אחרי 'Add to cart') - ממתינים להם ולא
        # סוגרים את הלשונית מתחת לידיים של מי שפותר אותם
        if not (self.is_signin_page() or self.is_verification_page()):
            return
        self.logger.info("Sign-in / verification page shown, completing it")
        if not self.credentials:
            self.logger.warning("No EBAY_USER/EBAY_PASS - waiting for manual sign-in in the browser")
        last_action = {"username": -self.RETRY_SEC, "password": -self.RETRY_SEC}
        reported_challenge = None
        for second in range(self.manual_wait_ms // 1000):
            if self.is_verification_page():
                if reported_challenge != self.page.url.split("?")[0]:
                    reported_challenge = self.page.url.split("?")[0]
                    self.logger.warning("eBay asks for verification (CAPTCHA / code) - "
                                        "complete it in the browser window")
                    self.take_screenshot("signin_verification")
            elif self.is_signin_page():
                if self.credentials:
                    self._sign_in_step(second, last_action)
            else:
                break
            self.page.wait_for_timeout(1000)
        if self.is_signin_page() or self.is_verification_page():
            self.take_screenshot("signin_not_completed")
            raise SignInRequiredError(
                "eBay requires signing in and it was not completed. Check EBAY_USER/EBAY_PASS, or "
                "finish the CAPTCHA / verification code in the browser within "
                f"{self.manual_wait_ms // 1000}s (EBAY_LOGIN_WAIT_SEC).")
        self._save_auth_state()
        self.logger.info(f"Sign-in completed | url={self.page.url}")

    def _sign_in_step(self, second: int, last_action: dict) -> None:
        """פעולה אחת לפי המסך: סיסמה אם השדה מוצג, אחרת שם משתמש. לא חוזרים על אותה פעולה
        לפני RETRY_SEC, כדי לתת לעמוד זמן להגיב."""
        if self.page.locator(self.PASSWORD_INPUT).is_visible():
            step, field, value, button = ("password", self.PASSWORD_INPUT,
                                          self.credentials.password, self.SUBMIT_BUTTON)
        elif self.page.locator(self.USERNAME_INPUT).is_visible():
            step, field, value, button = ("username", self.USERNAME_INPUT,
                                          self.credentials.username, self.CONTINUE_BUTTON)
        else:
            return
        if second - last_action[step] < self.RETRY_SEC:
            return
        retry = last_action[step] >= 0
        if retry:
            self.logger.warning(f"Sign-in {step} step did not respond - typing and pressing Enter")
        self._submit_field(field, value, button, retry)
        last_action[step] = second

    def _submit_field(self, field: str, value: str, button: str, retry: bool) -> None:
        """ניסיון ראשון: מילוי ולחיצה על הכפתור. ניסיון חוזר: כמו משתמש - הקלדה תו-תו ו-Enter,
        כי בעמוד שנטען לאט הרכיב של eBay לא תמיד קולט fill ולחיצה (כמו בפילטר המחיר)."""
        locator = self.page.locator(field)
        if not retry:
            locator.fill(value)
            self.page.locator(button).click()
            return
        locator.click()
        locator.press("Control+A")
        locator.press("Backspace")
        locator.press_sequentially(value, delay=80)
        locator.press("Enter")

    def is_verification_page(self) -> bool:
        """CAPTCHA או אימות זהות (step-up) - שלב בתוך ההתחברות, לא התחברות שהושלמה."""
        url = self.page.url
        return ("/splashui/captcha" in url or "/stepup" in url
                or urlparse(url).netloc.startswith("accounts."))

    def _save_auth_state(self) -> None:
        AUTH_STATE_FILE.parent.mkdir(exist_ok=True)
        self.page.context.storage_state(path=str(AUTH_STATE_FILE))
        self.logger.info(f"Auth state saved: {AUTH_STATE_FILE}")
