#!/usr/bin/env python3
"""HCR 채용공고 전처리 v2: raw 파일 -> OCR/LLM 정규화 JSONL."""

import hashlib
import html as html_lib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import pandas as pd
import requests
from PIL import Image
from tqdm import tqdm

BASE_DIR = Path(__file__).resolve().parent

def load_dotenv_file(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

load_dotenv_file(BASE_DIR / ".env")
load_dotenv_file(BASE_DIR.parent / ".env")

RAW_DIR = BASE_DIR / "data/raw"
OUT_PATH = BASE_DIR / "data/processed/jobs_normalized_v2.jsonl"
OCR_CACHE_PATH = BASE_DIR / "data/cache/ocr_cache.json"
LLM_CACHE_PATH = BASE_DIR / "data/cache/llm_cache_v2.json"
PAGE_CACHE_PATH = BASE_DIR / "data/cache/page_cache.json"
IMG_CACHE_DIR = BASE_DIR / "data/cache/images"

OPENAI_URL = "https://api.openai.com/v1/responses"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
MAX_LLM_CHARS = int(os.getenv("MAX_LLM_CHARS", "30000"))
USE_LLM = os.getenv("USE_LLM", "1") != "0"
SCHEMA_VERSION = "hcr_job_schema_v2_7"

IMAGE_COLUMNS = ["detail_img", "image_url", "images"]
TEXT_COLUMNS = ["detail_text", "content", "description", "detail"]
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
IMG_SRC_RE = re.compile(r"<img[^>]+src=[\"']?([^\"' >]+)", re.I)
IMAGE_EXT_RE = re.compile(r"\.(png|jpg|jpeg|gif|webp)(\?|$)", re.I)
OCR_READER = None


def load_raw_files(raw_dir=RAW_DIR):
    frames = []
    for path in sorted(raw_dir.glob("*")):
        if path.suffix == ".csv":
            df = pd.read_csv(path, dtype=str).fillna("")
        elif path.suffix == ".tsv":
            df = pd.read_csv(path, sep="\t", dtype=str).fillna("")
        elif path.suffix == ".jsonl":
            df = pd.read_json(path, lines=True).fillna("")
        elif path.suffix == ".json":
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            df = pd.json_normalize(data).fillna("")
        else:
            continue
        df = df.drop(columns=["company_info"], errors="ignore")
        df["_source_file"] = str(path)
        df["_source_row"] = df.index.astype(int) + 2
        frames.append(df)
    return frames


def load_schema():
    with (BASE_DIR / "job_schema_v2.json").open(encoding="utf-8") as f:
        return json.load(f)


def load_cache(path):
    if path.exists():
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def find_image_urls(row):
    urls = []
    for col in IMAGE_COLUMNS:
        if col in row and row[col]:
            urls.extend(URL_RE.findall(str(row[col])))
    return list(dict.fromkeys(urls))


def image_cache_path(url):
    parsed = urlparse(url)
    readable = unquote(f"{parsed.netloc}{parsed.path}").strip("/")
    if not Path(readable).suffix and parsed.query:
        readable = f"{readable}_{unquote(parsed.query)}"
    readable = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", readable).strip("_")
    readable = readable[:140] or "image"
    suffix = Path(readable).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        suffix = ".png"
        readable = readable.rstrip(".") + suffix
    digest = hashlib.sha256(url.encode()).hexdigest()[:8]
    stem = Path(readable).stem
    return IMG_CACHE_DIR / f"{stem}_{digest}{suffix}"


def download_image(url):
    IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    img_path = image_cache_path(url)
    if img_path.exists():
        return img_path
    response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    img_path.write_bytes(response.content)
    with Image.open(img_path) as img:
        img.verify()
    return img_path


def extract_html_text_and_images(html, base_url):
    image_urls = [urljoin(base_url, src) for src in IMG_SRC_RE.findall(html)]
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text, list(dict.fromkeys(image_urls))


def get_ocr_reader():
    global OCR_READER
    if OCR_READER is None:
        import easyocr
        OCR_READER = easyocr.Reader(["ko", "en"], gpu=False)
    return OCR_READER


def read_url_text(url):
    reader = get_ocr_reader()
    response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "image" in content_type or IMAGE_EXT_RE.search(url):
        img_path = download_image(url)
        return "\n".join(reader.readtext(str(img_path), detail=0))
    response.encoding = response.apparent_encoding
    html_text, html_images = extract_html_text_and_images(response.text, url)
    texts = [html_text] if html_text else []
    for image_url in html_images[:5]:
        try:
            img_path = download_image(image_url)
            texts.append("\n".join(reader.readtext(str(img_path), detail=0)))
        except Exception:
            pass
    return "\n\n".join(t for t in texts if t)


def ocr_image_urls(urls, cache):
    if not urls:
        return "", False
    texts = []
    for url in urls:
        if url not in cache or str(cache[url]).startswith("[OCR_ERROR]"):
            try:
                cache[url] = read_url_text(url)
            except Exception as e:
                cache[url] = f"[OCR_ERROR] {e}"
        if cache[url] and not str(cache[url]).startswith("[OCR_ERROR]"):
            texts.append(cache[url])
    return "\n\n".join(texts), bool(texts)


def fetch_source_page_text(source_url):
    if not source_url:
        return ""
    try:
        response = requests.get(
            source_url, timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        )
        response.raise_for_status()
        text, _ = extract_html_text_and_images(response.text, source_url)
        return text
    except Exception:
        return ""


def get_detail_text(row):
    parts = []
    for col in TEXT_COLUMNS:
        if col in row and row[col]:
            parts.append(str(row[col]))
    return "\n\n".join(dict.fromkeys(parts))


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot(final_text, ocr_text):
    return {
        "raw_ocr_excerpt": ocr_text[:1200],
        "final_text_excerpt": final_text[:1200],
        "llm_input_hash": text_hash(final_text),
    }


def is_low_quality_ocr(text):
    if not text or len(text.strip()) < 80:
        return False
    lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 4]
    if not lines:
        return False
    bad = 0
    for line in lines:
        allowed = sum(
            1 for ch in line
            if '\uAC00' <= ch <= '\uD7A3'
            or ch.isalnum()
            or ch.isspace()
            or ch in ".,;:/()[]{}+-~%&@#'\"<>*•"
        )
        allowed_ratio = allowed / max(len(line), 1)
        noise = sum(1 for ch in line if ch in "_|\\^`")
        noise_ratio = noise / max(len(line), 1)
        if allowed_ratio < 0.60 or noise_ratio > 0.10:
            bad += 1
    return bad / max(len(lines), 1) >= 0.40


def is_meaningful_ocr(text):
    if not text:
        return False
    korean = sum(1 for ch in text if '\uAC00' <= ch <= '\uD7A3')
    alnum = sum(1 for ch in text if ch.isalnum())
    readable_ratio = (korean + alnum) / max(len(text), 1)
    noise = sum(1 for ch in text if ch in "#@_|=[]{}<>\\^`~")
    noise_ratio = noise / max(len(text), 1)
    job_terms = [
        "채용", "모집", "직무", "담당", "업무", "자격", "요건", "우대",
        "경력", "신입", "근무", "고용", "급여", "전형", "접수", "지원",
        "학력", "전공", "제출", "서류", "복리", "후생", "인턴", "정규직",
    ]
    term_hits = sum(1 for term in job_terms if term in text)
    return readable_ratio >= 0.35 and noise_ratio <= 0.03 and term_hits >= 3


def build_final_text(detail_text, ocr_text, ocr_low_quality, source_url="", page_cache=None):
    detail = detail_text.strip()
    ocr = ocr_text.strip()

    if not ocr_low_quality:
        return "\n\n".join(filter(None, [detail, ocr])), "normal"

    if ocr and len(ocr) > len(detail) * 3 and is_meaningful_ocr(ocr):
        return "\n\n".join(filter(None, [detail, ocr])), "low_quality_but_rich"

    if len(detail) >= 200:
        return detail, "ocr_excluded"

    if source_url and page_cache is not None:
        if source_url not in page_cache:
            page_cache[source_url] = fetch_source_page_text(source_url)
        page_text = page_cache.get(source_url, "")
        if len(page_text) > len(detail):
            return "\n\n".join(filter(None, [page_text, ocr])), "page_fetched"

    return "\n\n".join(filter(None, [detail, ocr])), "fallback"


def empty_track():
    return {"requirements": [], "preferred": [], "responsibilities": [], "documents": []}


def normalize_headcount(raw):
    raw = str(raw or "").strip()
    if not raw or re.fullmatch(r"0+명?", raw):
        return None
    match = re.search(r"\d+", raw)
    return int(match.group()) if match else None


def extract_deadline_fallback(text):
    matches = re.findall(r"(20\d{2})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})", text)
    if not matches:
        return ""
    year, month, day = matches[-1]
    return f"{year}-{int(month):02d}-{int(day):02d}"


def add_warning(record, message):
    log = record.setdefault("preprocess_log", {})
    warnings = log.setdefault("parse_warnings", [])
    if message and message not in warnings:
        warnings.append(message)


def tracks_are_identical(tracks):
    newcomer = tracks.get("newcomer", {})
    experienced = tracks.get("experienced", {})
    return (
        newcomer.get("requirements") == experienced.get("requirements")
        and newcomer.get("preferred") == experienced.get("preferred")
        and newcomer.get("responsibilities") == experienced.get("responsibilities")
        and any(newcomer.get(key) for key in ("requirements", "preferred", "responsibilities"))
    )


def infer_source_site(source_url, source_file=""):
    text = f"{source_url} {source_file}".lower()
    if "catch.co.kr" in text or "catch" in text:
        return "catch"
    if "jobkorea.co.kr" in text or "jobkorea" in text:
        return "jobkorea"
    if "incruit" in text or "incru.it" in text:
        return "incruit"
    return ""


def refresh_runtime_fields(record, row, final_text, ocr_text, ocr_used, llm_used=True, llm_error=""):
    source_url = row.get("source_url", row.get("url", ""))
    record["source_url"] = source_url
    record["raw_meta"] = {
        "source_file": row.get("_source_file", ""),
        "source_row": int(row.get("_source_row", 0) or 0),
        "source_url": source_url,
        "ocr_used": ocr_used,
        "llm_used": llm_used,
        "llm_error": llm_error,
    }
    log = record.setdefault("preprocess_log", {})
    log["original_text_snapshot"] = snapshot(final_text, ocr_text)
    return record


def postprocess_record(record, row, final_text):
    source_url = row.get("source_url", row.get("url", ""))
    record["source_site"] = record.get("source_site") or infer_source_site(source_url, row.get("_source_file", ""))

    work = record.setdefault("work_conditions", {})
    if not work.get("deadline"):
        work["deadline"] = extract_deadline_fallback(final_text)
    if not work.get("recruit_url"):
        work["recruit_url"] = source_url

    for job in record.get("jobs", []):
        job["headcount_value"] = normalize_headcount(job.get("headcount", ""))
        if tracks_are_identical(job.get("tracks", {})):
            add_warning(
                record,
                f"{job.get('job_name', '')}: newcomer/experienced requirements 동일 — 원공고 구분 없음 가능성",
            )
    return record


def empty_record(row, ocr_used, final_text="", ocr_text="", llm_error=""):
    source_url = row.get("source_url", row.get("url", ""))
    return {
        "company_name": row.get("company_name", row.get("company", "")),
        "posting_title": row.get("posting_title", row.get("title", "")),
        "source_site": row.get("source_site", ""),
        "source_url": source_url,
        "common": {"education": "", "major": "", "preferred": [], "documents": []},
        "jobs": [
            {
                "job_name": "",
                "headcount": "",
                "headcount_value": None,
                "education": "",
                "major": "",
                "locations": [],
                "responsibilities": [],
                "preferred_common": [],
                "tracks": {"newcomer": empty_track(), "experienced": empty_track()},
            }
        ],
        "process": [],
        "work_conditions": {
            "employment_type": "", "work_type": "", "salary": "",
            "benefits": [], "deadline": "", "recruit_url": source_url,
        },
        "raw_meta": {
            "source_file": row.get("_source_file", ""),
            "source_row": int(row.get("_source_row", 0) or 0),
            "source_url": source_url,
            "ocr_used": ocr_used,
            "llm_used": False,
            "llm_error": llm_error,
        },
        "preprocess_log": {
            "dropped_fields": [],
            "low_confidence": [],
            "parse_warnings": [llm_error] if llm_error else [],
            "original_text_snapshot": snapshot(final_text, ocr_text),
        },
    }


JOB_SCHEMA = load_schema()


def llm_cache_key(final_text, row):
    source = row.get("source_url", row.get("url", ""))
    title = row.get("posting_title", row.get("title", ""))
    raw = "\n".join([SCHEMA_VERSION, OPENAI_MODEL, source, title, final_text])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def openai_headers():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 없습니다.")
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def extract_response_text(data):
    if data.get("output_text"):
        return data["output_text"]
    for item in data.get("output", []):
        for content in item.get("content", []):
            if "text" in content:
                return content["text"]
    raise RuntimeError(f"OpenAI 응답에서 텍스트를 찾지 못했습니다: {data}")


def call_openai_normalizer(final_text, ocr_text, row, ocr_used):
    input_hash = text_hash(final_text)
    prompt = f"""
아래 채용공고 텍스트는 OCR 결과라 오타가 있을 수 있습니다.
문맥상 명확한 OCR 오타는 자연스럽게 보정하되, 확실하지 않은 내용은 추측하지 마세요.
비어 있거나 찾을 수 없는 값은 빈 문자열 또는 빈 배열로 두세요.

구조화 규칙:
- common: 공고 전체 공통 학력, 전공, 우대사항, 제출서류입니다.
- jobs[]: 반드시 모집분야/직무 단위로 분리하세요.
- 모집분야/직무명이 여러 개면 반드시 jobs[]를 여러 개로 만드세요.
- 서로 다른 모집분야의 근무지, 전공, 자격요건, 담당업무를 하나의 job에 합치지 마세요.
- 표에서 같은 행 또는 같은 모집분야 블록에 있는 정보만 같은 job에 넣으세요.
- jobs[].responsibilities: 신입/경력 모두에 해당하는 직무 공통 업무입니다.
- jobs[].preferred_common: 해당 직무 신입/경력 모두에 해당하는 우대사항입니다.
- tracks.newcomer: 신입 전용 자격요건, 우대사항, 업무, 제출서류입니다.
- tracks.experienced: 경력 전용 자격요건, 우대사항, 업무, 제출서류입니다.
- 우대사항이 불분명한 경우 더 공통적인 상위 레벨에 배치하세요.
- 같은 우대사항을 preferred_common과 track.preferred에 중복해서 넣지 마세요.
- "0명", "00명"은 미기재가 아니라 원문 표현이므로 headcount에 그대로 보존하세요.
- deadline은 timezone을 추정하지 말고 YYYY-MM-DD 문자열로 보존하세요.
- 이메일 주소는 recruit_url에 넣지 마세요. recruit_url은 실제 지원/공고 URL만 넣고, 이메일 접수만 있으면 빈 문자열로 두세요.
- 이메일, 전화번호처럼 스키마에 전용 필드가 없는 값은 preprocess_log.parse_warnings에 기록하세요.
- 자격요건, 필수조건, 경력조건, 학력요건, 자격증 보유 조건은 responsibilities에 넣지 말고 requirements 또는 education에 넣으세요.
- responsibilities에는 실제 수행 업무만 넣으세요. 예: 유지보수, 설계, 개발, 정비, 시운전, 문서 작성 등.
- preferred에는 우대사항만 넣으세요. 필수조건과 우대사항을 섞지 마세요.
- 특정 직무명 주변에만 등장하는 우대사항은 common.preferred에 넣지 말고 해당 jobs[].preferred_common 또는 track.preferred에 넣으세요.
- common.preferred에는 공고 전체 모든 직무에 적용된다고 명확한 우대사항만 넣으세요.
- 특정 직무, 연구소, 발전소 유형, 트랙 주변에 등장한 우대사항은 common.preferred에 넣지 마세요.
- education에는 학력만 넣으세요. 예: 고졸, 전문학사, 학사, 석사, 박사, 무관.
- 산업기사, 기사, 면허, 자격증, 어학점수, 경력연수는 education이 아니라 requirements에 넣으세요.
- major에는 전공만 넣으세요. 계측제어/전기/전자/컴퓨터/정보통신처럼 전공 또는 계열로 볼 수 있는 값만 넣으세요.

preprocess_log 작성 규칙:
- dropped_fields: 원문에는 있었지만 스키마에 넣지 못했거나 버린 값과 이유입니다.
- low_confidence: 분류가 애매하거나 신뢰도가 낮은 판단입니다. confidence는 0.0~1.0 숫자입니다.
- parse_warnings: 날짜, 위치, 인원, 제출서류, OCR 오타 등 파싱 이슈입니다.
- original_text_snapshot.llm_input_hash는 반드시 아래 해시를 그대로 넣으세요: {input_hash}
- original_text_snapshot.raw_ocr_excerpt와 final_text_excerpt는 제공 텍스트 앞부분 일부를 넣으세요.

기본 메타:
- company_name 후보: {row.get("company_name", row.get("company", ""))}
- posting_title 후보: {row.get("posting_title", row.get("title", ""))}
- source_site 후보: {row.get("source_site", "")}
- source_url 후보: {row.get("source_url", row.get("url", ""))}
- source_file: {row.get("_source_file", "")}
- source_row: {row.get("_source_row", 0)}
- ocr_used: {ocr_used}

채용공고 텍스트:
{final_text[:MAX_LLM_CHARS]}
""".strip()
    body = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": "너는 채용공고 OCR 텍스트를 DB 적재용 JSON으로 정규화하는 데이터 전처리기다."},
            {"role": "user", "content": prompt},
        ],
        "text": {"format": {"type": "json_schema", "name": "hcr_job_posting_v2", "schema": JOB_SCHEMA, "strict": True}},
    }
    last_error = None
    for attempt in range(3):
        try:
            response = requests.post(OPENAI_URL, headers=openai_headers(), json=body, timeout=90)
            if "insufficient_quota" in response.text:
                raise SystemExit("OpenAI quota가 부족합니다. 결제/크레딧/프로젝트 한도를 확인한 뒤 다시 실행하세요.")
            if "invalid_api_key" in response.text:
                raise SystemExit("OPENAI_API_KEY가 올바르지 않습니다. .env의 키를 확인하세요.")
            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = response.text
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            record = json.loads(extract_response_text(response.json()))
            refresh_runtime_fields(record, row, final_text, ocr_text, ocr_used, llm_used=True)
            return postprocess_record(record, row, final_text)
        except Exception as e:
            last_error = str(e)
            time.sleep(2 ** attempt)
    raise RuntimeError(last_error)


def normalize_with_llm(final_text, ocr_text, row, ocr_used, llm_cache):
    if not USE_LLM:
        record = empty_record(row, ocr_used, final_text, ocr_text, llm_error="USE_LLM=0")
        refresh_runtime_fields(record, row, final_text, ocr_text, ocr_used, llm_used=False, llm_error="USE_LLM=0")
        return postprocess_record(record, row, final_text)
    key = llm_cache_key(final_text, row)
    if key in llm_cache and not str(llm_cache[key]).startswith("[LLM_ERROR]"):
        record = json.loads(json.dumps(llm_cache[key], ensure_ascii=False))
        refresh_runtime_fields(record, row, final_text, ocr_text, ocr_used, llm_used=True)
        return postprocess_record(record, row, final_text)
    try:
        record = call_openai_normalizer(final_text, ocr_text, row, ocr_used)
        llm_cache[key] = record
        return record
    except Exception as e:
        llm_cache[key] = f"[LLM_ERROR] {e}"
        record = empty_record(row, ocr_used, final_text, ocr_text, llm_error=str(e))
        refresh_runtime_fields(record, row, final_text, ocr_text, ocr_used, llm_used=False, llm_error=str(e))
        return postprocess_record(record, row, final_text)


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ocr_cache = load_cache(OCR_CACHE_PATH)
    llm_cache = load_cache(LLM_CACHE_PATH)
    page_cache = load_cache(PAGE_CACHE_PATH)
    frames = load_raw_files()
    if USE_LLM and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY가 없습니다. .env 또는 터미널 환경변수에 넣어주세요.")
    if not frames:
        print(f"No input files found in: {RAW_DIR}")
        return
    total = 0
    warning_map = {
        "ocr_excluded": "OCR 품질이 낮아 LLM 입력에서는 제외하고 raw_ocr_excerpt에만 보존함",
        "low_quality_but_rich": "OCR 품질이 낮음 - detail보다 훨씬 길어 LLM 입력에 포함, 수동 검수 권장",
        "page_fetched": "OCR/detail 품질 낮아 source_url 직접 크롤링하여 보완함",
        "fallback": "OCR·detail·페이지 모두 빈약 - 데이터 품질 낮음, 수동 검수 필요",
    }
    try:
        with OUT_PATH.open("w", encoding="utf-8") as out:
            for df in frames:
                for _, row in tqdm(df.iterrows(), total=len(df)):
                    row = row.to_dict()
                    detail_text = get_detail_text(row)
                    image_urls = find_image_urls(row)
                    ocr_text, ocr_used = ocr_image_urls(image_urls, ocr_cache)
                    ocr_low_quality = is_low_quality_ocr(ocr_text)
                    source_url = row.get("source_url", row.get("url", ""))
                    final_text, text_source = build_final_text(
                        detail_text, ocr_text, ocr_low_quality,
                        source_url=source_url, page_cache=page_cache,
                    )
                    record = normalize_with_llm(final_text, ocr_text, row, ocr_used, llm_cache)
                    record.setdefault("raw_meta", {})["ocr_low_quality"] = ocr_low_quality
                    record.setdefault("raw_meta", {})["text_source"] = text_source
                    if text_source in warning_map:
                        add_warning(record, warning_map[text_source])
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    total += 1
                    if total % 5 == 0:
                        save_cache(ocr_cache, OCR_CACHE_PATH)
                        save_cache(llm_cache, LLM_CACHE_PATH)
    finally:
        save_cache(ocr_cache, OCR_CACHE_PATH)
        save_cache(llm_cache, LLM_CACHE_PATH)
        save_cache(page_cache, PAGE_CACHE_PATH)
    print(f"saved: {OUT_PATH}")
    print(f"saved: {OCR_CACHE_PATH}")
    print(f"saved: {LLM_CACHE_PATH}")
    print(f"saved: {PAGE_CACHE_PATH}")


if __name__ == "__main__":
    main()
    