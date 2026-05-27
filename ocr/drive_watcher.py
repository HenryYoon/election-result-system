"""
ocr/drive_watcher.py
구글 드라이브에서 계수표 이미지를 시간대별로 다운로드하는 모듈

동작:
  - 매 시간 :10분에 실행
  - 현재 시간대(hour)에 업로드된 파일만 다운로드 (중복 방지)
  - 처리된 파일 ID는 processed_ids.json에 기록

환경변수:
  GOOGLE_SERVICE_ACCOUNT_JSON   구글 서비스 계정 자격증명
  DRIVE_FOLDER_ID               계수표 이미지가 올라오는 드라이브 폴더 ID
"""

import os
import sys
import json
import logging
import schedule
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ==========================================
# 로깅 설정
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("drive_watcher.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ==========================================
# 설정
# ==========================================
BASE_DIR = Path(__file__).resolve().parent.parent
IMAGES_DIR = BASE_DIR / "images"
OCR_QUEUE_DIR = BASE_DIR / "ocr_results"
PROCESSED_IDS_FILE = BASE_DIR / "processed_ids.json"

DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")

# KST = UTC+9
KST = timezone(timedelta(hours=9))

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]


def _get_drive_credentials() -> Credentials:
    """구글 드라이브 서비스 계정 자격증명 로드."""
    import base64

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise EnvironmentError("GOOGLE_SERVICE_ACCOUNT_JSON 환경변수가 필요합니다.")

    if os.path.isfile(raw):
        return Credentials.from_service_account_file(raw, scopes=DRIVE_SCOPES)

    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        info = json.loads(decoded)
    except Exception:
        info = json.loads(raw)

    return Credentials.from_service_account_info(info, scopes=DRIVE_SCOPES)


def get_drive_service():
    """Google Drive API 서비스 객체 반환."""
    creds = _get_drive_credentials()
    return build("drive", "v3", credentials=creds)


def load_processed_ids() -> set:
    """이미 처리된 파일 ID 집합을 로드."""
    if PROCESSED_IDS_FILE.exists():
        with open(PROCESSED_IDS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_processed_ids(ids: set) -> None:
    """처리된 파일 ID 집합을 저장."""
    with open(PROCESSED_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(ids), f, ensure_ascii=False, indent=2)


def list_new_images(service, folder_id: str, target_hour: int) -> list[dict]:
    """
    지정된 폴더에서 target_hour 시간대에 업로드된 이미지 파일 목록을 반환.

    Args:
        service: Drive API 서비스 객체
        folder_id: 드라이브 폴더 ID
        target_hour: 수집 대상 시간 (0~23, KST)

    Returns:
        파일 메타데이터 목록 [{id, name, createdTime, mimeType}, ...]
    """
    now_kst = datetime.now(KST)
    # target_hour의 시작~끝 (KST)
    start_kst = now_kst.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    end_kst = start_kst + timedelta(hours=1)

    # RFC3339 UTC 형식으로 변환
    start_utc = start_kst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc = end_kst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    query = (
        f"'{folder_id}' in parents"
        f" and mimeType contains 'image/'"
        f" and createdTime >= '{start_utc}'"
        f" and createdTime < '{end_utc}'"
        f" and trashed = false"
    )

    results = []
    page_token = None

    while True:
        response = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, createdTime, mimeType)",
            pageToken=page_token,
            orderBy="createdTime",
        ).execute()

        results.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return results


def download_file(service, file_id: str, file_name: str, dest_dir: Path) -> Path:
    """
    드라이브에서 파일을 다운로드한다.

    Returns:
        저장된 로컬 파일 경로
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / file_name

    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()

    dest_path.write_bytes(fh.getvalue())
    return dest_path


def create_ocr_job(image_path: Path, file_meta: dict) -> None:
    """
    OCR 대기 큐에 작업 파일(JSON)을 생성한다.
    ocr_runner.py가 이 파일을 읽어 OCR을 수행한다.
    """
    OCR_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    job = {
        "file_id": file_meta["id"],
        "file_name": file_meta["name"],
        "image_path": str(image_path),
        "created_time": file_meta["createdTime"],
        "status": "pending",  # pending → ocr_done → approved
        "ocr_result": None,
        "observer_info": None,
    }

    job_path = OCR_QUEUE_DIR / f"{file_meta['id']}.json"
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)

    logger.info(f"  OCR 작업 생성: {job_path.name}")


def watch_and_download() -> None:
    """현재 시간대 이미지를 드라이브에서 다운로드하고 OCR 큐에 등록한다."""
    if not DRIVE_FOLDER_ID:
        logger.error("DRIVE_FOLDER_ID 환경변수가 설정되지 않았습니다.")
        return

    now_kst = datetime.now(KST)
    target_hour = now_kst.hour

    logger.info(f"드라이브 감시 시작 | 대상 시간: {target_hour:02d}시 (KST)")

    try:
        service = get_drive_service()
        files = list_new_images(service, DRIVE_FOLDER_ID, target_hour)
        logger.info(f"발견된 이미지 파일: {len(files)}개")

        if not files:
            logger.info("새 이미지 없음.")
            return

        processed = load_processed_ids()
        new_count = 0

        for file_meta in files:
            fid = file_meta["id"]
            fname = file_meta["name"]

            if fid in processed:
                logger.debug(f"이미 처리됨, 건너뜀: {fname}")
                continue

            # 시간대별 폴더에 저장
            hour_dir = IMAGES_DIR / f"{now_kst.strftime('%Y%m%d')}_{target_hour:02d}h"
            local_path = download_file(service, fid, fname, hour_dir)
            logger.info(f"다운로드 완료: {local_path}")

            create_ocr_job(local_path, file_meta)

            processed.add(fid)
            new_count += 1

        save_processed_ids(processed)
        logger.info(f"이번 수집: {new_count}개 신규 다운로드")

    except Exception as e:
        logger.error(f"드라이브 감시 중 오류: {e}", exc_info=True)


def job_wrapper() -> None:
    try:
        watch_and_download()
    except Exception as e:
        logger.critical(f"치명적 오류: {e}", exc_info=True)


# ==========================================
# 진입점
# ==========================================
if __name__ == "__main__":
    logger.info("🚀 드라이브 감시 프로세스 시작")

    # 즉시 한 번 실행
    job_wrapper()

    # 매 시간 :10분에 실행 (크롤러 :05, OCR 감시 :10으로 순서 배치)
    schedule.every().hour.at(":10").do(job_wrapper)

    while True:
        schedule.run_pending()
        time.sleep(5)
