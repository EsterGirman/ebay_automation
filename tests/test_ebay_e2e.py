import allure
import pytest

from utils.config import Credentials, Scenario, load_scenarios


# Full E2E purchase flow
@pytest.mark.e2e
@allure.feature("eBay E2E Purchase Flow")
@allure.story("Search under price, add to cart, verify cart total within budget")
@pytest.mark.parametrize("scenario", load_scenarios(), ids=lambda s: s.id)
def test_ebay_e2e_purchase_flow(ebay, scenario: Scenario):
    ebay.authenticate(Credentials.from_env())
    ebay.clear_cart()

    urls = ebay.search_items_by_name_under_price(
        scenario.search_query, scenario.max_price, scenario.limit, scenario.max_pages)
    assert urls, f"No items found matching the criteria. Search report: {ebay.search_report}"

    added = ebay.add_items_to_cart(urls, spare_urls=ebay.spare_urls)
    assert len(added) == len(urls), (
        f"Only {len(added)}/{len(urls)} items were added to the cart and no spare candidates "
        "were left (see item_*_add_failure screenshots)")

    ebay.assert_items_in_cart(added)
    ebay.assert_cart_total_not_exceeds(scenario.budget_per_item, len(added))
