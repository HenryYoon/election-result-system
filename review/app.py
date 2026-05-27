"""
review/app.py
계수표 OCR 검수 Streamlit 앱

UI 레이아웃:
  ┌──────────────────┬──────────────────────────────┐
  │                  │  [참관인 정보 OCR 결과]        │
  │  계수표 이미지    │  (수정 가능한 폼)              │
  │                  ├──────────────────┬────────────┤
  │                  │  [계수 현황 OCR]  │  [승인버튼] │
  │                  │  (수정 가능한 폼) │            │
  └──────────────────┴──────────────────┴────────────┘

승인 시:
  - 계수 현황 → 결과 집계 구글 시트
  - 참관인 정보 → 참관인 구글 시트

환경변수:
  GOOGLE_SERVICE_ACCOUNT_JSON
  SPREADSHEET_ID          결과 집계 시트 (기본값 하드코딩)
  OBSERVER_SPREADSHEET_ID 참관인 정보 시트 (기본값 하드코딩)
"""

import os
import sys
import json
import shutil
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd
from PIL import Image

# shared 모듈 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.gsheet_uploader import append_rows

# ==========================================
# 설정
# ==========================================
BASE_DIR = Path(__file__).resolve().parent.parent
OCR_RESULTS_DIR = BASE_DIR / "ocr_results"
APPROVED_DIR = BASE_DIR / "approved"

SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1sBLCs8So45lCD5xQfhmrPr_HlA1zgwR0-i1T23bw09s"
)
OBSERVER_SPREADSHEET_ID = os.environ.get(
    "OBSERVER_SPREADSHEET_ID", "1LeJthvK8DYxYoCmCP0evfegeDcCZh2O5lGCAQNQauf4"
)

# ==========================================
# 유틸리티 함수
# ==========================================

def load_pending_jobs() -> list[dict]:
    """검수 대기 중(ocr_done)인 작업 목록을 반환한다."""
    if not OCR_RESULTS_DIR.exists():
        return []
    jobs = []
    for p in sorted(OCR_RESULTS_DIR.glob("*.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                job = json.load(f)
            if job.get("status") == "ocr_done":
                job["_path"] = str(p)
                jobs.append(job)
        except Exception:
            pass
    return jobs


def save_job(job: dict) -> None:
    """수정된 작업을 파일에 저장한다."""
    path = job.pop("_path")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)
    job["_path"] = path


def approve_job(job: dict) -> None:
    """작업을 승인 처리하고 구글 시트에 업로드 후 approved/ 폴더로 이동한다."""
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 계수 현황 → 결과 집계 시트
    counting_data = job.get("ocr_result", [])
    if counting_data:
        obs = job.get("observer_info", {}) or {}
        rows = []
        for item in counting_data:
            rows.append({
                "파일명": job.get("file_name", ""),
                "날짜": obs.get("date", ""),
                "시도": obs.get("province", ""),
                "시군구": obs.get("city", ""),
                "투표소번호": obs.get("booth_number", ""),
                "투표소명": obs.get("booth_name", ""),
                "관내": obs.get("inside_count", ""),
                "관외": obs.get("outside_count", ""),
                "행번호": item.get("row", ""),
                "소계": item.get("subtotal", ""),
                "누계": item.get("cumulative", ""),
                "현재쪽수": job.get("current_page", ""),
                "전체쪽수": job.get("total_pages", ""),
                "승인시각": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

        df_counting = pd.DataFrame(rows)
        append_rows(SPREADSHEET_ID, "계수표 집계", df_counting)

    # 2) 참관인 정보 → 참관인 시트
    obs_info = job.get("observer_info", {}) or {}
    if obs_info:
        df_observer = pd.DataFrame([{
            "파일명": job.get("file_name", ""),
            "날짜": obs_info.get("date", ""),
            "시도": obs_info.get("province", ""),
            "시군구": obs_info.get("city", ""),
            "투표소번호": obs_info.get("booth_number", ""),
            "투표소명": obs_info.get("booth_name", ""),
            "관내": obs_info.get("inside_count", ""),
            "관외": obs_info.get("outside_count", ""),
            "이름": obs_info.get("observer_name", ""),
            "전화번호": obs_info.get("observer_phone", ""),
            "승인시각": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }])
        append_rows(OBSERVER_SPREADSHEET_ID, "참관인 정보", df_observer)

    # 3) 파일을 approved/ 폴더로 이동
    src = Path(job["_path"])
    dst = APPROVED_DIR / src.name
    shutil.move(str(src), str(dst))


# ==========================================
# Streamlit UI
# ==========================================

def render_observer_form(obs: dict, key_prefix: str) -> dict:
    """참관인 정보 편집 폼을 렌더링하고 수정된 값을 반환한다."""
    st.subheader("📋 참관인 정보")
    col1, col2 = st.columns(2)
    with col1:
        date = st.text_input("날짜", value=obs.get("date", ""), key=f"{key_prefix}_date")
        province = st.text_input("시도", value=obs.get("province", ""), key=f"{key_prefix}_province")
        city = st.text_input("시군구", value=obs.get("city", ""), key=f"{key_prefix}_city")
        booth_number = st.text_input("투표소 번호", value=str(obs.get("booth_number", "") or ""), key=f"{key_prefix}_booth_num")
    with col2:
        booth_name = st.text_input("투표소명", value=obs.get("booth_name", ""), key=f"{key_prefix}_booth_name")
        inside_count = st.text_input("관내", value=str(obs.get("inside_count", "") or ""), key=f"{key_prefix}_inside")
        outside_count = st.text_input("관외", value=str(obs.get("outside_count", "") or ""), key=f"{key_prefix}_outside")
        observer_name = st.text_input("참관인 이름", value=obs.get("observer_name", ""), key=f"{key_prefix}_obs_name")
        observer_phone = st.text_input("전화번호", value=obs.get("observer_phone", ""), key=f"{key_prefix}_obs_phone")

    return {
        "date": date,
        "province": province,
        "city": city,
        "booth_number": booth_number,
        "booth_name": booth_name,
        "inside_count": inside_count,
        "outside_count": outside_count,
        "observer_name": observer_name,
        "observer_phone": observer_phone,
    }


def render_counting_form(counting_data: list, key_prefix: str) -> list:
    """계수 현황(소계/누계) 편집 폼을 렌더링하고 수정된 값을 반환한다."""
    st.subheader("🔢 계수 현황 (소계 / 누계)")

    # 10행으로 맞추기
    rows = list(counting_data) if counting_data else []
    while len(rows) < 10:
        rows.append({"row": len(rows) + 1, "subtotal": None, "cumulative": None})

    result = []
    header_cols = st.columns([1, 3, 3])
    header_cols[0].markdown("**행**")
    header_cols[1].markdown("**소계**")
    header_cols[2].markdown("**누계**")

    for item in rows[:10]:
        row_num = item.get("row", "")
        subtotal = item.get("subtotal")
        cumulative = item.get("cumulative")

        c0, c1, c2 = st.columns([1, 3, 3])
        c0.markdown(f"**{row_num}**")
        new_sub = c1.text_input(
            f"소계_{row_num}",
            value="" if subtotal is None else str(subtotal),
            key=f"{key_prefix}_sub_{row_num}",
            label_visibility="collapsed",
        )
        new_cum = c2.text_input(
            f"누계_{row_num}",
            value="" if cumulative is None else str(cumulative),
            key=f"{key_prefix}_cum_{row_num}",
            label_visibility="collapsed",
        )

        result.append({
            "row": row_num,
            "subtotal": int(new_sub) if new_sub.strip().isdigit() else (None if not new_sub.strip() else new_sub),
            "cumulative": int(new_cum) if new_cum.strip().isdigit() else (None if not new_cum.strip() else new_cum),
        })

    return result


def main():
    st.set_page_config(
        page_title="계수표 OCR 검수",
        page_icon="🗳️",
        layout="wide",
    )
    st.title("🗳️ 사전투표 계수표 OCR 검수 시스템")

    # 사이드바: 작업 선택
    jobs = load_pending_jobs()

    if not jobs:
        st.info("✅ 현재 검수 대기 중인 계수표가 없습니다.")
        if st.button("🔄 새로고침"):
            st.rerun()
        return

    st.sidebar.header(f"대기 중: {len(jobs)}건")

    job_labels = [
        f"{i+1}. {j.get('file_name', j.get('file_id', '?'))}"
        for i, j in enumerate(jobs)
    ]
    selected_idx = st.sidebar.radio("검수할 파일 선택", range(len(jobs)), format_func=lambda i: job_labels[i])

    if st.sidebar.button("🔄 목록 새로고침"):
        st.rerun()

    job = jobs[selected_idx]
    file_name = job.get("file_name", "알 수 없음")
    image_path = job.get("image_path", "")

    st.markdown(f"### 파일: `{file_name}`")

    # 메인 2컬럼 레이아웃
    col_img, col_form = st.columns([1, 1])

    with col_img:
        st.subheader("🖼️ 계수표 이미지")
        if image_path and Path(image_path).exists():
            img = Image.open(image_path)
            st.image(img, use_container_width=True)
        else:
            st.warning(f"이미지 파일을 찾을 수 없습니다:\n{image_path}")

    with col_form:
        key_prefix = job.get("file_id", str(selected_idx))

        # 참관인 정보 폼
        obs_data = job.get("observer_info") or {}
        updated_obs = render_observer_form(obs_data, key_prefix)

        st.divider()

        # 계수 현황 폼
        counting_data = job.get("ocr_result") or []
        updated_counting = render_counting_form(counting_data, key_prefix)

        # 현재 쪽 / 전체 쪽
        st.divider()
        p_col1, p_col2 = st.columns(2)
        current_page = p_col1.text_input(
            "현재 쪽수",
            value=str(job.get("current_page", "") or ""),
            key=f"{key_prefix}_cur_page",
        )
        total_pages = p_col2.text_input(
            "전체 쪽수",
            value=str(job.get("total_pages", "") or ""),
            key=f"{key_prefix}_tot_page",
        )

        st.divider()

        # 승인 버튼
        if st.button("✅ 승인 및 구글 시트 업로드", type="primary", use_container_width=True):
            # 수정된 데이터를 job에 반영
            job["observer_info"] = updated_obs
            job["ocr_result"] = updated_counting
            job["current_page"] = current_page
            job["total_pages"] = total_pages

            with st.spinner("구글 시트에 업로드 중..."):
                try:
                    approve_job(job)
                    st.success(f"✅ '{file_name}' 승인 완료! 구글 시트에 업로드되었습니다.")
                    st.balloons()
                    # 잠시 후 새로고침
                    import time; time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"업로드 실패: {e}")

        # 건너뛰기 (에러 표시)
        if st.button("⏭️ 건너뛰기 (오류 표시)", use_container_width=True):
            job_path = job["_path"]
            with open(job_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            raw["status"] = "skipped"
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            st.warning("건너뛰었습니다.")
            import time; time.sleep(0.5)
            st.rerun()

    # 하단: 진행 현황
    st.divider()
    total_approved = len(list(APPROVED_DIR.glob("*.json"))) if APPROVED_DIR.exists() else 0
    st.caption(f"승인 완료: {total_approved}건 | 대기 중: {len(jobs)}건")


if __name__ == "__main__":
    main()
