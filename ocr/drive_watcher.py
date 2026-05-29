"""
ocr/drive_watcher.py
구글 드라이브의 보고 폴더를 감시해 신규 파일을 다운로드한다.

- 이미지(jpg/png/...) → 검수 큐(ocr_results/) 등록
- 텍스트(.txt) → 자동 파싱해 data/inside_counts.csv, data/outside_counts.csv 에 append
"""

from __future__ import annotations

import io
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from ocr.job import OCRJob
from ocr.text_parser import CSV_COLUMNS, parse, to_csv_row
from shared.auth import drive_credentials
from shared.config import settings
from shared.daemon import Daemon
from shared.reviewers import ReviewerAssigner


KST = timezone(timedelta(hours=9))

INSIDE_CSV = settings.data_dir / "inside_counts.csv"
OUTSIDE_CSV = settings.data_dir / "outside_counts.csv"
TEXT_RAW_DIR = settings.data_dir / "text_submissions"

_TEXT_MIME_PREFIX = "text/"
_TEXT_EXTS = {".txt"}


# ==========================================
# 처리된 ID 영속화
# ==========================================

class ProcessedIDStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            return set(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception:
            return set()

    def save(self, ids: set[str]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(sorted(ids), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


# ==========================================
# 드라이브
# ==========================================

class DriveSource:
    def __init__(self, folder_id: str) -> None:
        self.folder_id = folder_id
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = build("drive", "v3", credentials=drive_credentials())
        return self._service

    def list_files(self) -> list[dict]:
        """이미지 + 텍스트 모두 조회."""
        query = (
            f"'{self.folder_id}' in parents"
            f" and (mimeType contains 'image/' or mimeType contains 'text/')"
            f" and trashed = false"
        )
        results: list[dict] = []
        page_token = None
        while True:
            resp = self.service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, createdTime, mimeType)",
                pageToken=page_token,
                orderBy="createdTime",
            ).execute()
            results.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return results

    def download(self, file_id: str, file_name: str, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / file_name

        request = self.service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        dest_path.write_bytes(buf.getvalue())
        return dest_path


# ==========================================
# CSV append
# ==========================================

def _append_csv(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([row]).reindex(columns=CSV_COLUMNS, fill_value="")
    write_header = not path.exists()
    df.to_csv(path, mode="a", header=write_header, index=False, encoding="utf-8-sig")


# ==========================================
# 데몬
# ==========================================

class DriveWatcherDaemon(Daemon):
    name = "drive_watcher"

    def __init__(self) -> None:
        super().__init__()
        self.interval_sec = settings.drive_poll_interval * 60
        self.source = DriveSource(settings.drive_folder_id)
        self.store = ProcessedIDStore(settings.ocr_results_dir / "processed_ids.json")
        self.assigner = ReviewerAssigner()
        self.logger.info(f"폴링 간격: {settings.drive_poll_interval}분")

    def tick(self) -> None:
        if not settings.drive_folder_id:
            self.logger.error("DRIVE_FOLDER_ID 미설정")
            return

        try:
            files = self.source.list_files()
        except Exception as e:
            self.logger.error(f"드라이브 조회 실패: {e}", exc_info=True)
            return

        self.logger.info(f"발견된 파일: {len(files)}개")
        processed = self.store.load()
        new_count = 0

        for file_meta in files:
            if file_meta["id"] in processed:
                continue
            try:
                if self._is_text(file_meta):
                    self._handle_text(file_meta)
                else:
                    self._handle_image(file_meta)
                processed.add(file_meta["id"])
                new_count += 1
            except Exception as e:
                self.logger.error(f"파일 처리 실패 {file_meta.get('name')}: {e}", exc_info=True)

        self.store.save(processed)
        self.logger.info(f"이번 수집: {new_count}개 신규")

    @staticmethod
    def _is_text(file_meta: dict) -> bool:
        mime = file_meta.get("mimeType", "")
        ext = Path(file_meta.get("name", "")).suffix.lower()
        return mime.startswith(_TEXT_MIME_PREFIX) or ext in _TEXT_EXTS

    def _handle_image(self, file_meta: dict) -> None:
        target_dir = settings.images_dir / datetime.now(KST).strftime("%Y%m%d")
        local_path = self.source.download(file_meta["id"], file_meta["name"], target_dir)
        self.logger.info(f"이미지 다운로드: {local_path}")

        assignee = self.assigner.assign()
        OCRJob.create(
            queue_dir=settings.ocr_results_dir,
            file_id=file_meta["id"],
            file_name=file_meta["name"],
            image_path=local_path,
            created_time=file_meta.get("createdTime", ""),
            assignee=assignee,
        )
        self.logger.info(f"검수 큐 등록: {file_meta['id']}.json → 담당: {assignee or '미배정'}")

    def _handle_text(self, file_meta: dict) -> None:
        local_path = self.source.download(file_meta["id"], file_meta["name"], TEXT_RAW_DIR)
        text = local_path.read_text(encoding="utf-8", errors="replace")

        report = parse(text, file_meta["name"])
        if report is None:
            self.logger.warning(f"텍스트 파싱 실패 (건너뜀): {file_meta['name']}")
            return

        _append_csv(INSIDE_CSV, to_csv_row(report, report.관내))
        _append_csv(OUTSIDE_CSV, to_csv_row(report, report.관외))
        self.logger.info(
            f"텍스트 보고 저장: {file_meta['name']} → 관내/관외 CSV append"
        )


if __name__ == "__main__":
    DriveWatcherDaemon().run()
