import allure

from .base_page import BasePage


class HomePage(BasePage):
    """eBay homepage - the entry point of the scenario."""
    URL = "https://www.ebay.com/"

    @allure.step("כניסה לאתר דרך דף הבית")
    def open(self) -> None:
        """Enter through the homepage like a user: a fresh browser that jumps straight to an inner page
        (like the cart) gets eBay's "Something went wrong" until the site's base cookies exist."""
        self.navigate(self.URL)
