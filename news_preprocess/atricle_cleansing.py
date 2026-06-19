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
SIMILARITY_THRESHOLD = 0.3
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
    [Worker Process] 유사 기사의 본문을 합치고, 미디어와 URL을 리스트로 통합합니다.
    """
    # [수정] 튜플 분리를 확실히 하여 문자열 데이터만 취득
    base_name = os.path.basename(file_path)
    company_name = os.path.splitext(base_name)[0]
    
    news_list = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    news_list.append(json.loads(line))
    except Exception as e:
        return company_name, f"실패: {str(e)}", []

    if not news_list:
        return company_name, "데이터 없음", []

    # --------------------------------------------------
    # [수정] 기사가 1개만 있을 때의 예외 처리 로직 완전 정상화
    # --------------------------------------------------
    if len(news_list) == 1:
        n = news_list[0]
        pub_date = extract_date_from_text(n.get('title', ''), n.get('body', ''))
        single_record = {
            "company_name": company_name,
            "publish_date": pub_date,
            # "combined_content": f"[{n.get('media', '알수없음')}] {n.get('title', '')}\n{n.get('body', '')}",
            "combined_content": f"{clean_text(n.get('body', ''))}",
            "media_list": [n.get('media', '알수없음')] if n.get('media') else ["알수없음"],
            "url_list": [n.get('url', '')],
            "similar_count": 1
        }
        return company_name, "성공", [single_record]

    # 2. TF-IDF 벡터화 및 유사도 계산
    # corpus = [f"{n.get('title', '')} {n.get('body', '')}" for n in news_list]
    corpus = [f"{clean_text(n.get('body', ''))}" for n in news_list]
    vectorizer = TfidfVectorizer(max_features=3000, min_df=1) 
    tfidf_matrix = vectorizer.fit_transform(corpus)
    sim_matrix = cosine_similarity(tfidf_matrix, tfidf_matrix)
    
    visited = np.zeros(len(news_list), dtype=bool)
    refined_records = []
    
    # 3. 군집화 루프 (완벽 수정 버전)
    for idx in range(len(news_list)):
        if visited[idx]:
            continue
            
        # 해당 기사와 82% 이상 유사한 기사들의 인덱스를 올바르게 추출
        similar_scores = sim_matrix[idx]
        similar_indices = np.where(similar_scores > SIMILARITY_THRESHOLD)[0]
        
        # 아직 방문하지 않은(처리되지 않은) 기사들만 필터링
        valid_indices = [int(i) for i in similar_indices if not visited[i]]
        
        if not valid_indices:
            valid_indices = [idx]
            
        # 찾은 기사들은 다음 루프에서 중복 처리되지 않도록 방문 마킹 (이게 지워지는 핵심 역할)
        for i in valid_indices:
            visited[i] = True
            
        group_news = [news_list[i] for i in valid_indices]
        
        contents_vessel = []
        media_set = set()
        url_list = []
        
        for n in group_news:
            contents_vessel.append(f"{clean_text(n.get('body', ''))}")
            if n.get('media'):
                media_set.add(n['media'])
            url_list.append(n.get('url', ''))
            
        # 여러 개의 비슷한 본문을 하나로 병합
        combined_content = "\n\n---\n\n".join(contents_vessel)
        
        # 대표 기사 선정 (날짜 추출용)
        representative = group_news[0]
        pub_date = extract_date_from_text(representative.get('title', ''), representative.get('body', ''))
        
        refined_records.append({
            "company_name": company_name,
            "publish_date": pub_date,
            "combined_content": combined_content,
            "media_list": list(media_set) if media_set else ["알수없음"],
            "url_list": url_list,
            "similar_count": len(group_news) # 몇 개의 기사가 하나로 압축되었는지 기록
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
    file_list = [r"C:\myfolder\spc\scp_genai\tmp_xray_model\article\CJ ENM_scraped_news_result.json"]
    sample_saved = False
    SAMPLE_OUTPUT_PATH = r"C:\myfolder\spc\scp_genai\tmp_xray_model\0refined_sample_check.jsonl"

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
