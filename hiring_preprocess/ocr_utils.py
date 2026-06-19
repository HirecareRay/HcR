"""이미지 OCR, HTML 정제, LLM 입력 텍스트 조합."""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import requests
from PIL import Image


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR if (BASE_DIR / "data").exists() else BASE_DIR.parent
IMG_CACHE_DIR = PROJECT_DIR / "data/cache/images"

IMAGE_COLUMNS = [
    "detail_img", "image_url", "images",
    "image_urls", "detail_images", "img_urls",
]
TEXT_COLUMNS = [
    "detail_text", "content", "description", "detail",
    "detail_html", "html", "body", "posting_body", "summary_text",
]
SOURCE_URL_COLUMNS = ["source_url", "url", "posting_url", "recruit_url", "link"]

URL_RE = re.compile(r"https?://[^\s\"'<>]+")
IMG_SRC_RE = re.compile(r"<img[^>]+src=[\"']?([^\"' >]+)", re.I)
IMAGE_EXT_RE = re.compile(r"\.(png|jpg|jpeg|gif|webp)(?:\?|$)", re.I)
OCR_READER = None


def first_value(row: dict[str, Any], columns: list[str]) -> str:
    for column in columns:
        value = row.get(column)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def extract_html(html: str, base_url: str) -> tuple[str, list[str]]:
    images = [urljoin(base_url, src) for src in IMG_SRC_RE.findall(html)]
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip(), list(dict.fromkeys(images))


def find_image_urls(row: dict[str, Any]) -> list[str]:
    urls = []
    for column in IMAGE_COLUMNS:
        urls.extend(URL_RE.findall(str(row.get(column) or "")))
    return list(dict.fromkeys(urls))


def image_cache_path(url: str) -> Path:
    parsed = urlparse(url)
    readable = unquote(f"{parsed.netloc}{parsed.path}").strip("/")
    readable = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", readable).strip("_")
    suffix = Path(readable).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        suffix = ".png"
    stem = (Path(readable).stem[:110] or "image")
    digest = hashlib.sha256(url.encode()).hexdigest()[:10]
    return IMG_CACHE_DIR / f"{stem}_{digest}{suffix}"


def download_image(url: str) -> Path:
    IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = image_cache_path(url)
    if path.exists():
        return path
    response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    path.write_bytes(response.content)
    try:
        with Image.open(path) as image:
            image.verify()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def get_ocr_reader():
    global OCR_READER
    if OCR_READER is None:
        import easyocr
        OCR_READER = easyocr.Reader(["ko", "en"], gpu=False)
    return OCR_READER


def read_url_text(url: str) -> str:
    response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    reader = get_ocr_reader()
    if "image" in response.headers.get("content-type", "").lower() or IMAGE_EXT_RE.search(url):
        return "\n".join(reader.readtext(str(download_image(url)), detail=0))

    response.encoding = response.apparent_encoding
    html_text, html_images = extract_html(response.text, url)
    texts = [html_text] if html_text else []
    for image_url in html_images[:10]:
        try:
            texts.append("\n".join(reader.readtext(str(download_image(image_url)), detail=0)))
        except Exception:
            continue
    return "\n\n".join(filter(None, texts))


def ocr_image_urls(urls: list[str], cache: dict[str, Any]) -> tuple[str, bool]:
    texts = []
    for url in urls:
        if url not in cache or str(cache[url]).startswith("[OCR_ERROR]"):
            try:
                cache[url] = read_url_text(url)
            except Exception as error:
                cache[url] = f"[OCR_ERROR] {error}"
        value = str(cache.get(url, ""))
        if value and not value.startswith("[OCR_ERROR]"):
            texts.append(value)
    return "\n\n".join(texts), bool(texts)


def get_detail_text(row: dict[str, Any]) -> str:
    parts = []
    base_url = first_value(row, SOURCE_URL_COLUMNS)
    for column in TEXT_COLUMNS:
        text = str(row.get(column) or "").strip()
        if not text:
            continue
        if "<" in text and ">" in text:
            text, _ = extract_html(text, base_url)
        parts.append(text)
    return "\n\n".join(dict.fromkeys(parts))


def has_html_text(row: dict[str, Any]) -> bool:
    return any(
        re.search(r"<[a-zA-Z][^>]*>", str(row.get(column) or ""))
        for column in TEXT_COLUMNS
    )


def fetch_page_text(url: str) -> str:
    if not url:
        return ""
    try:
        response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        return extract_html(response.text, url)[0]
    except Exception:
        return ""


def cached_page_text(url: str, page_cache: dict[str, Any]) -> str:
    if not url:
        return ""
    if url not in page_cache:
        page_cache[url] = fetch_page_text(url)
    return str(page_cache.get(url, ""))


def is_low_quality_ocr(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 4]
    if len(text.strip()) < 80 or not lines:
        return False
    tokens = re.findall(r"\S+", text)
    mixed_tokens = sum(
        (
            any("\uac00" <= ch <= "\ud7a3" for ch in token)
            and any(ch.isascii() and ch.isalnum() for ch in token)
        )
        or any(ch in "#@_|=[]{}<>\\^`~" for ch in token)
        for token in tokens
    )
    mixed_ratio = mixed_tokens / max(len(tokens), 1)
    special_ratio = sum(
        not (ch.isalnum() or ch.isspace() or ch in ".,;:/()[]{}+-~%&@#'\"")
        for ch in text
    ) / len(text)
    bad_lines = 0
    for line in lines:
        readable = sum(
            ch.isalnum() or ch.isspace() or ch in ".,;:/()[]{}+-~%&@#'\""
            for ch in line
        )
        noise = sum(ch in "_|\\^`=<>{}" for ch in line)
        bad_lines += readable / len(line) < 0.60 or noise / len(line) > 0.10
    return (
        bad_lines / len(lines) >= 0.40
        or mixed_ratio >= 0.18
        or special_ratio >= 0.06
    )


def is_meaningful_ocr(text: str) -> bool:
    readable = sum(ch.isalnum() for ch in text) / max(len(text), 1)
    noise = sum(ch in "#@_|=[]{}<>\\^`~" for ch in text) / max(len(text), 1)
    terms = ("채용", "모집", "직무", "업무", "자격", "우대", "경력", "신입", "근무")
    return readable >= 0.35 and noise <= 0.03 and sum(term in text for term in terms) >= 3


def build_final_text(
    detail: str,
    ocr: str,
    low_quality: bool,
    has_images: bool,
    url: str,
    page_cache: dict[str, Any],
) -> tuple[str, str]:
    detail, ocr = detail.strip(), ocr.strip()
    if not has_images:
        if detail:
            return detail, "html_text"
        page_text = cached_page_text(url, page_cache)
        return (page_text, "page_fetched") if page_text else ("", "fallback")
    if ocr and not low_quality:
        return "\n\n".join(filter(None, [detail, ocr])), "image_ocr"
    if ocr and len(ocr) > max(len(detail) * 3, 500) and is_meaningful_ocr(ocr):
        return "\n\n".join(filter(None, [detail, ocr])), "low_quality_but_rich"
    if len(detail) >= 200:
        return detail, "ocr_failed_text_fallback" if not ocr else "ocr_excluded"
    page_text = cached_page_text(url, page_cache)
    if len(page_text) > len(detail):
        return "\n\n".join(filter(None, [page_text, ocr])), "page_fetched"
    return "\n\n".join(filter(None, [detail, ocr])), "fallback"
