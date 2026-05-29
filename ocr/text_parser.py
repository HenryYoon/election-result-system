"""
ocr/text_parser.py
참관인이 슬랙 봇 등으로 드라이브에 올린 텍스트 보고서를 파싱한다.

예시:
  파일명: 0528_서울_강남구_압구정동사전투표소_홍길동_0603.TXT
  본문:
    [사전투표자수 - 5월 28일]
    홍길동 (010-XXXX-0603)
    서울 강남구 압구정동사전투표소
    - 7시까지 관내: 0명 / 관외: 0명
    ...
    - 18시까지 관내: 0명 / 관외: 0명
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


HOURS = list(range(7, 19))  # 7시~18시

_DATE_RE = re.compile(r"\[사전투표자수\s*-\s*(\d+)\s*월\s*(\d+)\s*일\]")
_OBSERVER_RE = re.compile(r"^\s*(\S+)\s*\(([^)]+)\)\s*$", re.MULTILINE)
_ROW_RE = re.compile(
    r"-?\s*(\d{1,2})\s*시까지\s*관내\s*:\s*(\d+)\s*명\s*/\s*관외\s*:\s*(\d+)\s*명"
)


@dataclass
class ParsedReport:
    날짜: str = ""
    참관인: str = ""
    전화번호: str = ""
    도시: str = ""
    시군구: str = ""
    투표소: str = ""
    관내: dict[int, int] = field(default_factory=dict)  # 7 → 인원
    관외: dict[int, int] = field(default_factory=dict)


def _meta_from_filename(name: str) -> dict[str, str]:
    """파일명 패턴 0528_서울_강남구_투표소_이름_뒷자리.확장자에서 메타 추출."""
    stem = Path(name).stem
    parts = stem.split("_")
    if len(parts) < 6:
        return {}
    return {
        "날짜_파일": parts[0],
        "도시": parts[1],
        "시군구": parts[2],
        "투표소": parts[3],
        "참관인": parts[4],
        "전화뒷자리": parts[5],
    }


def parse(text: str, file_name: str = "") -> Optional[ParsedReport]:
    """텍스트 본문 + 파일명을 결합해 ParsedReport 생성."""
    report = ParsedReport()
    meta = _meta_from_filename(file_name) if file_name else {}

    # 날짜: 본문 우선, 없으면 파일명
    m = _DATE_RE.search(text)
    if m:
        report.날짜 = f"{int(m.group(1))}.{int(m.group(2)):02d}"
    elif "날짜_파일" in meta:
        d = meta["날짜_파일"]
        if len(d) == 4 and d.isdigit():
            report.날짜 = f"{int(d[:2])}.{int(d[2:]):02d}"

    # 참관인 + 전화: 본문 우선
    m = _OBSERVER_RE.search(text)
    if m:
        report.참관인 = m.group(1).strip()
        report.전화번호 = m.group(2).strip()
    else:
        report.참관인 = meta.get("참관인", "")
        if meta.get("전화뒷자리"):
            report.전화번호 = f"010-XXXX-{meta['전화뒷자리']}"

    # 도시/시군구/투표소: 파일명이 가장 일관됨
    report.도시 = meta.get("도시", "")
    report.시군구 = meta.get("시군구", "")
    report.투표소 = meta.get("투표소", "")

    # 시간대별 데이터
    for hour_str, inside, outside in _ROW_RE.findall(text):
        h = int(hour_str)
        if h in HOURS:
            report.관내[h] = int(inside)
            report.관외[h] = int(outside)

    if not report.관내 and not report.관외:
        return None
    return report


def to_csv_row(report: ParsedReport, counts: dict[int, int]) -> dict:
    """관내 또는 관외 카운트 dict를 CSV 한 행으로 변환."""
    row = {
        "날짜": report.날짜,
        "참관인": report.참관인,
        "전화번호": report.전화번호,
        "도시": report.도시,
        "시군구": report.시군구,
        "투표소": report.투표소,
    }
    for h in HOURS:
        row[f"{h}시"] = counts.get(h, "")
    return row


CSV_COLUMNS = [
    "날짜", "참관인", "전화번호", "도시", "시군구", "투표소",
    *[f"{h}시" for h in HOURS],
]
