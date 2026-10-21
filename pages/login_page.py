from typing import Optional
from urllib.parse import urlparse

import allure
from playwright.sync_api import Page

from utils.config import AUTH_STATE_FILE, Credentials, load_profile
from .base_page import BasePage, SignInRequiredError


class LoginPage(BasePage):
    SIGNIN_URL = "https://signin.ebay.com/"
    PROTECTED_URL = "https://www.ebay.com/mye/myebay/summary"  # requires full sign-in

    USERNAME_INPUT = "//input[@id='userid']"
    CONTINUE_BUTTON = "//button[@id='signin-continue-btn']"
    PASSWORD_INPUT = "//input[@id='pass']"
    SUBMIT_BUTTON = "//button[@id='sgnBt']"
    RETRY_SEC = 15

    def __init__(self, page: Page, credentials: Optional[Credentials] = None):
        super().__init__(page)
        self.credentials = credentials or Credentials.from_env()
        self.manual_wait_ms = load_profile().login_wait_sec * 1000

    @allure.step("בדיקה האם המשתמש מחובר")
    def is_signed_in(self) -> bool:
        self.navigate(self.PROTECTED_URL)
        return not (self.is_signin_page() or self.is_verification_page())

    @allure.step("הזדהות למערכת")
    def login(self) -> None:
        if self.is_signed_in():
            self.logger.info("Already signed in (saved auth state)")
            return
        self.complete_sign_in_if_prompted()
        if not self.is_signed_in():
            self.take_screenshot("signin_not_verified")
            raise SignInRequiredError("Sign-in looked complete but My eBay still asks to sign in.")
        self._save_auth_state()
        self.logger.info("Signed in (verified on My eBay)")

    @allure.step("השלמת התחברות")
    def complete_sign_in_if_prompted(self) -> None:
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
        locator = self.page.locator(field)
        if retry:
            locator.click()
            locator.press("Control+A")
            locator.press("Backspace")
            locator.press_sequentially(value, delay=80)
        else:
            locator.fill(value)
        if locator.input_value() != value:
            locator.fill(value)  # a key was lost
            if locator.input_value() != value:
                self.logger.warning("Sign-in field value did not match - not submitting")
                return
        if retry:
            locator.press("Enter")
        else:
            self.page.locator(button).click()

    def is_verification_page(self) -> bool:
        url = self.page.url
        return ("/splashui/captcha" in url or "/stepup" in url
                or urlparse(url).netloc.startswith("accounts."))

    def _save_auth_state(self) -> None:
        AUTH_STATE_FILE.parent.mkdir(exist_ok=True)
        self.page.context.storage_state(path=str(AUTH_STATE_FILE))
        self.logger.info(f"Auth state saved: {AUTH_STATE_FILE}")
