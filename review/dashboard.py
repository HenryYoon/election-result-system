"""
review/dashboard.py
사전투표 결과 현황 대시보드 (Streamlit)

실행:
  streamlit run review/dashboard.py --server.port 8502

환경변수:
  GOOGLE_SERVICE_ACCOUNT_JSON
  SPREADSHEET_ID         선관위 결과 집계 시트
  DATE_CODE              수집 일차 (1 or 2)
"""

import os
import sys
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared.config import settings

DATE_CODE = settings.date_code
DATA_DIR = settings.data_dir
CHANGED_DIR = DATA_DIR / "changed"
ENTRIES_CSV = DATA_DIR / "manual_entries.csv"

# ==========================================
# 데이터 로드 (모두 CSV 기반)
# ==========================================

@st.cache_data(ttl=60)
def load_official_data(date_code: str) -> pd.DataFrame:
    """선관위 크롤러가 저장한 가장 최근 통합 CSV를 로드한다."""
    if not DATA_DIR.exists():
        return pd.DataFrame()
    # data/YYYY_MM_DD_HH_MM/{suffix}/csv/전체지역.csv 중 가장 최근
    candidates = sorted(
        DATA_DIR.glob("*/*/csv/전체지역.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return pd.DataFrame()
    try:
        return pd.read_csv(candidates[0], encoding="utf-8-sig")
    except Exception as e:
        st.warning(f"선관위 CSV 로드 실패: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_manual_data() -> pd.DataFrame:
    """검수 앱이 저장한 수동 계수 CSV를 로드한다."""
    if not ENTRIES_CSV.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(ENTRIES_CSV, encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_counting_data() -> pd.DataFrame:
    """수동 계수 = 검수 결과와 동일."""
    return load_manual_data()


def load_change_files() -> pd.DataFrame:
    """changed_*.csv 파일들을 합쳐 반환한다."""
    if not CHANGED_DIR.exists():
        return pd.DataFrame()
    dfs = []
    for f in sorted(CHANGED_DIR.glob("changed_*.csv"), reverse=True)[:10]:
        try:
            dfs.append(pd.read_csv(f, encoding="utf-8-sig"))
        except Exception:
            pass
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def _to_int(v) -> int:
    try:
        return int(str(v).replace(",", ""))
    except Exception:
        return 0


# ==========================================
# 페이지별 렌더링
# ==========================================

def page_overview(official_df: pd.DataFrame, manual_df: pd.DataFrame) -> None:
    st.header("📊 전체 현황")

    if official_df.empty:
        st.info("선관위 데이터가 없습니다.")
        return

    # 시간당 투표수 컬럼 탐색
    hourly_col = "시간당_투표수" if "시간당_투표수" in official_df.columns else None
    cum_cols = [c for c in official_df.columns if "누계" in c]
    cum_col = cum_cols[0] if cum_cols else None

    col1, col2, col3 = st.columns(3)

    if cum_col:
        total_votes = official_df[cum_col].apply(_to_int).sum()
        col1.metric("선관위 누계 총합", f"{total_votes:,}")

    if not manual_df.empty and "누계" in manual_df.columns:
        # 투표소별 마지막 누계의 합
        agg = manual_df.groupby("투표소명")["누계"].apply(lambda s: s.apply(_to_int).max()).sum()
        col2.metric("수동 계수 누계 총합", f"{agg:,}")

    col3.metric("수집 지역 수", f"{official_df['구군명'].nunique() if '구군명' in official_df.columns else '-'}")

    # 시간대별 시간당 투표수 추이
    if hourly_col and "조회_시간코드" in official_df.columns:
        st.subheader("⏱️ 시간대별 시간당 투표수 추이")
        hourly_df = official_df.groupby("조회_시간코드")[hourly_col].apply(
            lambda s: s.apply(_to_int).sum()
        ).reset_index()
        hourly_df.columns = ["시간대", "시간당_투표수"]
        hourly_df = hourly_df.sort_values("시간대")
        fig = px.line(hourly_df, x="시간대", y="시간당_투표수", markers=True)
        fig.update_layout(height=300)
        st.plotly_chart(fig)


def page_comparison(official_df: pd.DataFrame, counting_df: pd.DataFrame) -> None:
    st.header("🔍 선관위 vs 수동 계수 비교")

    if official_df.empty and counting_df.empty:
        st.info("비교할 데이터가 없습니다.")
        return

    cum_cols = [c for c in official_df.columns if "누계" in c] if not official_df.empty else []
    cum_col = cum_cols[0] if cum_cols else None

    # 투표소별 비교 테이블
    if not counting_df.empty and cum_col and not official_df.empty:
        st.subheader("투표소별 비교")

        # 수동 계수 집계 (투표소별 최대 누계)
        if "투표소명" in counting_df.columns and "누계" in counting_df.columns:
            manual_agg = counting_df.groupby("투표소명")["누계"].apply(
                lambda s: s.apply(_to_int).max()
            ).reset_index()
            manual_agg.columns = ["투표소명", "수동_누계"]

            if "사전투표소명" in official_df.columns:
                official_agg = official_df.groupby("사전투표소명")[cum_col].apply(
                    lambda s: s.apply(_to_int).max()
                ).reset_index()
                official_agg.columns = ["투표소명", "선관위_누계"]

                merged = official_agg.merge(manual_agg, on="투표소명", how="outer").fillna(0)
                merged["차이"] = merged["선관위_누계"].apply(_to_int) - merged["수동_누계"].apply(_to_int)

                # 차이가 있는 투표소 강조
                def highlight_diff(row):
                    if abs(row["차이"]) > 0:
                        return ["background-color: #ffe0e0"] * len(row)
                    return [""] * len(row)

                st.dataframe(merged.style.apply(highlight_diff, axis=1))

                # 바차트
                fig = go.Figure()
                fig.add_bar(x=merged["투표소명"], y=merged["선관위_누계"], name="선관위")
                fig.add_bar(x=merged["투표소명"], y=merged["수동_누계"], name="수동 계수")
                fig.update_layout(barmode="group", height=400, xaxis_tickangle=-45)
                st.plotly_chart(fig)
    else:
        st.info("수동 계수 데이터가 없거나 비교 불가합니다.")


def page_changes(change_df: pd.DataFrame) -> None:
    st.header("⚠️ 선관위 데이터 변경 이력")

    if change_df.empty:
        st.success("감지된 변경 없음")
        return

    st.warning(f"총 {len(change_df)}건의 변경이 감지되었습니다.")

    # 필터
    if "시도명" in change_df.columns:
        sido_list = ["전체"] + sorted(change_df["시도명"].dropna().unique().tolist())
        selected_sido = st.selectbox("시도 필터", sido_list)
        if selected_sido != "전체":
            change_df = change_df[change_df["시도명"] == selected_sido]

    st.dataframe(change_df)

    # 변경 건수 시간대별 바차트
    if "감지시각" in change_df.columns:
        change_df = change_df.copy()
        change_df["감지시각_dt"] = pd.to_datetime(change_df["감지시각"], errors="coerce")
        change_df["시간"] = change_df["감지시각_dt"].dt.strftime("%m-%d %H시")
        count_by_hour = change_df.groupby("시간").size().reset_index(name="변경건수")
        fig = px.bar(count_by_hour, x="시간", y="변경건수", title="시간대별 변경 감지 건수")
        fig.update_layout(height=300)
        st.plotly_chart(fig)


def page_progress(counting_df: pd.DataFrame, official_df: pd.DataFrame) -> None:
    st.header("📋 수동 계수 진행 현황")

    if official_df.empty:
        st.info("선관위 데이터가 없습니다.")
        return

    total_booths = official_df["사전투표소명"].nunique() if "사전투표소명" in official_df.columns else 0

    if not counting_df.empty and "투표소명" in counting_df.columns:
        done_booths = counting_df["투표소명"].nunique()
    else:
        done_booths = 0

    col1, col2, col3 = st.columns(3)
    col1.metric("전체 투표소", f"{total_booths:,}")
    col2.metric("계수 완료", f"{done_booths:,}")
    col3.metric("진행률", f"{done_booths / total_booths * 100:.1f}%" if total_booths > 0 else "0%")

    st.progress(done_booths / total_booths if total_booths > 0 else 0)

    if not counting_df.empty:
        st.subheader("완료된 투표소 목록")
        st.dataframe(
            counting_df[["시도", "시군구", "투표소명", "승인시각"]].drop_duplicates() if
            all(c in counting_df.columns for c in ["시도", "시군구", "투표소명", "승인시각"])
            else counting_df,
        )


# ==========================================
# 메인
# ==========================================

def main():
    st.set_page_config(
        page_title="사전투표 현황 대시보드",
        page_icon="📊",
        layout="wide",
    )
    st.title("📊 사전투표 결과 현황 대시보드")
    st.caption(f"마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (새로고침하면 최신 데이터 로드)")

    tab1, tab2, tab3, tab4 = st.tabs([
        "전체 현황", "선관위 vs 수동 비교", "변경 감지 이력", "계수 진행 현황"
    ])

    with st.spinner("데이터 로드 중..."):
        official_df = load_official_data(DATE_CODE)
        manual_df = load_manual_data()
        counting_df = load_counting_data()
        change_df = load_change_files()

    with tab1:
        page_overview(official_df, manual_df)
    with tab2:
        page_comparison(official_df, counting_df)
    with tab3:
        page_changes(change_df)
    with tab4:
        page_progress(counting_df, official_df)

    if st.sidebar.button("🔄 데이터 새로고침"):
        st.cache_data.clear()
        st.rerun()

    st.sidebar.caption(f"선거 {DATE_CODE}일차")


if __name__ == "__main__":
    main()
