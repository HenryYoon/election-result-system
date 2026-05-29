"""
ocr/job.py
OCR 작업 도메인 객체 — pending/done/error 상태와 JSON 직렬화를 캡슐화.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

STATUS_PENDING = "pending"
STATUS_DONE = "ocr_done"
STATUS_ERROR = "error"

# 작업 JSON과 구분되는 파일명 (큐 디렉토리 안에 같이 있는 메타 파일)
_NON_JOB_STEMS = {"processed", "processed_ids"}


@dataclass
class OCRJob:
    path: Path
    data: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> Optional["OCRJob"]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        if not isinstance(data, dict) or "status" not in data:
            return None
        return cls(path=path, data=data)

    @classmethod
    def list_pending(cls, queue_dir: Path) -> Iterator["OCRJob"]:
        if not queue_dir.exists():
            return
        for p in queue_dir.glob("*.json"):
            if p.stem in _NON_JOB_STEMS:
                continue
            job = cls.load(p)
            if job and job.is_pending():
                yield job

    # ----- 상태 조회 -----

    def is_pending(self) -> bool:
        return self.data.get("status") == STATUS_PENDING

    @property
    def image_path(self) -> Path:
        return Path(self.data.get("image_path", ""))

    @property
    def file_name(self) -> str:
        return self.data.get("file_name", self.path.name)

    # ----- 상태 변경 -----

    def mark_done(self, result: dict[str, Any]) -> None:
        self.data["status"] = STATUS_DONE
        self.data["ocr_result"] = result.get("counting_data", [])
        self.data["observer_info"] = result.get("observer_info", {})
        self.data["current_page"] = result.get("current_page")
        self.data["total_pages"] = result.get("total_pages")
        self.data.pop("error", None)
        self._save()

    def mark_error(self, error: str) -> None:
        self.data["status"] = STATUS_ERROR
        self.data["error"] = error
        self._save()

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    # ----- 생성 (drive_watcher가 신규 작업 만들 때) -----

    @classmethod
    def create(
        cls,
        queue_dir: Path,
        file_id: str,
        file_name: str,
        image_path: Path,
        created_time: str,
        assignee: str = "",
    ) -> "OCRJob":
        queue_dir.mkdir(parents=True, exist_ok=True)
        path = queue_dir / f"{file_id}.json"
        data = {
            "file_id": file_id,
            "file_name": file_name,
            "image_path": str(image_path),
            "created_time": created_time,
            "status": STATUS_PENDING,
            "assignee": assignee,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return cls(path=path, data=data)
