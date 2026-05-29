"""
crawler/fetch_timecode.py
requests 기반 독립 수집기 — 특정 timeCode(예: 18시 최종)를 전 지역 빠르게 수집.
셀레늄 없이 POST 1방. 메인 크롤러와 독립 실행.

사용:
  python crawler/fetch_timecode.py <timeCode> [--wait]
  --wait: 해당 timeCode가 발표될 때까지(합계>0) 대기 후 수집
"""
from __future__ import annotations

import sys, time, csv as csvmod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import settings

ENDPOINT = "https://info.nec.go.kr/electioninfo/electionInfo_report.xhtml"
WORKERS = 16

def post(city, town, tc):
    data = {
        "electionId": settings.election_id,
        "requestURI": f"/electioninfo/{settings.election_id}/vc/vcap02.jsp",
        "topMenuId": "VC", "secondMenuId": "VCAP02", "menuId": "VCAP02",
        "statementId": "VCAP02_#3",
        "prevoteDate1": "20260529", "prevoteDate2": "20260530",
        "cityCode": city, "townCode": town, "dateCode": settings.date_code, "timeCode": tc,
    }
    for attempt in range(3):
        try:
            r = requests.post(ENDPOINT, data=data, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            return r.text
        except Exception:
            time.sleep(2)
    return ""

def parse(html, region, tc):
    soup = BeautifulSoup(html, "html.parser")
    t = soup.find("table", id="table01")
    if not t or not t.find("tbody"):
        return []
    body = t.find("tbody").find_all("tr")
    if len(body) <= 1:
        return []
    out = []
    for tr in body:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        cells += [region["cityName"], region["townName"], region["cityCode"],
                  region["townCode"], settings.date_code, tc]
        out.append(cells)
    return out

HEADERS = ["읍면동명","사전투표소명","사전투표자수","관내사전투표자수","관외사전투표자수",
           "시도명","구군명","조회_시도코드","조회_구군코드","조회_일자","조회_시간코드"]

def is_published(regions, tc) -> bool:
    """대표 지역 합계>0 이면 발표된 것으로 판단."""
    s = regions[0]
    html = post(str(s["cityCode"]), str(s["townCode"]), tc)
    soup = BeautifulSoup(html, "html.parser")
    t = soup.find("table", id="table01")
    if not t or not t.find("tbody"): return False
    first = t.find("tbody").find_all("tr")
    if not first: return False
    tds = [td.get_text(strip=True).replace(",","") for td in first[0].find_all("td")]
    nums = [int(x) for x in tds if x.isdigit()]
    return any(n > 0 for n in nums)

def collect(regions, tc):
    rows = []
    def one(region):
        html = post(str(region["cityCode"]), str(region["townCode"]), tc)
        return parse(html, region, tc)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(one, r): r for r in regions}
        for f in as_completed(futs):
            try: rows.extend(f.result())
            except Exception: pass
    return rows

def main():
    tc = sys.argv[1] if len(sys.argv) > 1 else "18"
    wait = "--wait" in sys.argv
    targets = pd.read_csv(settings.url_csv)
    regions = [r for _, r in targets.iterrows()]
    print(f"timeCode={tc} | 지역 {len(regions)}개 | wait={wait}", flush=True)

    if wait:
        while not is_published(regions, tc):
            print(f"[{time.strftime('%H:%M:%S')}] tc={tc} 미발표 — 60초 후 재확인", flush=True)
            time.sleep(60)
        print(f"[{time.strftime('%H:%M:%S')}] tc={tc} 발표 감지 → 수집 시작", flush=True)

    t0 = time.time()
    rows = collect(regions, tc)
    df = pd.DataFrame(rows, columns=HEADERS)
    out = settings.data_dir / f"최종_{settings.date_code}일차_{tc}시.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    booths = df[df["사전투표소명"].astype(str).str.strip().ne("") & df["사전투표소명"].ne("합계")]
    print(f"완료 {time.time()-t0:.1f}초 | 행 {len(df)} | 투표소 {len(booths)} | 저장 {out}", flush=True)

if __name__ == "__main__":
    main()
