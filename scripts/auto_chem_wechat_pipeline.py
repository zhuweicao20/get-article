#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions 版：开放获取化学论文自动下载 + 自动调用公众号工作流。

流程：
1. 读取 config/journals.yml 与 config/keywords.yml
2. 通过 RSS / Crossref 抓最近文章
3. 按期刊优先级与关键词打分
4. 通过 Nature/Science/ACS 规则、页面 meta、Unpaywall 查找开放 PDF
5. 下载 PDF 并校验
6. 调用 workflow/paper_wechat_workflow_wechat_style_v3/src/main.py 生成 article.html / article.md / images / outputs.zip
7. 保存 output/report.csv，并更新 state/seen_dois.json

注意：只处理开放获取 PDF。非开放全文不会绕过权限。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
STATE_DIR = ROOT / "state"
DOWNLOAD_DIR = ROOT / "downloads"
OUTPUT_DIR = ROOT / "output"
ARTICLE_DIR = OUTPUT_DIR / "articles"
REPORT_CSV = OUTPUT_DIR / "report.csv"
CANDIDATES_JSON = OUTPUT_DIR / "candidates.json"
SEEN_FILE = STATE_DIR / "seen_dois.json"
LOG_FILE = OUTPUT_DIR / "auto_pipeline.log"

CROSSREF_API = "https://api.crossref.org/works"
UNPAYWALL_API = "https://api.unpaywall.org/v2"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


@dataclass
class Paper:
    journal: str
    title: str
    doi: str
    link: str
    abstract: str = ""
    published: str = ""
    priority: int = 0
    keywords: List[str] = None
    score: int = 0
    pdf_url: str = ""
    source: str = ""

    @property
    def unique_id(self) -> str:
        return normalize_doi(self.doi) if self.doi else self.link.strip().lower()


def ensure_dirs() -> None:
    for d in [STATE_DIR, DOWNLOAD_DIR, OUTPUT_DIR, ARTICLE_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def clean_text(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "lxml")
    text = soup.get_text(" ", strip=True)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_doi(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", text)
    if not m:
        return ""
    return m.group(0).rstrip(".),;]").strip()


def normalize_doi(doi: str) -> str:
    doi = (doi or "").strip()
    doi = doi.replace("https://doi.org/", "").replace("http://dx.doi.org/", "")
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    return doi.lower().strip()


def safe_slug(text: str, max_len: int = 70) -> str:
    text = clean_text(text)
    text = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._-")
    return text[:max_len] or "paper"


def parse_date(value: str) -> str:
    if not value:
        return ""
    try:
        return date_parser.parse(value).strftime("%Y-%m-%d")
    except Exception:
        return clean_text(value)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 AcademicOpenPaperBot/1.0 (+https://github.com/)",
        "Accept": "text/html,application/xhtml+xml,application/xml,application/rss+xml,application/pdf;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    })
    return s


def get_text(session: requests.Session, url: str, timeout: int = 30) -> str:
    for i in range(3):
        try:
            r = session.get(url, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                return r.text
            logging.warning("GET %s -> %s", url, r.status_code)
            if r.status_code in (403, 429, 500, 502, 503, 504):
                time.sleep(5 + i * 10)
        except Exception as exc:
            logging.warning("GET failed %s | %s", url, exc)
            time.sleep(3 + i * 5)
    return ""


def load_seen() -> set:
    if not SEEN_FILE.exists():
        return set()
    try:
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(seen: set) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")


def keyword_hits(title: str, abstract: str, keywords: List[str]) -> List[str]:
    blob = f"{title} {abstract}".lower()
    hits = []
    for kw in keywords:
        if kw.lower() in blob:
            hits.append(kw)
    return hits


def supplement_from_page(session: requests.Session, link: str) -> Dict[str, str]:
    out = {"doi": "", "abstract": "", "published": "", "pdf_url": ""}
    if not link:
        return out
    html_text = get_text(session, link)
    if not html_text:
        return out
    soup = BeautifulSoup(html_text, "lxml")
    meta_map = {
        "doi": [
            {"name": "citation_doi"}, {"name": "dc.Identifier"}, {"name": "DC.Identifier"}
        ],
        "abstract": [
            {"name": "citation_abstract"}, {"name": "description"}, {"property": "og:description"},
            {"name": "dc.Description"}, {"name": "DC.Description"}
        ],
        "published": [
            {"name": "citation_publication_date"}, {"name": "citation_online_date"},
            {"property": "article:published_time"}, {"name": "dc.Date"}, {"name": "DC.Date"}
        ],
        "pdf_url": [
            {"name": "citation_pdf_url"}, {"property": "og:pdf"}
        ],
    }
    for key, metas in meta_map.items():
        for attrs in metas:
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                val = tag.get("content", "")
                if key == "pdf_url":
                    out[key] = urljoin(link, val.strip())
                else:
                    out[key] = clean_text(val)
                break
    if not out["doi"]:
        out["doi"] = extract_doi(html_text)
    # common PDF anchors
    if not out["pdf_url"]:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = clean_text(a.get_text(" ")).lower()
            if ".pdf" in href.lower() or "pdf" == text or "download pdf" in text:
                out["pdf_url"] = urljoin(link, href)
                break
    return out


def fetch_rss_journal(session: requests.Session, j: Dict[str, Any], keywords: List[str]) -> List[Paper]:
    name = j["name"]
    url = j.get("feed_url")
    if not url:
        return []
    logging.info("RSS: %s", name)
    xml_text = get_text(session, url)
    if not xml_text:
        return []
    feed = feedparser.parse(xml_text)
    papers: List[Paper] = []
    for entry in feed.entries:
        title = clean_text(entry.get("title", ""))
        link = entry.get("link", "") or entry.get("id", "")
        abstract = clean_text(entry.get("summary", "") or entry.get("description", ""))
        published = parse_date(entry.get("published", "") or entry.get("updated", ""))
        doi = extract_doi(" ".join([entry.get("id", ""), entry.get("guid", ""), link, title, abstract]))
        pdf_url = ""
        if (not doi or len(abstract) < 40 or not pdf_url) and link:
            extra = supplement_from_page(session, link)
            doi = doi or extra.get("doi", "")
            abstract = abstract if len(abstract) >= 40 else extra.get("abstract", abstract)
            published = published or parse_date(extra.get("published", ""))
            pdf_url = extra.get("pdf_url", "")
        if not title or not (doi or link):
            continue
        hits = keyword_hits(title, abstract, keywords)
        papers.append(Paper(
            journal=name, title=title, doi=normalize_doi(doi), link=link,
            abstract=abstract, published=published, priority=int(j.get("priority", 0)),
            keywords=hits, source="rss", pdf_url=pdf_url,
        ))
    return papers


def crossref_date_parts(item: Dict[str, Any]) -> str:
    for key in ["published-online", "published-print", "published"]:
        parts = ((item.get(key) or {}).get("date-parts") or [])
        if parts and parts[0]:
            ymd = parts[0]
            if len(ymd) >= 3:
                return f"{ymd[0]:04d}-{ymd[1]:02d}-{ymd[2]:02d}"
            if len(ymd) == 2:
                return f"{ymd[0]:04d}-{ymd[1]:02d}"
            return str(ymd[0])
    return ""


def fetch_crossref_journal(session: requests.Session, j: Dict[str, Any], keywords: List[str], days: int, rows: int = 50) -> List[Paper]:
    name = j["name"]
    logging.info("Crossref: %s", name)
    today = date.today()
    start = today - timedelta(days=days)
    params = {
        "query.container-title": name,
        "filter": f"from-pub-date:{start.isoformat()},until-pub-date:{today.isoformat()},type:journal-article",
        "rows": rows,
        "sort": "published",
        "order": "desc",
        "select": "DOI,title,container-title,abstract,published-print,published-online,published,URL",
    }
    try:
        r = session.get(CROSSREF_API, params=params, timeout=30)
        r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])
    except Exception as exc:
        logging.warning("Crossref failed for %s | %s", name, exc)
        return []
    papers: List[Paper] = []
    for item in items:
        title = clean_text(" ".join(item.get("title", []) or []))
        journal = clean_text(" ".join(item.get("container-title", []) or [])) or name
        if name.lower() not in journal.lower() and journal.lower() not in name.lower():
            continue
        doi = normalize_doi(item.get("DOI", ""))
        link = item.get("URL", "")
        abstract = clean_text(item.get("abstract", ""))
        if not abstract and link:
            extra = supplement_from_page(session, link)
            abstract = extra.get("abstract", "")
        if not title or not doi:
            continue
        hits = keyword_hits(title, abstract, keywords)
        papers.append(Paper(
            journal=name, title=title, doi=doi, link=link,
            abstract=abstract, published=crossref_date_parts(item), priority=int(j.get("priority", 0)),
            keywords=hits, source="crossref",
        ))
    return papers


def score_paper(p: Paper) -> int:
    score = int(p.priority) * 10 + len(p.keywords or []) * 5
    # 化学大类较适合公众号的主题加权
    hot = ["catalysis", "photocatalysis", "electrocatalysis", "degradation", "battery", "hydrogen", "MOF", "COF", "DFT"]
    blob = f"{p.title} {p.abstract}".lower()
    score += sum(3 for h in hot if h.lower() in blob)
    # 没有关键词但顶刊也可以保留低分候选
    return score


def dedupe_and_rank(papers: Iterable[Paper], seen: set, max_candidates: int) -> List[Paper]:
    by_id: Dict[str, Paper] = {}
    for p in papers:
        if not p.unique_id:
            continue
        if p.unique_id in seen:
            continue
        p.score = score_paper(p)
        # 至少命中关键词，或期刊优先级很高
        if not p.keywords and p.priority < 9:
            continue
        if p.unique_id not in by_id or p.score > by_id[p.unique_id].score:
            by_id[p.unique_id] = p
    return sorted(by_id.values(), key=lambda x: (x.score, x.published), reverse=True)[:max_candidates]


def nature_pdf_from_doi_or_url(doi: str, link: str) -> str:
    doi = normalize_doi(doi)
    if doi.startswith("10.1038/"):
        return f"https://www.nature.com/articles/{doi.split('/',1)[1]}.pdf"
    if "nature.com/articles/" in (link or ""):
        clean = link.split("?")[0].rstrip("/")
        if clean.endswith(".pdf"):
            return clean
        return clean + ".pdf"
    return ""


def rule_based_pdf_url(p: Paper) -> str:
    doi = normalize_doi(p.doi)
    if not doi:
        return p.pdf_url or ""
    # Nature Portfolio, including Nat Commun / Communications Chemistry / npj
    u = nature_pdf_from_doi_or_url(doi, p.link)
    if u:
        return u
    # Science Advances DOI pages often support /doi/pdf/<doi>
    if doi.startswith("10.1126/") or "science.org" in p.link:
        return f"https://www.science.org/doi/pdf/{doi}"
    # ACS open access articles can often be requested from /doi/pdf/<doi>
    if doi.startswith("10.1021/") or "pubs.acs.org" in p.link:
        return f"https://pubs.acs.org/doi/pdf/{doi}"
    return p.pdf_url or ""


def unpaywall_pdf_url(session: requests.Session, doi: str, email: str) -> str:
    doi = normalize_doi(doi)
    if not doi or not email:
        return ""
    url = f"{UNPAYWALL_API}/{doi}"
    try:
        r = session.get(url, params={"email": email}, timeout=30)
        if r.status_code != 200:
            logging.info("Unpaywall %s -> %s", doi, r.status_code)
            return ""
        data = r.json()
        best = data.get("best_oa_location") or {}
        if best.get("url_for_pdf"):
            return best["url_for_pdf"]
        for loc in data.get("oa_locations") or []:
            if loc.get("url_for_pdf"):
                return loc["url_for_pdf"]
    except Exception as exc:
        logging.warning("Unpaywall failed for %s | %s", doi, exc)
    return ""


def is_pdf_response(resp: requests.Response) -> bool:
    ctype = resp.headers.get("content-type", "").lower()
    return "application/pdf" in ctype or resp.content[:4] == b"%PDF"


def download_pdf(session: requests.Session, url: str, out_path: Path) -> Tuple[bool, str]:
    if not url:
        return False, "empty pdf url"
    headers = {"Accept": "application/pdf,text/html;q=0.8,*/*;q=0.5", "Referer": "https://doi.org/"}
    for i in range(3):
        try:
            with session.get(url, headers=headers, timeout=60, stream=True, allow_redirects=True) as r:
                if r.status_code != 200:
                    msg = f"http {r.status_code}"
                    logging.info("PDF %s -> %s", url, msg)
                    time.sleep(3 + i * 5)
                    continue
                first = next(r.iter_content(chunk_size=8192), b"")
                ctype = r.headers.get("content-type", "").lower()
                if b"%PDF" not in first[:64] and "application/pdf" not in ctype:
                    return False, f"not pdf, content-type={ctype}"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with out_path.open("wb") as f:
                    f.write(first)
                    for chunk in r.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
                if out_path.stat().st_size < 10_000:
                    return False, "pdf too small"
                return True, "ok"
        except Exception as exc:
            logging.warning("Download failed %s | %s", url, exc)
            time.sleep(3 + i * 5)
    return False, "download failed"


def save_metadata(dir_path: Path, p: Paper) -> None:
    (dir_path / "paper.json").write_text(json.dumps(asdict(p), ensure_ascii=False, indent=2), encoding="utf-8")
    (dir_path / "title.txt").write_text(p.title, encoding="utf-8")
    (dir_path / "source.txt").write_text(
        f"Journal: {p.journal}\nDOI: {p.doi}\nURL: {p.link}\nPDF: {p.pdf_url}\nPublished: {p.published}\nKeywords: {', '.join(p.keywords or [])}\n",
        encoding="utf-8",
    )


def call_workflow(pdf_path: Path, out_dir: Path, word_count: int, max_figures: int) -> Tuple[bool, str]:
    script = ROOT / "workflow" / "paper_wechat_workflow_wechat_style_v3" / "src" / "main.py"
    cmd = [
        sys.executable, str(script),
        "--pdf", str(pdf_path),
        "--out", str(out_dir),
        "--word-count", str(word_count),
        "--max-figures", str(max_figures),
    ]
    logging.info("Run workflow: %s", " ".join(cmd))
    try:
        r = subprocess.run(cmd, cwd=str(script.parent), text=True, capture_output=True, timeout=600)
        log = (r.stdout or "") + "\n" + (r.stderr or "")
        (out_dir / "workflow_run.log").write_text(log, encoding="utf-8")
        if r.returncode != 0:
            return False, f"workflow exit {r.returncode}"
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def write_report(rows: List[Dict[str, str]]) -> None:
    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fields = ["status", "journal", "title", "doi", "published", "score", "keywords", "pdf_url", "article_dir", "message"]
    with REPORT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def main() -> int:
    ensure_dirs()
    parser = argparse.ArgumentParser()
    parser.add_argument("--journals", default=str(CONFIG_DIR / "journals.yml"))
    parser.add_argument("--keywords", default=str(CONFIG_DIR / "keywords.yml"))
    parser.add_argument("--pipeline", default=str(CONFIG_DIR / "pipeline.yml"))
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--max-candidates", type=int, default=None)
    parser.add_argument("--max-downloads", type=int, default=None)
    parser.add_argument("--max-articles", type=int, default=None)
    parser.add_argument("--word-count", type=int, default=None)
    parser.add_argument("--max-figures", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="ignore seen_dois and process recent papers again")
    args = parser.parse_args()

    cfg = load_yaml(Path(args.pipeline))
    days = args.days if args.days is not None else int(cfg.get("lookback_days", 3))
    max_candidates = args.max_candidates if args.max_candidates is not None else int(cfg.get("max_candidates", 12))
    max_downloads = args.max_downloads if args.max_downloads is not None else int(cfg.get("max_downloads", 5))
    max_articles = args.max_articles if args.max_articles is not None else int(cfg.get("max_articles", 2))
    word_count = args.word_count if args.word_count is not None else int(cfg.get("word_count", 1800))
    max_figures = args.max_figures if args.max_figures is not None else int(cfg.get("max_figures", 8))
    email = os.getenv("UNPAYWALL_EMAIL", "").strip() or cfg.get("unpaywall_email_fallback", "")

    journals = load_yaml(Path(args.journals)).get("journals", [])
    keywords = load_yaml(Path(args.keywords)).get("keywords", [])

    seen = set() if args.force else load_seen()
    first_run = not SEEN_FILE.exists()
    session = make_session()
    all_papers: List[Paper] = []
    for j in journals:
        try:
            if j.get("mode") == "rss":
                all_papers.extend(fetch_rss_journal(session, j, keywords))
            else:
                all_papers.extend(fetch_crossref_journal(session, j, keywords, days=days))
        except Exception as exc:
            logging.exception("Fetch failed for %s | %s", j.get("name"), exc)

    candidates = dedupe_and_rank(all_papers, seen=seen, max_candidates=max_candidates)
    CANDIDATES_JSON.write_text(json.dumps([asdict(p) for p in candidates], ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Candidates after filtering: %d", len(candidates))

    rows: List[Dict[str, str]] = []
    articles_done = 0
    downloads_tried = 0

    if first_run and not bool(cfg.get("push_on_first_run", False)) and not args.force:
        # 首次只建立去重库，防止一下子处理历史 RSS 文章。
        for p in all_papers:
            if p.unique_id:
                seen.add(p.unique_id)
        save_seen(seen)
        rows.append({"status": "first_run_index_only", "message": "首次运行仅建立 DOI 库；如需测试请手动 Run workflow 并加 --force。"})
        write_report(rows)
        logging.info("First run: indexed %d papers, no article generated.", len(seen))
        return 0

    for p in candidates:
        if downloads_tried >= max_downloads or articles_done >= max_articles:
            break
        downloads_tried += 1
        p.pdf_url = rule_based_pdf_url(p)
        if not p.pdf_url and p.doi:
            p.pdf_url = unpaywall_pdf_url(session, p.doi, email=email)
        # Unpaywall 再兜底一次，避免规则链接被拒。
        pdf_hash = hashlib.md5((p.doi or p.link or p.title).encode("utf-8")).hexdigest()[:8]
        slug = f"{articles_done + 1:03d}_{safe_slug(p.title, 55)}_{pdf_hash}"
        dl_dir = DOWNLOAD_DIR / slug
        pdf_path = dl_dir / "paper.pdf"
        ok, msg = download_pdf(session, p.pdf_url, pdf_path)
        if not ok and p.doi:
            alt = unpaywall_pdf_url(session, p.doi, email=email)
            if alt and alt != p.pdf_url:
                p.pdf_url = alt
                ok, msg = download_pdf(session, p.pdf_url, pdf_path)
        if not ok:
            rows.append({
                "status": "download_failed", "journal": p.journal, "title": p.title, "doi": p.doi,
                "published": p.published, "score": str(p.score), "keywords": "; ".join(p.keywords or []),
                "pdf_url": p.pdf_url, "message": msg,
            })
            seen.add(p.unique_id)
            continue
        save_metadata(dl_dir, p)
        out_dir = ARTICLE_DIR / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        save_metadata(out_dir, p)
        ok2, msg2 = call_workflow(pdf_path, out_dir, word_count=word_count, max_figures=max_figures)
        if not ok2:
            rows.append({
                "status": "workflow_failed", "journal": p.journal, "title": p.title, "doi": p.doi,
                "published": p.published, "score": str(p.score), "keywords": "; ".join(p.keywords or []),
                "pdf_url": p.pdf_url, "article_dir": str(out_dir.relative_to(ROOT)), "message": msg2,
            })
            seen.add(p.unique_id)
            continue
        rows.append({
            "status": "success", "journal": p.journal, "title": p.title, "doi": p.doi,
            "published": p.published, "score": str(p.score), "keywords": "; ".join(p.keywords or []),
            "pdf_url": p.pdf_url, "article_dir": str(out_dir.relative_to(ROOT)), "message": "ok",
        })
        seen.add(p.unique_id)
        articles_done += 1

    if not rows:
        rows.append({"status": "no_new_paper", "message": "本轮没有符合条件的新开放论文。"})
    write_report(rows)
    save_seen(seen)
    logging.info("Done. Articles generated: %d", articles_done)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
