"""טעינת קלטי הבדיקה (Data-Driven) ופרטי התחברות. סודות מגיעים רק ממשתני סביבה, לעולם לא מקובץ."""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_DATA_FILE = PROJECT_ROOT / "config" / "test_data.json"
PROFILES_FILE = PROJECT_ROOT / "config" / "profiles.json"
# מצב התחברות שמור (עוגיות + localStorage) - נוצר אחרי התחברות מוצלחת ונטען בהרצות הבאות
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

    @classmethod
    def from_env(cls) -> Optional["Credentials"]:
        username, password = os.getenv("EBAY_USER"), os.getenv("EBAY_PASS")
        return cls(username, password) if username and password else None

    def __repr__(self) -> str:                     # שלא תודפס סיסמה ללוג/דוח בטעות
        return f"Credentials(username={self.username!r}, password='***')"


@dataclass(frozen=True)
class Profile:
    """פרופיל הרצה (סביבה): איך מריצים, להבדיל מ-Scenario שקובע מה בודקים."""
    name: str
    headless: bool
    block_images: bool
    default_timeout_ms: int
    login_wait_sec: int


def load_scenarios(path: Path = TEST_DATA_FILE) -> List[Scenario]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Scenario(**item) for item in data["scenarios"]]


def load_profile(path: Path = PROFILES_FILE) -> Profile:
    """הפרופיל נבחר ב-EBAY_PROFILE (ברירת מחדל local). משתני סביבה ספציפיים גוברים על הפרופיל:
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
