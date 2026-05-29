FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    fonts-nanum \
    tzdata \
    --no-install-recommends \
 && ln -snf /usr/share/zoneinfo/Asia/Seoul /etc/localtime \
 && echo "Asia/Seoul" > /etc/timezone \
 && wget -q -O- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/trusted.gpg.d/google-chrome.gpg \
 && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update && apt-get install -y google-chrome-stable --no-install-recommends \
 && CHROME_VERSION=$(google-chrome --version | awk '{print $3}') \
 && CHROMEDRIVER_URL="https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chromedriver-linux64.zip" \
 && wget -q "$CHROMEDRIVER_URL" -O chromedriver_linux64.zip \
 && unzip chromedriver_linux64.zip \
 && mv chromedriver-linux64/chromedriver /usr/local/bin/ \
 && chmod +x /usr/local/bin/chromedriver \
 && rm -rf chromedriver_linux64.zip chromedriver-linux64 \
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
