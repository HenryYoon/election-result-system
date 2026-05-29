"""
shared/auth.py
구글 드라이브 서비스 계정 자격증명 로더.
GOOGLE_SERVICE_ACCOUNT_JSON은 파일 경로 / base64 / 평문 JSON 모두 지원.
"""

from __future__ import annotations

import base64
import json
import os

from google.oauth2.service_account import Credentials

from shared.config import settings


DRIVE_RO_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]

# 백업 업로드용 — 앱이 생성한 파일에 대한 쓰기 권한
DRIVE_RW_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]


def _credentials(scopes: list[str]) -> Credentials:
    raw = settings.google_service_account_json
    if not raw:
        raise EnvironmentError("GOOGLE_SERVICE_ACCOUNT_JSON 환경변수가 필요합니다.")

    if os.path.isfile(raw):
        return Credentials.from_service_account_file(raw, scopes=scopes)

    try:
        info = json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception:
        info = json.loads(raw)

    return Credentials.from_service_account_info(info, scopes=scopes)


def drive_credentials() -> Credentials:
    return _credentials(DRIVE_RO_SCOPES)


def drive_rw_credentials() -> Credentials:
    return _credentials(DRIVE_RW_SCOPES)
