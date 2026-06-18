"""추출 보조 로직(근거 인용의 본문 실재 검증) 단위 테스트.

네트워크/LLM 없이 순수 함수만 검증한다. 실행: python -m pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.extract import _grounded  # noqa: E402


def test_grounded_accepts_quote_present_in_text():
    text = "우리는 신뢰, 존중, 도전, 탐구를 핵심가치로 삼는다."
    assert _grounded("신뢰, 존중, 도전, 탐구", text)


def test_grounded_ignores_whitespace_differences():
    text = "핵심가치는 공감력  독창성   사명감 입니다."
    assert _grounded("공감력 독창성 사명감", text)


def test_grounded_rejects_fabricated_quote():
    # 본문에 없는(지어낸) 인용은 거부 → 할루시네이션 차단.
    text = "고품질 FF패널 자재로 안전한 시공을 합니다."
    assert not _grounded("창의적이고 도전적인 인재를 추구합니다", text)


def test_grounded_rejects_too_short_quote():
    assert not _grounded("도전", "도전 정신")
