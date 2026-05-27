"""
crawler/scrape_nec.py
선관위 사전투표 결과 크롤러

동작 방식:
  - 매 시간 :05분에 실행 (선관위 발표 지연 여유 5분)
  - 실행 시각 기준 07시~현재시까지 timeCode를 누적 수집
  - 지역코드는 nec_urls.csv에서 로드 (1일차/2일차 공용)
  - HTML + CSV 저장 후 구글 시트에 upsert

환경변수:
  ELECTION_ID            선거 ID (기본: 0020260603)
  TEST_MODE              1이면 테스트 모드 (electionId=0020250603, 10분 간격 시뮬레이션)
  DATE_CODE              수집 일차 1 또는 2 (기본: 1)
  GOOGLE_SERVICE_ACCOUNT_JSON  구글 서비스 계정 자격증명
  SPREADSHEET_ID         결과 집계 스프레드시트 ID
"""

import os
import sys
import time
import random
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import schedule
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

# shared 모듈 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.gsheet_uploader import upsert_rows

# ==========================================
# 로깅 설정
# ==========================================
log_format = "[%(asctime)s] [%(levelname)s] %(message)s"
date_format = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    datefmt=date_format,
    handlers=[
        logging.FileHandler("crawler_running.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ==========================================
# 설정
# ==========================================
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
URL_CSV = BASE_DIR / "nec_urls.csv"

TEST_MODE = os.environ.get("TEST_MODE", "0") == "1"
ELECTION_ID = "0020250603" if TEST_MODE else os.environ.get("ELECTION_ID", "0020260603")
DATE_CODE = os.environ.get("DATE_CODE", "1")
SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1sBLCs8So45lCD5xQfhmrPr_HlA1zgwR0-i1T23bw09s"
)

BASE_URL = "https://info.nec.go.kr/main/showDocument.xhtml"

# 테스트 모드: 프로그램 시작 시각 기록
_TEST_START_TIME: datetime | None = None
_TEST_HOUR_OFFSET: int = 0  # 테스트 시뮬레이션 누적 시간


def build_url(city_code: str, town_code: str, date_code: str, time_code: str) -> str:
    return (
        f"{BASE_URL}"
        f"?electionId=00{ELECTION_ID}"
        f"&topMenuId=VC"
        f"&secondMenuId=VCAP02"
        f"&cityCode={city_code}"
        f"&townCode={town_code}"
        f"&dateCode={date_code}"
        f"&timeCode={time_code}"
    )


def get_time_codes_to_collect(now: datetime) -> list[str]:
    """
    현재 시각 기준으로 수집해야 할 timeCode 목록을 반환한다.
    07시부터 현재 시각의 시(hour)까지 누적.
    테스트 모드에서는 10분 간격으로 1시간씩 증가하는 시뮬레이션.
    """
    if TEST_MODE:
        global _TEST_START_TIME, _TEST_HOUR_OFFSET
        if _TEST_START_TIME is None:
            _TEST_START_TIME = now

        elapsed_minutes = int((now - _TEST_START_TIME).total_seconds() / 60)
        simulated_offset = elapsed_minutes // 10  # 10분마다 1시간 증가
        end_hour = 7 + simulated_offset
        logger.info(
            f"[TEST] 경과 {elapsed_minutes}분 → 시뮬레이션 종료시간: {end_hour:02d}시"
        )
    else:
        end_hour = now.hour

    # 07시 이전이면 수집 없음
    if end_hour < 7:
        return []

    # 18시가 최대
    end_hour = min(end_hour, 18)

    return [f"{h:02d}" for h in range(7, end_hour + 1)]


def make_filename_suffix(time_codes: list[str]) -> str:
    """
    timeCode 목록에서 파일명 suffix 생성.
    ['07'] → '07시'
    ['07','08'] → '07~08시'
    ['07','08','09'] → '07~09시'
    """
    if len(time_codes) == 1:
        return f"{time_codes[0]}시"
    return f"{time_codes[0]}~{time_codes[-1]}시"


def make_chrome_driver() -> webdriver.Chrome:
    """Headless Chrome 드라이버를 생성한다."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=options)


def fetch_table_for_region(
    driver: webdriver.Chrome,
    city_code: str,
    town_code: str,
    date_code: str,
    time_code: str,
    city_name: str,
    town_name: str,
    max_attempts: int = 10,
) -> tuple[list[list], list[str]] | tuple[None, None]:
    """
    선관위 사이트에서 특정 지역·시간대 테이블을 파싱해 반환한다.

    Returns:
        (rows, headers) 또는 실패 시 (None, None)
    """
    url = build_url(city_code, town_code, date_code, time_code)

    for attempt in range(1, max_attempts + 1):
        try:
            driver.get(url)
            time.sleep(random.uniform(1.5, 2.5))

            soup = BeautifulSoup(driver.page_source, "html.parser")
            table_tag = soup.find("table", id="table01")

            if not table_tag or not table_tag.find("tbody"):
                raise ValueError("테이블 구조 없음")

            table_rows = table_tag.find("tbody").find_all("tr")
            if len(table_rows) <= 1:
                raise ValueError("데이터 행 없음 (미발표)")

            headers = [th.get_text(strip=True) for th in table_tag.find("thead").find_all("th")]
            headers.extend(["시도명", "구군명", "조회_시도코드", "조회_구군코드", "조회_일자", "조회_시간코드"])

            rows = []
            for tr in table_rows:
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                cells.extend([city_name, town_name, city_code, town_code, date_code, time_code])
                rows.append(cells)

            logger.info(f"   ✓ [{city_name} {town_name}] timeCode={time_code} 수집 완료 ({len(rows)}행)")
            return rows, headers

        except Exception as e:
            logger.warning(
                f"   ↻ [{city_name} {town_name}] timeCode={time_code} "
                f"재시도 {attempt}/{max_attempts}: {e}"
            )
            if attempt < max_attempts:
                time.sleep(60)

    logger.error(f"   ✗ [{city_name} {town_name}] timeCode={time_code} 수집 실패")
    return None, None


def save_files(
    folder: Path,
    city_name: str,
    town_name: str,
    time_suffix: str,
    all_rows: list[list],
    headers: list[str],
    html_source: str,
) -> None:
    """HTML과 CSV를 시간대 폴더 아래 html/, csv/ 서브폴더에 저장한다.

    저장 경로:
        {folder}/{time_suffix}/html/{city}_{town}.html
        {folder}/{time_suffix}/csv/{city}_{town}.csv
    """
    safe_city = city_name.replace("/", "_")
    safe_town = town_name.replace("/", "_")
    base_name = f"{safe_city}_{safe_town}"

    time_folder = folder / time_suffix
    html_dir = time_folder / "html"
    csv_dir = time_folder / "csv"
    html_dir.mkdir(parents=True, exist_ok=True)
    csv_dir.mkdir(parents=True, exist_ok=True)

    # CSV 저장
    csv_path = csv_dir / f"{base_name}.csv"
    df = pd.DataFrame(all_rows, columns=headers)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # HTML 저장
    html_path = html_dir / f"{base_name}.html"
    html_path.write_text(html_source, encoding="utf-8")


def upload_to_gsheet(all_data: list[list], headers: list[str], date_code: str) -> None:
    """수집된 전체 데이터를 구글 시트에 업로드한다."""
    if not all_data:
        logger.warning("업로드할 데이터가 없습니다.")
        return

    try:
        df = pd.DataFrame(all_data, columns=headers)

        # 관내/관외 분리 (컬럼명에 '관내' 또는 '관외' 포함 여부로 판단)
        day = DATE_CODE

        sheet_name_in = f"{day}일차 관내 발표"
        sheet_name_out = f"{day}일차 관외 발표"

        # 컬럼에 따라 분리 시도
        in_cols = [c for c in df.columns if "관내" in c or c in ["읍면동명", "사전투표소명", "시도명", "구군명",
                                                                   "조회_시도코드", "조회_구군코드", "조회_일자", "조회_시간코드"]]
        out_cols = [c for c in df.columns if "관외" in c or c in ["읍면동명", "사전투표소명", "시도명", "구군명",
                                                                    "조회_시도코드", "조회_구군코드", "조회_일자", "조회_시간코드"]]

        key_cols = ["조회_시도코드", "조회_구군코드", "조회_일자", "조회_시간코드", "읍면동명", "사전투표소명"]
        existing_keys = [c for c in key_cols if c in df.columns]

        upsert_rows(SPREADSHEET_ID, sheet_name_in, df, key_cols=existing_keys or None)
        logger.info(f"구글 시트 '{sheet_name_in}' 업로드 완료")

        # 관외 발표가 별도 데이터인 경우를 위해 같은 df를 관외 시트에도 업로드
        # (실제 구조 확인 후 필요시 분리)
        upsert_rows(SPREADSHEET_ID, sheet_name_out, df, key_cols=existing_keys or None)
        logger.info(f"구글 시트 '{sheet_name_out}' 업로드 완료")

    except Exception as e:
        logger.error(f"구글 시트 업로드 실패: {e}", exc_info=True)


def crawl_all_targets() -> None:
    """메인 크롤링 함수: CSV에서 지역 목록을 읽어 전체 수집 수행."""

    # URL CSV 검증
    if not URL_CSV.exists():
        logger.error(f"URL CSV 파일을 찾을 수 없습니다: {URL_CSV}")
        return

    targets_df = pd.read_csv(URL_CSV)
    logger.info(f"지역 목록 로드 완료: 총 {len(targets_df)}개 구군")

    now = datetime.now()
    time_codes = get_time_codes_to_collect(now)

    if not time_codes:
        logger.info("수집할 시간대가 없습니다 (07시 이전).")
        return

    time_suffix = make_filename_suffix(time_codes)
    folder_name = now.strftime("%Y_%m_%d_%H_%M")
    save_folder = DATA_DIR / folder_name

    logger.info(
        f"수집 시작 | 폴더: {folder_name} | 시간대: {time_codes} | "
        f"지역: {len(targets_df)}개 | 선거ID: {ELECTION_ID}"
    )

    driver = make_chrome_driver()
    # tc → (rows, headers, html) 매핑
    tc_data: dict[str, tuple[list[list], list[str], str]] = {}

    try:
        for tc in time_codes:
            tc_suffix = make_filename_suffix(time_codes[: time_codes.index(tc) + 1])
            tc_rows_all: list[list] = []
            tc_headers: list[str] | None = None
            tc_last_html: str = ""

            for _, row in targets_df.iterrows():
                city_code = str(row["cityCode"]).strip()
                town_code = str(row["townCode"]).strip()
                city_name = str(row.get("cityName", city_code)).strip()
                town_name = str(row.get("townName", town_code)).strip()

                logger.info(f"[{city_name} {town_name}] timeCode={tc} 수집 시작...")
                rows, headers = fetch_table_for_region(
                    driver,
                    city_code,
                    town_code,
                    DATE_CODE,
                    tc,
                    city_name,
                    town_name,
                )

                if rows is None:
                    time.sleep(random.uniform(0.5, 1.5))
                    continue

                if tc_headers is None:
                    tc_headers = headers

                tc_rows_all.extend(rows)
                tc_last_html = driver.page_source

                # 지역별 파일 저장 (해당 timeCode suffix로)
                save_files(
                    save_folder,
                    city_name,
                    town_name,
                    tc_suffix,
                    rows,
                    headers,
                    driver.page_source,
                )

                time.sleep(random.uniform(0.5, 1.5))

            if tc_headers:
                tc_data[tc] = (tc_rows_all, tc_headers, tc_last_html)

                # 전체지역 통합 CSV (timeCode별) → {time_suffix}/csv/ 아래
                total_csv_dir = save_folder / tc_suffix / "csv"
                total_csv_dir.mkdir(parents=True, exist_ok=True)
                total_csv = total_csv_dir / "전체지역.csv"
                pd.DataFrame(tc_rows_all, columns=tc_headers).to_csv(
                    total_csv, index=False, encoding="utf-8-sig"
                )
                logger.info(f"통합 CSV 저장: {total_csv}")

    finally:
        driver.quit()
        logger.info("Chrome 드라이버 종료")

    # 구글 시트 업로드 — 가장 마지막(최신) timeCode 데이터로
    if tc_data:
        last_tc = time_codes[-1]
        if last_tc in tc_data:
            all_data, all_headers, _ = tc_data[last_tc]
            upload_to_gsheet(all_data, all_headers, DATE_CODE)
    else:
        logger.warning("수집된 데이터가 없습니다.")

    logger.info("=" * 60 + f" 크롤링 완료: {folder_name}")


def job_wrapper() -> None:
    """스케줄러 래퍼 — 예외 발생 시 프로세스 전체가 죽는 것을 방지."""
    try:
        crawl_all_targets()
    except Exception as e:
        logger.critical(f"치명적 오류 발생: {e}", exc_info=True)


# ==========================================
# 진입점
# ==========================================
if __name__ == "__main__":
    logger.info(
        f"🚀 사전투표 수집기 시작 | TEST_MODE={TEST_MODE} | "
        f"ELECTION_ID={ELECTION_ID} | DATE_CODE={DATE_CODE}"
    )

    if TEST_MODE:
        logger.info("[TEST] 10분 간격 시뮬레이션 모드 — 즉시 첫 실행 후 10분마다 반복")
        job_wrapper()
        schedule.every(10).minutes.do(job_wrapper)
    else:
        logger.info("매 시간 :05분에 실행 등록")
        schedule.every().hour.at(":05").do(job_wrapper)

    while True:
        schedule.run_pending()
        time.sleep(5)
