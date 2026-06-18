"""
dart_audit.py
DART 공시에서 감사보고서(pblntf_ty="F")를 조회하고 텍스트를 추출하는 모듈.

중소기업·비상장사는 재무 API에 데이터가 없어서 감사보고서 원문에서
텍스트를 추출해 RAG 파이프라인의 fallback 소스로 활용한다.

파싱 흐름:
  1) DART list.json → pblntf_ty="F" 로 감사보고서 목록 조회
  2) document.xml  → 접수번호로 ZIP 다운로드 (dart_report._download_report_zip 재사용)
  3) ZIP 안 메인 XML 파싱 (dart_report._load_main_xml 재사용)
  4) "감사의견" / "핵심감사사항" / "재무제표에 대한 감사" 섹션 우선 추출
  5) 못 찾으면 전체 텍스트 앞부분으로 대체 (RAG fallback)
"""

import csv
import io
import json
import re
import warnings
import zipfile
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from config import DART_API_KEY
from dart_report import (
    # ZIP 다운로드·XML 파싱 로직을 그대로 재사용 (중복 구현 방지)
    _download_report_zip,
    _load_main_xml,
    _clean_text,
    OPENDART_BASE,
    _HEADERS,
)

# ── 내부 ZIP 유틸 ────────────────────────────────────────────────────────────

def _load_all_xmls(zip_bytes: bytes, rcept_no: str) -> list[BeautifulSoup]:
    """
    ZIP 안의 모든 XML 파일을 파일명 오름차순으로 파싱해 BeautifulSoup 리스트로 반환한다.

    파싱에 실패한 파일은 warnings.warn 후 건너뜀.
    디코딩 로직은 _load_main_xml과 동일하게 utf-8 errors='replace' 사용.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except Exception as exc:
        warnings.warn(f'[경고] ZIP 열기 실패: {exc}')
        return []

    xml_files = sorted(n for n in z.namelist() if n.endswith('.xml'))
    if not xml_files:
        return []

    soups: list[BeautifulSoup] = []
    for name in xml_files:
        try:
            raw = z.read(name).decode('utf-8', errors='replace')
            soups.append(BeautifulSoup(raw, 'html.parser'))
        except Exception as exc:
            warnings.warn(f'[경고] XML 파싱 건너뜀 ({name}): {exc}')

    return soups


# ── 상수 ──────────────────────────────────────────────────────────────────────

KEY_AUDIT_MAX_LEN = 500  # 핵심감사사항 최대 저장 글자 수

DATA_DIR = Path("data")
AUDIT_CSV = DATA_DIR / "감사보고서.csv"

# 감사보고서 본문 시작 마커: 이 문자열 이전의 모든 내용(숫자·코드 등)을 제거
_AUDIT_START_MARKER = "독립된 감사인의 감사보고서"

# 감사의견 컨텍스트 앵커: 이 키워드 뒤에 나오는 의견 키워드를 우선 신뢰
# "우리의 의견으로는 ...", "우리의 의견은 ...", "감사의견은 ..." 패턴 대응
_OPINION_CONTEXT_ANCHOR = r"(?:의견으로는|우리의\s*의견|감사의견은?)"

# 심각도별 (label, 컨텍스트_re, 단순_re): 심각도 높은 순 배치
# - 컨텍스트_re: 앵커 뒤 200자 이내에서 탐색할 패턴 (정확도 높음)
# - 단순_re:     전문 전체에서 fallback으로 탐색할 패턴
# "공정하게 표시": IFRS 형식 감사보고서에서 "적정"을 대신해 쓰는 무한정적정의견 표현
_OPINION_KEYWORDS: list[tuple[str, str, str]] = [
    ("의견거절", r"의견\s*거절",      r"의견\s*거절"),
    ("부적정",   r"부적정",           r"부적정"),
    ("한정",     r"한정",             r"한정"),
    ("적정",     r"적정|공정하게\s*표시", r"적정|공정하게\s*표시"),
]


# ── 내부 유틸 ─────────────────────────────────────────────────────────────────

def _clean_audit_text(text: str) -> str:
    """
    전문 텍스트 앞의 목차·숫자·코드 쓰레기를 제거한다.

    "독립된 감사인의 감사보고서"가 목차에도 등장하고 본문 제목에도 등장하므로
    두 번째 등장 위치부터 반환한다 (두 번째 = 실제 본문 제목).
    두 번 이상 등장하지 않으면 첫 번째 위치로 fallback한다.
    마커 자체를 찾지 못하면 원본 텍스트를 그대로 반환한다.
    """
    first = text.find(_AUDIT_START_MARKER)
    if first == -1:
        # 마커 없음 — 원본 그대로 반환
        return text

    # 첫 번째 등장 이후에서 두 번째 등장 위치 탐색
    second = text.find(_AUDIT_START_MARKER, first + len(_AUDIT_START_MARKER))
    if second != -1:
        # 두 번째 등장 = 실제 본문 제목 (목차 이후)
        return text[second:]

    # 두 번째가 없으면 첫 번째 위치로 fallback
    return text[first:]


def _extract_audit_opinion(text: str) -> str:
    """
    감사보고서 텍스트에서 감사의견 키워드를 추출한다.
    심각도 높은 순(의견거절 → 부적정 → 한정 → 적정)으로 탐색한다.

    각 키워드마다 두 단계로 검색:
      1단계) 컨텍스트 패턴: 앵커 키워드("의견으로는", "우리의 의견", "감사의견") 뒤
             200자 이내에서 의견 키워드 탐색 — 정확도 높음
      2단계) 단순 전문 검색: 컨텍스트 없이 텍스트 전체에서 키워드 탐색 — fallback
    아무것도 찾지 못하면 "확인불가"를 반환한다.
    """
    for label, ctx_re, simple_re in _OPINION_KEYWORDS:
        # 1단계: 앵커 뒤 200자 이내에서 해당 키워드 탐색 (re.DOTALL로 줄바꿈 포함)
        if re.search(
            rf"{_OPINION_CONTEXT_ANCHOR}.{{0,200}}?{ctx_re}",
            text,
            re.DOTALL,
        ):
            return label
        # 2단계: 전문 전체에서 단순 키워드 검색
        if re.search(simple_re, text):
            return label
    return "확인불가"


# ── 공개 함수 ─────────────────────────────────────────────────────────────────

def get_audit_reports(corp_code: str) -> list[dict]:
    """
    DART list.json에서 감사보고서(pblntf_ty="F") 목록을 조회한다.

    수집 기간: 재작년 1월 1일 ~ 오늘
      - 재작년 계산: today.year - 2  (예: 2026 기준 → 2024-01-01)
      - 비상장·중소기업도 포함하기 위해 pblntf_ty="F" 전수 조회

    Args:
        corp_code: DART 기업 고유번호 (8자리)

    Returns:
        [{"rcept_no": ..., "report_nm": ..., "rcept_dt": ...}, ...] 형태의 목록
        조회 실패 또는 데이터 없으면 빈 리스트
    """
    today = datetime.today()

    # collect.py의 _calc_start_year와 동일한 로직:
    # 사업보고서가 익년 3월에 발행되므로 4개월을 빼서 기준 연도를 안정화한다.
    ref_year = today.year if today.month > 4 else today.year - 1
    start_year = ref_year - 2
    bgn_de = f"{start_year}0101"
    end_de = today.strftime("%Y%m%d")

    print(f"[get_audit_reports] corp_code={corp_code}, 기간={bgn_de}~{end_de}")

    try:
        resp = requests.get(
            f"{OPENDART_BASE}/list.json",
            params={
                "crtfc_key":  DART_API_KEY,
                "corp_code":  corp_code,
                "bgn_de":     bgn_de,   # [사용] 수집 시작일
                "end_de":     end_de,   # [사용] 수집 종료일
                "pblntf_ty":  "F",      # [사용] 감사보고서 공시유형 코드
                "page_count": 100,      # [사용] 한 번 조회로 최대한 많이 가져오기
            },
            headers=_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "000" and "list" in data:
            items = data["list"]
            result = [
                {
                    # [사용] rcept_no: ZIP 다운로드 키 + DB 식별자
                    "rcept_no":  item.get("rcept_no", ""),
                    # [사용] report_nm: 보고서명 (어떤 연도 감사보고서인지 팀원 확인용)
                    "report_nm": item.get("report_nm", ""),
                    # [사용] rcept_dt: 접수일자 (최신 여부 판단 + 정렬 기준)
                    "rcept_dt":  item.get("rcept_dt", ""),
                }
                for item in items
            ]
            print(f"[get_audit_reports] {len(result)}건 조회 완료")
            return result

        # "013" = 조회 데이터 없음 — 공시 없는 기업에서 정상적으로 발생
        if data.get("status") == "013":
            print(f"[get_audit_reports] 감사보고서 없음 (corp_code={corp_code})")
            return []

        warnings.warn(
            f"[경고] list.json 오류: status={data.get('status')} message={data.get('message')}"
        )

    except Exception as exc:
        warnings.warn(f"[경고] 감사보고서 목록 조회 실패 (corp_code={corp_code}): {exc}")

    return []


_SUPV_OPIN_MAP: list[tuple[int, str]] = [
    (0, "적정"),
    (1, "한정"),
    (2, "부적정"),
    (3, "의견거절"),
]


def _extract_opinion_from_summary(soup) -> str | None:
    """
    DART XML SUMMARY 섹션의 <EXTRACTION ACODE="SUPV_OPIN"> 코드로 감사의견을 반환한다.

    코드 형식: 12자리 비트 문자열 (예: "100000000000")
      - 0번 자리 = 1 → 적정
      - 1번 자리 = 1 → 한정
      - 2번 자리 = 1 → 부적정
      - 3번 자리 = 1 → 의견거절
    """
    tag = soup.find("extraction", attrs={"acode": "SUPV_OPIN"})
    if not tag:
        return None
    code = tag.get_text(strip=True)
    for idx, label in _SUPV_OPIN_MAP:
        if len(code) > idx and code[idx] == "1":
            return label
    return None


def _parse_amount(text: str) -> int | None:
    """
    재무제표 셀 텍스트를 int 금액으로 변환한다.

    처리하는 음수 표현:
      (123,456)  → -123456
      △123,456   → -123456
      -123,456   → -123456
    숫자로 변환 불가하면 None 반환.
    """
    text = text.strip().replace(",", "").replace(" ", "").replace("\xa0", "")
    if not text or text in ("-", "—", "△"):
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    elif text.startswith("△"):
        negative = True
        text = text[1:]
    elif text.startswith("-"):
        negative = True
        text = text[1:]
    try:
        val = int(text)
        return -val if negative else val
    except ValueError:
        return None


def _extract_financial_figures(soup) -> dict[str, int | None]:
    """
    soup의 모든 <table>을 순회하며 주요 재무수치를 추출한다.

    탐색 순서: 행의 첫 번째 셀에 키워드가 포함되면 두 번째 셀부터 숫자를 탐색.
    각 항목마다 첫 번째 매칭값만 저장하고, 파싱 실패 시 None을 유지한다.

    금액 유효성:
      - abs(amount) >= MIN_AMOUNT(1,000,000)인 값만 채택 (행 순번·소계 오파싱 방지)

    fallback 탐색:
      - 매출액 행이 없으면 영업수익으로 재탐색 (금융·신탁사 대응)
      - 자산총계 행이 없으면 자산합계로 재탐색
    """
    MIN_AMOUNT = 1_000_000  # 행 번호·소계 등 소수 오파싱 방지

    figures: dict[str, int | None] = {
        "revenue":            None,
        "operating_income":   None,
        "net_income":         None,
        # BS
        "total_assets":       None,
        "total_liabilities":  None,
        "total_equity":       None,
        # SCE
        "ending_capital":     None,
        # CF
        "operating_cash_flow": None,
        "investing_cash_flow": None,
        "financing_cash_flow": None,
    }

    # (탐색 키워드, figures 키): 당기순손실도 net_income으로 저장
    # CF는 "으로인한현금흐름" 장문 표현도 병행 탐색 (GAAP/IFRS 혼용 대응)
    keyword_to_key: list[tuple[str, str]] = [
        ("매출액",     "revenue"),
        ("영업이익",   "operating_income"),
        ("당기순이익", "net_income"),
        ("당기순손실", "net_income"),
        # BS
        ("자산총계",   "total_assets"),
        ("부채총계",   "total_liabilities"),
        ("자본총계",   "total_equity"),
        # SCE
        # 주의: 기말자본(ending_capital)은 여기서 키워드로 추출하지 않는다.
        #   자본변동표(SCE)의 기말 행은 "기말자본"이 아니라 "YYYY.MM.DD(당기말)"·
        #   "보고기간말"·"기말잔액" 등으로 표기되며, 기말 총자본은 행의 첫 금액 셀(자본금)이
        #   아니라 마지막 금액 셀(총계/합계 열)에 있다. 이 함수의 "첫 매칭 셀" 로직과 맞지 않으므로
        #   전용 함수 _extract_ending_capital()로 별도 처리한다.
        # CF
        ("영업활동현금흐름",        "operating_cash_flow"),
        ("영업활동으로인한현금흐름", "operating_cash_flow"),
        ("투자활동현금흐름",        "investing_cash_flow"),
        ("투자활동으로인한현금흐름", "investing_cash_flow"),
        ("재무활동현금흐름",        "financing_cash_flow"),
        ("재무활동으로인한현금흐름", "financing_cash_flow"),
    ]

    def _scan_tables(kw_map: list[tuple[str, str]]) -> None:
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                # DART XML은 표준 td/th 외에 커스텀 <te> 태그를 사용하는 중소기업 포맷도 존재
                cells = tr.find_all(["td", "th", "te"])
                if len(cells) < 2:
                    continue

                label = cells[0].get_text(strip=True).replace(" ", "").replace("\xa0", "")

                for keyword, result_key in kw_map:
                    if figures[result_key] is not None:
                        continue
                    if keyword not in label:
                        continue
                    for cell in cells[1:]:
                        amount = _parse_amount(cell.get_text())
                        if amount is not None and abs(amount) >= MIN_AMOUNT:
                            figures[result_key] = amount
                            break

            if all(v is not None for v in figures.values()):
                break

    try:
        _scan_tables(keyword_to_key)

        # 매출액 미발견 시 영업수익으로 재탐색 (금융·신탁사 fallback)
        if figures["revenue"] is None:
            _scan_tables([("영업수익", "revenue")])

        # 자산총계 미발견 시 자산합계로 재탐색
        if figures["total_assets"] is None:
            _scan_tables([("자산합계", "total_assets")])
    except Exception as exc:
        warnings.warn(f"[경고] 재무수치 파싱 중 오류: {exc}")

    return figures


def _extract_all_financial_tables(soup) -> dict:
    """
    감사보고서 XML의 모든 테이블을 순회하며 재무제표 4종을 섹션별로 추출한다.

    섹션 구분: 테이블 직전 200자 이내 텍스트에서 키워드 탐색
      "재무상태표" → "BS", "손익계산서" → "IS",
      "자본변동표" → "SCE", "현금흐름표" → "CF"
    같은 섹션 테이블이 여러 개면 계정과목을 누적(update)한다.
    """
    _SECTION_KW: list[tuple[str, str]] = [
        ("재무상태표", "BS"),
        ("손익계산서", "IS"),
        ("자본변동표", "SCE"),
        ("현금흐름표", "CF"),
    ]

    tables_data: dict[str, dict] = {}

    try:
        for table in soup.find_all("table"):
            # 직전 200자 이내 텍스트에서 섹션 키워드 탐색
            prev_parts: list[str] = []
            prev_len = 0
            for sibling in table.previous_siblings:
                sib_text = (
                    sibling.get_text(" ", strip=True)
                    if hasattr(sibling, "get_text")
                    else str(sibling).strip()
                )
                prev_parts.append(sib_text)
                prev_len += len(sib_text)
                if prev_len >= 200:
                    break
            prev_text = " ".join(reversed(prev_parts))[-200:]
            prev_text_nospace = prev_text.replace(" ", "")

            section_key: str | None = None
            for keyword, key in _SECTION_KW:
                if keyword in prev_text_nospace:
                    section_key = key
                    break

            if section_key is None:
                continue

            # 행 파싱: 첫 번째 셀 = 과목명, 두 번째 = 당기(current), 세 번째 = 전기(previous)
            section_entries: dict = {}
            for tr in table.find_all("tr"):
                cells = tr.find_all(["td", "th", "te"])
                if len(cells) < 2:
                    continue

                label = cells[0].get_text(strip=True).replace("\xa0", "").strip()

                # 과목명이 비어있거나 숫자만으로 구성된 행 건너뜀
                if not label or re.fullmatch(r"[\d,.()\-\s△—]+", label):
                    continue

                # 행 번호·소수점 숫자(abs < 1000)를 건너뛰고
                # 유효한 금액 셀 순서대로 current, previous 추출
                amounts: list[int] = []
                for cell in cells[1:]:
                    amount = _parse_amount(cell.get_text())
                    if amount is not None and abs(amount) >= 1000:
                        amounts.append(amount)
                    if len(amounts) >= 2:
                        break

                current  = amounts[0] if len(amounts) >= 1 else None
                previous = amounts[1] if len(amounts) >= 2 else None

                # current/previous 모두 None이면 저장하지 않음
                if current is None and previous is None:
                    continue

                section_entries[label] = {"current": current, "previous": previous}

            if section_entries:
                if section_key not in tables_data:
                    tables_data[section_key] = {}
                tables_data[section_key].update(section_entries)

    except Exception as exc:
        warnings.warn(f"[경고] 재무제표 전체 파싱 중 오류: {exc}")

    return tables_data


def _extract_key_audit_from_text(full_text: str) -> str:
    """
    전문 텍스트에서 '핵심감사사항' 절을 추출한다 (최대 KEY_AUDIT_MAX_LEN자).

    감사보고서 구조:
      핵심감사사항 (서술 절) → ... → 외부감사 실시내용 (회의록 테이블)
    회의록 테이블 안에도 "핵심감사사항"이 논의 주제로 등장하므로,
    "외부감사 실시내용" 절 이전 구간에서만 탐색한다.
    """
    # 회의록 구간 시작점을 찾아 그 이전까지만 탐색 범위로 제한
    supv_match = re.search(r"\n외부감사\s*실시내용", full_text)
    search_text = full_text[: supv_match.start()] if supv_match else full_text

    marker = "핵심감사사항"
    pos = search_text.find(marker)
    if pos == -1:
        return ""

    section = search_text[pos + len(marker):]

    # 다음 절 제목이 나올 때까지만
    next_section = re.search(
        r"\n(?:감사의견근거|계속기업|강조사항|기타사항|외부감사\s*실시내용|재무제표에\s*대한\s*경영진|이사의\s*책임|감사인의\s*책임)",
        section,
    )
    if next_section:
        section = section[: next_section.start()]

    return section.strip()[:KEY_AUDIT_MAX_LEN]


def get_audit_text(rcept_no: str) -> dict:
    """
    감사보고서 원문 ZIP을 다운로드해 핵심 정보만 추출한다.

    Returns:
        {
          "audit_opinion":      "적정"/"한정"/"부적정"/"의견거절"/"확인불가",
          "key_audit_matter":   감사인이 지목한 주요 리스크 (최대 500자),
          "revenue":            int 또는 None,
          "operating_income":   int 또는 None,
          "net_income":         int 또는 None,
          "total_assets":       int 또는 None,
          "total_liabilities":  int 또는 None,
          "total_equity":       int 또는 None,
          "ending_capital":     int 또는 None,
          "operating_cash_flow": int 또는 None,
          "investing_cash_flow": int 또는 None,
          "financing_cash_flow": int 또는 None,
          "financial_tables":   dict (재무제표 4종 전체 계정과목),
        }
    """
    result: dict = {
        "audit_opinion":      "확인불가",
        "key_audit_matter":   "",
        "revenue":            None,
        "operating_income":   None,
        "net_income":         None,
        # BS
        "total_assets":       None,
        "total_liabilities":  None,
        "total_equity":       None,
        # SCE
        "ending_capital":     None,
        # CF
        "operating_cash_flow": None,
        "investing_cash_flow": None,
        "financing_cash_flow": None,
    }

    print(f"[get_audit_text] ZIP 다운로드 중 (rcept_no={rcept_no}) ...")
    zip_bytes = _download_report_zip(rcept_no)
    if not zip_bytes:
        warnings.warn(f"[경고] ZIP 다운로드 실패 → 빈 결과 반환 (rcept_no={rcept_no})")
        return result

    soup = _load_main_xml(zip_bytes, rcept_no)
    if not soup:
        warnings.warn(f"[경고] XML 파싱 실패 → 빈 결과 반환 (rcept_no={rcept_no})")
        return result

    # 1순위: SUMMARY의 SUPV_OPIN 구조적 코드 (텍스트 파싱보다 신뢰도 높음)
    opinion = _extract_opinion_from_summary(soup)
    if opinion:
        result["audit_opinion"] = opinion
        print(f"[get_audit_text] 감사의견 (SUPV_OPIN) → {opinion}")
    else:
        # 2순위: 전문 텍스트에서 키워드 검색
        full_text = _clean_audit_text(_clean_text(soup, max_len=None))
        result["audit_opinion"] = _extract_audit_opinion(full_text)
        print(f"[get_audit_text] 감사의견 (텍스트 파싱) → {result['audit_opinion']}")

    # 핵심감사사항: 전문 텍스트에서 절 추출
    full_text = _clean_audit_text(_clean_text(soup, max_len=None))
    key_text = _extract_key_audit_from_text(full_text)
    if key_text:
        result["key_audit_matter"] = key_text
        print(f"[get_audit_text] 핵심감사사항 → {len(key_text)}자 추출")

    # 재무수치: 본문 테이블에서 주요 수치 추출 (fallback용 9개 항목)
    figures = _extract_financial_figures(soup)
    result.update(figures)

    # 기말자본: 기말 시점 총자본 = 재무상태표 자본총계와 정의상 동일하다.
    #   자본변동표(SCE) 표는 포맷이 매우 다양해(자본 구성요소 매트릭스 / 당기·전기 비교형 /
    #   별도·연결 혼재) 기말 총자본 열을 표 스캔으로 안정적으로 특정하기 어렵고,
    #   "기말잔액" 키워드는 현금흐름표(기말 현금잔액)와도 충돌한다.
    #   따라서 이미 신뢰성 있게 추출한 자본총계(total_equity)를 기말자본 값으로 사용한다.
    ending_capital = figures.get("total_equity")
    result["ending_capital"] = ending_capital
    print(
        f"[get_audit_text] 재무수치 → "
        f"revenue={figures['revenue']}, "
        f"operating_income={figures['operating_income']}, "
        f"net_income={figures['net_income']}, "
        f"total_assets={figures['total_assets']}, "
        f"total_liabilities={figures['total_liabilities']}, "
        f"total_equity={figures['total_equity']}, "
        f"ending_capital={ending_capital}, "
        f"operating_cash_flow={figures['operating_cash_flow']}, "
        f"investing_cash_flow={figures['investing_cash_flow']}, "
        f"financing_cash_flow={figures['financing_cash_flow']}"
    )

    # 재무제표 전체 계정과목: ZIP 내 모든 XML을 순회해 4종 테이블 합산
    # 이미 수집된 섹션의 계정과목은 덮어쓰지 않고 누적(update)한다.
    all_soups = _load_all_xmls(zip_bytes, rcept_no)
    merged_tables: dict = {}
    for s in all_soups:
        partial = _extract_all_financial_tables(s)
        for section, accounts in partial.items():
            if section not in merged_tables:
                merged_tables[section] = {}
            merged_tables[section].update(accounts)
    result["financial_tables"] = merged_tables

    return result


def save_audit_csv(records: list[dict], corp_name: str) -> None:
    """
    감사보고서 수집 결과를 단일 CSV 파일에 누적 저장한다.

    저장 경로: data/감사보고서.csv (모든 기업 통합, append)
    파일이 없으면 헤더 포함해서 새로 생성, 있으면 헤더 없이 이어 쓴다.
    financial_tables는 JSON 문자열로 단일 컬럼에 저장한다.

    Args:
        records:   [{"rcept_no": ..., "report_nm": ..., "rcept_dt": ...,
                     "audit_opinion": ..., "key_audit_matter": ..., ...}, ...]
        corp_name: 회사명 (corp_name 컬럼으로 기록)
    """
    DATA_DIR.mkdir(exist_ok=True)

    fieldnames = [
        "corp_name",           # 기업명
        "rcept_no",            # 원문 접근 키
        "dart_link",           # DART 원문 링크
        "report_nm",           # 보고서명
        "rcept_dt",            # 접수일자
        "audit_opinion",       # 감사의견 키워드 — 적정/한정/부적정/의견거절/확인불가
        "key_audit_matter",    # 감사인이 지목한 주요 리스크 (최대 500자)
        "revenue",             # 손익계산서 매출액 (원, int 또는 공백)
        "operating_income",    # 손익계산서 영업이익 (원, int 또는 공백)
        "net_income",          # 손익계산서 당기순이익(손실) (원, int 또는 공백)
        # BS
        "total_assets",        # 재무상태표 자산총계 (원, int 또는 공백)
        "total_liabilities",   # 재무상태표 부채총계 (원, int 또는 공백)
        "total_equity",        # 재무상태표 자본총계 (원, int 또는 공백)
        # SCE
        "ending_capital",      # 자본변동표 기말자본 (원, int 또는 공백)
        # CF
        "operating_cash_flow", # 현금흐름표 영업활동현금흐름 (원, int 또는 공백)
        "investing_cash_flow", # 현금흐름표 투자활동현금흐름 (원, int 또는 공백)
        "financing_cash_flow", # 현금흐름표 재무활동현금흐름 (원, int 또는 공백)
        "financial_tables",    # 재무제표 4종 전체 계정과목 (JSON 문자열)
    ]

    # 파일 존재 여부로 헤더 출력 결정
    write_header = not AUDIT_CSV.exists()

    try:
        with open(AUDIT_CSV, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            for rec in records:
                writer.writerow({
                    "corp_name":           corp_name,
                    "rcept_no":            rec.get("rcept_no", ""),
                    "dart_link":           f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rec.get('rcept_no', '')}" if rec.get("rcept_no") else "",
                    "report_nm":           rec.get("report_nm", ""),
                    "rcept_dt":            rec.get("rcept_dt", ""),
                    "audit_opinion":       rec.get("audit_opinion", "확인불가"),
                    "key_audit_matter":    rec.get("key_audit_matter", ""),
                    "revenue":             rec.get("revenue", ""),
                    "operating_income":    rec.get("operating_income", ""),
                    "net_income":          rec.get("net_income", ""),
                    # BS
                    "total_assets":        rec.get("total_assets", ""),
                    "total_liabilities":   rec.get("total_liabilities", ""),
                    "total_equity":        rec.get("total_equity", ""),
                    # SCE
                    "ending_capital":      rec.get("ending_capital", ""),
                    # CF
                    "operating_cash_flow": rec.get("operating_cash_flow", ""),
                    "investing_cash_flow": rec.get("investing_cash_flow", ""),
                    "financing_cash_flow": rec.get("financing_cash_flow", ""),
                    "financial_tables":    json.dumps(rec.get("financial_tables", {}), ensure_ascii=False),
                })
        print(f"[save_audit_csv] CSV 저장 완료 → {AUDIT_CSV} ({len(records)}건 추가)")
    except Exception as exc:
        warnings.warn(f"[경고] CSV 저장 실패: {exc}")


# ── 직접 실행 시 테스트 ───────────────────────────────────────────────────────

if __name__ == "__main__":
    from sqlalchemy.orm import Session
    from db import engine, init_db, save_audit

    # 에코이앤티: 비상장 중소기업, 감사보고서 3건 확인 (2024~2026)
    CORP_CODE = "01631694"
    CORP_NAME = "에코이앤티"

    print("=" * 64)
    print(f"  {CORP_NAME} 감사보고서 수집 테스트 (비상장 중소기업)")
    print("=" * 64)

    # 1. DB 초기화 (audit_reports 테이블 포함)
    init_db()

    # 2. 감사보고서 목록 조회
    print(f"\n[1단계] 감사보고서 목록 조회 (corp_code={CORP_CODE})")
    reports = get_audit_reports(CORP_CODE)
    print(f"  → {len(reports)}건 조회됨")
    for r in reports[:5]:  # 상위 5건만 출력
        print(f"    {r['rcept_dt']} | {r['report_nm']} | {r['rcept_no']}")

    if not reports:
        print("\n[오류] 감사보고서가 없습니다. 종료합니다.")
    else:
        # 3. 가장 최신 감사보고서 텍스트 추출 (접수일자 내림차순 후 첫 번째)
        reports.sort(key=lambda x: x.get("rcept_dt", ""), reverse=True)
        latest = reports[0]

        print(f"\n[2단계] 최신 감사보고서 텍스트 추출")
        print(f"  → {latest['rcept_dt']} | {latest['report_nm']} | {latest['rcept_no']}")

        text_data = get_audit_text(latest["rcept_no"])

        print(f"\n  [감사의견] ({len(text_data['감사의견'])}자)")
        print(f"  {text_data['감사의견'][:300]}")
        print(f"\n  [핵심감사사항] ({len(text_data['핵심감사사항'])}자)")
        print(f"  {text_data['핵심감사사항'][:300]}")
        print(f"\n  [전문 미리보기] (총 {len(text_data['전문'])}자 중 300자)")
        print(f"  {text_data['전문'][:300]}")

        # 4. DB 저장
        print(f"\n[3단계] DB 저장")
        with Session(engine) as session:
            save_audit(session, CORP_CODE, {**latest, **text_data})
            session.commit()
        print("  → DB 저장 완료")

        # 5. CSV 저장 (최신 3건)
        print(f"\n[4단계] CSV 저장 (최신 3건)")
        csv_records = []
        for report in reports[:3]:
            t = get_audit_text(report["rcept_no"])
            csv_records.append({**report, **t})
        save_audit_csv(csv_records, CORP_NAME)

        print("\n" + "=" * 64)
        print("  테스트 완료")
        print("=" * 64)
