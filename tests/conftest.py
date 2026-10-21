"""Fixtures מעל pytest-playwright: הדפדפן, ה-context וה-page מגיעים מהתוסף ומותאמים כאן.
Trace / צילום מסך בכישלון / ערוץ Chrome מוגדרים ב-pytest.ini."""
import logging
import re
from pathlib import Path
from typing import Iterator

import allure
import pytest
from playwright.sync_api import BrowserContext, Page

from pages.base_page import SCREENSHOTS_DIR, BasePage
from services.ebay_workflow_service import EBayWorkflowService
from utils.config import AUTH_STATE_FILE, Credentials, Profile, Scenario, env_flag, load_profile

_HEAVY_MEDIA = re.compile(r"\.(png|jpe?g|gif|webp|avif|mp4|webm)(\?|$)", re.IGNORECASE)
_AUTH_MARKERS = ("captcha", "signin.ebay.com")


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """נתיבי הדוחות יחסיים לשורש הפרויקט ולא לתיקייה שממנה הורץ pytest (PyCharm מריץ מ-tests/)."""
    for option in ("allure_report_dir", "output", "xmlpath"):
        value = getattr(config.option, option, None)
        if value and not Path(value).is_absolute():
            setattr(config.option, option, str(config.rootpath / value))


def pytest_sessionfinish(session):
    """פרטי סביבה שמוצגים בעמוד הראשי של דוח Allure."""
    results_dir = getattr(session.config.option, "allure_report_dir", None)
    if not results_dir or not Path(results_dir).is_dir():
        return
    props = {
        "Site": "https://www.ebay.com",
        "Browser": f"chromium ({session.config.getoption('--browser-channel') or 'bundled'})",
        "Auth": "signed-in (EBAY_USER)" if Credentials.from_env() else "guest",
        "Profile": load_profile().name,
        "Test data": "config/test_data.json",
    }
    (Path(results_dir) / "environment.properties").write_text(
        "\n".join(f"{k}={v}" for k, v in props.items()), encoding="utf-8")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture(scope="session")
def profile() -> Profile:
    """פרופיל ההרצה (config/profiles.json, נבחר ב-EBAY_PROFILE)."""
    return load_profile()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, profile: Profile):
    return {
        **browser_type_launch_args,
        "headless": profile.headless,
        # בלי דגל האוטומציה של Chrome (navigator.webdriver / פס "Chrome is being controlled")
        "args": ["--start-maximized", "--disable-blink-features=AutomationControlled"],
        "ignore_default_args": ["--enable-automation"],
    }


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    args = {**browser_context_args, "locale": "en-US", "no_viewport": True}
    if AUTH_STATE_FILE.exists():
        args["storage_state"] = str(AUTH_STATE_FILE)   # מדלג על מסך ההתחברות
    return args


@pytest.fixture
def context(context: BrowserContext, profile: Profile) -> BrowserContext:
    context.set_default_timeout(profile.default_timeout_ms)
    context.on("page", BasePage.install_popup_handlers)   # גם ללשוניות הפריטים
    if profile.block_images:
        # תמונות ווידאו לא נחוצים לבדיקה וכבדים; חסימתם מקצרת טעינה ברשת איטית/מסוננת
        context.route(_HEAVY_MEDIA, _block_media_except_auth)
    return context


def _block_media_except_auth(route) -> None:
    """חוסם תמונות - חוץ ממסכי ההתחברות וה-CAPTCHA: אתגר CAPTCHA בנוי מתמונות, ובלעדיהן אי אפשר
    לפתור אותו ידנית."""
    try:
        frame_url = route.request.frame.url
    except Exception:                     # בקשה שאינה שייכת ל-frame (service worker)
        frame_url = ""
    if any(marker in url for url in (route.request.url, frame_url)
           for marker in _AUTH_MARKERS):
        route.continue_()
    else:
        route.abort()


def _attach_failure_artifacts(request, page: Page) -> None:
    """צילום מסך + כתובת בכישלון, פעם אחת לטסט, לפני כל ניקוי שמשנה את מצב העמוד."""
    report = getattr(request.node, "rep_call", None)
    if (report is None or not report.failed or page.is_closed()
            or getattr(request.node, "_failure_attached", False)):
        return
    request.node._failure_attached = True
    try:
        allure.attach(page.screenshot(full_page=True), name="failure_screenshot",
                      attachment_type=allure.attachment_type.PNG)
        allure.attach(page.url, name="failure_url", attachment_type=allure.attachment_type.URI_LIST)
    except Exception:
        pass


@pytest.fixture(scope="session")
def fresh_screenshots_dir() -> None:
    """צילומי מסך מהרצות קודמות מתערבבים עם הנוכחית ומטעים; מנקים פעם אחת בתחילת ההרצה.
    רק בהרצה שכוללת טסט E2E (הרצת unit בלבד לא מוחקת כלום)."""
    for png in SCREENSHOTS_DIR.glob("*.png"):
        png.unlink(missing_ok=True)


@pytest.fixture
def ebay(page: Page, scenario: Scenario, request,
         fresh_screenshots_dir) -> Iterator[EBayWorkflowService]:
    """שכבת התהליך העסקי לטסט. Teardown (rollback): ריקון הסל גם אם הטסט נכשל, כדי שהחשבון
    יישאר נקי להרצה הבאה. EBAY_KEEP_CART=1 משאיר את הסל לבדיקה ידנית."""
    service = EBayWorkflowService(page, currency=scenario.currency)
    yield service
    _attach_failure_artifacts(request, page)      # קודם מתעדים את מצב הכישלון, אחר כך מנקים
    if env_flag("EBAY_KEEP_CART", False) or page.is_closed():
        return
    try:
        service.clear_cart()
    except Exception as e:                     # ניקוי שנכשל לא אמור להפוך טסט שעבר לכישלון
        logging.getLogger("teardown").warning(f"Cart cleanup failed: {e}")


@pytest.fixture
def page(page: Page, request) -> Iterator[Page]:
    yield page
    _attach_failure_artifacts(request, page)
