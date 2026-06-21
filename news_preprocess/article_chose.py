from glob import glob
import json
import os
import re
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from tqdm import tqdm

# bge-m3, bge-small-ko-v1.5, ko-sroberta-multitask 등을 사용 가능
model = SentenceTransformer("BAAI/bge-m3")

def full_path(filename: str) -> str:
    return os.path.join(os.path.dirname(__file__), filename)

SIM_THRESHOLD = 0.85
BODY_SIM_THRESHOLD = 0.85
MODIFY_VALUE = 0.05
OUTPUT_DIR = full_path("chosen_articles")
os.makedirs(OUTPUT_DIR, exist_ok=True)

REMOVE_PATTERNS = [
    r"\[.*?재판매 및 DB 금지.*?\]",
    r"무단 전재.*",
    r"기사제보.*",
    r"저작권자.*",
    r"ⓒ.*",
    r"Copyright.*",
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    r"\[.*?\]",
    r"【.*?】",
    r"\(종합\)",
    r"\(속보\)",
    r"\(단독\)",
    r"속보",
    r"단독",
]

def clean_text(text):
    text = text.replace("\n", " ")
    for p in REMOVE_PATTERNS:
        text = re.sub(p, "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def news_chose(file_path: str):
    company_name = os.path.basename(file_path).split("_scraped_")[0]
        
    rows = []

    with open(file_path, encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))

    date_merge = {}
    for r in rows:
        date = r.get("date")
        if date:
            date = r["date"].split()[0]
            if date_merge.get(date):
                date_merge[date]["url"] += [r.get("url")]
                date_merge[date]["title"] += [r.get("title")]
                date_merge[date]["media"] += [r.get("media")]
                date_merge[date]["body"] += [clean_text(r.get("body"))]
                date_merge[date]["merge_body"] += "\n" + clean_text(r.get("body"))
            else:
                date_merge.update({date: {
                    "url": [r.get("url")], 
                    "title": [r.get("title")], 
                    "media": [r.get("media")], 
                    "body": [clean_text(r.get("body"))],
                    "merge_body": clean_text(r.get("body"))
                    }})
    
    # 로컬 모델
    selected = {}

    for date, data in tqdm(date_merge.items()):

        bodies = data.get("body")
        titles = data.get("title")
        n = len(bodies)
        
        # --------------------------------------------------
        # 1. 가장 본문이 긴 기사
        # --------------------------------------------------
        body_lengths = [len(x) for x in bodies]
        longest_idx = int(np.argmax(body_lengths))

        if n == 0:
            continue
        elif n == 1:
            selected[date] = {
                "longest": {
                    "idx": 0,
                    "url": data["url"][0],
                    "title": data["title"][0],
                    "media": data["media"][0],
                    "body": data["body"][0],
                    "length": body_lengths[0],
                },
                "n": n,
                # "similarity_matrix": sim,
            }
            continue
        elif n == 2:
            representative_idx = 0 if longest_idx == 1 else 1
            selected[date] = {
                "longest": {
                    "idx": longest_idx,
                    "url": data["url"][longest_idx],
                    "title": data["title"][longest_idx],
                    "media": data["media"][longest_idx],
                    "body": data["body"][longest_idx],
                    "length": body_lengths[longest_idx],
                },
                "representative": {
                    "idx": representative_idx,
                    "url": data["url"][representative_idx],
                    "title": data["title"][representative_idx],
                    "media": data["media"][representative_idx],
                    "body": data["body"][representative_idx],
                    "length": body_lengths[representative_idx],
                    "similar_count": 1,
                    "threshold_value": "title",
                    "final_threshold": SIM_THRESHOLD
                },
                "n": n,
                # "similarity_matrix": sim,
            }
            continue


        # --------------------------------------------------
        # 2. 임베딩 생성
        # --------------------------------------------------
        embeddings = model.encode(
            titles,
            normalize_embeddings=True,
            show_progress_bar=False
        )

        sim = cosine_similarity(embeddings)

        # 자기 자신 제외
        np.fill_diagonal(sim, 0)

        # threshold 이상 기사 개수
        counts = (sim >= SIM_THRESHOLD).sum(axis=1)
        modify_sim_threshold = SIM_THRESHOLD
        modify_body_sim_threshold = 1
        while sum(counts) == 0:
            modify_sim_threshold -= MODIFY_VALUE
            if modify_sim_threshold > 0:
                # threshold 이상 기사 개수
                counts = (sim >= modify_sim_threshold).sum(axis=1)
            else:
                modify_body_sim_threshold = BODY_SIM_THRESHOLD
                embeddings = model.encode(
                    bodies,
                    normalize_embeddings=True,
                    show_progress_bar=False
                )

                sim = cosine_similarity(embeddings)

                # 자기 자신 제외
                np.fill_diagonal(sim, 0)
                # threshold 이상 기사 개수
                counts = (sim >= modify_body_sim_threshold).sum(axis=1)
                while sum(counts) == 0:
                    modify_body_sim_threshold -= MODIFY_VALUE
                    # threshold 이상 기사 개수
                    counts = (sim >= modify_body_sim_threshold).sum(axis=1)
                    if modify_body_sim_threshold <= 0:
                        break
            if modify_sim_threshold <= 0:
                break
        
        if modify_body_sim_threshold == 1:
            threshold_value = "title"
            final_threshold = modify_sim_threshold
        else:
            threshold_value = "body"
            final_threshold = modify_body_sim_threshold

        # 동점이면 본문 긴 기사 선택
        # longest 기사 제외
        candidate_indices = [i for i in range(n) if i != longest_idx]

        representative_idx = max(
            candidate_indices,
            key=lambda i: (counts[i], body_lengths[i])
        )

        selected[date] = {
            "longest": {
                "idx": longest_idx,
                "url": data["url"][longest_idx],
                "title": data["title"][longest_idx],
                "media": data["media"][longest_idx],
                "body": data["body"][longest_idx],
                "length": body_lengths[longest_idx],
            },
            "representative": {
                "idx": representative_idx,
                "url": data["url"][representative_idx],
                "title": data["title"][representative_idx],
                "media": data["media"][representative_idx],
                "body": data["body"][representative_idx],
                "length": body_lengths[representative_idx],
                "similar_count": int(counts[representative_idx]),
                "threshold_value": threshold_value,
                "final_threshold": final_threshold
            },
            "n": n,
            # "similarity_matrix": sim,
        }

    with open(os.path.join(OUTPUT_DIR, f"{company_name}_selected_articles.json"), "w", encoding="utf-8") as f:
        json.dump(selected, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    jsonls = glob(f"{full_path('article')}\\*.jsonl")
    for jsonl in tqdm(jsonls):
        news_chose(jsonl)