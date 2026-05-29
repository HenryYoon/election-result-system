FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# requests 엔진 사용 → 크롬/크롬드라이버 불필요 (tzdata만 설치)
RUN apt-get update && apt-get install -y \
    tzdata \
    --no-install-recommends \
 && ln -snf /usr/share/zoneinfo/Asia/Seoul /etc/localtime \
 && echo "Asia/Seoul" > /etc/timezone \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONPATH=/app
ENV TZ=Asia/Seoul

COPY docker/crawler/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY crawler/ /app/crawler/
COPY nec_urls.csv /app/nec_urls.csv

CMD ["python", "crawler/scrape_nec.py"]
