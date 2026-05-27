FROM python:3.11-slim

# Chrome + ChromeDriver 설치
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    fonts-nanum \
    --no-install-recommends \
 && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
 && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" \
    >> /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update && apt-get install -y google-chrome-stable --no-install-recommends \
 && CHROME_VERSION=$(google-chrome --version | grep -oP '\d+\.\d+\.\d+') \
 && CHROMEDRIVER_VERSION=$(curl -s "https://chromedriver.storage.googleapis.com/LATEST_RELEASE_${CHROME_VERSION%%.*}") \
 && wget -q "https://chromedriver.storage.googleapis.com/${CHROMEDRIVER_VERSION}/chromedriver_linux64.zip" \
 && unzip chromedriver_linux64.zip -d /usr/local/bin/ \
 && rm chromedriver_linux64.zip \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 설치
COPY docker/crawler/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY shared/ /app/shared/
COPY crawler/ /app/crawler/
COPY nec_urls.csv /app/nec_urls.csv

# 데이터 폴더 생성
RUN mkdir -p /app/data

CMD ["python", "crawler/scrape_nec.py"]
