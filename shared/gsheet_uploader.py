"""
shared/gsheet_uploader.py
구글 스프레드시트 공통 업로드 모듈

환경변수:
    GOOGLE_SERVICE_ACCOUNT_JSON  서비스 계정 JSON 파일 경로 또는 base64 인코딩 문자열
"""

import os
import json
import base64
import logging
import tempfile
from typing import Optional

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_credentials() -> Credentials:
    """환경변수에서 서비스 계정 자격증명을 로드한다."""
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise EnvironmentError(
            "GOOGLE_SERVICE_ACCOUNT_JSON 환경변수가 설정되지 않았습니다."
        )

    # 파일 경로인 경우
    if os.path.isfile(raw):
        return Credentials.from_service_account_file(raw, scopes=SCOPES)

    # base64 인코딩된 JSON 문자열인 경우
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        info = json.loads(decoded)
    except Exception:
        # 일반 JSON 문자열인 경우
        info = json.loads(raw)

    return Credentials.from_service_account_info(info, scopes=SCOPES)


def get_client() -> gspread.Client:
    """인증된 gspread 클라이언트를 반환한다."""
    creds = _get_credentials()
    return gspread.authorize(creds)


def upsert_rows(
    spreadsheet_id: str,
    sheet_name: str,
    df: pd.DataFrame,
    key_cols: Optional[list] = None,
) -> None:
    """
    DataFrame 데이터를 구글 시트에 upsert(추가 또는 갱신)한다.

    Args:
        spreadsheet_id: 스프레드시트 ID
        sheet_name: 대상 시트명
        df: 업로드할 DataFrame
        key_cols: 중복 판단 기준 컬럼 목록 (None이면 전체 덮어쓰기)
    """
    if df.empty:
        logger.warning(f"[{sheet_name}] 업로드할 데이터가 없습니다.")
        return

    client = get_client()
    spreadsheet = client.open_by_key(spreadsheet_id)

    # 시트가 없으면 새로 생성
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=sheet_name, rows=str(len(df) + 100), cols=str(len(df.columns) + 5)
        )
        logger.info(f"시트 '{sheet_name}' 새로 생성했습니다.")

    existing_data = worksheet.get_all_records()

    if not existing_data or key_cols is None:
        # 기존 데이터 없거나 key_cols 미지정 → 전체 덮어쓰기
        worksheet.clear()
        worksheet.update(
            [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
        )
        logger.info(f"[{sheet_name}] {len(df)}행 전체 쓰기 완료.")
        return

    # key_cols 기준으로 upsert
    existing_df = pd.DataFrame(existing_data)

    # 기존 시트와 헤더 맞추기
    for col in df.columns:
        if col not in existing_df.columns:
            existing_df[col] = ""

    # key 기반 merge
    merged = existing_df.set_index(key_cols)
    new_indexed = df.set_index(key_cols)
    merged.update(new_indexed)

    # 신규 행 추가
    new_keys = new_indexed.index.difference(merged.index)
    if not new_keys.empty:
        merged = pd.concat([merged, new_indexed.loc[new_keys]])

    result_df = merged.reset_index()

    # 컬럼 순서 유지 (기존 순서 우선, 신규 컬럼은 뒤에)
    col_order = list(existing_df.columns)
    for col in result_df.columns:
        if col not in col_order:
            col_order.append(col)
    result_df = result_df.reindex(columns=col_order, fill_value="")

    worksheet.clear()
    worksheet.update(
        [result_df.columns.tolist()]
        + result_df.fillna("").astype(str).values.tolist()
    )
    logger.info(f"[{sheet_name}] upsert 완료 (총 {len(result_df)}행).")


def append_rows(
    spreadsheet_id: str,
    sheet_name: str,
    df: pd.DataFrame,
) -> None:
    """
    DataFrame 행을 기존 시트 뒤에 추가(append)한다.
    헤더가 없으면 헤더도 함께 씁니다.
    """
    if df.empty:
        return

    client = get_client()
    spreadsheet = client.open_by_key(spreadsheet_id)

    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=sheet_name, rows="1000", cols=str(len(df.columns) + 5)
        )

    existing = worksheet.get_all_values()
    if not existing:
        # 헤더 포함 전체 쓰기
        worksheet.update(
            [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
        )
    else:
        # 데이터 행만 추가
        worksheet.append_rows(df.fillna("").astype(str).values.tolist())

    logger.info(f"[{sheet_name}] {len(df)}행 append 완료.")
