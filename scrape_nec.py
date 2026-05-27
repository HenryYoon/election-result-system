import time
import random
import os
import logging
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import schedule

# ==========================================
# 1. 로깅(Logging) 세팅
# ==========================================
# 로그 포맷 설정: [2026-05-27 13:00:05] [INFO] 메시지 내용
log_format = "[%(asctime)s] [%(levelname)s] %(message)s"
date_format = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO, # INFO 레벨 이상(INFO, WARNING, ERROR) 모두 기록
    format=log_format,
    datefmt=date_format,
    handlers=[
        # 크롤러가 실행되는 위치에 자동으로 log 파일 생성 (utf-8 설정으로 한글 깨짐 방지)
        logging.FileHandler("crawler_running.log", encoding="utf-8"),
        # 개발 중 콘솔에서도 보기 위해 StreamHandler 유지 (exe 빌드 시엔 파일에만 기록됨)
        logging.StreamHandler()
    ]
)

def crawl_all_targets():
    """CSV 파일의 지역 코드를 읽어 루프를 돌며 수집하고, 진행 상황을 로깅하는 함수"""
    
    input_filename = 'target_list.csv'
    
    # 대상 리스트 CSV 파일 검증
    if not os.path.exists(input_filename):
        logging.error(f"입력 파일 '{input_filename}'이 존재하지 않습니다. 수집을 중단합니다.")
        return
        
    try:
        targets_df = pd.read_csv(input_filename)
        logging.info(f"target_list.csv 로드 완료. 총 {len(targets_df)}개의 지역 수집을 시작합니다.")
    except Exception as e:
        logging.error(f"'{input_filename}' 파일을 읽는 중 오류 발생: {e}")
        return

    # 크롬 Headless 옵션 설정
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

    logging.info("크롬 Headless 브라우저를 구동합니다.")
    driver = webdriver.Chrome(options=chrome_options)
    
    total_data = []
    all_headers = None
    
    # 기본 페이지 접속
    base_url = "https://info.nec.go.kr/electioninfo/electionInfo_report.xhtml"
    try:
        driver.get(base_url)
        time.sleep(2.0)
    except Exception as e:
        logging.error(f"선관위 메인 페이지 접속 실패: {e}")
        driver.quit()
        return

    # CSV 타겟 리스트 루프 시작
    for idx, row in targets_df.iterrows():
        city_code = str(row['citycode']).strip()
        town_code = str(row['towncode']).strip()
        date_code = str(row['datecode']).strip()
        
        logging.info(f"[{idx+1}/{len(targets_df)}] 수집 시작 -> 시도:{city_code}, 구군:{town_code}, 일자:{date_code}")
        
        max_attempts = 10  
        attempt = 1
        target_success = False
        
        while attempt <= max_attempts:
            try:
                if attempt > 1:
                    driver.get(base_url)
                    time.sleep(1.5)
                
                # 셀레니움 요소 조작
                Select(driver.find_element(By.ID, "cityCode")).select_by_value(city_code)
                time.sleep(random.uniform(0.4, 0.8))
                
                Select(driver.find_element(By.ID, "townCode")).select_by_value(town_code)
                time.sleep(random.uniform(0.4, 0.8))
                
                Select(driver.find_element(By.ID, "dateCode")).select_by_value(date_code)
                time.sleep(random.uniform(0.4, 0.8))

                # 조회 버튼 클릭
                driver.find_element(By.CSS_SELECTOR, "#spanSubmit input[type='image']").click()
                time.sleep(random.uniform(2.0, 3.0)) 
                
                # BeautifulSoup 파싱 및 검증
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                table_tag = soup.find('table', id='table01')

                if not table_tag or not table_tag.find('tbody'):
                    raise ValueError("정시 데이터 테이블 구조가 화면에 없음")
                    
                table_rows = table_tag.find('tbody').find_all('tr')
                if len(table_rows) <= 1:
                    raise ValueError("테이블은 존재하나 실제 데이터 행이 비어있음 (미발표 상태)")

                # 데이터 파싱 진행
                if all_headers is None:
                    all_headers = [th.text.strip() for th in table_tag.find('thead').find_all('th')]
                    all_headers.extend(['조회_시도코드', '조회_구군코드', '조회_일자'])

                for t_row in table_rows:
                    cells = t_row.find_all('td')
                    row_data = [cell.text.strip() for cell in cells]
                    row_data.extend([city_code, town_code, date_code])
                    total_data.append(row_data)
                
                logging.info(f"   ㄴ [성공] {town_code} 지역 데이터 확보 완료.")
                target_success = True
                break 
                
            except Exception as e:
                logging.warning(f"   ㄴ [대기] {town_code} 데이터 미확보 ({attempt}/{max_attempts}): {e}")
                logging.info("   ㄴ 1분 후 재시도합니다...")
                time.sleep(60)
                attempt += 1
                
        if not target_success:
            logging.error(f"   ㄴ [실패] 10분간 재시도했으나 해당 지역({town_code}) 데이터를 가져오지 못했습니다.")

    # 데이터 통합 및 저장
    if total_data and all_headers:
        final_df = pd.DataFrame(total_data, columns=all_headers)
        current_file_time = time.strftime('%Y%m%d_%H00')
        output_filename = f'전체지역_사전투표진행상황_{current_file_time}.csv'
        
        final_df.to_csv(output_filename, index=False, encoding='utf-8-sig')
        logging.info(f"🎉 [작업 완료] 통합 CSV 파일 저장 성공: {output_filename}")
    else:
        logging.warning("⚠️ 이번 정시 루프에서는 수집된 데이터가 최종적으로 존재하지 않습니다.")

    # 자원 해제
    logging.info("🧹 크롬 브라우저 세션을 종료하고 메모리를 반환합니다.\n" + "="*50)
    driver.quit()

def job_wrapper():
    """스케줄러 래퍼 (예외 발생 시 프로그램 전체가 죽는 것을 방지)"""
    try:
        crawl_all_targets()
    except Exception as e:
        logging.critical(f"🚨 스케줄러 실행 중 치명적 시스템 에러 발생: {e}", exc_info=True)

# ==========================================
# 실행부 및 스케줄러 무한 루프
# ==========================================
if __name__ == "__main__":
    logging.info("🚀 사전투표 수집기 스케줄러 프로그램이 가동되었습니다.")
    
    # 매 시간 정시(:00분)마다 실행 등록
    schedule.every().hour.at(":00").do(job_wrapper)
    
    while True:
        schedule.run_pending()
        time.sleep(5)