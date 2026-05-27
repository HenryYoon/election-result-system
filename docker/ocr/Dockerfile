FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1-mesa-glx \
    --no-install-recommends \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/ocr/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY ocr/ /app/ocr/

RUN mkdir -p /app/images /app/ocr_results /app/approved

# drive_watcher + ocr_runner 동시 실행을 위한 스타트업 스크립트
COPY docker/ocr/start.sh /app/start.sh
RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]
