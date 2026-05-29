#!/bin/bash
# OCR 컨테이너: 드라이브 감시자만 실행
# (계수표 OCR은 검수 앱에서 수동 입력으로 대체)

set -e

echo "=== 드라이브 감시 시작 ==="
exec python ocr/drive_watcher.py
