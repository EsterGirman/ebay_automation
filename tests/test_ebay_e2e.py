import allure
import pytest

from utils.config import Credentials, Scenario, load_scenarios


@pytest.mark.e2e
@allure.feature("eBay E2E Purchase Flow")
@allure.story("Search under price, add to cart, verify cart total within budget")
@pytest.mark.parametrize("scenario", load_scenarios(), ids=lambda s: s.id)
def test_ebay_e2e_purchase_flow(ebay, scenario: Scenario):
    """התרחיש המלא מהתרגיל. ebay = שכבת התהליך העסקי (services), scenario = קלט מ-test_data.json."""
    # 1. הזדהות: עם EBAY_USER/EBAY_PASS - התחברות; אם eBay חוסמת (CAPTCHA שלא הושלם, חשבון בלי
    #    כתובת) - ממשיכים כאורח, מסומן בדוח. בלי פרטי התחברות - אורח מההתחלה.
    ebay.authenticate(Credentials.from_env())
    # מתחילים מסל ריק, כדי שפריטים מהרצות קודמות לא ינפחו את הסכום (הסל מתרוקן גם בסוף - fixture ebay)
    ebay.clear_cart()

    # 2. חיפוש עם תנאי מחיר (4.1): עד limit פריטים שמחירם <= max_price, כולל מעבר עמודים.
    #    פחות פריטים (וגם 0) זו תוצאה תקינה של הפונקציה - אבל בלי פריטים אין מה לבדוק בהמשך.
    urls = ebay.search_items_by_name_under_price(
        scenario.search_query, scenario.max_price, scenario.limit, scenario.max_pages)
    assert urls, f"No items found matching the criteria. Search report: {ebay.search_report}"

    # 3. addItemsToCart (4.2): כל פריט בלשונית משלו, וריאנטים אקראיים, Add to cart.
    #    פריט שלא ניתן להוסיף מוחלף בפריט רזרבי מאותו חיפוש, כדי שבסל יהיו כל הפריטים שהחיפוש החזיר.
    added = ebay.add_items_to_cart(urls, spare_urls=ebay.spare_urls)
    assert len(added) == len(urls), (
        f"Only {len(added)}/{len(urls)} items were added to the cart and no spare candidates "
        "were left (see item_*_add_failure screenshots)")

    # אימות נוסף: הפריטים נמצאים בסל עצמו (מספר + מזהים), ולא רק אושרו בדף הפריט
    ebay.assert_items_in_cart(added)
    # 4. assertCartTotalNotExceeds (4.3): סכום הפריטים <= התקציב לפריט * מספר הפריטים שנוספו
    ebay.assert_cart_total_not_exceeds(scenario.budget_per_item, len(added))
