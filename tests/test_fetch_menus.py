import unittest
from pathlib import Path

from scraper import fetch_menus as MODULE

ROOT = Path(__file__).resolve().parents[1]


class ParseMenuHtmlTest(unittest.TestCase):
    def test_rowspan_and_colspan_are_expanded(self):
        html = (ROOT / "tests" / "fixtures" / "diet_sample.html").read_text(
            encoding="utf-8"
        )

        parsed = MODULE.parse_menu_html(html)

        self.assertEqual(parsed["weekRange"], "2026.07.27. ~ 2026.08.02.")
        meals = parsed["days"]["2026-07-30"]["중식"]
        self.assertEqual([meal["title"] for meal in meals], ["중식A", "중식B"])
        self.assertEqual(meals[0]["content"], "제육덮밥\n배추김치")
        self.assertEqual(meals[0]["notes"], "")
        self.assertEqual(
            meals[1]["notes"], "재료 수급에 따라 변경될 수 있습니다."
        )

    def test_empty_menu_rows_are_skipped(self):
        html = (ROOT / "tests" / "fixtures" / "diet_sample.html").read_text(
            encoding="utf-8"
        )

        parsed = MODULE.parse_menu_html(html)

        self.assertNotIn("조식", parsed["days"]["2026-07-30"])
        self.assertEqual(
            parsed["days"]["2026-07-31"]["석식"][0]["content"],
            "돈가스\n쌀밥",
        )


if __name__ == "__main__":
    unittest.main()
