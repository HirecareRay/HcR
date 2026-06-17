import os
from dotenv import load_dotenv  # python-dotenv 라이브러리 필요

load_dotenv()  # .env 파일을 읽어서 환경변수로 등록

# 환경변수에서 키 꺼내오기
DART_API_KEY = os.getenv('DART_API_KEY')

# 키가 제대로 불러와졌는지 확인 (없으면 에러)
if not DART_API_KEY:
    raise ValueError('.env에서 DART_API_KEY를 찾을 수 없음')
