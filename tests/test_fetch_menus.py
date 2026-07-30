import unittest
from datetime import datetime
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


class MergeHistoryTest(unittest.TestCase):
    def test_history_url_is_restricted_to_github_pages_json(self):
        self.assertTrue(
            MODULE._is_allowed_history_url(
                "https://tester.github.io/ku-meal/data/menu.json"
            )
        )
        self.assertFalse(
            MODULE._is_allowed_history_url(
                "https://tester.github.io/ku-meal/data/menu.json?redirect=1"
            )
        )
        self.assertFalse(
            MODULE._is_allowed_history_url(
                "https://example.com/ku-meal/data/menu.json"
            )
        )

    def test_recent_history_is_kept_and_current_menu_wins(self):
        old_entry = {
            "title": "",
            "content": "과거 메뉴",
            "notes": "",
            "unexpected": "제거 대상",
        }
        current_entry = {"title": "", "content": "새 메뉴", "notes": ""}
        snapshot = {
            "schemaVersion": 1,
            "restaurants": {
                "science": {
                    "status": "ok",
                    "days": {
                        "2026-07-30": {"중식": [current_entry]},
                    },
                },
            },
        }
        history = {
            "schemaVersion": 1,
            "restaurants": {
                "science": {
                    "days": {
                        "2026-04-01": {"중식": [old_entry]},
                        "2026-07-29": {"중식": [old_entry]},
                        "2026-07-30": {"중식": [old_entry]},
                    },
                },
            },
        }

        merged = MODULE.merge_history(
            snapshot,
            history,
            datetime(2026, 7, 30, 12, tzinfo=MODULE.SEOUL),
        )
        days = merged["restaurants"]["science"]["days"]

        self.assertNotIn("2026-04-01", days)
        self.assertEqual(days["2026-07-29"]["중식"][0]["content"], "과거 메뉴")
        self.assertNotIn("unexpected", days["2026-07-29"]["중식"][0])
        self.assertEqual(days["2026-07-30"]["중식"][0]["content"], "새 메뉴")
        self.assertEqual(merged["historyRetentionDays"], 90)

    def test_failed_current_fetch_can_retain_recent_history(self):
        snapshot = {
            "schemaVersion": 1,
            "restaurants": {
                "science": {"status": "error", "days": {}},
            },
        }
        history = {
            "schemaVersion": 1,
            "restaurants": {
                "science": {
                    "days": {
                        "2026-07-29": {
                            "중식": [
                                {"title": "", "content": "보존 메뉴", "notes": ""}
                            ],
                        },
                    },
                },
            },
        }

        merged = MODULE.merge_history(
            snapshot,
            history,
            datetime(2026, 7, 30, 12, tzinfo=MODULE.SEOUL),
        )

        self.assertIn("2026-07-29", merged["restaurants"]["science"]["days"])

    def test_snapshot_size_limit_prunes_oldest_dates(self):
        large_entry = {"title": "", "content": "가" * 400, "notes": ""}
        snapshot = {
            "schemaVersion": 1,
            "restaurants": {
                "science": {
                    "days": {
                        "2026-07-28": {"중식": [large_entry]},
                        "2026-07-29": {"중식": [large_entry]},
                        "2026-07-30": {"중식": [large_entry]},
                    },
                },
            },
        }

        limited = MODULE.enforce_snapshot_size(snapshot, maximum_bytes=1600)
        encoded = (
            MODULE.json.dumps(limited, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")

        self.assertLessEqual(len(encoded), 1600)
        self.assertNotIn("2026-07-28", limited["restaurants"]["science"]["days"])
        self.assertGreater(limited["historyPrunedDays"], 0)


if __name__ == "__main__":
    unittest.main()
