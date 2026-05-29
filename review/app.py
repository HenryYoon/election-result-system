"""
review/app.py
사전투표자수 계수지 수동 입력 검수 앱 (Streamlit).

UI 레이아웃:
  ┌──────────────────┬──────────────────────────────┐
  │  계수표 이미지    │  계수지 양식 입력 폼          │
  │  (드라이브에서    │  (날짜/시도/시군구/투표소/    │
  │   다운로드)       │   참관인/계수표/쪽수)         │
  └──────────────────┴──────────────────────────────┘

저장: data/manual_entries.csv 에 append.
"""

from __future__ import annotations

import json
import random
import shutil
import string
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import settings
from shared.reviewers import register_reviewer

# ==========================================
# 경로
# ==========================================
QUEUE_DIR = settings.ocr_results_dir       # 검수 대기 이미지 메타 (drive_watcher가 생성)
APPROVED_DIR = settings.approved_dir
ENTRIES_CSV = settings.data_dir / "manual_entries.csv"
REVIEWER_CACHE = Path.home() / ".election_reviewer.json"


# ==========================================
# 검수자 등록 (로컬 캐시 기반)
# ==========================================

def _load_local_reviewer() -> dict | None:
    if not REVIEWER_CACHE.exists():
        return None
    try:
        return json.loads(REVIEWER_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_local_reviewer(reviewer: dict) -> None:
    REVIEWER_CACHE.write_text(json.dumps(reviewer, ensure_ascii=False), encoding="utf-8")


def _new_reviewer(name: str) -> dict:
    suffix = "".join(random.choices(string.digits, k=4))
    reviewer = {
        "id": f"{name}_{suffix}",
        "name": name,
        "registered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_local_reviewer(reviewer)
    register_reviewer(reviewer["id"])  # 서버 명부에도 추가 → drive_watcher가 배정
    return reviewer


def render_registration_page() -> dict | None:
    st.title("🗳️ 검수 시스템 — 사용자 등록")
    st.info("이름을 입력해 검수자 등록을 완료해주세요.")
    name = st.text_input("이름")
    if st.button("등록") and name.strip():
        reviewer = _new_reviewer(name.strip())
        st.success(f"등록 완료: `{reviewer['id']}`")
        st.session_state["reviewer"] = reviewer
        st.rerun()
    return None


def ensure_reviewer() -> dict | None:
    if "reviewer" in st.session_state:
        return st.session_state["reviewer"]
    cached = _load_local_reviewer()
    if cached:
        st.session_state["reviewer"] = cached
        return cached
    return render_registration_page()


# ==========================================
# 검수 대상 이미지 큐
# ==========================================

def load_pending_jobs(reviewer_id: str) -> list[dict]:
    """drive_watcher가 만든 작업 JSON 중 미승인된 것들."""
    if not QUEUE_DIR.exists():
        return []
    jobs = []
    for p in sorted(QUEUE_DIR.glob("*.json")):
        if p.stem == "processed_ids":
            continue
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(job, dict):
            continue
        if job.get("status") in ("approved", "skipped"):
            continue
        assignee = job.get("assignee", "")
        if assignee and assignee != reviewer_id:
            continue
        job["_path"] = str(p)
        jobs.append(job)
    return jobs


# ==========================================
# CSV 저장
# ==========================================

ENTRY_COLUMNS = [
    "파일명", "날짜", "시도", "시군구", "투표소번호", "투표소명",
    "관내", "관외", "참관인이름", "참관인전화번호",
    "현재쪽수", "전체쪽수",
    "행번호", "시작시각", "소계", "누계",
    "검수자", "승인시각",
]


def append_entries(rows: list[dict]) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows)
    df = df.reindex(columns=ENTRY_COLUMNS, fill_value="")
    ENTRIES_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not ENTRIES_CSV.exists()
    df.to_csv(ENTRIES_CSV, mode="a", header=write_header, index=False, encoding="utf-8-sig")


def approve_job(job: dict, meta: dict, counting: list[dict], reviewer_id: str) -> None:
    """폼 입력값을 CSV에 append하고 작업 파일은 approved/로 이동."""
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    approved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = [
        {
            **meta,
            "행번호": item["행"],
            "시작시각": item["시작시각"],
            "소계": item["소계"],
            "누계": item["누계"],
            "파일명": job.get("file_name", ""),
            "검수자": reviewer_id,
            "승인시각": approved_at,
        }
        for item in counting
    ]
    append_entries(rows)

    job_path = Path(job["_path"])
    job["status"] = "approved"
    job["approved_at"] = approved_at
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.move(str(job_path), str(APPROVED_DIR / job_path.name))


# ==========================================
# 입력 폼
# ==========================================

def render_meta_form(key_prefix: str) -> dict:
    """계수지 상단 메타 정보 폼."""
    st.subheader("📋 계수지 상단 정보")

    date_choice = st.radio("날짜", ["5.29", "5.30"], horizontal=True, key=f"{key_prefix}_date")

    c1, c2 = st.columns(2)
    with c1:
        province = st.text_input("시도", key=f"{key_prefix}_prov")
        city = st.text_input("시군구", key=f"{key_prefix}_city")
        booth_number = st.text_input("사전투표소 번호", key=f"{key_prefix}_bnum")
        booth_name = st.text_input("사전투표소 투표소명", key=f"{key_prefix}_bname")
    with c2:
        inside_count = st.number_input("관내", min_value=0, step=1, value=0, key=f"{key_prefix}_inside")
        outside_count = st.number_input("관외", min_value=0, step=1, value=0, key=f"{key_prefix}_outside")
        observer_name = st.text_input("참관인 이름", key=f"{key_prefix}_oname")
        observer_phone = st.text_input("참관인 전화번호", key=f"{key_prefix}_ophone")

    return {
        "날짜": date_choice,
        "시도": province,
        "시군구": city,
        "투표소번호": booth_number,
        "투표소명": booth_name,
        "관내": inside_count,
        "관외": outside_count,
        "참관인이름": observer_name,
        "참관인전화번호": observer_phone,
    }


def render_counting_table(key_prefix: str) -> list[dict]:
    """계수지 본문 10행 표: 시작시각 / 소계 / 누계."""
    st.subheader("🔢 계수 현황")
    df = pd.DataFrame([
        {"행": i + 1, "시작시각": "", "소계": None, "누계": None}
        for i in range(10)
    ])
    edited = st.data_editor(
        df,
        key=f"{key_prefix}_counting",
        num_rows="fixed",
        hide_index=True,
        column_config={
            "행": st.column_config.NumberColumn("행", disabled=True, width="small"),
            "시작시각": st.column_config.TextColumn("시작시각", help="예: 06:00"),
            "소계": st.column_config.NumberColumn("소계", min_value=0, step=1),
            "누계": st.column_config.NumberColumn("누계", min_value=0, step=1),
        },
    )
    return [
        {
            "행": int(row["행"]),
            "시작시각": row["시작시각"] or "",
            "소계": None if pd.isna(row["소계"]) else int(row["소계"]),
            "누계": None if pd.isna(row["누계"]) else int(row["누계"]),
        }
        for _, row in edited.iterrows()
    ]


def render_pages(key_prefix: str) -> tuple[int | None, int | None]:
    c1, c2 = st.columns(2)
    cur = c1.number_input("현재 쪽수", min_value=0, step=1, value=0, key=f"{key_prefix}_cur")
    tot = c2.number_input("전체 쪽수", min_value=0, step=1, value=0, key=f"{key_prefix}_tot")
    return (cur or None), (tot or None)


# ==========================================
# 메인
# ==========================================

def main() -> None:
    st.set_page_config(page_title="계수표 검수", page_icon="🗳️", layout="wide")

    reviewer = ensure_reviewer()
    if reviewer is None:
        return

    st.title("🗳️ 사전투표 계수표 검수 시스템")
    st.caption(f"검수자: {reviewer['id']}")

    with st.sidebar:
        st.header(f"👤 {reviewer['name']}")
        st.caption(f"ID: {reviewer['id']}")
        if st.button("다른 계정으로 변경"):
            REVIEWER_CACHE.unlink(missing_ok=True)
            del st.session_state["reviewer"]
            st.rerun()

    jobs = load_pending_jobs(reviewer["id"])
    if not jobs:
        st.info("✅ 현재 검수 대기 중인 계수표가 없습니다.")
        if st.button("🔄 새로고침"):
            st.rerun()
        return

    st.sidebar.header(f"대기 중: {len(jobs)}건")
    labels = [f"{i+1}. {j.get('file_name', j.get('file_id', '?'))}" for i, j in enumerate(jobs)]
    idx = st.sidebar.radio("검수할 파일", range(len(jobs)), format_func=lambda i: labels[i])
    if st.sidebar.button("🔄 목록 새로고침"):
        st.rerun()

    job = jobs[idx]
    file_name = job.get("file_name", "?")
    image_path = job.get("image_path", "")
    key_prefix = job.get("file_id", str(idx))

    st.markdown(f"### 파일: `{file_name}`")
    col_img, col_form = st.columns([1, 1])

    with col_img:
        st.subheader("🖼️ 계수표 이미지")
        if image_path and Path(image_path).exists():
            st.image(Image.open(image_path))
        else:
            st.warning(f"이미지를 찾을 수 없습니다: {image_path}")

    with col_form:
        meta = render_meta_form(key_prefix)
        st.divider()
        counting = render_counting_table(key_prefix)
        st.divider()
        cur_page, tot_page = render_pages(key_prefix)
        meta["현재쪽수"] = cur_page or ""
        meta["전체쪽수"] = tot_page or ""
        st.divider()

        if st.button("✅ 승인 및 CSV 저장", type="primary"):
            try:
                approve_job(job, meta, counting, reviewer["id"])
                st.success(f"✅ '{file_name}' 저장 완료 → {ENTRIES_CSV}")
                st.balloons()
                st.rerun()
            except Exception as e:
                st.error(f"저장 실패: {e}")

        if st.button("⏭️ 건너뛰기"):
            p = Path(job["_path"])
            data = json.loads(p.read_text(encoding="utf-8"))
            data["status"] = "skipped"
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            st.warning("건너뛰었습니다.")
            st.rerun()

    st.divider()
    total_approved = len(list(APPROVED_DIR.glob("*.json"))) if APPROVED_DIR.exists() else 0
    st.caption(f"승인 완료: {total_approved}건 | 대기 중: {len(jobs)}건 | CSV: `{ENTRIES_CSV}`")


if __name__ == "__main__":
    main()
