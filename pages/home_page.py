import allure

from .base_page import BasePage


class HomePage(BasePage):
    URL = "https://www.ebay.com/"

    @allure.step("כניסה לאתר דרך דף הבית")
    def open(self) -> None:
        self.navigate(self.URL)
