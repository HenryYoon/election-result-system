#!/bin/bash
# OCR 컨테이너 시작 스크립트
# drive_watcher와 ocr_runner를 동시에 실행

set -e

echo "=== OCR 파이프라인 시작 ==="

# 드라이브 감시 프로세스 (백그라운드)
python ocr/drive_watcher.py &
WATCHER_PID=$!
echo "드라이브 감시 PID: $WATCHER_PID"

# OCR 실행 프로세스 (포그라운드)
python ocr/ocr_runner.py &
OCR_PID=$!
echo "OCR 실행 PID: $OCR_PID"

# 두 프로세스 중 하나가 종료되면 나머지도 종료
wait -n $WATCHER_PID $OCR_PID
echo "프로세스 종료 감지, 컨테이너 종료"
kill $WATCHER_PID $OCR_PID 2>/dev/null || true
