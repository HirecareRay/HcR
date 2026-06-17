"""
corp_code.py
DART 기업 고유번호 목록을 내려받아 저장하고,
회사명 → 고유번호 변환 기능을 제공하는 모듈
"""

import io
import csv
import re
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


def _normalize(name: str) -> str:
    """
    비교용 정규화: 법인 표기·공백·특수문자를 제거한다.
    예) '효성 ITX(주)' → '효성ITX', '주식회사 삼성전자' → '삼성전자'
    """
    # 법인 표기 제거 (앞뒤 위치 모두 처리)
    name = re.sub(r'주식회사|㈜|\(주\)|\(유\)|유한회사', '', name)
    # 공백·괄호·점 제거
    name = re.sub(r'[\s\(\)\.\,\&]', '', name)
    return name.strip()


def _pick_best(candidates: list[dict]) -> str | None:
    """
    후보 목록에서 최적 corp_code를 반환한다.
    상장사(stock_code 있는 것)를 비상장사보다 우선하고,
    동순위면 modify_date가 최신인 것을 선택한다.
    """
    if not candidates:
        return None
    listed   = [r for r in candidates if r.get("stock_code")]
    pool     = listed if listed else candidates
    best     = max(pool, key=lambda r: r.get("modify_date", ""))
    return best["corp_code"]


def get_corp_code(corp_name: str, force_download: bool = False) -> str | None:
    """
    회사명을 넣으면 DART 고유번호(8자리 문자열)를 반환한다.

    3단계 매칭 순서:
      1단계) 정확히 일치
      2단계) 정규화(법인표기·공백 제거) 후 일치 — '효성 ITX' ↔ '효성ITX'
      3단계) 정규화 후 포함 관계 — '한겨레신문' ↔ '한겨레신문사'

    동명 법인이 여럿이면 상장사 우선, 동순위면 modify_date 최신 순으로 선택한다.

    Args:
        corp_name:      찾을 회사명 (예: "삼성전자")
        force_download: True 이면 목록을 강제로 다시 내려받음
    """
    records = load_corp_codes(force_download=force_download)

    # 1단계: 정확히 일치
    exact = [r for r in records if r["corp_name"] == corp_name]
    if exact:
        return _pick_best(exact)

    # 2단계: 정규화 후 일치 (공백·법인표기 차이 흡수)
    norm_input = _normalize(corp_name)
    norm_exact = [r for r in records if _normalize(r["corp_name"]) == norm_input]
    if norm_exact:
        return _pick_best(norm_exact)

    # 3단계: 정규화 후 포함 관계 — 입력이 DART 회사명 안에 있는 경우만 허용
    # (반대 방향 "DART명 in 입력"은 단글자·단어 오매칭을 유발하므로 제외)
    # 후보가 여럿이면 길이가 가장 짧은 것(= 가장 구체적 매칭) 중에서 _pick_best
    if len(norm_input) >= 4:
        candidates = [r for r in records if norm_input in _normalize(r["corp_name"])]
        if candidates:
            min_len   = min(len(_normalize(r["corp_name"])) for r in candidates)
            shortest  = [r for r in candidates if len(_normalize(r["corp_name"])) == min_len]
            return _pick_best(shortest)

    return None  # 일치하는 회사 없음


def fuzzy_match_companies(
    input_file: str | Path = "companies.txt",
    output_file: str | Path = DATA_DIR / "matched_result.csv",
    auto_threshold: float = 90.0,
    manual_threshold: float = 60.0,
    top_k: int = 3,
) -> None:
    """
    companies.txt의 회사명을 DART corp_codes.csv와 퍼지 매칭하여 matched_result.csv로 저장한다.

    - 유사도 90% 이상 → 자동 매칭
    - 유사도 60~90% → 후보 top_k개 출력 후 수동 선택
    - 유사도 60% 미만 → 매칭 실패

    Args:
        input_file:       회사명 목록 파일 (한 줄에 하나)
        output_file:      결과 CSV 저장 경로
        auto_threshold:   자동 매칭 최소 유사도 (기본 90%)
        manual_threshold: 수동 선택 최소 유사도 (기본 60%)
        top_k:            수동 선택 후보 수 (기본 3)
    """
    try:
        from rapidfuzz import fuzz, process as rfprocess
    except ImportError as exc:
        raise ImportError("rapidfuzz가 설치되지 않았습니다. `pip install rapidfuzz`를 실행하세요.") from exc

    input_path = Path(input_file)
    output_path = Path(output_file)

    companies = [
        line.strip()
        for line in input_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"입력 회사 수: {len(companies):,}개")

    records = load_corp_codes()
    dart_names = [r["corp_name"] for r in records]
    dart_norm  = [_normalize(n) for n in dart_names]
    print(f"DART 기업 수: {len(dart_names):,}개\n")

    results: list[dict] = []

    for idx, company in enumerate(companies, 1):
        print(f"[{idx}/{len(companies)}] '{company}' 매칭 중...")

        # 1단계: 정확 매칭
        exact = [r for r in records if r["corp_name"] == company]
        if exact:
            best_code = _pick_best(exact)
            best_rec  = next(r for r in exact if r["corp_code"] == best_code)
            results.append({
                "corp_name_input": company,
                "corp_name_dart":  best_rec["corp_name"],
                "corp_code":       best_rec["corp_code"],
                "similarity":      100.0,
                "match_type":      "자동",
            })
            print(f"  → 정확 매칭: {best_rec['corp_name']} ({best_rec['corp_code']})\n")
            continue

        # 2단계: 정규화된 이름으로 퍼지 매칭
        norm_input = _normalize(company)
        # extract returns list of (matched_str, score, index)
        candidates = rfprocess.extract(
            norm_input,
            dart_norm,
            scorer=fuzz.token_sort_ratio,
            limit=top_k,
        )

        if not candidates:
            results.append({
                "corp_name_input": company,
                "corp_name_dart":  "",
                "corp_code":       "",
                "similarity":      0.0,
                "match_type":      "실패",
            })
            print("  → 후보 없음 (매칭 실패)\n")
            continue

        best_score = candidates[0][1]

        if best_score >= auto_threshold:
            best_rec = records[candidates[0][2]]
            results.append({
                "corp_name_input": company,
                "corp_name_dart":  best_rec["corp_name"],
                "corp_code":       best_rec["corp_code"],
                "similarity":      round(best_score, 1),
                "match_type":      "자동",
            })
            print(f"  → 자동 매칭 ({best_score:.1f}%): {best_rec['corp_name']} ({best_rec['corp_code']})\n")

        elif best_score >= manual_threshold:
            print(f"  유사도 {best_score:.1f}% — 후보를 선택하세요:")
            for i, (_, score, rec_idx) in enumerate(candidates, 1):
                rec   = records[rec_idx]
                stock = f" [{rec['stock_code']}]" if rec.get("stock_code") else ""
                print(f"    {i}. {rec['corp_name']}{stock}  ({score:.1f}%)")
            print("    0. 매칭 실패로 기록")

            while True:
                try:
                    choice = int(input(f"  선택 (0~{len(candidates)}): ").strip())
                    if 0 <= choice <= len(candidates):
                        break
                    print(f"  0~{len(candidates)} 사이의 숫자를 입력하세요.")
                except ValueError:
                    print("  숫자를 입력하세요.")

            if choice == 0:
                results.append({
                    "corp_name_input": company,
                    "corp_name_dart":  "",
                    "corp_code":       "",
                    "similarity":      round(best_score, 1),
                    "match_type":      "실패",
                })
                print("  → 매칭 실패로 기록\n")
            else:
                _, chosen_score, chosen_idx = candidates[choice - 1]
                chosen_rec = records[chosen_idx]
                results.append({
                    "corp_name_input": company,
                    "corp_name_dart":  chosen_rec["corp_name"],
                    "corp_code":       chosen_rec["corp_code"],
                    "similarity":      round(chosen_score, 1),
                    "match_type":      "수동",
                })
                print(f"  → 수동 매칭: {chosen_rec['corp_name']} ({chosen_rec['corp_code']})\n")

        else:
            results.append({
                "corp_name_input": company,
                "corp_name_dart":  "",
                "corp_code":       "",
                "similarity":      round(best_score, 1),
                "match_type":      "실패",
            })
            print(f"  → 매칭 실패 (최고 유사도 {best_score:.1f}%)\n")

    # CSV 저장
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["corp_name_input", "corp_name_dart", "corp_code", "similarity", "match_type"],
        )
        writer.writeheader()
        writer.writerows(results)

    auto_cnt   = sum(1 for r in results if r["match_type"] == "자동")
    manual_cnt = sum(1 for r in results if r["match_type"] == "수동")
    fail_cnt   = sum(1 for r in results if r["match_type"] == "실패")
    print(f"완료: 자동 {auto_cnt}개 | 수동 {manual_cnt}개 | 실패 {fail_cnt}개")
    print(f"결과 저장: {output_path}")


# ── 직접 실행 시 테스트 ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "fuzzy":
        # 사용법: python corp_code.py fuzzy [input_file] [output_file]
        kwargs: dict = {}
        if len(sys.argv) > 2:
            kwargs["input_file"] = sys.argv[2]
        if len(sys.argv) > 3:
            kwargs["output_file"] = sys.argv[3]
        fuzzy_match_companies(**kwargs)
    else:
        test_company = "삼성전자"
        print(f"\n[테스트] '{test_company}' 고유번호 조회")
        code = get_corp_code(test_company)
        if code:
            print(f"결과: {test_company} → 고유번호 {code}")
        else:
            print(f"결과: '{test_company}'을 찾지 못했습니다.")
