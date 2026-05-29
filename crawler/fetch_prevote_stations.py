"""
crawler/fetch_prevote_stations.py
공공데이터포털(중앙선관위) API로 전국 사전투표소 마스터 목록을 수집한다.
주소·설치장소 포함 → 크롤링 데이터의 투표소 완전성 검증 및 위치 enrichment 용.

API: PolplcInfoInqireService2/getPrePolplcOtlnmapTrnsportInfoInqire
출력: data/prevote_stations.csv
"""

from __future__ import annotations

import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import settings

API = "https://apis.data.go.kr/9760000/PolplcInfoInqireService2/getPrePolplcOtlnmapTrnsportInfoInqire"
SERVICE_KEY = os.environ.get("DATA_GO_KR_KEY", "")  # URL 인코딩된 키
OUT_CSV = settings.data_dir / "prevote_stations.csv"

SIDO = [
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
    "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원특별자치도",
    "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도",
    "경상남도", "제주특별자치도",
]

FIELDS = ["sgId", "sdName", "wiwName", "emdName", "evPsName", "evOrder", "placeName", "addr", "floor"]


def _get(params: dict) -> str:
    rest = urllib.parse.urlencode(params)
    url = f"{API}?serviceKey={SERVICE_KEY}&{rest}"
    with urllib.request.urlopen(url, timeout=40) as r:
        return r.read().decode("utf-8")


PAGE_SIZE = 100  # API 페이지당 최대치


def fetch_sido(sg_id: str, sd_name: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        xml = _get({"sgId": sg_id, "sdName": sd_name,
                    "numOfRows": str(PAGE_SIZE), "pageNo": str(page)})
        root = ET.fromstring(xml)
        if root.findtext(".//resultCode") not in ("INFO-00", None):
            break
        items = root.findall(".//item")
        if not items:
            break
        for it in items:
            rows.append({f: (it.findtext(f) or "").strip() for f in FIELDS})
        total = int(root.findtext(".//totalCount") or "0")
        if len(rows) >= total or page * PAGE_SIZE >= total:
            break
        page += 1
        time.sleep(0.3)
    return rows


def main() -> None:
    if not SERVICE_KEY:
        print("DATA_GO_KR_KEY 환경변수가 필요합니다 (URL 인코딩된 인증키).")
        sys.exit(1)

    sg_id = settings.election_id
    all_rows: list[dict] = []
    for sd in SIDO:
        rows = fetch_sido(sg_id, sd)
        print(f"{sd}: {len(rows)}개")
        all_rows.extend(rows)
        time.sleep(0.3)

    df = pd.DataFrame(all_rows, columns=FIELDS)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n총 {len(df)}개 사전투표소 저장: {OUT_CSV}")


if __name__ == "__main__":
    main()
