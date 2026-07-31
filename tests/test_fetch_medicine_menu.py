import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scraper import fetch_medicine_menu as MODULE


def png_header(width: int = 1224, height: int = 749) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )


def notice_payload(title: str = "[의과대학본관식당] 주간식단표(0706-0710)") -> dict:
    return {
        "list": [
            {
                "articleNo": 56028,
                "title": "[의과대학본관식당] 식대 인상 안내",
                "createdDt": 1783040902000,
                "content": "<p>안내</p>",
            },
            {
                "articleNo": 56059,
                "title": title,
                "createdDt": 1783040902000,
                "content": (
                    '<p><img src="/displayEditorFile.do?'
                    'filePath=/4/example"></p>'
                ),
            },
        ]
    }


class MedicineMenuCollectorTest(unittest.TestCase):
    def test_selects_weekly_notice_and_skips_other_restaurant_notices(self):
        notice = MODULE.select_latest_weekly_notice(notice_payload())

        self.assertEqual(notice.article_no, 56059)
        self.assertEqual(notice.week_start.isoformat(), "2026-07-06")
        self.assertEqual(notice.week_end.isoformat(), "2026-07-10")
        self.assertEqual(
            notice.source_image_url,
            "https://medicine.korea.ac.kr/displayEditorFile.do?"
            "filePath=/4/example",
        )

    def test_prefers_the_week_containing_collection_date_over_next_week(self):
        payload = notice_payload()
        payload["list"].insert(
            0,
            {
                "articleNo": 56070,
                "title": "[의과대학본관식당] 주간식단표(0713-0717)",
                "createdDt": 1783641600000,
                "content": '<img src="/displayEditorFile.do?filePath=/4/next">',
            },
        )

        notice = MODULE.select_latest_weekly_notice(
            payload,
            target_date=MODULE.date(2026, 7, 10),
        )

        self.assertEqual(notice.article_no, 56059)

    def test_rejects_a_list_without_a_weekly_menu_image(self):
        with self.assertRaisesRegex(ValueError, "주간식단표 이미지"):
            MODULE.select_latest_weekly_notice(
                {"list": [{"articleNo": 1, "title": "식대 인상 안내"}]}
            )

    def test_png_dimensions_are_validated(self):
        extension, media_type, width, height = MODULE.inspect_image(
            png_header(1200, 700)
        )
        self.assertEqual(
            (extension, media_type, width, height),
            ("png", "image/png", 1200, 700),
        )
        with self.assertRaisesRegex(ValueError, "크기"):
            MODULE.inspect_image(png_header(100, 100))

    def test_collect_writes_image_and_adds_restaurant_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            menu_path = Path(directory) / "data" / "menu.json"
            menu_path.parent.mkdir()
            menu_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "generatedAt": "2026-07-03T12:00:00+09:00",
                        "restaurants": {},
                    }
                ),
                encoding="utf-8",
            )

            restaurant = MODULE.collect_medicine_menu(
                menu_path,
                notice_fetcher=notice_payload,
                image_fetcher=lambda _: png_header(),
                now=datetime(2026, 7, 3, 12, tzinfo=MODULE.SEOUL),
            )

            image = restaurant["imageMenu"]
            self.assertEqual(image["articleNo"], 56059)
            self.assertEqual(image["imagePath"], "medicine-menu/article-56059.png")
            self.assertEqual(len(image["sha256"]), 64)
            self.assertTrue(
                (menu_path.parent / image["imagePath"]).is_file()
            )
            written = json.loads(menu_path.read_text(encoding="utf-8"))
            self.assertEqual(
                written["restaurants"]["medicine"]["shortName"],
                "의대본관",
            )


if __name__ == "__main__":
    unittest.main()
