import allure
import pytest

from utils.config import Credentials, Scenario, load_scenarios


@pytest.mark.e2e
@allure.feature("eBay E2E Purchase Flow")
@allure.story("Search under price, add to cart, verify cart total within budget")
@pytest.mark.parametrize("scenario", load_scenarios(), ids=lambda s: s.id)
def test_ebay_e2e_purchase_flow(ebay, scenario: Scenario):
    """The full scenario of the assignment. ebay = business-process layer (services), scenario = input from test_data.json."""
    # 1. Authentication: with EBAY_USER/EBAY_PASS - sign in; if eBay blocks it (CAPTCHA not completed,
    #    account without an address) - continue as guest, marked in the report. No credentials - guest.
    ebay.authenticate(Credentials.from_env())
    # Start from an empty cart so leftovers from earlier runs don't inflate the total (emptied at the end too)
    ebay.clear_cart()

    # 2. Search with a price condition (4.1): up to limit items with price <= max_price, including paging.
    #    Fewer items (even 0) is a valid result of the function - but with no items there's nothing to test.
    urls = ebay.search_items_by_name_under_price(
        scenario.search_query, scenario.max_price, scenario.limit, scenario.max_pages)
    assert urls, f"No items found matching the criteria. Search report: {ebay.search_report}"

    # 3. addItemsToCart (4.2): each item in its own tab, random variants, Add to cart.
    #    An item that can't be added is replaced by a spare from the same search, so the cart gets them all.
    added = ebay.add_items_to_cart(urls, spare_urls=ebay.spare_urls)
    assert len(added) == len(urls), (
        f"Only {len(added)}/{len(urls)} items were added to the cart and no spare candidates "
        "were left (see item_*_add_failure screenshots)")

    # Extra check: the items are in the cart itself (count + IDs), not only confirmed on the item page
    ebay.assert_items_in_cart(added)
    # 4. assertCartTotalNotExceeds (4.3): items total <= budget per item * number of items added
    ebay.assert_cart_total_not_exceeds(scenario.budget_per_item, len(added))
