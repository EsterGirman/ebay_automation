# ReadMeAIBugs – סקירת קוד הבדיקה שנכתב בעזרת AI

עברתי על הטסט. בקצרה: הוא לא בודק כלום, והוא גם לא יכול לעבור – הסלקטורים לא קיימים באתר שהוא
פותח. מעבר לזה יש בעיות של ניהול משאבים והמתנות, שיגרמו לנפילות אקראיות גם אחרי שהסלקטורים יתוקנו.
סידרתי את הבעיות מהחמורה לקלה.

---

## 1. אין אף בדיקה בטסט

```python
results = page.locator(".result-item")
```

הטסט מחפש, לוחץ, מגדיר את `results` – ונגמר. אין `assert` ואין `expect`, אז הוא יסומן כ-Passed בכל
מקרה שבו הדף לא קורס: גם אם החיפוש לא החזיר כלום, וגם אם הוא החזיר תוצאות לא נכונות. טסט כזה גרוע
מטסט חסר, כי הוא נותן תחושת ביטחון בלי שום כיסוי.

חשוב לדעת ש-`page.locator(...)` לא ניגש לעמוד בכלל – Locator ב-Playwright "עצל", והוא מחפש את
האלמנט רק כשמשתמשים בו. כלומר השורה הזו לא בודקת אפילו שיש תוצאות.

**תיקון** – לאמת את מה שהטסט אמור להוכיח, עם `expect` של Playwright (שגם ממתין לתוצאות):

```python
expect(results.first).to_be_visible()
expect(results).not_to_have_count(0)
```

---

## 2. הסלקטורים לא קיימים באתר, ו-`.button` כללי מדי

```python
page.goto("https://example.com")
search_box = page.locator("#search")
page.locator(".button").click()
results = page.locator(".result-item")
```

ב-example.com אין שדה חיפוש, אין כפתור ואין תוצאות. הטסט ייפול כבר ב-`fill` אחרי 30 שניות של
המתנה. נראה שה-AI "השלים" סלקטורים שנראים הגיוניים, בלי קשר לאתר האמיתי.

וגם באתר שבו הם כן קיימים, `.button` הוא class שמתאים לכל כפתור בעמוד. אם יש יותר מאחד, Playwright
ייכשל על strict mode violation, ואם לא – הוא עלול ללחוץ על כפתור אחר ממה שהתכוונו.

**תיקון** – כתובת האתר מקונפיגורציה ולא קבועה בקוד, ולוקייטורים לפי תפקיד וטקסט, כמו שמשתמש רואה
את העמוד:

```python
page.goto(BASE_URL)
page.get_by_role("searchbox").fill("playwright testing")
page.get_by_role("button", name="Search").click()
results = page.get_by_test_id("result-item")
```

---

## 3. `time.sleep` במקום המתנה אמיתית

```python
time.sleep(2)
...
time.sleep(3)
```

המתנה קבועה מפסידה בשני הכיוונים: כשהאתר מהיר, הטסט מבזבז 5 שניות בכל הרצה; כשהאתר איטי (רשת
עמוסה, שרת CI חלש), 2 שניות לא מספיקות והטסט נופל. זו הסיבה הקלאסית לטסטים "שלפעמים עוברים".

ב-Playwright אין צורך בזה: `fill`, `click` ו-`expect` כבר ממתינים בעצמם עד שהאלמנט מוצג, פעיל
ויציב.

**תיקון** – למחוק את שתי השורות ואת `import time`. ההמתנה תגיע מה-`expect` שבסעיף 1.

---

## 4. Playwright לא נסגר, והדפדפן נשאר פתוח כשהטסט נופל

```python
browser = sync_playwright().start().chromium.launch()
...
browser.close()
```

שתי בעיות כאן:
- `sync_playwright().start()` מפעיל תהליך של Playwright שאף אחד לא סוגר (אין `stop()`). נסגר רק
  הדפדפן.
- `browser.close()` נמצא בסוף הפונקציה, אז אם משהו נופל באמצע (וזה קורה כאן – ראו סעיף 2), הוא לא
  ירוץ בכלל.

אחרי כמה הרצות נשארים תהליכי Chromium ו-Node "יתומים" שאוכלים זיכרון, ובשרת CI זה מצטבר.

**תיקון** – `with`, שסוגר הכול גם כשיש Exception:

```python
with sync_playwright() as p:
    browser = p.chromium.launch()
    ...
```

(בפרויקט עם pytest עדיף עוד יותר להשתמש ב-fixture `page` של pytest-playwright, שמנהל את כל זה לבד.)

---

## 5. ייבוא של Selenium בטסט של Playwright

```python
from selenium import webdriver
```

לא בשימוש. זה כנראה שארית מדוגמה אחרת שה-AI ערבב. הנזק המעשי: הטסט ייפול ב-ImportError בכל מחשב
שאין בו Selenium מותקן, ומי שקורא את הקוד יחשוב ששתי הספריות בשימוש.

**תיקון** – למחוק את השורה.

---

## 6. המשתנה `results` לא בשימוש

אותה שורה מסעיף 1, מזווית אחרת: משתנה שמוגדר בסוף פונקציה ולא נעשה בו כלום הוא סימן מובהק לכך
שמשהו חסר – כאן, הבדיקה עצמה. שווה לשים לב לזה כבר בקריאה ראשונה של קוד (וגם linter כמו flake8 או
ruff יתפוס את זה).

**תיקון** – להשתמש בו ב-`expect`, כמו בסעיף 1.

---

## הקוד אחרי התיקונים

```python
from playwright.sync_api import expect, sync_playwright

BASE_URL = "https://my-app.example"   # האתר הנבדק בפועל; בפרויקט אמיתי - מקובץ קונפיגורציה


def test_search_functionality():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        page.goto(BASE_URL)
        page.get_by_role("searchbox").fill("playwright testing")
        page.get_by_role("button", name="Search").click()

        results = page.get_by_test_id("result-item")
        expect(results.first).to_be_visible()
        expect(results).not_to_have_count(0)
```

אין צורך ב-`browser.close()` – ה-`with` סוגר את Playwright ואת הדפדפן בסוף, גם אם אחד ה-`expect`
נכשל. הסלקטורים כאן הם דוגמה: צריך להתאים אותם לאתר האמיתי, עדיף לפי role או `data-testid`.
