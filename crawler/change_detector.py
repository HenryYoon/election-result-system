"""
crawler/change_detector.py
선관위 데이터 변경 감지 데몬.

15분 간격으로 최신 timeCode 스냅샷을 수집해 직전 값과 비교하고,
누계가 변경된 투표소를 data/changed/*.csv로 기록한다.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from crawler.nec_client import SeleniumNECClient
from crawler.scrape_nec import TestClock, time_codes_until
from shared.config import settings
from shared.daemon import Daemon


_KEY_COLS = ["조회_시도코드", "조회_구군코드", "읍면동명", "사전투표소명"]


# ==========================================
# 스냅샷 I/O
# ==========================================

class SnapshotStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self, snapshot: dict[str, dict]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False)


def snapshot_key(record: dict) -> str:
    return "_".join(str(record.get(k, "")) for k in _KEY_COLS)


def collect_region_snapshot(region: pd.Series, time_codes: list[str]) -> list[dict]:
    """단일 지역의 최신 timeCode 데이터를 dict 리스트로 반환."""
    city_code = str(region["cityCode"]).strip()
    town_code = str(region["townCode"]).strip()
    city_name = str(region.get("cityName", city_code)).strip()
    town_name = str(region.get("townName", town_code)).strip()

    records: list[dict] = []
    with SeleniumNECClient() as client:
        for tc in reversed(time_codes):
            result = client.fetch(
                city_code, town_code, settings.date_code, tc,
                city_name, town_name, max_attempts=3,
            )
            if result is None:
                continue
            for row in result.rows:
                record = dict(zip(result.headers, row))
                record["_snapshot_tc"] = tc
                records.append(record)
            break  # 최신 timeCode 1개만
    return records


# ==========================================
# 변경 감지
# ==========================================

def detect_changes(prev: dict[str, dict], curr: dict[str, dict]) -> list[dict]:
    changes: list[dict] = []
    cum_cols: Optional[list[str]] = None

    for key, curr_rec in curr.items():
        prev_rec = prev.get(key)
        if not prev_rec:
            continue
        if cum_cols is None:
            cum_cols = [k for k in curr_rec if "누계" in k]

        for col in cum_cols or []:
            prev_v = _to_int(prev_rec.get(col))
            curr_v = _to_int(curr_rec.get(col))
            if prev_v is None or curr_v is None or prev_v == curr_v:
                continue
            changes.append({
                "투표소_키": key,
                "시도명": curr_rec.get("시도명", ""),
                "구군명": curr_rec.get("구군명", ""),
                "읍면동명": curr_rec.get("읍면동명", ""),
                "사전투표소명": curr_rec.get("사전투표소명", ""),
                "컬럼": col,
                "이전값": prev_v,
                "현재값": curr_v,
                "차이": curr_v - prev_v,
                "감지시각": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            break

    return changes


def _to_int(v) -> Optional[int]:
    try:
        return int(str(v).replace(",", ""))
    except Exception:
        return None


# ==========================================
# 데몬
# ==========================================

class ChangeDetectorDaemon(Daemon):
    name = "change_detector"
    interval_sec = 900  # 15분

    def __init__(self) -> None:
        super().__init__()
        self.store = SnapshotStore(settings.data_dir / "snapshots" / "latest_snapshot.json")
        self.test_clock = TestClock() if settings.test_mode else None
        self.logger.info(
            f"ELECTION_ID={settings.election_id} | DATE_CODE={settings.date_code} | "
            f"THREADS={settings.crawler_threads}"
        )

    def _time_codes(self, now: datetime) -> list[str]:
        # 선관위는 직전 시간대 누계만 발표 → -1
        end = self.test_clock.end_hour(now) if self.test_clock else now.hour - 1
        return time_codes_until(end)

    def tick(self) -> None:
        self.logger.info("스냅샷 수집 시작")
        codes = self._time_codes(datetime.now())
        if not codes:
            self.logger.info("07시 이전 → 스킵")
            return
        if not settings.url_csv.exists():
            self.logger.error(f"URL CSV 없음: {settings.url_csv}")
            return

        curr = self._collect(codes)
        if not curr:
            self.logger.info("수집된 스냅샷 없음 (미발표 가능성)")
            return

        prev = self.store.load()
        if prev:
            changes = detect_changes(prev, curr)
            if changes:
                self.logger.warning(f"변경 감지: {len(changes)}개 투표소")
                csv_path = self._save_changes_csv(changes)
                self.logger.info(f"변경 내역: {csv_path}")
            else:
                self.logger.info("변경 없음")
        else:
            self.logger.info("초기 스냅샷 저장")
        self.store.save(curr)

    def _collect(self, codes: list[str]) -> dict[str, dict]:
        targets = pd.read_csv(settings.url_csv)
        regions = [row for _, row in targets.iterrows()]
        snapshot: dict[str, dict] = {}

        with ThreadPoolExecutor(max_workers=settings.crawler_threads) as ex:
            futures = {ex.submit(collect_region_snapshot, r, codes): r for r in regions}
            for fut in as_completed(futures):
                try:
                    for rec in fut.result():
                        snapshot[snapshot_key(rec)] = rec
                except Exception as e:
                    self.logger.error(f"스냅샷 수집 오류: {e}")
        return snapshot

    @staticmethod
    def _save_changes_csv(changes: list[dict]) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = settings.data_dir / "changed"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"changed_{ts}.csv"
        pd.DataFrame(changes).to_csv(out_path, index=False, encoding="utf-8-sig")
        return out_path


if __name__ == "__main__":
    ChangeDetectorDaemon().run()
