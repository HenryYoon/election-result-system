"""
shared/config.py
환경변수를 한 곳에서 로드/검증하는 설정 객체.

사용:
    from shared.config import settings
    settings.election_id
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _bool(key: str, default: bool = False) -> bool:
    return _env(key, "1" if default else "0") == "1"


def _int(key: str, default: int) -> int:
    raw = _env(key, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # 선거
    election_id: str
    date_code: str
    test_mode: bool

    # 크롤러
    crawler_threads: int
    crawler_engine: str  # "requests"(기본, 크롬 불필요) | "selenium"

    # 구글 드라이브 (검수용 이미지 소스)
    google_service_account_json: str
    drive_folder_id: str
    drive_poll_interval: int

    # 구글 드라이브 백업 (data/ → 이 폴더로 증분 백업, 비우면 비활성)
    backup_drive_folder_id: str

    # 경로
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    images_dir: Path = BASE_DIR / "images"
    ocr_results_dir: Path = BASE_DIR / "ocr_results"
    approved_dir: Path = BASE_DIR / "approved"
    logs_dir: Path = BASE_DIR / "logs"
    url_csv: Path = BASE_DIR / "nec_urls.csv"

    @classmethod
    def load(cls) -> "Settings":
        test_mode = _bool("TEST_MODE", False)
        election_id = "0020250603" if test_mode else _env("ELECTION_ID", "0020260603")
        return cls(
            election_id=election_id,
            date_code=_env("DATE_CODE", "1"),
            test_mode=test_mode,
            crawler_threads=_int("CRAWLER_THREADS", 4),
            crawler_engine=_env("CRAWLER_ENGINE", "requests"),
            google_service_account_json=_env("GOOGLE_SERVICE_ACCOUNT_JSON"),
            drive_folder_id=_env("DRIVE_FOLDER_ID"),
            drive_poll_interval=_int("DRIVE_POLL_INTERVAL", 60),
            backup_drive_folder_id=_env("BACKUP_DRIVE_FOLDER_ID"),
        )


settings = Settings.load()
