"""Test inputs (data-driven), run profiles and credentials. Secrets come only from environment variables, never from a file."""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_DATA_FILE = PROJECT_ROOT / "config" / "test_data.json"
PROFILES_FILE = PROJECT_ROOT / "config" / "profiles.json"
# Saved auth state (cookies + localStorage) - created after a successful sign-in and loaded by later runs
AUTH_STATE_FILE = PROJECT_ROOT / ".auth" / "ebay_state.json"
REPORTS_DIR = PROJECT_ROOT / "reports"


@dataclass(frozen=True)
class Scenario:
    """One test scenario from config/test_data.json (what to test)."""
    id: str
    search_query: str
    max_price: float
    budget_per_item: float
    currency: str = "USD"
    limit: int = 5
    max_pages: int = 5


@dataclass(frozen=True)
class Credentials:
    """Credentials - only from the EBAY_USER / EBAY_PASS environment variables, never from a file."""
    username: str
    password: str

    @classmethod
    def from_env(cls) -> Optional["Credentials"]:
        username, password = os.getenv("EBAY_USER"), os.getenv("EBAY_PASS")
        return cls(username, password) if username and password else None

    def __repr__(self) -> str:
        # Allure shows step parameters - never print the password, or the full email, to the report or log
        name, _, domain = self.username.partition("@")
        masked = f"{name[:3]}***@{domain}" if domain else f"{name[:3]}***"
        return f"Credentials(username='{masked}', password='***')"


@dataclass(frozen=True)
class Profile:
    """Run profile (environment): how to run, as opposed to Scenario, which defines what to test."""
    name: str
    headless: bool
    block_images: bool
    default_timeout_ms: int
    login_wait_sec: int


def load_scenarios(path: Path = TEST_DATA_FILE) -> List[Scenario]:
    """Each scenario in the file becomes a separate test run (pytest.mark.parametrize)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Scenario(**item) for item in data["scenarios"]]


def load_profile(path: Path = PROFILES_FILE) -> Profile:
    """Profile is selected by EBAY_PROFILE (default: local). Specific environment variables override it:
    EBAY_HEADLESS, EBAY_BLOCK_IMAGES, EBAY_TIMEOUT_MS, EBAY_LOGIN_WAIT_SEC."""
    name = os.getenv("EBAY_PROFILE", "local")
    profiles = json.loads(path.read_text(encoding="utf-8"))
    if name not in profiles:
        raise ValueError(f"Unknown EBAY_PROFILE '{name}'. Available: {', '.join(profiles)}")
    p = profiles[name]
    return Profile(
        name=name,
        headless=env_flag("EBAY_HEADLESS", p["headless"]),
        block_images=env_flag("EBAY_BLOCK_IMAGES", p["block_images"]),
        default_timeout_ms=env_int("EBAY_TIMEOUT_MS", p["default_timeout_ms"]),
        login_wait_sec=env_int("EBAY_LOGIN_WAIT_SEC", p["login_wait_sec"]),
    )


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def env_flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")
