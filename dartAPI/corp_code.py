"""
corp_code.py
DART 기업 고유번호 목록을 내려받아 저장하고,
회사명 → 고유번호 변환 기능을 제공하는 모듈
"""

import io
import csv
import zipfile
import requests
import xml.etree.ElementTree as ET
from pathlib import Path

from config import DART_API_KEY  # 인증키는 config.py에서 가져옴

# ── 상수 ────────────────────────────────────────────────────────────────────
CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DATA_DIR = Path("data")           # 저장 폴더
CORP_CSV = DATA_DIR / "corp_codes.csv"  # 저장 파일 경로


# ── 내부 함수 ────────────────────────────────────────────────────────────────

def _download_corp_codes() -> list[dict]:
    """
    DART API에서 전체 기업 고유번호 목록을 내려받는다.
    응답이 zip 파일이므로 압축을 풀어 내부 CORPCODE.xml을 파싱한다.
    반환값: [{corp_code, corp_name, stock_code, modify_date}, ...] 리스트
    """
    print("DART에서 기업 목록을 내려받는 중...")

    response = requests.get(
        CORP_CODE_URL,
        params={"crtfc_key": DART_API_KEY},
        timeout=30,  # 30초 이내 응답 없으면 에러
    )

    # HTTP 오류(400, 500 등)가 있으면 예외 발생
    response.raise_for_status()

    # 응답 바이트를 메모리에서 바로 zip으로 열기
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        # zip 안에서 CORPCODE.xml 파일을 찾아 읽기
        xml_data = zf.read("CORPCODE.xml")

    # XML 파싱
    root = ET.fromstring(xml_data)

    records = []
    for item in root.findall("list"):
        records.append({
            "corp_code":   item.findtext("corp_code", "").strip(),
            "corp_name":   item.findtext("corp_name", "").strip(),
            "stock_code":  item.findtext("stock_code", "").strip(),
            "modify_date": item.findtext("modify_date", "").strip(),
        })

    print(f"  → {len(records):,}개 기업 정보 수신 완료")
    return records


def _save_to_csv(records: list[dict]) -> None:
    """
    기업 목록을 CSV 파일로 저장한다.
    data/ 폴더가 없으면 자동으로 만든다.
    """
    DATA_DIR.mkdir(exist_ok=True)  # data/ 폴더 없으면 생성

    with open(CORP_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["corp_code", "corp_name", "stock_code", "modify_date"]
        )
        writer.writeheader()
        writer.writerows(records)

    print(f"  → {CORP_CSV}에 저장 완료")


def _load_from_csv() -> list[dict]:
    """
    저장된 CSV 파일에서 기업 목록을 읽어온다.
    반환값: [{corp_code, corp_name, stock_code, modify_date}, ...] 리스트
    """
    with open(CORP_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── 공개 함수 ────────────────────────────────────────────────────────────────

def load_corp_codes(force_download: bool = False) -> list[dict]:
    """
    기업 고유번호 목록을 반환한다.

    - CSV가 이미 있으면 파일에서 읽어 반환 (네트워크 요청 없음)
    - CSV가 없거나 force_download=True 이면 DART에서 새로 내려받아 저장 후 반환

    Args:
        force_download: True 이면 캐시 무시하고 강제로 다시 내려받음
    """
    if not force_download and CORP_CSV.exists():
        print(f"캐시 파일 사용: {CORP_CSV}")
        return _load_from_csv()

    # 캐시 없거나 강제 갱신인 경우 → API 호출
    records = _download_corp_codes()
    _save_to_csv(records)
    return records


def get_corp_code(corp_name: str, force_download: bool = False) -> str | None:
    """
    회사명을 넣으면 DART 고유번호(8자리 문자열)를 반환한다.

    - 정확히 일치하는 이름을 우선 반환
    - 일치하는 회사가 없으면 None 반환

    Args:
        corp_name:      찾을 회사명 (예: "삼성전자")
        force_download: True 이면 목록을 강제로 다시 내려받음
    """
    records = load_corp_codes(force_download=force_download)

    for row in records:
        if row["corp_name"] == corp_name:  # 정확히 일치하는 이름만
            return row["corp_code"]

    return None  # 일치하는 회사 없음


# ── 직접 실행 시 테스트 ──────────────────────────────────────────────────────

if __name__ == "__main__":
    test_company = "삼성전자"
    print(f"\n[테스트] '{test_company}' 고유번호 조회")

    code = get_corp_code(test_company)

    if code:
        print(f"결과: {test_company} → 고유번호 {code}")
    else:
        print(f"결과: '{test_company}'을 찾지 못했습니다.")
