"""
shared/drive_backup.py
로컬 data/ 디렉토리를 구글 드라이브 폴더로 증분 백업.

- 백업 폴더 안에 로컬과 동일한 디렉토리 구조를 mirror
- 이미 올린 파일(경로+크기+mtime 동일)은 건너뜀 → 증분 업로드
- 업로드 이력은 data/state/backup_manifest.json 에 저장

전제: BACKUP_DRIVE_FOLDER_ID 폴더가 서비스 계정 이메일에 '편집자'로 공유되어 있어야 함.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from shared.auth import drive_rw_credentials
from shared.config import settings

logger = logging.getLogger(__name__)

_FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveBackup:
    def __init__(self, root_folder_id: str, manifest_path: Path) -> None:
        self.root_folder_id = root_folder_id
        self.manifest_path = manifest_path
        self._service = None
        self._folder_cache: dict[str, str] = {}   # 상대경로(디렉토리) → drive folder id
        self._manifest: dict[str, str] = self._load_manifest()  # 상대경로(파일) → "size:mtime"
        self._lock = threading.Lock()

    @property
    def service(self):
        if self._service is None:
            self._service = build("drive", "v3", credentials=drive_rw_credentials())
        return self._service

    # ----- manifest -----

    def _load_manifest(self) -> dict[str, str]:
        if not self.manifest_path.exists():
            return {}
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_manifest(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(self._manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _signature(path: Path) -> str:
        st = path.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"

    # ----- 드라이브 폴더 mirror -----

    def _ensure_folder(self, rel_dir: str) -> str:
        """상대 디렉토리 경로에 대응하는 드라이브 폴더 ID를 보장(없으면 생성)."""
        if rel_dir in ("", "."):
            return self.root_folder_id
        if rel_dir in self._folder_cache:
            return self._folder_cache[rel_dir]

        parent_rel = str(Path(rel_dir).parent) if Path(rel_dir).parent != Path(".") else ""
        parent_id = self._ensure_folder(parent_rel)
        name = Path(rel_dir).name

        folder_id = self._find_child_folder(parent_id, name) or self._create_folder(parent_id, name)
        self._folder_cache[rel_dir] = folder_id
        return folder_id

    def _find_child_folder(self, parent_id: str, name: str) -> str | None:
        safe = name.replace("'", "\\'")
        q = (
            f"'{parent_id}' in parents and name = '{safe}'"
            f" and mimeType = '{_FOLDER_MIME}' and trashed = false"
        )
        resp = self.service.files().list(q=q, fields="files(id)", pageSize=1).execute()
        files = resp.get("files", [])
        return files[0]["id"] if files else None

    def _create_folder(self, parent_id: str, name: str) -> str:
        meta = {"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]}
        created = self.service.files().create(body=meta, fields="id").execute()
        return created["id"]

    # ----- 파일 업로드 -----

    def _upload_file(self, local_path: Path, rel_path: str) -> None:
        rel_dir = str(Path(rel_path).parent) if Path(rel_path).parent != Path(".") else ""
        folder_id = self._ensure_folder(rel_dir)
        media = MediaFileUpload(str(local_path), resumable=False)
        meta = {"name": local_path.name, "parents": [folder_id]}
        self.service.files().create(body=meta, media_body=media, fields="id").execute()

    # ----- 공개 API -----

    def sync(self, local_root: Path) -> int:
        """local_root 아래 모든 파일 중 신규/변경분만 업로드. 업로드한 파일 수 반환."""
        if not local_root.exists():
            return 0
        uploaded = 0
        with self._lock:
            for path in sorted(local_root.rglob("*")):
                if not path.is_file():
                    continue
                rel = str(path.relative_to(local_root))
                sig = self._signature(path)
                if self._manifest.get(rel) == sig:
                    continue
                try:
                    self._upload_file(path, rel)
                    self._manifest[rel] = sig
                    uploaded += 1
                except Exception as e:
                    logger.error(f"백업 업로드 실패 {rel}: {e}")
            if uploaded:
                self._save_manifest()
        return uploaded


def make_backup() -> DriveBackup | None:
    """설정에 백업 폴더 ID가 있으면 DriveBackup, 없으면 None."""
    if not settings.backup_drive_folder_id:
        return None
    return DriveBackup(
        root_folder_id=settings.backup_drive_folder_id,
        manifest_path=settings.data_dir / "state" / "backup_manifest.json",
    )
