import allure

from .base_page import BasePage


class HomePage(BasePage):
    """דף הבית של eBay - נקודת הכניסה לתרחיש."""
    URL = "https://www.ebay.com/"

    @allure.step("כניסה לאתר דרך דף הבית")
    def open(self) -> None:
        """נכנסים דרך דף הבית כמו משתמש: דפדפן חדש שקופץ ישר לעמוד פנימי (כמו הסל) מקבל מ-eBay
        "Something went wrong" עד שנוצרות עוגיות הבסיס של האתר."""
        self.navigate(self.URL)
