#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GitHub Actions 定时版期刊论文监控程序

监控：
1. JACS
2. Nature Communications

功能：
1. 定时通过 GitHub Actions 运行
2. 每次运行只检查一轮，检查完自动退出
3. 抓取最新论文标题、摘要、DOI、发表时间、链接
4. 本地保存已采集 DOI，用于去重
5. 检测到新论文后通过 Server 酱推送微信
6. 自动整理标题+摘要，方便复制给 AI 生成公众号推文
"""

import os
import re
import json
import time
import random
import logging
import warnings
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

import requests
import feedparser
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from dateutil.parser import UnknownTimezoneWarning


# =========================
# 0. 忽略 RSS 时间格式小警告
# =========================

warnings.filterwarnings("ignore", category=UnknownTimezoneWarning)


# =========================
# 1. 基础配置
# =========================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "journal_monitor_data"
DATA_DIR.mkdir(exist_ok=True)

SEEN_FILE = DATA_DIR / "seen_dois.json"
OUTPUT_MD = DATA_DIR / "new_papers_for_ai.md"
LOG_FILE = DATA_DIR / "journal_monitor.log"

# 第一次运行时是否推送已有文章：
# False = 首次只建立 DOI 库，不推送历史文章
# True  = 首次也推送当前 RSS 里的文章，不推荐
PUSH_ON_FIRST_RUN = False

# Server 酱 SendKey
# 不要直接写在代码里，GitHub 里通过 Secrets 设置：
# Settings -> Secrets and variables -> Actions -> New repository secret
# Name: SERVERCHAN_SENDKEY
SERVERCHAN_SENDKEY = os.getenv("SERVERCHAN_SENDKEY", "").strip()

# PushPlus 备用，可不用
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "").strip()

# 关键词过滤：
# 留空 [] 表示所有新论文都推送
KEYWORDS_FILTER = [
    # "photocatalysis",
    # "electrocatalysis",
    # "degradation",
    # "catalyst",
    # "functional material",
    # "battery",
    # "pollutant",
]

# 目前只监控这两个期刊
JOURNALS = [
    {
        "name": "JACS",
        "feed_url": "https://pubs.acs.org/action/showFeed?type=axatoc&feed=rss&jc=jacsat",
        "home": "https://pubs.acs.org/toc/jacsat/0/0",
    },
    {
        "name": "Nature Communications",
        "feed_url": "https://www.nature.com/ncomms.rss",
        "home": "https://www.nature.com/ncomms/articles",
    },
]


# =========================
# 2. 日志配置
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ],
)


# =========================
# 3. 通用工具函数
# =========================

def load_seen_dois() -> set:
    """读取本地已见 DOI。"""
    if not SEEN_FILE.exists():
        return set()

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data)
    except Exception as e:
        logging.warning(f"读取 DOI 去重库失败，将重新创建：{e}")
        return set()


def save_seen_dois(seen: set) -> None:
    """保存已见 DOI。"""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)


def clean_text(text: str) -> str:
    """清理 HTML、换行、重复空格。"""
    if not text:
        return ""

    soup = BeautifulSoup(text, "lxml")
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_doi(text: str) -> Optional[str]:
    """从文本中提取 DOI。"""
    if not text:
        return None

    pattern = r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+"
    match = re.search(pattern, text)

    if not match:
        return None

    doi = match.group(0).strip()
    doi = doi.rstrip(".),;]")
    return doi


def normalize_doi(doi: str) -> str:
    """统一 DOI 格式，方便去重。"""
    if not doi:
        return ""

    doi = doi.strip()
    doi = doi.replace("https://doi.org/", "")
    doi = doi.replace("http://dx.doi.org/", "")
    doi = doi.replace("doi:", "")
    return doi.lower().strip()


def parse_date(value: str) -> str:
    """把 RSS 里的日期统一成 YYYY-MM-DD HH:MM。"""
    if not value:
        return ""

    try:
        dt = date_parser.parse(value)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value


def article_matches_keywords(title: str, abstract: str) -> bool:
    """关键词过滤。KEYWORDS_FILTER 为空时不过滤。"""
    if not KEYWORDS_FILTER:
        return True

    blob = f"{title} {abstract}".lower()
    return any(k.lower() in blob for k in KEYWORDS_FILTER)


# =========================
# 4. 网络请求与页面补充解析
# =========================

def make_session() -> requests.Session:
    """创建带请求头的 Session。"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36 "
            "AcademicJournalMonitor/1.0"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "Connection": "keep-alive",
    })
    return session


def fetch_url(session: requests.Session, url: str, timeout: int = 20) -> Optional[str]:
    """稳定请求 URL，带简单重试。"""
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=timeout)

            if resp.status_code == 200:
                return resp.text

            logging.warning(f"请求失败 {resp.status_code}: {url}")

            if resp.status_code in (403, 429, 500, 502, 503, 504):
                time.sleep(10 + attempt * 20)

        except Exception as e:
            logging.warning(f"请求异常，第 {attempt + 1} 次：{url} | {e}")
            time.sleep(5 + attempt * 10)

    return None


def supplement_from_article_page(session: requests.Session, link: str) -> Dict[str, str]:
    """
    访问单篇文章页面，尝试补充 DOI、摘要、发表时间。
    注意：不是所有期刊页面都会开放完整摘要。
    """
    result = {
        "doi": "",
        "abstract": "",
        "published": "",
    }

    if not link:
        return result

    html = fetch_url(session, link)

    if not html:
        return result

    soup = BeautifulSoup(html, "lxml")

    meta_candidates = {
        "doi": [
            {"name": "citation_doi"},
            {"name": "dc.Identifier"},
            {"name": "DC.Identifier"},
        ],
        "abstract": [
            {"name": "citation_abstract"},
            {"name": "description"},
            {"property": "og:description"},
            {"name": "dc.Description"},
            {"name": "DC.Description"},
        ],
        "published": [
            {"name": "citation_publication_date"},
            {"name": "citation_online_date"},
            {"name": "dc.Date"},
            {"name": "DC.Date"},
            {"property": "article:published_time"},
        ],
    }

    for key, metas in meta_candidates.items():
        for attrs in metas:
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                result[key] = clean_text(tag.get("content", ""))
                break

    if not result["doi"]:
        doi = extract_doi(html)
        if doi:
            result["doi"] = doi

    return result


# =========================
# 5. RSS 解析
# =========================

def parse_feed(session: requests.Session, journal: Dict[str, str]) -> List[Dict[str, str]]:
    """解析单个期刊 RSS，返回论文列表。"""
    name = journal["name"]
    feed_url = journal["feed_url"]

    logging.info(f"正在检查：{name}")

    xml_text = fetch_url(session, feed_url)

    if not xml_text:
        logging.warning(f"{name} RSS 获取失败，跳过。")
        return []

    feed = feedparser.parse(xml_text)
    papers = []

    if feed.bozo:
        logging.warning(f"{name} RSS 解析可能有问题：{feed.bozo_exception}")

    for entry in feed.entries:
        title = clean_text(entry.get("title", ""))
        link = entry.get("link", "")

        summary = (
            entry.get("summary", "")
            or entry.get("description", "")
            or entry.get("subtitle", "")
        )
        abstract = clean_text(summary)

        published = (
            entry.get("published", "")
            or entry.get("updated", "")
            or entry.get("created", "")
        )
        published = parse_date(published)

        doi = ""
        possible_text = " ".join([
            entry.get("id", ""),
            entry.get("guid", ""),
            link,
            title,
            abstract,
        ])

        found_doi = extract_doi(possible_text)
        if found_doi:
            doi = found_doi

        # DOI 或摘要缺失时，访问单篇页面补充
        if (not doi or len(abstract) < 40) and link:
            time.sleep(random.uniform(3, 8))
            extra = supplement_from_article_page(session, link)

            if not doi and extra.get("doi"):
                doi = extra["doi"]

            if len(abstract) < 40 and extra.get("abstract"):
                abstract = extra["abstract"]

            if not published and extra.get("published"):
                published = parse_date(extra["published"])

        unique_id = normalize_doi(doi) if doi else link.strip().lower()

        if not unique_id:
            continue

        paper = {
            "journal": name,
            "title": title,
            "abstract": abstract,
            "doi": normalize_doi(doi) if doi else "",
            "unique_id": unique_id,
            "published": published,
            "link": link,
        }

        papers.append(paper)

    logging.info(f"{name} 获取到 {len(papers)} 条记录。")
    return papers


# =========================
# 6. 推送与文本整理
# =========================

def build_ai_text(paper: Dict[str, str]) -> str:
    """整理成适合复制给 AI 写公众号推文的文本。"""
    title = paper.get("title", "")
    abstract = paper.get("abstract", "")
    journal = paper.get("journal", "")
    doi = paper.get("doi", "")
    published = paper.get("published", "")
    link = paper.get("link", "")

    if not abstract:
        abstract = "摘要暂未从 RSS 或页面中解析到，请打开原文链接查看。"

    text = f"""## 新论文素材

期刊：{journal}
发表时间：{published}
标题：{title}
DOI：{doi}
链接：{link}

摘要：
{abstract}

可交给 AI 的公众号写作提示：
请根据以上论文标题和摘要，写一段适合科研公众号发布的中文推文草稿。要求：
1. 用通俗但不夸张的语言概括研究背景；
2. 提炼核心创新点；
3. 说明潜在应用价值；
4. 不要编造摘要中没有的信息；
5. 结尾给出一句适合公众号风格的总结。
"""
    return text.strip()


def append_to_markdown(papers: List[Dict[str, str]]) -> None:
    """把新增论文追加保存到 Markdown 文件。"""
    if not papers:
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(OUTPUT_MD, "a", encoding="utf-8") as f:
        f.write(f"\n\n# 本次新增论文 | {now}\n\n")
        for paper in papers:
            f.write(build_ai_text(paper))
            f.write("\n\n---\n\n")


def push_serverchan(title: str, content: str) -> bool:
    """Server 酱微信推送。"""
    if not SERVERCHAN_SENDKEY:
        logging.warning("未设置 SERVERCHAN_SENDKEY，无法推送 Server 酱。")
        return False

    url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"

    try:
        resp = requests.post(
            url,
            data={
                "title": title[:100],
                "desp": content,
            },
            timeout=30,
        )

        logging.info(f"Server 酱推送返回：{resp.status_code} | {resp.text[:300]}")
        return resp.status_code == 200

    except Exception as e:
        logging.warning(f"Server 酱推送失败：{e}")
        return False


def push_pushplus(title: str, content: str) -> bool:
    """PushPlus 备用推送。"""
    if not PUSHPLUS_TOKEN:
        return False

    url = "https://www.pushplus.plus/send"

    try:
        resp = requests.post(
            url,
            json={
                "token": PUSHPLUS_TOKEN,
                "title": title[:100],
                "content": content,
                "template": "markdown",
            },
            timeout=30,
        )

        logging.info(f"PushPlus 推送返回：{resp.status_code} | {resp.text[:300]}")
        return resp.status_code == 200

    except Exception as e:
        logging.warning(f"PushPlus 推送失败：{e}")
        return False


def push_wechat_for_paper(paper: Dict[str, str]) -> None:
    """对单篇论文进行微信提醒。"""
    title = f"新论文提醒：{paper.get('journal', '')}"

    content = f"""### {paper.get("title", "")}

**期刊：** {paper.get("journal", "")}

**发表时间：** {paper.get("published", "")}

**DOI：** {paper.get("doi", "")}

**链接：** {paper.get("link", "")}

**摘要：**

{paper.get("abstract", "摘要暂未解析到。")}

---

### 公众号素材整理

{build_ai_text(paper)}
"""

    ok = False

    if SERVERCHAN_SENDKEY:
        ok = push_serverchan(title, content)

    if not ok and PUSHPLUS_TOKEN:
        ok = push_pushplus(title, content)

    if not ok:
        logging.warning("未成功推送微信。请检查 SERVERCHAN_SENDKEY 或 PUSHPLUS_TOKEN。")


# =========================
# 7. 主检查逻辑
# =========================

def check_once(first_run: bool = False) -> None:
    """执行一轮检查。"""
    session = make_session()
    seen = load_seen_dois()
    old_seen_count = len(seen)

    new_papers = []

    for idx, journal in enumerate(JOURNALS):
        try:
            papers = parse_feed(session, journal)

            for paper in papers:
                unique_id = paper["unique_id"]

                if unique_id in seen:
                    continue

                if not article_matches_keywords(paper["title"], paper["abstract"]):
                    seen.add(unique_id)
                    continue

                seen.add(unique_id)
                new_papers.append(paper)

        except Exception as e:
            logging.exception(f"检查期刊失败：{journal['name']} | {e}")

        # 两个期刊之间稍微等待，减少连续请求
        if idx < len(JOURNALS) - 1:
            delay = random.uniform(15, 45)
            logging.info(f"等待 {delay:.1f} 秒后检查下一个期刊。")
            time.sleep(delay)

    save_seen_dois(seen)

    if first_run and not PUSH_ON_FIRST_RUN:
        logging.info(
            f"首次运行：已建立 DOI 库。原有 {old_seen_count} 条，当前 {len(seen)} 条；不推送历史文章。"
        )
        return

    if not new_papers:
        logging.info("本轮没有发现新增论文。")
        return

    logging.info(f"发现新增论文 {len(new_papers)} 篇。")
    append_to_markdown(new_papers)

    for paper in new_papers:
        push_wechat_for_paper(paper)
        time.sleep(random.uniform(5, 12))


# =========================
# 8. GitHub Actions 入口
# =========================

if __name__ == "__main__":
    logging.info("GitHub Actions 单次检查模式启动。")
    logging.info(f"数据目录：{DATA_DIR}")
    check_once(first_run=not SEEN_FILE.exists())