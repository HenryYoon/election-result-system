"""
crawler/nec_client.py
선관위 사이트에서 한 지역의 사전투표 집계 테이블을 가져오는 클라이언트.

Strategy 패턴(NECClient):
  - RequestsNECClient : POST 한 방으로 수집 (크롬 불필요, 빠름) — 기본
  - SeleniumNECClient : 헤드리스 크롬 (폴백/디버그용)

엔진 선택: settings.crawler_engine ("requests" | "selenium")
"""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from shared.config import settings


BASE_URL = "https://info.nec.go.kr/main/showDocument.xhtml"
REPORT_URL = "https://info.nec.go.kr/electioninfo/electionInfo_report.xhtml"
EXTRA_COLS = ["시도명", "구군명", "조회_시도코드", "조회_구군코드", "조회_일자", "조회_시간코드"]


@dataclass
class FetchResult:
    rows: list[list[str]]
    headers: list[str]
    html: str


class NECClient(ABC):
    @abstractmethod
    def fetch(self, city_code, town_code, date_code, time_code,
              city_name, town_name, max_attempts: int = 10) -> Optional[FetchResult]:
        ...

    def close(self) -> None:  # 기본 no-op
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def build_url(city_code: str, town_code: str, date_code: str, time_code: str) -> str:
    return (
        f"{BASE_URL}?electionId={settings.election_id}"
        f"&topMenuId=VC&secondMenuId=VCAP02"
        f"&cityCode={city_code}&townCode={town_code}"
        f"&dateCode={date_code}&timeCode={time_code}"
    )


def _parse(html: str, city_name, town_name, city_code, town_code, date_code, time_code) -> FetchResult:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="table01")
    if not table or not table.find("tbody"):
        raise ValueError("테이블 구조 없음")
    body_rows = table.find("tbody").find_all("tr")
    if len(body_rows) <= 1:
        raise ValueError("데이터 행 없음 (미발표)")

    headers = [th.get_text(strip=True) for th in table.find("thead").find_all("th")]
    headers.extend(EXTRA_COLS)
    rows: list[list[str]] = []
    for tr in body_rows:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        cells.extend([city_name, town_name, city_code, town_code, date_code, time_code])
        rows.append(cells)
    return FetchResult(rows=rows, headers=headers, html=html)


class RequestsNECClient(NECClient):
    """POST electionInfo_report.xhtml (statementId=VCAP02_#3) 한 방으로 수집."""

    def __init__(self, logger=None) -> None:
        self.logger = logger
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def fetch(self, city_code, town_code, date_code, time_code,
              city_name, town_name, max_attempts: int = 5) -> Optional[FetchResult]:
        data = {
            "electionId": settings.election_id,
            "requestURI": f"/electioninfo/{settings.election_id}/vc/vcap02.jsp",
            "topMenuId": "VC", "secondMenuId": "VCAP02", "menuId": "VCAP02",
            "statementId": "VCAP02_#3",
            "prevoteDate1": "20260529", "prevoteDate2": "20260530",
            "cityCode": city_code, "townCode": town_code,
            "dateCode": date_code, "timeCode": time_code,
        }
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.session.post(REPORT_URL, data=data, timeout=30)
                result = _parse(resp.text, city_name, town_name,
                                city_code, town_code, date_code, time_code)
                if self.logger:
                    self.logger.info(f"✓ [{city_name} {town_name}] tc={time_code} ({len(result.rows)}행)")
                return result
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"↻ [{city_name} {town_name}] tc={time_code} 재시도 {attempt}/{max_attempts}: {e}")
                if attempt < max_attempts:
                    time.sleep(min(10, 2 ** attempt))
        if self.logger:
            self.logger.error(f"✗ [{city_name} {town_name}] tc={time_code} 수집 실패")
        return None


class SeleniumNECClient(NECClient):
    """헤드리스 크롬 폴백. selenium은 이 클래스 사용 시에만 import."""

    def __init__(self, logger=None) -> None:
        self.logger = logger
        self._driver = None

    def _driver_lazy(self):
        if self._driver is None:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            opts = Options()
            for a in ("--headless", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"):
                opts.add_argument(a)
            opts.add_argument(
                "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            self._driver = webdriver.Chrome(options=opts)
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    def fetch(self, city_code, town_code, date_code, time_code,
              city_name, town_name, max_attempts: int = 10) -> Optional[FetchResult]:
        from selenium.webdriver.common.by import By
        url = build_url(city_code, town_code, date_code, time_code)
        driver = self._driver_lazy()
        for attempt in range(1, max_attempts + 1):
            try:
                driver.get(url)
                time.sleep(random.uniform(1.5, 2.5))
                try:
                    driver.find_element(By.TAG_NAME, "form").submit()
                    time.sleep(random.uniform(2.0, 3.0))
                except Exception:
                    pass
                result = _parse(driver.page_source, city_name, town_name,
                                city_code, town_code, date_code, time_code)
                if self.logger:
                    self.logger.info(f"✓ [{city_name} {town_name}] tc={time_code} ({len(result.rows)}행)")
                return result
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"↻ [{city_name} {town_name}] tc={time_code} 재시도 {attempt}/{max_attempts}: {e}")
                if attempt < max_attempts:
                    time.sleep(min(60, 5 * 2 ** (attempt - 1)))
        if self.logger:
            self.logger.error(f"✗ [{city_name} {town_name}] tc={time_code} 수집 실패")
        return None


def make_client(logger=None) -> NECClient:
    if settings.crawler_engine == "selenium":
        return SeleniumNECClient(logger=logger)
    return RequestsNECClient(logger=logger)
