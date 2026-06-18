"""
dart_report.py
DART OpenAPI의 문서 다운로드(document.xml)를 통해 사업보고서 원문 ZIP을 받아
내부 XML에서 특정 챕터의 텍스트를 파싱하는 모듈.

파싱 흐름:
  1) opendart.fss.or.kr/api/document.xml → rcept_no에 해당하는 ZIP 다운로드
  2) ZIP 안의 메인 XML(rcept_no.xml)을 BeautifulSoup으로 파싱
  3) <TITLE ATOC="Y"> 태그를 순회하며 목표 챕터를 찾음
  4) 해당 <SECTION-N> 부모 요소 전체 텍스트 추출 → 불필요 소제목 섹션 제거 후 10000자 제한
"""

import io
import re
import warnings
import zipfile
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# DART XML을 html.parser로 파싱할 때 발생하는 경고 억제
warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)

from config import DART_API_KEY

# ── 상수 ──────────────────────────────────────────────────────────────────────
OPENDART_BASE = 'https://opendart.fss.or.kr/api'

# reprt_code → 보고서명 핵심 키워드 매핑
REPRT_CODE_NAME: dict[str, str] = {
    '11011': '사업보고서',
    '11012': '반기보고서',
    '11013': '1분기보고서',
    '11014': '3분기보고서',
}

TEXT_MAX_LEN = 10000  # RAG 파이프라인 청킹 전 최대 글자 수

# 텍스트 추출 시 해당 줄부터 다음 소제목 전까지 제거할 키워드 목록
_SKIP_SUBTITLE_KEYWORDS = [
    '예측정보에 대한 주의사항',
    '본 자료는 미래에 대한',
]

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
}


# ── 내부 유틸 ─────────────────────────────────────────────────────────────────


def _download_report_zip(rcept_no: str) -> bytes | None:
    """
    DART document.xml API로 보고서 원문 ZIP을 다운로드한다.

    Returns:
        ZIP 바이너리 데이터, 실패 시 None
    """
    try:
        resp = requests.get(
            f'{OPENDART_BASE}/document.xml',
            params={'crtfc_key': DART_API_KEY, 'rcept_no': rcept_no},
            headers=_HEADERS,
            timeout=120,  # 큰 파일이므로 넉넉하게
        )
        resp.raise_for_status()
        # 오류 응답은 XML로 온다 (Content-Type: text/xml)
        if 'xml' in resp.headers.get('Content-Type', '') and resp.content[:2] != b'PK':
            warnings.warn(f'[경고] ZIP이 아닌 응답: {resp.text[:200]}')
            return None
        return resp.content
    except Exception as exc:
        warnings.warn(f'[경고] ZIP 다운로드 실패 (rcept_no={rcept_no}): {exc}')
        return None


def _load_main_xml(zip_bytes: bytes, rcept_no: str) -> BeautifulSoup | None:
    """
    ZIP에서 메인 XML 파일(rcept_no.xml)을 꺼내 BeautifulSoup으로 파싱한다.

    DART ZIP은 보통 세 개의 파일을 포함한다:
      - {rcept_no}.xml          → 본문 메인 XML
      - {rcept_no}_{숫자}.xml   → 첨부 문서(재무제표 등)
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(zip_bytes))
        # 메인 파일: 접두어가 rcept_no이고 언더스코어+숫자 접미어가 없는 파일
        main_name = f'{rcept_no}.xml'
        if main_name not in z.namelist():
            xml_files = [n for n in z.namelist() if n.endswith('.xml')]
            # 폴백 1: 언더스코어 없는 XML (일반 사업보고서 형태)
            candidates = [n for n in xml_files if '_' not in n]
            if candidates:
                main_name = candidates[0]
            # 폴백 2: XML이 하나뿐이면 그대로 사용 (감사보고서는 rcept_no_숫자.xml 한 개)
            elif len(xml_files) == 1:
                main_name = xml_files[0]
            else:
                warnings.warn(f'[경고] ZIP에서 메인 XML을 찾지 못했음: {z.namelist()}')
                return None

        raw = z.read(main_name).decode('utf-8', errors='replace')
        return BeautifulSoup(raw, 'html.parser')
    except Exception as exc:
        warnings.warn(f'[경고] ZIP 파싱 실패: {exc}')
        return None


def _remove_skip_sections(soup_element: BeautifulSoup) -> set[str]:
    """
    _SKIP_SUBTITLE_KEYWORDS 로 시작하는 <TITLE ATOC="Y"> 섹션을 soup에서 직접 제거한다.

    soup 변경 중 이터레이션 문제를 피하기 위해
    제거 대상 부모 요소를 먼저 수집한 뒤 일괄 decompose 한다.

    Returns:
        제거되지 않고 남은 소제목 텍스트 집합 (텍스트 레벨 2차 정리에 사용)
    """
    all_titles = soup_element.find_all('title', attrs={'atoc': 'Y'})

    remaining_subtitles: set[str] = set()
    to_decompose: list = []

    for title_tag in all_titles:
        title_text = title_tag.get_text(strip=True)
        if any(title_text.startswith(kw) for kw in _SKIP_SUBTITLE_KEYWORDS):
            # 소제목을 포함하는 부모 요소(섹션 전체) 제거 대상으로 표시
            to_decompose.append(title_tag.parent)
        else:
            remaining_subtitles.add(title_text)

    for parent in to_decompose:
        try:
            parent.decompose()
        except Exception:
            pass  # 이미 제거된 경우 무시

    return remaining_subtitles


def _clean_text(soup_element: BeautifulSoup, max_len: int = TEXT_MAX_LEN) -> str:
    """
    BeautifulSoup 요소에서 순수 텍스트를 추출하고,
    불필요한 소제목 섹션을 제거한 뒤 max_len 자로 자른다.

    처리 순서:
      1) soup 레벨: <TITLE ATOC="Y"> 기준으로 섹션 통째로 제거
      2) 텍스트 레벨: soup에서 못 잡은 줄(예: <TITLE> 밖 키워드)도 제거
      3) max_len 자로 자름
    """
    # ① soup 레벨에서 제거 대상 소제목 섹션 일괄 삭제
    remaining_subtitles = _remove_skip_sections(soup_element)

    # ② 텍스트 추출 및 빈 줄 제거
    raw = soup_element.get_text(separator='\n', strip=True)
    lines = [line for line in raw.splitlines() if line.strip()]

    # ③ 텍스트 레벨 2차 정리: <TITLE> 밖에 있는 키워드도 제거
    #    키워드 줄 발견 시 → 다음 소제목 줄이 나올 때까지 건너뜀
    cleaned_lines: list[str] = []
    skipping = False
    for line in lines:
        if any(line.startswith(kw) for kw in _SKIP_SUBTITLE_KEYWORDS):
            # 제거 대상 구간 시작
            skipping = True
            continue
        if skipping and line in remaining_subtitles:
            # 다음 소제목에 도달 → 제거 구간 종료
            skipping = False
        if not skipping:
            cleaned_lines.append(line)

    # ④ 최대 글자 수로 자름
    return '\n'.join(cleaned_lines)[:max_len]


def _find_section_by_title(soup: BeautifulSoup, target: str) -> BeautifulSoup | None:
    """
    target 키워드와 일치하는 <TITLE ATOC="Y"> 태그를 찾아
    해당 섹션 최상위 부모 요소(예: <SECTION-1>)를 반환한다.

    매칭 우선순위:
      1) 완전 일치
      2) target이 태그 텍스트에 포함
      3) 태그 텍스트가 target에 포함 (짧은 제목 대응)
    """
    all_titles = soup.find_all('title', attrs={'atoc': 'Y'})

    matched_tag = None

    # 1) 완전 일치
    for tag in all_titles:
        if tag.get_text(strip=True) == target:
            matched_tag = tag
            break

    # 2) target이 태그 텍스트에 포함
    if not matched_tag:
        for tag in all_titles:
            if target in tag.get_text(strip=True):
                matched_tag = tag
                break

    # 3) 태그 텍스트가 target에 포함
    if not matched_tag:
        for tag in all_titles:
            title_text = tag.get_text(strip=True)
            if title_text and title_text in target:
                matched_tag = tag
                break

    if not matched_tag:
        return None

    # 부모를 타고 올라가며 <SECTION-N> 또는 <SOURCE> 레벨 요소를 반환
    parent = matched_tag.parent
    return parent


def _search_regular_disclosures(
    corp_code: str,
    bgn_de: str,
    end_de: str,
) -> list[dict]:
    """
    DART list.json에 정기공시(pblntf_ty=A) 필터와 page_count=100을 적용해
    공시 목록을 가져온다.

    get_disclosures()는 pblntf_ty·page_count를 지원하지 않아 여기서 직접 호출.
    """
    try:
        resp = requests.get(
            f'{OPENDART_BASE}/list.json',
            params={
                'crtfc_key': DART_API_KEY,
                'corp_code': corp_code,
                'bgn_de': bgn_de,
                'end_de': end_de,
                'pblntf_ty': 'A',  # 정기공시만
                'page_count': 100,  # 한 번에 최대 조회
            },
            headers=_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') == '000' and 'list' in data:
            return data['list']
        # "013" = 조회 데이터 없음 — 정상 케이스
        if data.get('status') == '013':
            return []
        warnings.warn(
            f'[경고] list.json 오류: status={data.get("status")} message={data.get("message")}'
        )
    except Exception as exc:
        warnings.warn(f'[경고] 정기공시 목록 조회 실패: {exc}')
    return []


# ── 공개 함수 ─────────────────────────────────────────────────────────────────


def get_latest_report_rcept_no(
    corp_code: str,
    reprt_code: str = '11011',
) -> str | None:
    """
    해당 기업·보고서 코드의 최신 접수번호(rcept_no)를 반환한다.

    최근 2년 정기공시(pblntf_ty=A) 목록을 조회한 뒤
    보고서명 키워드(예: "사업보고서")로 필터링한다.

    Args:
        corp_code:  DART 기업 고유번호 (8자리)
        reprt_code: 보고서 코드 (기본값 "11011" = 사업보고서)

    Returns:
        최신 접수번호 문자열, 없으면 None
    """
    target_keyword = REPRT_CODE_NAME.get(reprt_code, '사업보고서')
    today = datetime.today()
    end_de = today.strftime('%Y%m%d')
    bgn_de = (today - timedelta(days=730)).strftime('%Y%m%d')  # 최대 2년 전까지 탐색

    print(
        f'[get_latest_report_rcept_no] corp_code={corp_code}, 보고서={target_keyword}, 기간={bgn_de}~{end_de}'
    )

    items = _search_regular_disclosures(corp_code, bgn_de, end_de)
    if not items:
        warnings.warn(f'[경고] 정기공시 목록이 비어있음 (corp_code={corp_code})')
        return None

    # 보고서명에 키워드가 포함된 항목만 필터
    # "[기재정정]사업보고서" 처럼 접두어가 붙은 경우도 포함
    matched = [item for item in items if target_keyword in item.get('report_nm', '')]

    if not matched:
        warnings.warn(
            f"[경고] '{target_keyword}' 보고서를 찾지 못했음 (corp_code={corp_code})"
        )
        return None

    # 접수일자 내림차순 → 가장 최신 선택
    matched.sort(key=lambda x: x.get('rcept_dt', ''), reverse=True)
    best = matched[0]
    print(
        f'[get_latest_report_rcept_no] 최신 접수번호={best["rcept_no"]} ({best.get("report_nm")})'
    )
    return best['rcept_no']


def get_report_text(
    rcept_no: str,
    sections: list[str] | None = None,
) -> dict[str, str]:
    """
    DART 사업보고서 원문에서 지정 챕터의 텍스트를 파싱해 반환한다.

    파싱 흐름:
      1) DART document.xml API로 보고서 ZIP 다운로드
      2) ZIP 안의 메인 XML(rcept_no.xml)을 파싱
      3) <TITLE ATOC="Y"> 태그에서 챕터명 매칭 → 부모 섹션 요소 텍스트 추출
      4) 불필요 소제목 섹션 제거 후 TEXT_MAX_LEN(10000자)으로 잘라냄

    Args:
        rcept_no: 접수번호 (예: "20260310002820")
        sections: 가져올 챕터명 목록
                  (기본값: ["사업의 내용", "이사의 경영진단"])

    Returns:
        {"사업의 내용": "...", "이사의 경영진단": "..."} 형태의 dict.
        파싱에 실패한 챕터는 빈 문자열("") 반환.
    """
    if sections is None:
        sections = ['사업의 내용', '이사의 경영진단']

    # 결과 초기값: 모든 챕터를 빈 문자열로 초기화
    result: dict[str, str] = {s: '' for s in sections}

    # ① ZIP 다운로드
    print(f'[get_report_text] ZIP 다운로드 중 (rcept_no={rcept_no}) ...')
    zip_bytes = _download_report_zip(rcept_no)
    if not zip_bytes:
        return result

    # ② 메인 XML 파싱
    soup = _load_main_xml(zip_bytes, rcept_no)
    if not soup:
        return result

    print(f'[get_report_text] XML 파싱 완료, 챕터 추출 시작')

    # ③ 각 챕터별 텍스트 추출
    for section in sections:
        section_elem = _find_section_by_title(soup, section)
        if not section_elem:
            warnings.warn(
                f"[경고] 챕터 '{section}'을 찾지 못했음 (rcept_no={rcept_no})"
            )
            continue

        text = _clean_text(section_elem)
        result[section] = text
        print(f"[get_report_text] '{section}' → {len(text)}자 추출")

    return result


# ── 직접 실행 시 테스트 ───────────────────────────────────────────────────────

if __name__ == '__main__':
    SAMSUNG = '00126380'  # 삼성전자 고유번호
    TARGET_SECTIONS = ['사업의 내용', '이사의 경영진단']

    print('=' * 64)
    print('  삼성전자 사업보고서(11011) 텍스트 파싱 테스트')
    print('=' * 64)

    # 1. 최신 사업보고서 접수번호 조회
    rcept_no = get_latest_report_rcept_no(SAMSUNG, reprt_code='11011')
    if not rcept_no:
        print('\n[오류] 접수번호를 가져오지 못했습니다. 종료합니다.')
    else:
        print(f'\n최신 사업보고서 접수번호: {rcept_no}\n')

        # 2. 지정 챕터 텍스트 파싱
        texts = get_report_text(rcept_no, sections=TARGET_SECTIONS)

        for section, text in texts.items():
            print(f'\n{"─" * 64}')
            print(f'  [{section}]')
            print('─' * 64)
            if text:
                # 테스트 출력은 500자만 — 전체는 최대 10000자
                print(text[:500])
                print(f'\n... (파싱된 텍스트 총 {len(text)}자 / 최대 {TEXT_MAX_LEN}자)')
            else:
                print('  (텍스트를 가져오지 못했습니다)')
