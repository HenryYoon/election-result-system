# 사전투표 결과 수합 시스템

선관위 사전투표 결과를 자동 수집·OCR 처리·검수하여 구글 시트로 집계하는 시스템입니다.

## 시스템 구성

```
election-result-system/
├── crawler/
│   └── scrape_nec.py        선관위 크롤러 (Docker)
├── ocr/
│   ├── drive_watcher.py     구글 드라이브 이미지 감시·다운로드 (Docker)
│   └── ocr_runner.py        OpenRouter Gemini Flash OCR (Docker)
├── review/
│   └── app.py               Streamlit 검수 앱 (로컬 실행)
├── shared/
│   └── gsheet_uploader.py   구글 시트 공통 업로드 모듈
├── docker/
│   ├── crawler/             크롤러 Docker 설정
│   └── ocr/                 OCR Docker 설정
├── docker-compose.yml
└── .env.example
```

## 데이터 흐름

```
선관위 사이트
    ↓ (Selenium, 매시간 :05)
crawler/scrape_nec.py
    ↓ 저장
data/{연_월_일_시_분}/
    ↓
구글 시트 (결과 집계)

구글 드라이브 (계수표 이미지)
    ↓ (매시간 :10)
ocr/drive_watcher.py → images/
    ↓ (5분 간격)
ocr/ocr_runner.py → ocr_results/*.json
    ↓
review/app.py (Streamlit 검수)
    ↓ 승인
구글 시트 (계수표 집계) + (참관인 정보)
```

## 빠른 시작

### 1. 환경변수 설정

```bash
cp .env.example .env
# .env 파일을 편집하여 API 키와 스프레드시트 ID 설정
```

### 2. 구글 서비스 계정 설정

1. [Google Cloud Console](https://console.cloud.google.com/)에서 서비스 계정 생성
2. Google Sheets API, Google Drive API 활성화
3. 서비스 계정 JSON 키 다운로드
4. `.env`의 `GOOGLE_SERVICE_ACCOUNT_JSON`에 파일 경로 또는 base64 인코딩 값 입력
5. 해당 서비스 계정을 구글 시트와 드라이브 폴더에 **편집자** 권한으로 추가

### 3. Docker로 크롤러 + OCR 실행

```bash
# 크롤러와 OCR 파이프라인 시작
docker-compose up -d

# 로그 확인
docker-compose logs -f crawler
docker-compose logs -f ocr
```

### 4. Streamlit 검수 앱 실행 (로컬)

```bash
pip install streamlit gspread google-auth pandas Pillow

# .env 로드 후 실행
streamlit run review/app.py
```

---

## 테스트 모드

실제 선거일 이전에 시스템을 테스트하려면:

```bash
# .env에서 설정
TEST_MODE=1
ELECTION_ID=0020250603   # 과거 선거 ID 사용
```

- 10분 간격으로 1시간씩 누적 시뮬레이션 (총 2시간에 07~18시 완료)
- `+0분`: timeCode=07만 수집
- `+10분`: timeCode=07, 08 수집
- ...
- `+110분`: timeCode=07~18 전체 수집

---

## 크롤러 상세

### 파일 저장 구조

```
data/
└── 2026_05_29_08_05/
    ├── 서울특별시_종로구_07시.html
    ├── 서울특별시_종로구_07시.csv
    ├── 서울특별시_종로구_07~08시.html
    ├── 서울특별시_종로구_07~08시.csv
    ...
    └── 전체지역_07~08시.csv
```

### 구글 시트 구조

스프레드시트: `1sBLCs8So45lCD5xQfhmrPr_HlA1zgwR0-i1T23bw09s`

| 시트명 | 내용 |
|--------|------|
| `1일차 관내 발표` | 1일차 관내 사전투표 현황 |
| `1일차 관외 발표` | 1일차 관외 사전투표 현황 |
| `2일차 관내 발표` | 2일차 관내 사전투표 현황 |
| `2일차 관외 발표` | 2일차 관외 사전투표 현황 |

---

## OCR 상세

### 계수표 양식

계수표에서 추출하는 정보:
- **소계/누계**: 10행 × 2컬럼 (핵심 데이터)
- **참관인 정보**: 시도, 시군구, 투표소 번호/명, 관내/관외, 이름, 전화번호

### OCR 결과 파일 구조 (ocr_results/*.json)

```json
{
  "file_id": "구글드라이브파일ID",
  "file_name": "파일명.jpg",
  "image_path": "/app/images/...",
  "created_time": "2026-05-29T07:30:00Z",
  "status": "ocr_done",
  "ocr_result": [
    {"row": 1, "subtotal": 50, "cumulative": 50},
    ...
  ],
  "observer_info": {
    "date": "5.29",
    "province": "서울특별시",
    "city": "종로구",
    "booth_number": "1",
    "booth_name": "종로구사전투표소",
    "inside_count": 167,
    "outside_count": 38,
    "observer_name": "홍길동",
    "observer_phone": "010-1234-5678"
  },
  "current_page": 1,
  "total_pages": 3
}
```

### 참관인 정보 스프레드시트

스프레드시트: `1LeJthvK8DYxYoCmCP0evfegeDcCZh2O5lGCAQNQauf4`

---

## 운영 주의사항

1. **선관위 발표 시간**: 매 정시보다 늦을 수 있어 크롤러는 `:05`분에 실행
2. **드라이브 이미지 시간 필터**: 해당 시간대(hour)에 업로드된 파일만 처리 (중복 방지)
3. **재시도**: 데이터 미발표 시 1분 간격 최대 10회 재시도
4. **Docker 격리**: 크롤러와 OCR은 Docker 컨테이너에서 실행, 장애 발생 시 `unless-stopped`로 자동 재시작
5. **Streamlit 검수 앱**: 로컬에서 실행, `ocr_results/` 폴더를 OCR 컨테이너와 공유

---

## 의존성

### 크롤러 (Docker)
- selenium, beautifulsoup4, pandas, schedule, gspread, google-auth

### OCR (Docker)
- requests, schedule, google-api-python-client, gspread, Pillow

### 검수 앱 (로컬)
```bash
pip install streamlit gspread google-auth pandas Pillow
```
