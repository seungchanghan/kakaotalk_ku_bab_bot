#!/usr/bin/env python3
"""Fetch Korea University cafeteria menus and write a static JSON snapshot."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")
NO_MENU_TEXT = "등록된 식단내용이(가) 없습니다."
DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")
HISTORY_RETENTION_DAYS = 90
MAX_HISTORY_BYTES = 2 * 1024 * 1024
MAX_PUBLISHED_BYTES = 240 * 1024

RESTAURANTS = {
    "songrim": {
        "name": "수당삼양패컬티하우스 송림",
        "shortName": "송림",
        "aliases": ["송림", "수당삼양", "패컬티하우스"],
        "sourceUrl": "https://www.korea.ac.kr/ko/503/subview.do",
    },
    "science": {
        "name": "자연계 학생식당",
        "shortName": "자연계",
        "aliases": ["자연계", "자연계학생식당", "애기능"],
        "sourceUrl": "https://www.korea.ac.kr/ko/504/subview.do",
    },
    "dormitory": {
        "name": "안암학사 식당",
        "shortName": "안암학사",
        "aliases": ["안암학사", "기숙사", "긱식"],
        "sourceUrl": "https://www.korea.ac.kr/ko/505/subview.do",
    },
    "industry": {
        "name": "산학관 식당",
        "shortName": "산학관",
        "aliases": ["산학관", "산학"],
        "sourceUrl": "https://www.korea.ac.kr/ko/506/subview.do",
    },
    "student_union": {
        "name": "학생회관 학생식당",
        "shortName": "학생회관",
        "aliases": ["학생회관", "학관", "학생식당"],
        "sourceUrl": "https://www.korea.ac.kr/ko/508/subview.do",
    },
}


@dataclass
class Cell:
    tag: str
    text: str
    rowspan: int = 1
    colspan: int = 1


class DietTableParser(HTMLParser):
    """Extract rows from the diet table without third-party dependencies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.target_depth = 0
        self.in_target = False
        self.in_tbody = False
        self.current_row: list[Cell] | None = None
        self.current_cell: Cell | None = None
        self.cell_text: list[str] = []
        self.rows: list[list[Cell]] = []
        self.week_range = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)

        if tag == "div" and attr.get("id") == "_JW_diet_basic":
            self.in_target = True
            self.target_depth = 1
            return

        if self.in_target and tag == "div":
            self.target_depth += 1

        if not self.in_target:
            return

        if tag == "tbody":
            self.in_tbody = True
        elif self.in_tbody and tag == "tr":
            self.current_row = []
        elif self.current_row is not None and tag in {"th", "td"}:
            self.current_cell = Cell(
                tag=tag,
                text="",
                rowspan=_positive_int(attr.get("rowspan")),
                colspan=_positive_int(attr.get("colspan")),
            )
            self.cell_text = []
        elif self.current_cell is not None and tag == "br":
            self.cell_text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self.in_target:
            return

        if self.current_cell is not None and tag == self.current_cell.tag:
            self.current_cell.text = _clean_text("".join(self.cell_text))
            assert self.current_row is not None
            self.current_row.append(self.current_cell)
            self.current_cell = None
            self.cell_text = []
        elif tag == "tr" and self.current_row is not None:
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = None
        elif tag == "tbody":
            self.in_tbody = False

        if tag == "div":
            self.target_depth -= 1
            if self.target_depth == 0:
                self.in_target = False

    def handle_data(self, data: str) -> None:
        if not self.in_target:
            return
        if self.current_cell is not None:
            self.cell_text.append(data)
        elif not self.in_tbody:
            match = re.search(
                r"\d{4}\.\d{2}\.\d{2}\.\s*~\s*\d{4}\.\d{2}\.\d{2}\.",
                data,
            )
            if match:
                self.week_range = match.group(0)


def _positive_int(value: str | None) -> int:
    try:
        return max(1, int(value or "1"))
    except ValueError:
        return 1


def _clean_text(value: str) -> str:
    lines = []
    for line in value.replace("\r", "").splitlines():
        normalized = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if normalized and (not lines or lines[-1] != normalized):
            lines.append(normalized)
    return "\n".join(lines)


def _expand_table(rows: Iterable[list[Cell]], width: int = 5) -> list[list[str]]:
    expanded: list[list[str]] = []
    active: dict[int, tuple[int, str]] = {}

    for row in rows:
        logical: list[str | None] = [None] * width

        for column, (remaining, text) in list(active.items()):
            logical[column] = text
            if remaining <= 1:
                del active[column]
            else:
                active[column] = (remaining - 1, text)

        cursor = 0
        for cell in row:
            while cursor < width and logical[cursor] is not None:
                cursor += 1
            for offset in range(cell.colspan):
                column = cursor + offset
                if column >= width:
                    break
                logical[column] = cell.text
                if cell.rowspan > 1:
                    active[column] = (cell.rowspan - 1, cell.text)
            cursor += cell.colspan

        expanded.append([value or "" for value in logical])

    return expanded


def parse_menu_html(html: str) -> dict:
    parser = DietTableParser()
    parser.feed(html)
    parser.close()

    if not parser.rows:
        raise ValueError("식단 표를 찾지 못했습니다. 고려대 페이지 구조가 바뀌었을 수 있습니다.")

    days: dict[str, dict[str, list[dict[str, str]]]] = {}
    for date_text, meal_type, title, content, notes in _expand_table(parser.rows):
        match = DATE_RE.search(date_text)
        if not match or not meal_type:
            continue
        date = "-".join(match.groups())
        if NO_MENU_TEXT in title or NO_MENU_TEXT in content:
            continue

        entry = {
            "title": title,
            "content": content,
            "notes": "" if notes == "-" else notes,
        }
        if not entry["title"] and not entry["content"]:
            continue
        days.setdefault(date, {}).setdefault(meal_type, []).append(entry)

    if not days:
        raise ValueError("식단 표는 찾았지만 등록된 메뉴를 추출하지 못했습니다.")

    return {"weekRange": parser.week_range, "days": days}


def fetch_html(url: str, timeout: int = 20) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "KU-Meal-Bot/1.0 "
                "(educational project; contact via the GitHub repository)"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def fetch_history(url: str, timeout: int = 20) -> dict:
    if not _is_allowed_history_url(url):
        raise ValueError("과거 식단 URL은 GitHub Pages의 HTTPS data/menu.json이어야 합니다.")

    request = Request(
        url,
        headers={
            "User-Agent": "KU-Meal-Bot/1.0 history merge",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        if not _is_allowed_history_url(response.geturl()):
            raise ValueError("과거 식단 URL이 허용되지 않은 주소로 이동했습니다.")
        payload = response.read(MAX_HISTORY_BYTES + 1)
    if len(payload) > MAX_HISTORY_BYTES:
        raise ValueError("과거 식단 JSON이 허용 크기를 초과했습니다.")

    history = json.loads(payload.decode("utf-8"))
    if (
        not isinstance(history, dict)
        or history.get("schemaVersion") != 1
        or not isinstance(history.get("restaurants"), dict)
    ):
        raise ValueError("과거 식단 JSON 스키마가 올바르지 않습니다.")
    return history


def _is_allowed_history_url(url: str) -> bool:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".github.io")
        or not parsed.path.endswith("/data/menu.json")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return False
    return True


def merge_history(
    snapshot: dict,
    history: dict | None,
    now: datetime,
    retention_days: int = HISTORY_RETENTION_DAYS,
) -> dict:
    snapshot["historyRetentionDays"] = retention_days
    if not history or history.get("schemaVersion") != 1:
        return snapshot

    cutoff = now.astimezone(SEOUL).date() - timedelta(days=retention_days - 1)
    old_restaurants = history.get("restaurants", {})

    for key, current in snapshot["restaurants"].items():
        old = old_restaurants.get(key, {})
        old_days = old.get("days", {}) if isinstance(old, dict) else {}
        current_days = current.get("days", {})
        merged_days: dict = {}

        for source in (old_days, current_days):
            if not isinstance(source, dict):
                continue
            for day_text, meals in source.items():
                try:
                    menu_date = date.fromisoformat(day_text)
                except (TypeError, ValueError):
                    continue
                if menu_date < cutoff or not _valid_meals(meals):
                    continue
                merged_days[day_text] = _sanitize_meals(meals)

        current["days"] = dict(sorted(merged_days.items()))

    return snapshot


def _valid_meals(meals: object) -> bool:
    if not isinstance(meals, dict):
        return False
    for meal_type, entries in meals.items():
        if meal_type not in {"조식", "중식", "석식"} or not isinstance(entries, list):
            return False
        for entry in entries:
            if not isinstance(entry, dict):
                return False
            if any(
                not isinstance(entry.get(field, ""), str)
                for field in ("title", "content", "notes")
            ):
                return False
    return True


def _sanitize_meals(meals: dict) -> dict:
    return {
        meal_type: [
            {
                "title": entry.get("title", ""),
                "content": entry.get("content", ""),
                "notes": entry.get("notes", ""),
            }
            for entry in entries
        ]
        for meal_type, entries in meals.items()
    }


def enforce_snapshot_size(
    snapshot: dict,
    maximum_bytes: int = MAX_PUBLISHED_BYTES,
) -> dict:
    snapshot["historySizeLimitBytes"] = maximum_bytes
    snapshot["historyPrunedDays"] = 0
    removed = 0

    def encoded_size() -> int:
        return len(
            (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )

    while encoded_size() > maximum_bytes:
        candidates = [
            (day_text, restaurant)
            for restaurant in snapshot.get("restaurants", {}).values()
            if isinstance(restaurant, dict)
            for day_text in restaurant.get("days", {})
        ]
        if not candidates:
            break
        oldest = min(day_text for day_text, _ in candidates)
        for day_text, restaurant in candidates:
            if day_text == oldest:
                restaurant["days"].pop(day_text, None)
                removed += 1
        snapshot["historyPrunedDays"] = removed

    if encoded_size() > maximum_bytes:
        raise ValueError("식단 JSON 메타데이터가 배포 크기 제한을 초과했습니다.")
    return snapshot


def build_snapshot(
    selected: Iterable[str] | None = None,
    now: datetime | None = None,
) -> tuple[dict, int]:
    generated_at = (now or datetime.now(SEOUL)).astimezone(SEOUL).isoformat(timespec="seconds")
    keys = list(selected or RESTAURANTS.keys())
    result: dict = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "timezone": "Asia/Seoul",
        "restaurants": {},
        "errors": [],
    }
    successes = 0

    for key in keys:
        config = RESTAURANTS[key]
        record = {**config, "fetchedAt": generated_at}
        try:
            parsed = parse_menu_html(fetch_html(config["sourceUrl"]))
            record.update({"status": "ok", **parsed})
            successes += 1
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
            record.update({"status": "error", "weekRange": "", "days": {}})
            result["errors"].append(
                {"restaurant": key, "message": f"{type(error).__name__}: {error}"}
            )
        result["restaurants"][key] = record

    return result, successes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="public/data/menu.json",
        help="생성할 JSON 경로",
    )
    parser.add_argument(
        "--restaurant",
        action="append",
        choices=sorted(RESTAURANTS),
        help="특정 식당만 수집할 때 반복 지정",
    )
    parser.add_argument(
        "--history-url",
        help="이전에 배포한 GitHub Pages menu.json URL",
    )
    args = parser.parse_args()

    now = datetime.now(SEOUL)
    snapshot, successes = build_snapshot(args.restaurant, now=now)
    if successes == 0:
        for error in snapshot["errors"]:
            print(f"[error] {error['restaurant']}: {error['message']}", file=sys.stderr)
        print("[error] 모든 식당 수집에 실패하여 기존 배포를 유지합니다.", file=sys.stderr)
        return 1

    history = None
    if args.history_url:
        try:
            history = fetch_history(args.history_url)
        except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as error:
            print(
                f"[warning] 과거 식단을 불러오지 못해 현재 주간 데이터만 배포합니다: "
                f"{type(error).__name__}: {error}",
                file=sys.stderr,
            )
    try:
        snapshot = enforce_snapshot_size(merge_history(snapshot, history, now))
    except ValueError as error:
        print(f"[error] 안전한 배포 크기로 식단을 정리하지 못했습니다: {error}", file=sys.stderr)
        return 1

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[ok] {successes}/{len(snapshot['restaurants'])}개 식당 → {output}")
    for error in snapshot["errors"]:
        print(f"[warning] {error['restaurant']}: {error['message']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
