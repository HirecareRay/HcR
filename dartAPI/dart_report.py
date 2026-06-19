"""
dart_report.py
DART OpenAPI 문서 다운로드(document.xml)로 보고서 원문 ZIP을 받아
내부 XML을 BeautifulSoup으로 파싱하는 저수준 유틸 모듈.

사업보고서 원문 텍스트 수집 기능은 제거됐다(refactor: 원문·소송·기업정보 수집 제거).
현재는 dart_audit.py가 감사보고서 원문에서 재무수치를 추출할 때
ZIP 다운로드·XML 파싱·텍스트 정리 로직을 재사용하기 위해 유지된다.

제공 유틸:
  _download_report_zip  document.xml ZIP 다운로드
  _load_main_xml        ZIP에서 메인 XML 파싱
  _clean_text           불필요 소제목 섹션 제거 + 길이 제한 텍스트 추출
"""

import io
import warnings
import zipfile

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# DART XML을 html.parser로 파싱할 때 발생하는 경고 억제
warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)

from config import DART_API_KEY

# ── 상수 ──────────────────────────────────────────────────────────────────────
OPENDART_BASE = 'https://opendart.fss.or.kr/api'

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
