"""
crawler/scrape_nec.py
선관위 사전투표 결과 크롤러 데몬.

매 시간 :05분 (TEST_MODE면 10분 간격) 실행 →
지역 목록을 병렬 크롤링 → HTML/CSV 저장 → 구글 시트 와이드 포맷 upsert.
"""

from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from crawler.nec_client import FetchResult, make_client
from shared.config import settings
from shared.daemon import Daemon
from shared.drive_backup import make_backup
from shared.notify import make_notifier


# ==========================================
# 시간 계산
# ==========================================

class TestClock:
    """TEST_MODE에서 경과 10분 = 1시간으로 매핑하는 시뮬레이션 시계."""

    def __init__(self) -> None:
        self._start: Optional[datetime] = None

    def end_hour(self, now: datetime) -> int:
        if self._start is None:
            self._start = now
        elapsed_min = int((now - self._start).total_seconds() / 60)
        return 7 + (elapsed_min // 10)


def time_codes_until(end_hour: int) -> list[str]:
    # 선관위 사전투표 시간코드는 07~18만 유효 (06은 존재하지 않아 '전체'로 폴백됨).
    # 06시 개시분은 07시 누계에 포함되므로 07부터 수집해도 손실 없음.
    if end_hour < 7:
        return []
    return [f"{h:02d}" for h in range(7, min(end_hour, 18) + 1)]


def time_suffix(time_codes: list[str]) -> str:
    if len(time_codes) == 1:
        return f"{time_codes[0]}시"
    return f"{time_codes[0]}~{time_codes[-1]}시"


# ==========================================
# 지역 단위 작업
# ==========================================

_file_lock = threading.Lock()

# 스레드별 크롬 재사용 (사이클당 크롬 구동 256번 → 스레드 수만큼으로 절감)
_thread_local = threading.local()
_clients_lock = threading.Lock()
_all_clients: list = []


def _client():
    c = getattr(_thread_local, "client", None)
    if c is None:
        c = make_client()
        _thread_local.client = c
        with _clients_lock:
            _all_clients.append(c)
    return c


def _close_all_clients() -> None:
    with _clients_lock:
        for c in _all_clients:
            try:
                c.close()
            except Exception:
                pass
        _all_clients.clear()


def save_region_files(
    folder: Path,
    city_name: str,
    town_name: str,
    suffix: str,
    result: FetchResult,
) -> None:
    safe_city, safe_town = city_name.replace("/", "_"), town_name.replace("/", "_")
    base = f"{safe_city}_{safe_town}"

    time_folder = folder / suffix
    html_dir, csv_dir = time_folder / "html", time_folder / "csv"
    with _file_lock:
        html_dir.mkdir(parents=True, exist_ok=True)
        csv_dir.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(result.rows, columns=result.headers).to_csv(
        csv_dir / f"{base}.csv", index=False, encoding="utf-8-sig"
    )
    (html_dir / f"{base}.html").write_text(result.html, encoding="utf-8")


def crawl_region_tc(
    region: pd.Series,
    tc: str,
    suffix: str,
    save_folder: Path,
) -> FetchResult | None:
    """단일 지역 × 단일 timeCode 수집 (스레드별 크롬 재사용). 개별 CSV/HTML 저장."""
    city_code = str(region["cityCode"]).strip()
    town_code = str(region["townCode"]).strip()
    city_name = str(region.get("cityName", city_code)).strip()
    town_name = str(region.get("townName", town_code)).strip()

    result = _client().fetch(city_code, town_code, settings.date_code, tc, city_name, town_name)
    if result is not None:
        save_region_files(save_folder, city_name, town_name, suffix, result)
    time.sleep(random.uniform(0.3, 0.8))
    return result


# ==========================================
# 데몬
# ==========================================

class CrawlerDaemon(Daemon):
    name = "crawler"
    interval_sec = 600  # 10분

    def __init__(self) -> None:
        # 날짜코드별로 로그 파일 분리 (crawler_d1.log / crawler_d2.log) → 동시 실행 충돌 방지
        self.name = f"crawler_d{settings.date_code}"
        super().__init__()
        self.test_clock = TestClock() if settings.test_mode else None
        self.backup = make_backup()
        self.slack = make_notifier(prefix=f"[{self.name}] ")
        self._last_suffix: str | None = None  # 직전 사이클 최신 시간대 (새 시간대 판별용)
        if self.slack.enabled:
            self.logger.info("슬랙 알림 활성")
        if settings.test_mode:
            self.interval_sec = 600  # 10분
            self.logger.info("[TEST] 10분 간격 시뮬레이션 모드")
        if self.backup:
            self.logger.info(f"드라이브 백업 활성: {settings.backup_drive_folder_id}")
        self.logger.info(
            f"ELECTION_ID={settings.election_id} | DATE_CODE={settings.date_code} | "
            f"THREADS={settings.crawler_threads}"
        )

    def _current_time_codes(self, now: datetime) -> list[str]:
        if self.test_clock:
            end = self.test_clock.end_hour(now)
            self.logger.info(f"[TEST] 시뮬레이션 종료시간: {end:02d}시")
        elif settings.collect_full_day:
            # 확정일(과거 일차) 감시: 07~18 전부 이미 발표됨 → 현재 시각 무시하고 전수 수집
            return time_codes_until(18)
        else:
            # 선관위는 직전 시간대 누계만 발표 → 한 시간 빼서 발표 완료된 것만 수집
            end = now.hour - 1
        return time_codes_until(end)

    def tick(self) -> None:
        if not settings.url_csv.exists():
            self.logger.error(f"URL CSV 없음: {settings.url_csv}")
            return

        targets = pd.read_csv(settings.url_csv)
        now = datetime.now()
        codes = self._current_time_codes(now)
        if not codes:
            self.logger.info("수집할 시간대가 없습니다 (07시 이전).")
            return

        folder_name = now.strftime("%Y_%m_%d_%H_%M")
        # 날짜코드별 하위폴더(data/d1, data/d2)로 분리 → 동시 실행 시 출력 덮어쓰기 방지
        save_folder = settings.data_dir / f"d{settings.date_code}" / folder_name
        self.logger.info(
            f"수집 시작 | 폴더={folder_name} | 시간대={codes}(최신순) | "
            f"지역={len(targets)} | 스레드={settings.crawler_threads}"
        )

        regions = [row for _, row in targets.iterrows()]
        # 시간대별 폴더명(누적 suffix) 사전 계산: tc -> "06~NN시"
        suffix_of = {tc: time_suffix(codes[: i + 1]) for i, tc in enumerate(codes)}

        errors: list[str] = []  # 이 사이클의 지역 단위 오류 수집 → 알림 요약에 포함
        latest_rows = 0  # 최신 시간대(가장 먼저 처리) 행수

        # 시간대를 '최신순'으로 처리 → 최신 시각이 가장 먼저 완성/기록됨 (시의성)
        # 과거 시각은 그 뒤로 이어서 재수집 (사후조작 검증용 전수 수집 유지)
        try:
            with ThreadPoolExecutor(max_workers=settings.crawler_threads) as ex:
                for tc in reversed(codes):
                    suffix = suffix_of[tc]
                    rows: list[list] = []
                    headers: list[str] | None = None
                    futures = {
                        ex.submit(crawl_region_tc, r, tc, suffix, save_folder): r
                        for r in regions
                    }
                    for fut in as_completed(futures):
                        region = futures[fut]
                        try:
                            res = fut.result()
                            if res is not None:
                                rows.extend(res.rows)
                                if headers is None:
                                    headers = res.headers
                        except Exception as e:
                            city = region.get("cityName", "?"); town = region.get("townName", "?")
                            self.logger.error(f"[{city} {town}] tc={tc} 오류: {e}")
                            errors.append(f"{city} {town} tc={tc}: {e}")
                    # 이 시각 전체지역 CSV 즉시 기록
                    if headers and rows:
                        out_dir = save_folder / suffix / "csv"
                        out_dir.mkdir(parents=True, exist_ok=True)
                        pd.DataFrame(rows, columns=headers).to_csv(
                            out_dir / "전체지역.csv", index=False, encoding="utf-8-sig"
                        )
                        self.logger.info(f"✓ {suffix} 완성 ({len(rows)}행) → {out_dir/'전체지역.csv'}")
                        if tc == codes[-1]:  # 최신 시간대
                            latest_rows = len(rows)
        finally:
            _close_all_clients()

        self.logger.info("=" * 60 + f" 크롤링 완료: {folder_name}")
        self._notify_cycle(suffix_of[codes[-1]], folder_name, latest_rows, errors)

        if self.backup:
            try:
                n = self.backup.sync(settings.data_dir)
                self.logger.info(f"드라이브 백업: {n}개 파일 업로드")
            except Exception as e:
                self.logger.error(f"드라이브 백업 실패: {e}", exc_info=True)

    def _notify_cycle(
        self, latest_suffix: str, folder_name: str, latest_rows: int, errors: list[str]
    ) -> None:
        """사이클 완료 슬랙 알림.

        매 10분 동일 시간대를 재수집(특히 d1 전수 감시)하므로, 알림은
        '새 시간대가 발표됐을 때' 또는 '오류가 있을 때'만 보낸다 (스팸 방지).
        """
        if not self.slack.enabled:
            return
        new_timecode = latest_suffix != self._last_suffix
        self._last_suffix = latest_suffix
        if errors:
            head = "⚠️"
        elif new_timecode:
            head = "✅"
        else:
            return  # 새 시간대 없음 + 오류 없음 → 동일 데이터 재수집, 알림 생략
        msg = (
            f"{head} {latest_suffix} 수집 완료 | 폴더 {folder_name} | "
            f"최신 {latest_rows:,}행 | 오류 {len(errors)}건"
        )
        if errors:
            sample = "\n".join(f"  • {e}" for e in errors[:5])
            extra = f"\n…외 {len(errors) - 5}건" if len(errors) > 5 else ""
            msg += f"\n{sample}{extra}"
        self.slack.send(msg)

    def on_fatal(self, exc: Exception) -> None:
        self.slack.send(f"🔥 치명적 오류로 사이클 실패: {exc}")


if __name__ == "__main__":
    CrawlerDaemon().run()
