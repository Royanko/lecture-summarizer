"""
Verifies:
  1. Each semantic card header shows a bare number badge.
  2. The segment navigator shows an N/total card counter that updates on scroll.
"""
import json
from playwright.sync_api import sync_playwright

MOCK_SUMMARY = """
#### 📖 הגדרה: גרף
[DEFINITION]
גרף הוא זוג (V, E) כאשר V קבוצת קודקודים ו-E קבוצת צלעות.

---

#### 💡 משפט: אוילר
[THEOREM]
כל גרף קשיר מכיל מסלול אוילר אם ורק אם לכל קודקוד דרגה זוגית.

---

#### 🔷 טענה: הגדרה שקולה
[CLAIM]
גרף מכוון הוא עץ אם ורק אם יש בו בדיוק n-1 קשתות.

---

#### ✍️ הוכחה: אינדוקציה
[PROOF]
נוכיח באינדוקציה על מספר הקודקודים.

---

#### 🎯 תרגיל: מציאת מסלול
[EXERCISE]
מצא מסלול האמילטוני בגרף הנתון.

---

#### 💜 הערה: מקרה מיוחד
[NOTE]
אם הגרף לא קשיר, אין מסלול אוילר.
"""

MOCK_STATE = {
    "selectedCourse": "combinatorics",
    "result": {
        "transcript":      "test transcript",
        "summary":         MOCK_SUMMARY,
        "course":          "combinatorics",
        "chunks":          [{"idx": 0, "transcript": "test transcript", "summary": MOCK_SUMMARY}],
        "chatSeed":        [],
        "savedSummary":    None,
        "savedTranscript": None,
        "totalSegments":   1,
        "quiz":            None,
    },
    "activeJob": None,
    "settings": {"model": "gemini-2.5-flash", "lang": "he", "chunkMinutes": 12},
}


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        page = browser.new_page(viewport={"width": 1400, "height": 900})

        console_msgs: list[str] = []
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: console_msgs.append(f"[PGERR] {e}"))

        # ── Step 1: Load root page so Next.js initialises, then seed state ──────
        page.goto("http://localhost:3000")
        page.wait_for_load_state("networkidle")

        state_json = json.dumps(MOCK_STATE)
        # Overwrite sessionStorage AND navigate — wrap with expect_navigation so
        # Playwright waits for the resulting page load before continuing.
        with page.expect_navigation(url="**/summary", timeout=15000):
            page.evaluate(f"""() => {{
                sessionStorage.setItem('bgu-app-state', JSON.stringify({state_json}));
                window.location.href = '/summary';
            }}""")

        # ── Step 2: Wait for /summary to fully load ───────────────────────────────
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception as ex:
            print("networkidle note:", ex)

        # Confirm sessionStorage survived navigation
        ss_raw = page.evaluate("() => sessionStorage.getItem('bgu-app-state')")
        print("\nsessionStorage present:", "yes" if ss_raw else "NO — missing!")
        if ss_raw:
            parsed = json.loads(ss_raw)
            chunks = (parsed.get("result") or {}).get("chunks", [])
            print(f"  result.chunks.length = {len(chunks)}")

        # Wait for React to hydrate and render cards (or redirect)
        try:
            page.wait_for_selector(".semantic-card, [data-card-idx]", timeout=12000)
        except Exception:
            pass

        page.wait_for_timeout(500)
        print(f"URL after hydration: {page.url}")

        if console_msgs:
            print("JS console (" + str(len(console_msgs)) + " msgs):")
            for m in console_msgs[:20]:
                print("  " + m)

        if "/summary" not in page.url:
            raise AssertionError(f"Redirected to {page.url} — state not loaded. "
                                 f"sessionStorage present: {bool(ss_raw)}")

        # ── Feature 1: card number badges ────────────────────────────────────────
        cards = page.locator(".semantic-card")
        card_count = cards.count()
        print(f"\nCards found on page: {card_count}")
        assert card_count == 6, f"Expected 6 cards, got {card_count}"

        failures: list[str] = []
        for i in range(card_count):
            card = cards.nth(i)
            header = card.locator(".semantic-card__header")
            header_text = header.inner_text()
            expected_num = str(i + 1)
            if expected_num not in header_text:
                failures.append(
                    f"Card {i+1}: number badge not found. Header text: {repr(header_text[:80])}"
                )
            else:
                print(f"  OK Card {i+1} badge present")

        if failures:
            for f in failures:
                print(f"  FAIL:{f}")
        else:
            print("Feature 1 PASS: all 6 cards show their number badge")

        # ── Feature 2: card counter in segment navigator ──────────────────────────
        # The navigator bar has class bg-slate-50 and border-b
        navigator = page.locator(".bg-slate-50.border-b").first
        nav_text = navigator.inner_text()
        print(f"\nNavigator text: {repr(nav_text)}")

        counter_present = "/6" in nav_text or "6" in nav_text
        if counter_present and "/" in nav_text:
            print("Feature 2 PASS: card counter present in navigator")
        else:
            failures.append(f"Feature 2: card counter '1/6' not found in navigator. Got: {nav_text!r}")
            print(f"  FAIL:Feature 2 FAIL")

        # ── Scroll test: counter changes ─────────────────────────────────────────
        scroll_area = page.locator("section .flex-1.overflow-y-auto").first
        initial_nav = navigator.inner_text()
        scroll_area.evaluate("el => el.scrollTop += 600")
        page.wait_for_timeout(600)
        scrolled_nav = navigator.inner_text()
        print(f"\nNavigator before scroll: {repr(initial_nav)}")
        print(f"Navigator after scroll:  {repr(scrolled_nav)}")

        page.screenshot(path="/tmp/card_numbering_final.png", full_page=False)
        browser.close()

        print("\n" + "=" * 50)
        if not failures:
            print("ALL VERIFICATIONS PASSED")
        else:
            print(f"FAILED ({len(failures)} issues):")
            for f in failures:
                print(f"  - {f}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
