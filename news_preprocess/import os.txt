import os
import json
import glob
from re import search
from concurrent.futures import ProcessPoolExecutor, as_completed
import re
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import chromadb
from tqdm import tqdm

# ==========================================
# [설정] 환경 변수 및 하이퍼파라미터
# ==========================================
DATA_DIR = "./data"          
CHROMA_DB_DIR = "./chroma_db" 
FINAL_JSON_PATH = "./data/refined_news_output.json" 
SIMILARITY_THRESHOLD = 0.99
BATCH_SIZE = 50000              
NUM_WORKERS = os.cpu_count()  

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

def extract_date_from_text(title, body):
    match = search(r"(\d{4})[-./년\s](\d{1,2})[-./월\s](\d{1,2})", title + " " + body)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return "2024-01-01" 

def process_single_company(file_path):
    """
    [Worker Process] 한 기업의 뉴스 중 '날짜가 같은 데이터'를 1개로 통합합니다.
    동일 날짜 내에서 유사도가 높은 뉴스들의 본문을 합치고 메타데이터를 결합합니다.
    """
    base_name = os.path.basename(file_path)
    company_name = os.path.splitext(base_name)[0] # 튜플 버그 방지 (문자열만 추출)
    
    news_list = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    tmp = json.loads(line)
                    if tmp.get("body"):
                        news_list.append(json.loads(line))
    except Exception as e:
        return company_name, f"실패: {str(e)}", []

    if not news_list:
        return company_name, "데이터 없음", []

    # --------------------------------------------------
    # [변경 사항] 1단계: 모든 기사의 날짜를 먼저 추출하고, 날짜별로 그룹핑
    # --------------------------------------------------
    date_groups = {} # 예: {"2026-06-04": [기사1, 기사2, ...]}
    
    for n in news_list:
        pub_date = n.get("date", "")
        if pub_date:
            pub_date = pub_date[:10]
        if pub_date not in date_groups:
            date_groups[pub_date] = []
        date_groups[pub_date].append(n)

    refined_records = []

    # --------------------------------------------------
    # 2단계: 날짜별로 루프를 돌며 하루에 '단 1개의 통합본'만 생성
    # --------------------------------------------------
    for pub_date, group_news in date_groups.items():
        
        # 만약 해당 날짜에 기사가 1개뿐이라면 즉시 정제 레코드 생성
        if len(group_news) == 1:
            n = group_news[0]
            refined_records.append({
                "company_name": company_name,
                "publish_date": pub_date,
                "combined_content": f"{clean_text(n.get('body', ''))}",
                "media_list": [n.get('media', '알수없음')] if n.get('media') else ["알수없음"],
                "url_list": [n.get('url', '')],
                "similar_count": 1
            })
            continue

        # 해당 날짜에 기사가 여러 개 있다면, 내부에서 유사도 검사를 통해 중복을 2차 필터링하거나 병합
        # (날짜가 같은 기사들은 대개 같은 이슈이므로 모두 병합하여 1개의 거대한 '일일 뉴스 리포트'로 만듭니다)
        corpus = [f"{clean_text(n.get('body', ''))}" for n in group_news]
        vectorizer = TfidfVectorizer(max_features=10000, min_df=1) 
        tfidf_matrix = vectorizer.fit_transform(corpus)
        sim_matrix = cosine_similarity(tfidf_matrix, tfidf_matrix)
        
        visited = np.zeros(len(group_news), dtype=bool)
        
        # 하루치 뉴스 중 서로 유사한 것끼리 묶어서 최종 content를 빌드
        day_combined_contents = []
        total_media_set = set()
        total_url_list = []
        total_similar_count = 0
        
        for idx in range(len(group_news)):
            if visited[idx]:
                continue
                
            similar_scores = sim_matrix[idx]
            similar_indices = np.where(similar_scores > SIMILARITY_THRESHOLD)[0]
            valid_indices = [int(i) for i in similar_indices if not visited[i]]
            
            if not valid_indices:
                valid_indices = [idx]
                
            for i in valid_indices:
                visited[i] = True
                
            sub_group = [group_news[i] for i in valid_indices]
            total_similar_count += len(sub_group)
            
            # 유사한 그룹 중 가장 정보가 많은(본문이 긴) 대표 기사 하나만 본문으로 채택하여 텍스트 비대화 방지
            # (만약 같은 날의 모든 뉴스를 다 이어붙이고 싶다면 이 부분을 이전처럼 loop로 다 더하시면 됩니다)
            representative = max(sub_group, key=lambda x: len(x.get('body', '')))
            day_combined_contents.append(
                # f"[{representative.get('media', '알수없음')}] {representative.get('title', '')}\n{representative.get('body', '')}"
                f"{clean_text(representative.get('body', ''))}"
            )
            
            # 언론사와 URL은 누락 없이 모두 수집
            for n in sub_group:
                if n.get('media'):
                    total_media_set.add(n['media'])
                total_url_list.append(n.get('url', ''))

        # 같은 날짜의 기사들을 최종 1개 파일 구조로 병합 완료
        refined_records.append({
            "company_name": company_name,
            "publish_date": pub_date,
            "combined_content": "\n\n---\n\n".join(day_combined_contents),
            "media_list": list(total_media_set) if total_media_set else ["알수없음"],
            "url_list": total_url_list,
            "similar_count": total_similar_count
        })
        
    return company_name, "성공", refined_records

# ==========================================
# [Main] 메인 실행 제어부
# ==========================================
def main():
    print("🚀 뉴스 데이터 통합 및 JSON 변환 파이프라인 시작...")
    
    client = chromadb.PersistentClient(path=CHROMA_DB_DIR)
    collection = client.get_or_create_collection(
        name="company_news_report",
        metadata={"hnsw:space": "cosine"} 
    )
    
    file_list = glob.glob(os.path.join(DATA_DIR, "*.jsonl"))
    
    # 예외 처리: 만약 결과파일(refined_news_output.json)이 데이터 폴더에 있으면 입력 대상에서 제외
    file_list = [f for f in file_list if "refined_news_output.json" not in f]
    
    ####################################### test
    file_list = [r"C:\Users\NT551XED\gitws\HcR\news_preprocess\article\CJ ENM_scraped_news_result.jsonl"]
    file_list = [r"C:\Users\NT551XED\gitws\HcR\news_preprocess\article\CJ대한통운_scraped_news_result.jsonl"]
    sample_saved = False
    SAMPLE_OUTPUT_PATH = r"C:\Users\NT551XED\gitws\HcR\news_preprocess\0refined_sample_check.jsonl"

    print(f"📂 총 {len(file_list)}개 기업 파일을 처리합니다.")
    
    all_companies_refined_data = []
    total_inserted = 0

    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {executor.submit(process_single_company, f): f for f in file_list}
        
        for future in tqdm(as_completed(futures), total=len(futures), desc="기업별 정제 진행률"):
            try:
                comp_name, status, cleaned_data = future.result()
            except Exception as e:
                print(f"\n❌ 프로세스 실행 중 예기치 못한 에러 발생: {e}")
                continue
            
            if not cleaned_data:
                continue
            ########################### test
            if not sample_saved:
                with open(SAMPLE_OUTPUT_PATH, "w", encoding="utf-8") as sf:
                    for item in cleaned_data:
                        # 구조: {"content": "...", "metadata": {...}}
                        sf.write(json.dumps(item, ensure_ascii=False) + "\n")
                print(f"\n🎯 [샘플 저장 완료] '{comp_name}' 기업의 정제 데이터가 {SAMPLE_OUTPUT_PATH} 에 저장되었습니다! 눈으로 확인해보세요.")
                sample_saved = True # 한 번만 저장하도록 플래그 변경
                
            
            all_companies_refined_data.extend(cleaned_data)
                
            # ChromaDB 저장 부
            ids = []
            documents = []
            metadatas = []
            
            for i, data in enumerate(cleaned_data):
                ids.append(f"{comp_name}_{i}_{data['publish_date']}")
                documents.append(data['combined_content'])
                metadatas.append({
                    "company_name": data['company_name'],
                    "publish_date": data['publish_date'],
                    "media_primary": ", ".join(data['media_list']), # ChromaDB 호환용 문자열 변환
                    "urls": json.dumps(data['url_list'], ensure_ascii=False),
                    "similar_count": data['similar_count']
                })
                
                if len(ids) >= BATCH_SIZE:
                    collection.add(ids=ids, documents=documents, metadatas=metadatas)
                    total_inserted += len(ids)
                    ids, documents, metadatas = [], [], []
            
            if ids:
                collection.add(ids=ids, documents=documents, metadatas=metadatas)
                total_inserted += len(ids)

    # --------------------------------------------------
    # 최종 결과 파일 저장
    # --------------------------------------------------
    if all_companies_refined_data:
        print(f"\n💾 최종 파일 쓰기 중... ({FINAL_JSON_PATH})")
        with open(FINAL_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(all_companies_refined_data, f, ensure_ascii=False, indent=2)
        print("✅ 모든 데이터 정제 및 통합형 JSON 추출이 완료되었습니다!")
        print(f"📊 최종 생성된 뉴스 그룹 피스: {len(all_companies_refined_data)} 개")
    else:
        print("\n⚠️ 정제된 데이터가 존재하지 않아 JSON 파일을 저장하지 못했습니다.")

if __name__ == "__main__":
    main()
