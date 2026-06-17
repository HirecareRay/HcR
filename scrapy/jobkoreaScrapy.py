import requests, csv, os, re, random
from tqdm import tqdm
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time

start = time.perf_counter()

def clean_filename(name):
    return re.sub(r'[\\/:*?"<>|\t\n\r]', '_', name)
def full_path(filename: str) -> str:
    return os.path.join(os.path.dirname(__file__), filename)
os.makedirs(full_path("data"), exist_ok=True)
os.makedirs(full_path("error"), exist_ok=True)

baseUrl = "https://www.jobkorea.co.kr"
# subUrl = "/recruit/joblist?&local=I000,B020,B030,B031,B150,B160,B170&duty=1000236,1000237,1000242,1000418,1000422,1000423&career=1,8&order=2#anchorGICnt_"

url = baseUrl + "/Recruit/Home/_GI_List/"

payload = {
    "Page": 1,
    "PageSize": 50,
    "SearchType": 1,

    # "local": "I000,B020,B030,B031,B150,B160,B170",
    "local": "I000",
    # "duty": "1000236,1000237,1000242,1000418,1000422,1000423",
    "duty": "1000423,1000231",
    # "career": "1,8",
    # "cotype": "11,12", 

    "order": "2"
}

headers = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.jobkorea.co.kr/",
    "X-Requested-With": "XMLHttpRequest"
}

def clean_text(text):
    return re.sub(r'\s+', ' ', text).strip()

def gamejob_company_info(html):
    soup = BeautifulSoup(html, "html.parser")
    soup.select_one("section.content")
    result = {}

    # =========================
    # 기본 정보
    # =========================
    header = {}

    corp_name = soup.select_one(".corpName")
    if corp_name:
        header["회사명"] = clean_text(corp_name.get_text())

    status = soup.select_one(".corpHeader .now")
    if status:
        header["채용상태"] = clean_text(status.get_text())

    result["기업정보"] = header

    # =========================
    # 상세 기업정보
    # =========================
    company_info = {}

    for dl in soup.select(".corpInfo dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")

        for dt, dd in zip(dts, dds):
            key = clean_text(dt.get_text())
            value = clean_text(dd.get_text(" ", strip=True))
            company_info[key] = value

    result["기업상세정보"] = company_info

    # =========================
    # 기업소개 섹션
    # =========================
    intro_sections = {}

    for article in soup.select("#infoTab article.corpDesc"):
        title_tag = article.select_one("h4.corpTit")
        content_tag = article.select_one(".contArea")

        if not title_tag or not content_tag:
            continue

        title = clean_text(title_tag.get_text())

        # bullet 제거
        title = title.replace("•", "").replace("ㆍ", "")

        content = clean_text(content_tag.get_text("\n"))

        intro_sections[title] = content

    result["소개"] = intro_sections

    # =========================
    # 채용정보
    # =========================
    recruit_list = []

    for tr in soup.select("#recruitTab tbody tr"):
        title_tag = tr.select_one("strong")

        if not title_tag:
            continue

        job = {
            "공고명": clean_text(title_tag.get_text())
        }

        info_spans = tr.select(".info span")

        if len(info_spans) >= 4:
            job["경력"] = clean_text(info_spans[0].get_text())
            job["학력"] = clean_text(info_spans[1].get_text())
            job["근무지"] = clean_text(info_spans[2].get_text())
            job["분야"] = clean_text(info_spans[3].get_text())

        date_tag = tr.select_one(".date")
        if date_tag:
            job["마감일"] = clean_text(date_tag.get_text())

        recruit_list.append(job)

    result["채용정보"] = recruit_list

    # =========================
    # 기업뉴스
    # =========================
    news_list = []

    for li in soup.select("#newsTab .newslist li"):
        news = {}

        title = li.select_one(".tit")
        desc = li.select_one(".desc")
        date = li.select_one(".date")

        if title:
            news["제목"] = clean_text(title.get_text())

        if desc:
            news["내용"] = clean_text(desc.get_text())

        if date:
            news["날짜"] = clean_text(date.get_text())

        news_list.append(news)

    result["기업뉴스"] = news_list

    return result

def jobkorea_company_info(html: str, company: str, postUrl: str) -> dict:
    try:
        soup = BeautifulSoup(html, "html.parser")
        soup = soup.select_one("div.company-body-infomation")
        result = {
            "basic_info": {},
            "financial": {},
            "history": [],
            "employment": {},
            "benefits": {},
            "location": {}
        }

        # =========================
        # 1. 기업 기본정보
        # =========================
        basic_table = soup.select_one("table.table-basic-infomation-primary")

        if basic_table:
            for tr in basic_table.select("tr.field"):
                cells = tr.find_all(["th", "td"])

                i = 0
                while i < len(cells):
                    if cells[i].name == "th":
                        key = cells[i].get_text(" ", strip=True)

                        if i + 1 < len(cells):
                            value_td = cells[i + 1]

                            values = [
                                x.get_text(" ", strip=True)
                                for x in value_td.select(".value, .salary-average-item, .reference")
                            ]

                            value = " | ".join(values) if values else value_td.get_text(" ", strip=True)

                            result["basic_info"][key] = value

                        i += 2
                    else:
                        i += 1

        # =========================
        # 2. 재무정보
        # =========================
        for card in soup.select(".financial-analysis-card"):

            title_tag = card.select_one(".headers .header")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)

            value_tag = card.select_one(".revenue .value")
            value = value_tag.get_text(strip=True) if value_tag else None

            result["financial"][title] = {
                "current_value": value
            }

            yearly = {}

            for bar in card.select(".chart .bar"):
                year_tag = bar.select_one(".label")
                val_tag = bar.select_one(".value")

                if year_tag and val_tag:
                    yearly[year_tag.get_text(strip=True)] = val_tag.get_text(strip=True)

            if yearly:
                result["financial"][title]["history"] = yearly

        # =========================
        # 3. 연혁
        # =========================
        current_year = None

        for item in soup.select(".corporate-history-list-item"):

            year_tag = item.select_one(".year")
            if year_tag:
                current_year = year_tag.get_text(strip=True)

            month_tag = item.select_one(".month")

            descriptions = [
                x.get_text(strip=True)
                for x in item.select(".month-description")
            ]

            if month_tag:
                result["history"].append({
                    "year": current_year,
                    "month": month_tag.get_text(strip=True),
                    "events": descriptions
                })

        # =========================
        # 4. 채용 현황
        # =========================
        recruitments = []

        for row in soup.select(".table-in-progress-announcement tbody tr"):

            tds = row.find_all("td")

            if len(tds) >= 3:
                title_tag = row.select_one(".title")

                recruitments.append({
                    "period": tds[0].get_text(strip=True),
                    "title": title_tag.get_text(strip=True) if title_tag else "",
                    "details": tds[2].get_text(" | ", strip=True)
                })

        result["employment"]["recruitments"] = recruitments

        # =========================
        # 5. 복리후생
        # =========================
        for item in soup.select(".benefit-item-group .item"):

            category = item.select_one(".benefit-header")

            if not category:
                continue

            category_name = category.get_text(strip=True)

            benefits = [
                p.get_text(strip=True)
                for p in item.select(".benefit-body p")
            ]

            result["benefits"][category_name] = benefits

        # =========================
        # 6. 기업 위치
        # =========================
        address_tag = soup.select_one(".working-environment-map .address")

        if address_tag:
            result["location"]["address"] = address_tag.get_text(strip=True)

        map_link = soup.select_one(".btnMapApiL")

        if map_link:
            result["location"]["map_url"] = map_link.get("href")
        
        return result
    except AttributeError as e:
        print(e)
        with open(full_path(f"error\\{clean_filename(company)}.html"), "w", encoding="utf-8") as f:
            f.write(html)

        with open(full_path("error\\error.txt"), "a+", encoding="utf-8") as error_file:
            error_file.write(
                f"[{datetime.now()}]\n"
                f"num: {num}\n"
                f"URL: {postUrl}\n"
                f"Error: {type(e).__name__}: {e}\n"
                f"{'-'*80}\n"
            )

def super_company_info(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    soup = soup.select_one("#wrap")

    result = {}

    # 회사명
    company_name = soup.select_one("h2.giHd")
    if company_name:
        result["company_name"] = company_name.get_text(strip=True)

    # 기본정보
    corp_info = soup.select(".corpInfo dl")

    if len(corp_info) >= 4:
        result["founded"] = corp_info[0].select_one("strong").get_text(strip=True)
        result["employee_count"] = corp_info[1].select_one("strong").get_text(strip=True)
        result["company_type"] = corp_info[2].select_one("strong").get_text(strip=True)
        result["revenue"] = corp_info[3].select_one("strong").get_text(strip=True)

    # 입사해야 하는 이유
    reasons = []
    for li in soup.select(".corpInfo2 ul li"):
        reasons.append(li.get_text(strip=True))

    result["reasons_to_join"] = reasons

    # 기업 History
    history = []

    for item in soup.select(".history_cont > li"):
        year_tag = item.find("p")
        year = year_tag.get_text(strip=True) if year_tag else ""

        events = [
            x.get_text(" ", strip=True)
            for x in item.select("ul li")
        ]

        history.append({
            "year": year,
            "events": events
        })

    result["history"] = history

    # 복리후생
    benefits = []

    for item in soup.select(".culture_area > div"):
        title = item.select_one(".culture_txt p")
        desc = item.select_one(".culture_txt span")

        benefits.append({
            "title": title.get_text(strip=True) if title else "",
            "description": desc.get_text(" ", strip=True) if desc else ""
        })

    result["benefits"] = benefits

    # 인재상
    talents = []

    for item in soup.select(".talent li"):
        title = item.select_one("strong")
        desc = item.select_one("p")

        talents.append({
            "type": title.get_text(strip=True) if title else "",
            "description": desc.get_text(" ", strip=True) if desc else ""
        })

    result["talents"] = talents

    # 채용 프로세스
    process = []

    for li in soup.select(".process .step li"):
        step = li.select_one("em")
        name = li.select_one("p")

        process.append({
            "step": step.get_text(strip=True) if step else "",
            "name": name.get_text(" ", strip=True) if name else ""
        })

    result["recruit_process"] = process

    # 채용공고
    jobs = []

    for row in soup.select("#tbHistory tr"):
        cols = row.find_all("td")

        if len(cols) >= 3:
            jobs.append({
                "period": cols[0].get_text(strip=True),
                "title": cols[1].get_text(" ", strip=True),
                "career": cols[2].get_text(strip=True)
            })

    result["jobs"] = jobs

    # 주소
    address_tag = soup.select_one(".corMap_info dl dd")
    if address_tag:
        result["address"] = address_tag.get_text(" ", strip=True)

    # 홈페이지
    website_tag = soup.select_one(".corp_tel a")
    if website_tag:
        result["website"] = website_tag.get_text(strip=True)

    # 전화번호
    phone_tags = soup.select(".corp_tel")
    if len(phone_tags) >= 2:
        result["phone"] = phone_tags[1].get_text(" ", strip=True)
    
    return result

def scrapy_loop(post):
    postUrl = post.select_one("a.link")["href"]
    if postUrl.find("gamejob") != -1:
        result = requests.get(postUrl, headers=headers)
        postSoup = BeautifulSoup(result.text, "html.parser")
        result = requests.get("https://www.gamejob.co.kr/" + postSoup.select_one("div.view__header-title div.corp-name a")["href"], headers=headers)
        company_info = gamejob_company_info(result.text)
        details = postSoup.select_one("article.content__summary > div > article.recruit-data.flex.item-center.flex-wrap")
        job = details.select_one("dd.recruit-data-text").text.strip().split(", ")
        try:
            detail = "   ".join([_.text for _ in details])
        except:
            detail = ""
        postUrl = postUrl.split("?")[0].replace("d/", "d_Comt_Ifrm?Gno=").replace("/View?GI_N", "Comt_Ifrm?gn")
        company = post.select_one("td.tplCo > a.link").text
        title = post.select_one("strong a.link").text
    else:
        company = post.select_one(".link.normalLog").text
        title = post.select_one("strong a.link.normalLog")["title"]
        result = requests.get(baseUrl + post.select_one("a.link.normalLog")["href"], headers=headers)
        if result.text[:500].find("https://www.jobkorea.co.kr/super/") == -1:
            company_info = jobkorea_company_info(result.text, company, postUrl)
        else:
            company_info = super_company_info(result.text)
        postUrl = baseUrl + post.select_one("strong a.link.normalLog")["href"]
        result = requests.get(postUrl, headers=headers)
        postSoup = BeautifulSoup(result.text, "html.parser")
        details = postSoup.select_one("div.ml-auto > div > div > div.flex.flex-col")
        try:
            job = details.select_one("span.flex-1.line-clamp-1.overflow-hidden.text-ellipsis").text.strip().split()
        except AttributeError:
            try:
                job = postSoup.select_one("span.whitespace-pre-wrap.font-medium").text.strip().split()
            except AttributeError:
                job = title
        try:
            detail = "   ".join([_.text for _ in details.select("div > div > div > div > span ")] + [_.text for _ in details.select("div > div >div > div li")])
        except:
            detail = ""
        postUrl = postUrl.split("?")[0].replace("d/", "d_Comt_Ifrm?Gno=")
    result = requests.get(postUrl, headers=headers)
    postSoup = BeautifulSoup(result.text, "html.parser")
    try:
        text = postSoup.text if postSoup.text else ""
        img_url = postSoup.select("img")
        img_url = list({e.attrs.get("src") for e in img_url}) if img_url else []
    except Exception as e:
        text = ""
        img_url = ""

        with open(full_path("error.txt"), "a+", encoding="utf-8") as error_file:
            error_file.write(
                f"[{datetime.now()}]\n"
                f"num: {num}\n"
                f"URL: {postUrl}\n"
                f"Error: {type(e).__name__}: {e}\n"
                f"{'-'*80}\n"
            )
    data.append([
    company,
    title,
    job,
    text,
    img_url,
    detail,
    ", ".join([" ".join(cell.text.split()) for cell in post.select("p.etc > span") if cell.text]),
    company_info,
    postUrl,
    ])

for num in range(1, 999999):
    payload["Page"] = num
    res = requests.post(url, data=payload, headers=headers)
    if res.status_code == 200:
        soup = BeautifulSoup(res.text, "html.parser")
        if soup.select("#imgCaptcha"):
            print("캡챠 제거 요망")
            print(num)
            input("캡챠 제거 요망")
        else:
            data = [["company", "title", "job", "detail_text", "detail_img", "detail", "etc_info", "company_info", "url"]]
            for post in tqdm(soup.select(".devloopArea")):
                while True:
                    try:
                        scrapy_loop(post)
                        break
                    except (requests.exceptions.ConnectTimeout, TimeoutError) as e:
                        print(e)
                        time.sleep(random.uniform(3, 16))

            if soup.select(".nodata"):
                break
            fileName = full_path(f"data\\jobkorea_post_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.tsv")
            mode = "a+" if os.path.exists(fileName) else "w+"
            with open(fileName, mode, newline="", encoding="utf_8") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerows(data) if mode == "w+" else writer.writerows(data[1:])
            
            # time.sleep(random.uniform(1, 13))

# fileName = full_path(f"data\\jobkorea_post_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.tsv")
# mode = "a+" if os.path.exists(fileName) else "w+"
# with open(fileName, mode, newline="", encoding="utf_8") as f:
#     writer = csv.writer(f, delimiter="\t")
#     writer.writerows(data) if mode == "w+" else writer.writerows(data[1:])

# data = []
# with open(fileName, "r", encoding="utf_8") as f:
#     csv_reader = csv.reader(f, delimiter="\t")
#     for row in csv_reader[:10]:
#         data.append(row)
#     [print("\t".join(_) + "\n" + ("="*30) + "\n") for _ in data]

end = time.perf_counter()

elapsed = end - start
print(f"실행 시간: {timedelta(seconds=elapsed)}")