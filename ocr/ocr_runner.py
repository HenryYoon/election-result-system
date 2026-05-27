"""
ocr/ocr_runner.py
OpenRouter (Gemini Flash) 를 이용한 계수표 이미지 OCR 모듈

동작:
  - ocr_results/ 폴더의 status=pending 작업을 찾아 OCR 수행
  - OCR 결과(소계/누계 + 참관인 정보)를 JSON에 저장하고 status=ocr_done으로 변경
  - 검수 앱(review/app.py)이 ocr_done 항목을 표시

환경변수:
  OPENROUTER_API_KEY   OpenRouter API 키
"""

import os
import sys
import json
import base64
import logging
import time
import schedule
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ==========================================
# 로깅 설정
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("ocr_runner.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ==========================================
# 설정
# ==========================================
BASE_DIR = Path(__file__).resolve().parent.parent
OCR_RESULTS_DIR = BASE_DIR / "ocr_results"
APPROVED_DIR = BASE_DIR / "approved"

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OCR_MODEL = "google/gemini-flash-1.5"

# 계수표 OCR 프롬프트
COUNTING_SHEET_PROMPT = """
이 이미지는 한국 선거 사전투표 '사전투표자수 계수지' 양식입니다.

이미지에서 다음 두 가지 정보를 추출해 JSON으로 반환하세요.

## 1. 참관인 정보 (observer_info)
계수지 상단 표에서 추출합니다:
- date: 날짜 (5.29 또는 5.30)
- province: 시도
- city: 시군구
- booth_number: 사전투표소 번호
- booth_name: 사전투표소 투표소명
- inside_count: 관내 숫자
- outside_count: 관외 숫자
- observer_name: 참관인 이름
- observer_phone: 참관인 전화번호

## 2. 계수 현황 (counting_data)
계수지 본문 표(시작시각, 10, 20, 30, 40, 50, 소계, 누계 컬럼)에서
**소계와 누계 컬럼만** 추출합니다.
각 행(1~10번)의 소계와 누계 숫자를 읽습니다.
빈 칸은 null로 표시합니다.

## 출력 형식 (반드시 JSON만 반환, 설명 없이)
{
  "observer_info": {
    "date": "",
    "province": "",
    "city": "",
    "booth_number": "",
    "booth_name": "",
    "inside_count": null,
    "outside_count": null,
    "observer_name": "",
    "observer_phone": ""
  },
  "counting_data": [
    {"row": 1, "subtotal": null, "cumulative": null},
    {"row": 2, "subtotal": null, "cumulative": null},
    {"row": 3, "subtotal": null, "cumulative": null},
    {"row": 4, "subtotal": null, "cumulative": null},
    {"row": 5, "subtotal": null, "cumulative": null},
    {"row": 6, "subtotal": null, "cumulative": null},
    {"row": 7, "subtotal": null, "cumulative": null},
    {"row": 8, "subtotal": null, "cumulative": null},
    {"row": 9, "subtotal": null, "cumulative": null},
    {"row": 10, "subtotal": null, "cumulative": null}
  ],
  "current_page": null,
  "total_pages": null
}
"""


def encode_image_to_base64(image_path: str) -> str:
    """이미지 파일을 base64로 인코딩한다."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def get_image_mime_type(image_path: str) -> str:
    """파일 확장자로 MIME 타입을 추론한다."""
    ext = Path(image_path).suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }
    return mime_map.get(ext, "image/jpeg")


def call_openrouter_ocr(image_path: str) -> dict:
    """
    OpenRouter Gemini Flash로 이미지 OCR을 수행한다.

    Returns:
        파싱된 OCR 결과 딕셔너리
    Raises:
        RuntimeError: API 호출 실패 또는 JSON 파싱 실패 시
    """
    if not OPENROUTER_API_KEY:
        raise EnvironmentError("OPENROUTER_API_KEY 환경변수가 설정되지 않았습니다.")

    image_b64 = encode_image_to_base64(image_path)
    mime_type = get_image_mime_type(image_path)

    payload = {
        "model": OCR_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_b64}"
                        },
                    },
                    {
                        "type": "text",
                        "text": COUNTING_SHEET_PROMPT,
                    },
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 2048,
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/election-result-system",
        "X-Title": "Election Result OCR",
    }

    response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)

    if response.status_code != 200:
        raise RuntimeError(
            f"OpenRouter API 오류 {response.status_code}: {response.text[:500]}"
        )

    resp_json = response.json()
    content = resp_json["choices"][0]["message"]["content"].strip()

    # JSON 블록 추출 (```json ... ``` 형식 대응)
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"OCR 응답 JSON 파싱 실패: {e}\n응답: {content[:300]}")


def process_pending_jobs() -> None:
    """OCR 대기 중인 작업들을 처리한다."""
    if not OCR_RESULTS_DIR.exists():
        logger.info("ocr_results 폴더가 없습니다.")
        return

    pending_jobs = [
        p for p in OCR_RESULTS_DIR.glob("*.json")
        if p.stem != "processed"
    ]

    if not pending_jobs:
        logger.info("처리할 OCR 작업이 없습니다.")
        return

    logger.info(f"OCR 작업 {len(pending_jobs)}개 발견")

    for job_path in pending_jobs:
        try:
            with open(job_path, "r", encoding="utf-8") as f:
                job = json.load(f)

            if job.get("status") != "pending":
                continue

            image_path = job.get("image_path", "")
            if not image_path or not Path(image_path).exists():
                logger.warning(f"이미지 파일 없음: {image_path} ({job_path.name})")
                job["status"] = "error"
                job["error"] = "이미지 파일 없음"
                with open(job_path, "w", encoding="utf-8") as f:
                    json.dump(job, f, ensure_ascii=False, indent=2)
                continue

            logger.info(f"OCR 처리 중: {job['file_name']}")
            ocr_result = call_openrouter_ocr(image_path)

            job["status"] = "ocr_done"
            job["ocr_result"] = ocr_result.get("counting_data", [])
            job["observer_info"] = ocr_result.get("observer_info", {})
            job["current_page"] = ocr_result.get("current_page")
            job["total_pages"] = ocr_result.get("total_pages")

            with open(job_path, "w", encoding="utf-8") as f:
                json.dump(job, f, ensure_ascii=False, indent=2)

            logger.info(f"OCR 완료: {job_path.name}")

            # API 호출 간 딜레이 (rate limit 방지)
            time.sleep(2)

        except Exception as e:
            logger.error(f"OCR 처리 실패 ({job_path.name}): {e}", exc_info=True)
            try:
                with open(job_path, "r", encoding="utf-8") as f:
                    job = json.load(f)
                job["status"] = "error"
                job["error"] = str(e)
                with open(job_path, "w", encoding="utf-8") as f:
                    json.dump(job, f, ensure_ascii=False, indent=2)
            except Exception:
                pass


def job_wrapper() -> None:
    try:
        process_pending_jobs()
    except Exception as e:
        logger.critical(f"치명적 오류: {e}", exc_info=True)


# ==========================================
# 진입점
# ==========================================
if __name__ == "__main__":
    logger.info("🚀 OCR 실행기 시작")

    # 즉시 한 번 실행
    job_wrapper()

    # 5분마다 새 작업 확인
    schedule.every(5).minutes.do(job_wrapper)

    while True:
        schedule.run_pending()
        time.sleep(5)
