import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_DATA_FILE = PROJECT_ROOT / "config" / "test_data.json"
PROFILES_FILE = PROJECT_ROOT / "config" / "profiles.json"
AUTH_STATE_FILE = PROJECT_ROOT / ".auth" / "ebay_state.json"
REPORTS_DIR = PROJECT_ROOT / "reports"


@dataclass(frozen=True)
class Scenario:
    id: str
    search_query: str
    max_price: float
    budget_per_item: float
    currency: str = "USD"
    limit: int = 5
    max_pages: int = 5


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str

    # Credentials from env vars
    @classmethod
    def from_env(cls) -> Optional["Credentials"]:
        username, password = os.getenv("EBAY_USER"), os.getenv("EBAY_PASS")
        return cls(username, password) if username and password else None

    # Mask email and password
    def __repr__(self) -> str:
        name, _, domain = self.username.partition("@")
        masked = f"{name[:3]}***@{domain}" if domain else f"{name[:3]}***"
        return f"Credentials(username='{masked}', password='***')"


@dataclass(frozen=True)
class Profile:
    name: str
    headless: bool
    block_images: bool
    default_timeout_ms: int
    login_wait_sec: int


# Load test scenarios from JSON
def load_scenarios(path: Path = TEST_DATA_FILE) -> List[Scenario]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Scenario(**item) for item in data["scenarios"]]


# Load run profile + env overrides
def load_profile(path: Path = PROFILES_FILE) -> Profile:
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


# Int from env var
def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


# Bool from env var
def env_flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")
