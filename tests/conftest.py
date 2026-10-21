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
_AUTH_MARKERS = ("captcha", "signin.ebay.com", "accounts.ebay.com")  # CAPTCHA needs images


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    for option in ("allure_report_dir", "output", "xmlpath"):
        value = getattr(config.option, option, None)
        if value and not Path(value).is_absolute():
            setattr(config.option, option, str(config.rootpath / value))


def pytest_sessionfinish(session):
    results_dir = getattr(session.config.option, "allure_report_dir", None)
    if not results_dir or not Path(results_dir).is_dir():
        return
    props = {
        "Site": "https://www.ebay.com",
        "Browser": f"chromium ({session.config.getoption('--browser-channel') or 'bundled'})",
        "Credentials": "EBAY_USER provided" if Credentials.from_env() else "none (guest)",
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
    return load_profile()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args, profile: Profile):
    return {
        **browser_type_launch_args,
        "headless": profile.headless,
        "args": ["--start-maximized", "--disable-blink-features=AutomationControlled"],
        "ignore_default_args": ["--enable-automation"],
    }


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    args = {**browser_context_args, "locale": "en-US", "no_viewport": True}
    if AUTH_STATE_FILE.exists():
        args["storage_state"] = str(AUTH_STATE_FILE)
    return args


@pytest.fixture
def context(context: BrowserContext, profile: Profile) -> BrowserContext:
    context.set_default_timeout(profile.default_timeout_ms)
    context.on("page", BasePage.install_popup_handlers)
    if profile.block_images:
        context.route(_HEAVY_MEDIA, _block_media_except_auth)
    return context


def _block_media_except_auth(route) -> None:
    try:
        frame_url = route.request.frame.url
    except Exception:
        frame_url = ""
    if any(marker in url for url in (route.request.url, frame_url)
           for marker in _AUTH_MARKERS):
        route.continue_()
    else:
        route.abort()


def _attach_failure_artifacts(request, page: Page) -> None:
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
    for png in SCREENSHOTS_DIR.glob("*.png"):
        png.unlink(missing_ok=True)


@pytest.fixture
def ebay(page: Page, scenario: Scenario, request,
         fresh_screenshots_dir) -> Iterator[EBayWorkflowService]:
    service = EBayWorkflowService(page, currency=scenario.currency)
    yield service
    _attach_failure_artifacts(request, page)  # before cleanup
    if env_flag("EBAY_KEEP_CART", False) or page.is_closed():
        return
    try:
        service.clear_cart()
    except Exception as e:
        logging.getLogger("teardown").warning(f"Cart cleanup failed: {e}")


@pytest.fixture
def page(page: Page, request) -> Iterator[Page]:
    yield page
    _attach_failure_artifacts(request, page)
