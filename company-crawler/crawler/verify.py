"""홈페이지가 '정말 해당 기업의 것'인지 검증한다(요청 4번).

휴리스틱(이름 매칭)으로 1차 판단하고, 애매하면 LLM으로 보수적으로 확인한다.
"""
import json
import re

from .config import MODEL, get_client
from .fetch import Page

# 기업명에서 제거할 법인/형태 접미어
_CORP_TOKENS = (
    "주식회사", "유한회사", "(주)", "（주）", "㈜", "(유)",
    "co.,ltd.", "co.,ltd", "co.ltd", "corp.", "corp", "inc.", "inc",
    "ltd.", "ltd", "limited", "company", "group", "그룹",
)


def normalize_name(name: str) -> str:
    """비교용 정규화: 소문자화, 법인 접미어/공백/기호 제거."""
    text = (name or "").lower()
    for token in _CORP_TOKENS:
        text = text.replace(token, "")
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def name_matches(company_name: str, *signals: str) -> bool:
    """정규화된 기업명이 신호 텍스트(제목/og:site_name 등)와 일치하는지.

    보수적 규칙:
    - 한글/정식 명칭(충분히 김): 부분 문자열 포함이면 일치로 본다.
    - 짧은 영문 약어(예: 'FF', 'kt'): 다른 단어의 일부에 우연히 포함되는
      오탐('FF'⊂'FF패널')을 막기 위해 '정확히 같을 때만' 일치로 인정한다.
    """
    target = normalize_name(company_name)
    if len(target) < 2:  # 너무 짧으면 오탐 위험 → 매칭 비신뢰
        return False

    risky = target.isascii() and len(target) <= 3  # 짧은 영문 약어
    for signal in signals:
        if not signal:
            continue
        norm = normalize_name(signal)
        if not norm:
            continue
        if risky:
            if norm == target:  # 약어는 정확 일치만 신뢰
                return True
        elif target in norm:
            return True
    return False


def mentions_company(company_name: str, page: Page) -> bool:
    """참고(폴백) 페이지가 '대상 기업의 것'인지 보수적으로 확인한다.

    잡사이트 검색이 동명이 아닌 다른 회사의 채용공고/기업정보를 반환하는
    오매칭(예: '러쉬에잇' 검색에 '러쉬코리아' 공고)을 막는다. 본문은 사이드바·
    추천 영역에 타사명이 섞여 오탐이 크므로, 권위 있는 제목/사이트명만 신뢰한다.
    """
    if not page.ok:
        return False
    return name_matches(company_name, page.title, page.og_site_name)


def verify_homepage(company_name: str, page: Page) -> tuple[bool, str]:
    """(검증여부, 사유)를 반환한다. 보수적으로 판단한다.

    짧은 약어(예: 'FF')가 제품명 일부('FF패널')에 우연히 포함되는 오탐을 막기 위해,
    충분히 긴 이름만 단순 매칭으로 신뢰하고 나머지는 LLM으로 최종 판별한다.
    """
    if not page.ok:
        return False, f"페이지 비정상: {page.error}"

    target = normalize_name(company_name)
    # og:site_name은 사이트 운영 주체명이라 권위 있음 → 길이 2 이상이면 신뢰
    if len(target) >= 2 and name_matches(company_name, page.og_site_name):
        return True, "사이트명(og:site_name)에서 기업명 일치"

    # 제목 매칭은 3글자 이상일 때만 신뢰(짧은 약어의 부분일치 오탐 방지)
    if len(target) >= 3 and name_matches(company_name, page.title):
        return True, "제목에서 기업명 일치"

    # 그 외(짧은 이름·약한 매칭)는 LLM으로 회사명인지 제품 키워드인지 판별
    return _verify_with_llm(company_name, page)


def _verify_with_llm(company_name: str, page: Page) -> tuple[bool, str]:
    snippet = f"제목: {page.title}\n사이트명: {page.og_site_name}\n본문: {page.text[:3000]}"
    system = (
        "너는 주어진 웹페이지가 '입력 기업명' 바로 그 회사의 '공식 홈페이지'가 "
        "맞는지 보수적으로 판별한다. 애매하면 무조건 거부한다.\n"
        "규칙:\n"
        "1. 제공된 텍스트만 근거로 하고 외부 지식·추측은 금지한다.\n"
        "2. 먼저 '이 사이트를 운영하는 회사(운영 주체)'가 누구인지 텍스트에서 "
        "찾는다(회사소개·푸터·카피라이트·대표/연혁/사업자정보 등). 그 운영 주체가 "
        "입력 기업명과 '동일'할 때만 belongs=true.\n"
        "3. 입력 기업명이 운영사가 아니라 제품·브랜드·키워드로만 등장하면 "
        "belongs=false(예: 'FF'를 찾는데 페이지 운영사는 'FF패널'을 파는 다른 회사).\n"
        "4. 운영사가 입력 기업의 모회사·자회사·계열사 등 '다른 법인'이면 belongs=false "
        "(예: 'NAVER IS'를 찾는데 페이지는 모회사 '네이버 주식회사').\n"
        "5. 짧은 약어가 우연히 다른 단어의 일부로 들어간 경우 belongs=false.\n"
        "6. 운영 주체를 텍스트에서 분명히 확인할 수 없으면 belongs=false.\n"
        "7. 조금이라도 확신이 없으면 belongs=false.\n"
        'JSON만 출력: {"operator": "텍스트에서 파악한 운영 주체(모르면 null)", '
        '"belongs": true|false, "reason": "한 문장"}'
    )
    user = f"입력 기업명: {company_name}\n\n웹페이지 내용:\n{snippet}"
    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        belongs = bool(data.get("belongs"))
        reason = str(data.get("reason", ""))[:200]
        operator = str(data.get("operator") or "").strip()
        if operator and operator.lower() not in ("null", "none"):
            reason = f"{reason} (운영사: {operator})"[:240]
        return belongs, (reason or ("LLM 확인" if belongs else "LLM 불일치"))
    except Exception as e:
        return False, f"검증 중 오류: {e}"
