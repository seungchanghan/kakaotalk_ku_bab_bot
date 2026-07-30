#!/usr/bin/env python3
"""Fetch Korea University cafeteria menus and write a static JSON snapshot."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")
NO_MENU_TEXT = "등록된 식단내용이(가) 없습니다."
DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")

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
    args = parser.parse_args()

    snapshot, successes = build_snapshot(args.restaurant)
    if successes == 0:
        for error in snapshot["errors"]:
            print(f"[error] {error['restaurant']}: {error['message']}", file=sys.stderr)
        print("[error] 모든 식당 수집에 실패하여 기존 배포를 유지합니다.", file=sys.stderr)
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

