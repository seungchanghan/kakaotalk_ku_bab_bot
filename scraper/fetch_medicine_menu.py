#!/usr/bin/env python3
"""Collect the latest KU Medicine weekly cafeteria image for static hosting."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")
MEDICINE_ORIGIN = "https://medicine.korea.ac.kr"
NOTICE_LIST_URL = (
    f"{MEDICINE_ORIGIN}/kr/news/notice/list.do?"
    "category=&currentNum=1&searchType=title&searchKey=%EC%8B%9D%EB%8B%B9"
)
NOTICE_API_URL = f"{MEDICINE_ORIGIN}/api/article/157"
WEEKLY_TITLE_RE = re.compile(
    r"^\[의과대학본관식당\]\s*주간식단표\((\d{4})-(\d{4})\)\s*$"
)
MAX_API_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class WeeklyNotice:
    article_no: int
    title: str
    source_url: str
    source_image_url: str
    week_start: date
    week_end: date


class FirstImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.src: str | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if self.src is not None or tag.lower() != "img":
            return
        value = dict(attrs).get("src")
        if value:
            self.src = value


def _request_bytes(
    url: str,
    *,
    accept: str,
    maximum_bytes: int,
    timeout: int = 20,
) -> tuple[bytes, str]:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "KU-Meal-Bot/1.0 "
                "(educational project; contact via the GitHub repository)"
            ),
            "Accept": accept,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        parsed = urlsplit(final_url)
        if parsed.scheme != "https" or parsed.hostname != "medicine.korea.ac.kr":
            raise ValueError("의과대학 수집 요청이 허용되지 않은 주소로 이동했습니다.")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > maximum_bytes:
            raise ValueError("응답이 허용 크기를 초과했습니다.")
        payload = response.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            raise ValueError("응답이 허용 크기를 초과했습니다.")
        return payload, response.headers.get("Content-Type", "")


def fetch_notice_payload() -> dict:
    query = urlencode(
        {
            "instNo": 4,
            "boardNo": 157,
            "startIndex": 1,
            "pageRow": 10,
            "category": "",
            "title": "식당",
            "content": "",
            "searchKeyword": "",
        }
    )
    payload, _ = _request_bytes(
        f"{NOTICE_API_URL}?{query}",
        accept="application/json",
        maximum_bytes=MAX_API_BYTES,
    )
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict) or not isinstance(decoded.get("list"), list):
        raise ValueError("의과대학 공지 목록 응답 형식이 올바르지 않습니다.")
    return decoded


def fetch_image(url: str) -> bytes:
    payload, content_type = _request_bytes(
        url,
        accept="image/png,image/jpeg",
        maximum_bytes=MAX_IMAGE_BYTES,
    )
    if content_type and not content_type.lower().startswith("image/"):
        raise ValueError("공지 첨부 응답이 이미지 형식이 아닙니다.")
    return payload


def _parse_week(mmdd_start: str, mmdd_end: str, created_ms: object) -> tuple[date, date]:
    try:
        created = datetime.fromtimestamp(int(created_ms) / 1000, SEOUL).date()
        start_month, start_day = int(mmdd_start[:2]), int(mmdd_start[2:])
        end_month, end_day = int(mmdd_end[:2]), int(mmdd_end[2:])
        candidates = [
            date(year, start_month, start_day)
            for year in (created.year - 1, created.year, created.year + 1)
        ]
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("주간식단표 기간을 해석하지 못했습니다.") from error

    week_start = min(candidates, key=lambda candidate: abs((candidate - created).days))
    crosses_year = (end_month, end_day) < (start_month, start_day)
    end_year = week_start.year + int(crosses_year)
    try:
        week_end = date(end_year, end_month, end_day)
    except ValueError as error:
        raise ValueError("주간식단표 종료일을 해석하지 못했습니다.") from error
    if not 0 <= (week_end - week_start).days <= 7:
        raise ValueError("주간식단표 기간이 예상 범위를 벗어났습니다.")
    return week_start, week_end


def select_latest_weekly_notice(
    payload: dict,
    target_date: date | None = None,
) -> WeeklyNotice:
    notices: list[WeeklyNotice] = []
    for article in payload.get("list", []):
        if not isinstance(article, dict):
            continue
        title = str(article.get("title") or "").strip()
        match = WEEKLY_TITLE_RE.fullmatch(title)
        if not match:
            continue
        try:
            article_no = int(article["articleNo"])
        except (KeyError, TypeError, ValueError):
            continue

        parser = FirstImageParser()
        parser.feed(str(article.get("content") or ""))
        parser.close()
        if not parser.src:
            continue
        image_url = urljoin(MEDICINE_ORIGIN, parser.src)
        parsed_image = urlsplit(image_url)
        if (
            parsed_image.scheme != "https"
            or parsed_image.hostname != "medicine.korea.ac.kr"
        ):
            continue

        week_start, week_end = _parse_week(
            match.group(1),
            match.group(2),
            article.get("createdDt"),
        )
        notices.append(
            WeeklyNotice(
                article_no=article_no,
                title=title,
                source_url=(
                    f"{MEDICINE_ORIGIN}/kr/news/notice/view.do?"
                    f"articleNo={article_no}"
                ),
                source_image_url=image_url,
                week_start=week_start,
                week_end=week_end,
            )
        )
    if not notices:
        raise ValueError("공식 식당 검색 목록에서 주간식단표 이미지를 찾지 못했습니다.")
    if target_date is None:
        return notices[0]

    containing = [
        notice
        for notice in notices
        if notice.week_start <= target_date <= notice.week_end
    ]
    if containing:
        return max(containing, key=lambda notice: notice.article_no)

    def distance_key(notice: WeeklyNotice) -> tuple[int, int, int]:
        if target_date < notice.week_start:
            distance = (notice.week_start - target_date).days
            future_rank = 0
        else:
            distance = (target_date - notice.week_end).days
            future_rank = 1
        return distance, future_rank, -notice.article_no

    return min(notices, key=distance_key)


def inspect_image(payload: bytes) -> tuple[str, str, int, int]:
    if payload.startswith(b"\x89PNG\r\n\x1a\n") and len(payload) >= 24:
        width, height = struct.unpack(">II", payload[16:24])
        extension, media_type = "png", "image/png"
    elif payload.startswith(b"\xff\xd8"):
        width, height = _jpeg_dimensions(payload)
        extension, media_type = "jpg", "image/jpeg"
    else:
        raise ValueError("지원하지 않는 식단표 이미지 형식입니다.")

    if width < 600 or height < 300 or width > 10000 or height > 10000:
        raise ValueError(f"식단표 이미지 크기가 예상 범위를 벗어났습니다: {width}x{height}")
    return extension, media_type, width, height


def _jpeg_dimensions(payload: bytes) -> tuple[int, int]:
    index = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while index + 4 <= len(payload):
        if payload[index] != 0xFF:
            index += 1
            continue
        while index < len(payload) and payload[index] == 0xFF:
            index += 1
        if index >= len(payload):
            break
        marker = payload[index]
        index += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(payload):
            break
        segment_length = int.from_bytes(payload[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(payload):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(payload[index + 3 : index + 5], "big")
            width = int.from_bytes(payload[index + 5 : index + 7], "big")
            return width, height
        index += segment_length
    raise ValueError("JPEG 식단표 크기를 확인하지 못했습니다.")


def collect_medicine_menu(
    menu_json_path: Path,
    *,
    notice_fetcher: Callable[[], dict] = fetch_notice_payload,
    image_fetcher: Callable[[str], bytes] = fetch_image,
    now: datetime | None = None,
) -> dict:
    snapshot = json.loads(menu_json_path.read_text(encoding="utf-8"))
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schemaVersion") != 1
        or not isinstance(snapshot.get("restaurants"), dict)
    ):
        raise ValueError("기존 menu.json 스키마가 올바르지 않습니다.")

    collected_at = (now or datetime.now(SEOUL)).astimezone(SEOUL)
    notice = select_latest_weekly_notice(
        notice_fetcher(),
        target_date=collected_at.date(),
    )
    image = image_fetcher(notice.source_image_url)
    extension, media_type, width, height = inspect_image(image)
    filename = f"article-{notice.article_no}.{extension}"
    image_dir = menu_json_path.parent / "medicine-menu"
    image_dir.mkdir(parents=True, exist_ok=True)
    image_path = image_dir / filename
    image_path.write_bytes(image)

    fetched_at = collected_at.isoformat(timespec="seconds")
    snapshot["restaurants"]["medicine"] = {
        "name": "의과대학 본관식당",
        "shortName": "의대본관",
        "aliases": [
            "의대본관",
            "의대본관식당",
            "의과대학본관",
            "의과대학 본관식당",
            "의대식당",
            "의대 학식",
            "의대",
            "의과대학",
        ],
        "sourceUrl": notice.source_url,
        "fetchedAt": fetched_at,
        "status": "ok",
        "weekRange": (
            f"{notice.week_start.isoformat()} ~ {notice.week_end.isoformat()}"
        ),
        "days": {},
        "imageMenu": {
            "articleNo": notice.article_no,
            "title": notice.title,
            "weekStart": notice.week_start.isoformat(),
            "weekEnd": notice.week_end.isoformat(),
            "imagePath": f"medicine-menu/{filename}",
            "mediaType": media_type,
            "width": width,
            "height": height,
            "bytes": len(image),
            "sha256": hashlib.sha256(image).hexdigest(),
        },
    }
    menu_json_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return snapshot["restaurants"]["medicine"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--menu-json",
        type=Path,
        default=Path("public/data/menu.json"),
        help="의대본관 이미지 메타데이터를 추가할 menu.json",
    )
    args = parser.parse_args()

    try:
        restaurant = collect_medicine_menu(args.menu_json)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(
            f"[error] 의대본관 주간식단 이미지 수집 실패: "
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        print(
            "[error] 기존 Pages 배포를 유지하기 위해 이번 배포를 중단합니다.",
            file=sys.stderr,
        )
        return 1

    image = restaurant["imageMenu"]
    print(
        f"[ok] 의대본관 article {image['articleNo']} "
        f"{image['width']}x{image['height']} → {image['imagePath']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
