"""인재상/사업내용 추출. 강한 제약으로 할루시네이션을 제거한다(요청 1·2번)."""
import json
import re

from .config import MODEL, TEMPERATURE, get_client

SYSTEM_PROMPT = (
    "너는 기업 채용 정보를 추출하는 도구다. 아래 규칙을 절대적으로 지킨다.\n"
    "1. 오직 '제공된 웹페이지 텍스트'에 명시적으로 적힌 내용만 사용한다.\n"
    "2. 사전 지식, 추론, 일반 상식, 추측을 절대 사용하지 않는다.\n"
    "3. 텍스트에 근거가 없으면 반드시 null을 반환한다. 그럴듯하게 지어내지 않는다.\n"
    "4. 'talent_values(인재상)'은 '기업이 원하는 인재의 가치관·태도·역량·핵심가치'를 "
    "서술한 내용일 때만 추출한다. 인재상/핵심가치/추구하는 인재 섹션이 아니더라도, "
    "사람에게 바라는 자질·가치(예: '공감력, 독창성, 사명감', '창의적·능동적 인재')를 "
    "명시한 문구라면 인재상으로 본다.\n"
    "5. 다음은 사람의 자질이 아니라 다른 주제이므로 talent_values로 절대 반환하지 "
    "않는다(반드시 null): 제품·서비스·기술 소개, 사업 내용·실적, 보도자료, "
    "블로그·인사이트·뉴스 기사 본문, 특정 직무의 업무 설명, 마케팅·홍보 문구. "
    "판단 기준: '이 문장이 사람에게 바라는 가치/태도/역량을 말하는가?'가 아니면 null.\n"
    "5-1. 인재상은 사람의 성품·태도·역량 단어(예: 창의, 도전, 신뢰, 책임감, 열정, "
    "공감력, 독창성, 능동적, 협업, 전문성)가 핵심이어야 한다. 자재·제품·공법·시공·"
    "기술·매출·고객 등 '사업 활동/대상'을 서술하는 문장은 회사 슬로건이어도 "
    "인재상이 아니다(null). 예: '고품질 자재로 안전한 시공을 하는 기업' → null.\n"
    "6. 애매하면(사업 설명인지 인재상인지 불확실하면) null로 둔다.\n"
    "7. 회사명이 텍스트와 무관해 보이면 모든 값을 null로 둔다.\n"
    "8. talent_values는 인재상의 핵심 가치·태도·역량만 '간결하게' 정리한다. "
    "메뉴·네비게이션·버튼·제목 조각, 끊긴 단어 나열, 인재상과 무관한 꼬리 문구는 "
    "포함하지 않는다(예: '… 핵심가치 내재화 기술확보 · 혁신지향 지속성장 · 비전달성' "
    "같은 메뉴 텍스트 제외).\n"
    "9. 설명·마크다운 없이 순수 JSON만 출력한다."
)

USER_TEMPLATE = (
    "기업명: {company_name}\n\n"
    "웹페이지 텍스트(이 텍스트 밖의 정보는 사용 금지):\n{text}\n\n"
    "아래 JSON 형식으로만 답하라. 근거 없으면 해당 값은 null:\n"
    '{{\n'
    '  "talent_values": "사람에게 바라는 가치·태도·역량을 서술한 문구일 때만 요약. '
    '사업·제품 소개/품질 슬로건/블로그/뉴스면 null",\n'
    '  "talent_quote": "위 판단의 근거가 된 문장을 텍스트에서 \'그대로\' 인용(변형·요약 '
    '금지). 없으면 null",\n'
    '  "is_explicit_talent": "인재상/핵심가치/추구하는 인재 등 \'사람의 가치·태도·역량\'을 '
    '명시한 내용이면 true, 사업/제품/품질/실적 서술이면 false (불리언)",\n'
    '  "business_description": "주요 사업 내용 2~3문장(근거 있을 때만, 없으면 null)"\n'
    '}}'
)


def _empty(value) -> bool:
    if value is None:
        return True
    text = str(value).strip().lower()
    return text in ("", "null", "none", "n/a", "없음", "정보 없음")


def _grounded(quote: str, text: str) -> bool:
    """근거 인용의 핵심 토큰 다수가 본문에 실제로 존재하는지 확인.

    '40자 그대로-일치'는 따옴표·공백·기호 차이에 취약해 정답까지 죽였다.
    토큰 겹침 비율(60% 이상)로 완화해, 모델이 지어낸 인용만 거른다.
    """
    tokens = [t for t in re.findall(r"[0-9A-Za-z가-힣]+", quote) if len(t) >= 2]
    if len(tokens) < 2:  # 토큰이 너무 적으면 우연 일치 위험 → 비신뢰
        return False
    body = re.sub(r"\s+", "", text)
    hits = sum(1 for t in tokens if t in body)
    return hits / len(tokens) >= 0.6


CLASSIFY_SYSTEM = (
    "너는 주어진 문구가 '기업이 직원·인재에게 바라는 구체적 가치·태도·역량(인재상)'을 "
    "제시하는지 판별한다.\n"
    "규칙:\n"
    "1. 사람(인재·직원·구성원)의 성품·태도·역량 단어를 인재상으로 제시하면 talent "
    "(예: '창의적·능동적 사고', '신뢰·도전·열정', '신사고'). 다른 문장에 사업·고객 "
    "이야기가 섞여 있어도, 사람의 가치·태도·역량이 분명히 제시되면 talent로 본다.\n"
    "2. 문구의 핵심이 고객 약속·사업·제품·매출·전략·비전이면 business "
    "(예: '고객과의 Partnership을 최우선으로'). '인재·육성'이라는 단어가 있더라도 "
    "사람의 구체적 성품·태도·역량이 아니라 사업 목표·전략을 설명하는 것이면 business "
    "(예: 'AI 인재를 육성해 전사 시너지를 창출').\n"
    "3. 정말 어느 쪽인지 판단이 안 되면 unclear.\n"
    'JSON만 출력: {"category": "talent|business|unclear", "reason": "한 문장"}'
)


def _is_about_talent(company_name: str, talent_values: str, quote: str) -> bool:
    """추출된 문구가 '인재상'인지(사업/고객 서술이 아닌지) 한 번 더 판별한다.

    is_explicit_talent 자기판단이 슬로건을 통과시키는 누수를 막기 위한 집중 패스.
    분류기 호출이 실패하면(일시 오류) 앞 단계 검증을 신뢰해 값을 유지한다.
    """
    user = (f"기업명: {company_name}\n문구: {talent_values}\n"
            f"근거 인용: {quote}")
    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": CLASSIFY_SYSTEM},
                      {"role": "user", "content": user}],
            temperature=TEMPERATURE,
            response_format={"type": "json_object"},
        )
        category = str(json.loads(resp.choices[0].message.content)
                       .get("category", "")).strip().lower()
        return category == "talent"
    except Exception:
        return True  # 분류 불가 시 앞 단계 결과 유지(false positive보다 누락 회피)


def extract_talent(company_name: str, text: str) -> dict:
    """텍스트에서 인재상/사업내용을 보수적으로 추출한다. 없으면 None.

    인재상은 (1) 모델이 '사람에 대한 가치'라고 명시(is_explicit_talent)하고,
    (2) 근거 인용(talent_quote)이 실제 본문에 존재할 때만 인정한다.
    하나라도 충족 못 하면 None으로 떨어뜨려, 틀린 값보다 빈 값을 택한다.

    Raises:
        Exception: LLM 호출/파싱 실패 시(호출부에서 오류로 구분 처리).
    """
    user = USER_TEMPLATE.format(company_name=company_name, text=text)
    resp = get_client().chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": user}],
        temperature=TEMPERATURE,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content)

    talent = None if _empty(data.get("talent_values")) \
        else str(data["talent_values"]).strip()
    quote = None if _empty(data.get("talent_quote")) \
        else str(data["talent_quote"]).strip()

    # 보수적 게이트: 사람에 대한 가치 명시 + 근거 인용의 본문 실재 +
    # 인재상 vs 사업/고객 서술 분류(슬로건 누수 차단)
    if talent:
        if not bool(data.get("is_explicit_talent")):
            talent = None
        elif not quote or not _grounded(quote, text):
            talent = None
        elif not _is_about_talent(company_name, talent, quote):
            talent = None

    return {
        "talent_values": talent,
        "talent_context": quote if talent else None,
        "business_description": None if _empty(data.get("business_description"))
        else str(data["business_description"]).strip(),
    }
