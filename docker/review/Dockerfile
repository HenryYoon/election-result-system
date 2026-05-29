FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    tzdata \
    --no-install-recommends \
 && ln -snf /usr/share/zoneinfo/Asia/Seoul /etc/localtime \
 && echo "Asia/Seoul" > /etc/timezone \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONPATH=/app
ENV TZ=Asia/Seoul

COPY docker/review/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY review/ /app/review/

CMD ["streamlit", "run", "review/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
