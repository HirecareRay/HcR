import json
import random
import time
from bs4 import BeautifulSoup
import requests, os
from urllib.parse import urlparse, unquote
from tqdm import tqdm


dir_name = "jobplanet_review"
os.makedirs(dir_name, exist_ok=True)

session = requests.Session()

# 로그인 쿠키
cookies = {"refresh_token": "Lf_QnL9xh5zNQMYsORC-yi4mqF8vdOlFO_Fo0R-v1ro",
"access_token": "VQhN8cNrdaP4unAzQ6eMTXhgnHs2lgCUFLttrV4WYPQ",
"Jobplanet_remember_user_token": "eyJeyJfcmFpbHMiOnsibWVzc2FnZSI6IlcxczFORFEzTmpRd1hTd2lKREpoSkRFd0pHYzFNbGRDWnk1b2VsaHdjR2t5TmpKMlVtNUpZUzRpTENJeE56Z3hOakF5TkRFMExqTTRNekl6TXlKZCIsImV4cCI6IjIwMjYtMDctMTRUMDk6MzM6MzQuMzgzWiIsInB1ciI6ImNvb2tpZS5Kb2JwbGFuZXRfcmVtZW1iZXJfdXNlcl90b2tlbiJ9fQ%3D%3D--2c40440330db467241ab0488789b5015666dec66",
"__cf_bm": "teHPXqu3y5njvCTQa3inoieau7mHlTSO60sf8kGyldg-1781606483.1704185-1.0.1.1-GzGITw1dRE4t6IN0zUC4NIPrF.7G6f8mQb50wpKSdC_.cNgnetrk72mGPxR5N7GIfNxsMhyJG_bSRkZ_ge45vC_Ie_Zu9C9Ks0y9aMUpn_Y0ne1W_qfYlExFLp4BcaXy",
"_intween_x2o_net_session": "Wk9jV2xvam1xamplakxKTUsrQk40RUVxU3AzTUhtWnk4SFVTSEU1dHR1VGlCWEhjMFdvREtia0NXWlMyVkJnb3VlNzJWODY0SVQyRHlEREJDK2FyUUhveXl3cXBTM2VQd1FDUktuTnU5QWJsaXB0bXZlWEVoMDNpU0d2RlVHRkVOdjRVTXZGYjh5QXZUS0NxSlliWklZV0hLUkF0ZVVxYXJDNTZCVlFSMFNZblhLTEI0VnBoN1VLRm5PVW40ZG5lVDkvMU1GMHZVcjRGNTFZVDUyNythdGxXSmpORUp0K2p4ODlLeUxpU0I2TXNjUVJyWE1yQmY3WU9VWEZRNWRiWHpsS1hUbGlJWmhvcSszTkN2aVgyc3ZtaVR6NkUxWmQyblZsUWJ6eU9HUjI1Y09NSkxwdk9lcWROOXRyRmpFZnd5UGxZbVpLOWNJQ3FrK3o0RWN4TlpGVHVNeW0wRlZ6OWxTVnZjbmlSTmRYY3g5YlRNUUZrTHNQaGlFc1Y1Q0FzV2RiYVhUd1hjTlRrN09pRURFZ1MrVlBzVnY0QTluM1RqbFdkcm1YT2MwVVJDVjM0NTJYQ1ZLZTdPR1VpYzliYy0tbjVsQnVVdFpXbVRmc1NSa3VzdDJyZz09--87c1c789498772e6524724140bba04658a367475",
"AWSALBCORS": "a8nOfqI5pxc5TZCZO2FxRBnH2MwN/88HYzGivFXma9YKsi1GQiTNNwDIJ+oBKygX9lO4wNyZqsqB+OKFuGtuzlho2zRssXdRNHg18Cc3xsrN/EvHl5kzuAvQQm6i",
"AWSALB": "a8nOfqI5pxc5TZCZO2FxRBnH2MwN/88HYzGivFXma9YKsi1GQiTNNwDIJ+oBKygX9lO4wNyZqsqB+OKFuGtuzlho2zRssXdRNHg18Cc3xsrN/EvHl5kzuAvQQm6i"
}

# 쿠키 적용
for k, v in cookies.items():
    session.cookies.set(k, v)

# (중요) 일부 사이트는 헤더 필요
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
    "sec-ch-ua": '"Microsoft Edge";v="144", "Chromium";v="144", "Not=A?Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Referer": "https://www.jobplanet.co.kr/"
}

# 로그인 후 접근할 페이지
url = "https://www.jobplanet.co.kr/companies/{}"
api_url = "https://www.jobplanet.co.kr/api/v4/companies/reviews/list?device=desktop&company_id={}&page={}&{}"

# 안전한 HTTP 요청 및 재시도를 위한 공통 함수
def safe_request(session, method, target_url, headers, timeout=1000, max_retries=5, backoff_factor=2):
    """
    상태 코드가 200이 아니거나 예외 발생 시, 일정 시간 대기 후 재시도하는 함수
    """
    retries = 0
    delay = random.uniform(5, 19)
    
    waiting_time = 0
    while retries < max_retries:
        try:
            if method.upper() == 'GET':
                res = session.get(target_url, headers=headers, timeout=timeout)
            
            if res.status_code == 200:
                if retries > 0:
                    print(f"기다린 시간: {waiting_time}")
                return res
            else:
                print(f"[경고] 상태 코드 {res.status_code} 발생. {delay}초 후 재시도합니다. (시도 {retries + 1}/{max_retries})")
        except requests.RequestException as e:
            print(f"[오류] 네트워크 에러 발생: {e}. {delay}초 후 재시도합니다. (시도 {retries + 1}/{max_retries})")
        
        time.sleep(delay)
        retries += 1
        waiting_time += delay
        delay *= backoff_factor  # 실패할 때마다 대기 시간 늘림 (지수 백오프)
        
    print(f"[실패] {max_retries}회 재시도 후에도 연결에 실패했습니다: {target_url}")
    return None

for i in tqdm(range(1, 1000000)):
    # 1. 회사 기본 페이지 요청
    res = safe_request(session, 'GET', url.format(i), headers=headers)
    if not res:
        print("=" * 50)
        print("last cid:", i, "line: 74")
        break # 요청 실패 시 다음 ID로 건너뜀
        
    soup = BeautifulSoup(res.text, 'html.parser')
    if soup.select_one("#premiumReviewStatistics"):
        company = unquote(urlparse(res.url).path).strip('/').split('/')[-1]
        
        # 파일명으로 사용할 수 없는 특수문자 제거 (_로 대체)
        # 예: / , : * ? " < > | 등 방어 코드
        clean_company_name = "".join(c for c in company if c.isalnum() or c in (' ', '_', '-')).strip()
        filename = os.path.join(dir_name, f"{clean_company_name}_{i}_jobplanet.jsonl")
        
        year = ""
        # 2. 첫 번째 API 요청 (전체 카운트 확인)
        res = safe_request(session, 'GET', api_url.format(i, 1, year), headers=headers)
        if not res: 
            print("=" * 50)
            print("last cid:", i, "line: 91")
            break

        data = res.json()
        count = data.get("data", {}).get("approved_total_count", 0)
        
        if count > 300:
            year = "&year=2024,2025,2026"
            # 3. 필터링된 API 요청
            res = safe_request(session, 'GET', api_url.format(i, 1, year), headers=headers)
            if not res: 
                print("=" * 50)
                print("last cid:", i, "line: 103")
                break
            
            data = res.json()
            count = data.get("data", {}).get("approved_filtered_count", 0)
            
        if count > 0:
            max_page = count / 5 if count % 5 == 0 else (count // 5) + 1
            
            # JSONL 파일 오픈 (a 모드로 이어서 쓰기)
            with open(filename, 'a+', encoding='utf-8') as f:
                for page in tqdm(range(1, int(max_page) + 1)): # range 범위 정상화 (+1 추가)
                    # 4. 페이지별 리뷰 데이터 API 요청
                    res = safe_request(session, 'GET', api_url.format(i, page, year), headers=headers)
                    if not res: 
                        print("=" * 50)
                        print("last cid:", i, "page:", page, "line: 119")
                        break
                    
                    data = res.json()
                    items = data.get("data", {}).get("items", [])
                    
                    for item in items:
                        if item.get("type") == "COMPANY_REVIEW":
                            review_data = item.get("review")
                            if review_data:
                                # JSONL 형식으로 한 줄씩 저장 (ensure_ascii=False로 한글 깨짐 방지)
                                f.write(json.dumps(review_data, ensure_ascii=False) + "\n")
    else:
        print("=" * 50)
        print(res.url)
        with open("./jobplanet_error.txt", 'a+', encoding='utf-8') as f:
            f.write(f"{i}, {res.url}\n")
        print("last cid:", i, "line 133")
        continue
