#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = ROOT / ".local_wechat_profile"
ARTICLE_NAMES = ("article_rich.html", "article.html", "index.html")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(value or "", "lxml").get_text(" ", strip=True)).strip()


def find_html(article_dir: Path) -> Optional[Path]:
    for name in ARTICLE_NAMES:
        path = article_dir / name
        if path.exists():
            return path
    html_files = sorted(article_dir.glob("*.html"))
    return html_files[0] if html_files else None


def article_dirs(article_root: Path) -> list[Path]:
    if not article_root.exists():
        return []
    return sorted(
        [p for p in article_root.iterdir() if p.is_dir() and find_html(p)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def choose_article(article_root: Path, article_dir: str) -> Path:
    if article_dir:
        path = Path(article_dir)
        if not path.is_absolute():
            path = article_root / article_dir
        if not path.exists():
            raise SystemExit(f"Article directory not found: {article_dir}")
        return path
    dirs = article_dirs(article_root)
    if not dirs:
        raise SystemExit(f"No generated article folder found under {article_root}")
    return dirs[0]


def load_title(article_dir: Path, soup: BeautifulSoup) -> str:
    title_txt = article_dir / "title.txt"
    if title_txt.exists():
        title = title_txt.read_text(encoding="utf-8", errors="replace").strip()
        if title:
            return title[:64]
    paper_json = article_dir / "paper.json"
    if paper_json.exists():
        try:
            title = str(json.loads(paper_json.read_text(encoding="utf-8")).get("title", "")).strip()
            if title:
                return title[:64]
        except Exception:
            pass
    h1 = soup.find("h1")
    return (clean_text(str(h1)) if h1 else article_dir.name)[:64]


def simplify_html(raw: str) -> str:
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "meta", "title"]):
        tag.decompose()
    return str(soup.body or soup)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--article-root", default=str(ROOT / "output" / "articles"))
    parser.add_argument("--article-dir", default="")
    parser.add_argument("--publish", action="store_true", help="Explicitly publish instead of only saving a draft")
    args = parser.parse_args()

    article_root = Path(args.article_root)
    if not article_root.is_absolute():
        article_root = (ROOT / article_root).resolve()
    article_dir = choose_article(article_root, args.article_dir)
    html_path = find_html(article_dir)
    if not html_path:
        raise SystemExit(f"No article HTML found in {article_dir}")

    raw = html_path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "lxml")
    title = load_title(article_dir, soup)
    content_html = simplify_html(raw)

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://mp.weixin.qq.com/", wait_until="domcontentloaded")
        print("Scan QR code and finish login in the opened browser.")
        try:
            page.wait_for_url(re.compile(r".*mp\.weixin\.qq\.com.*"), timeout=180_000)
        except PlaywrightTimeoutError:
            pass

        print("Opening draft editor. If WeChat changes the URL, navigate manually to New Draft; the script will keep the browser open.")
        page.goto("https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=10&lang=zh_CN", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        title_selectors = [
            'input[placeholder*="标题"]',
            'textarea[placeholder*="标题"]',
            '[contenteditable="true"][placeholder*="标题"]',
        ]
        for selector in title_selectors:
            loc = page.locator(selector).first
            if loc.count():
                try:
                    loc.fill(title)
                    break
                except Exception:
                    pass

        editor_selectors = [
            "#ueditor_0",
            ".ProseMirror",
            '[contenteditable="true"]',
            "iframe",
        ]
        filled = False
        for selector in editor_selectors:
            loc = page.locator(selector).first
            if not loc.count():
                continue
            try:
                if selector == "iframe":
                    frame = loc.content_frame()
                    if frame:
                        frame.locator("body").evaluate("(el, html) => el.innerHTML = html", content_html)
                        filled = True
                else:
                    loc.evaluate("(el, html) => el.innerHTML = html", content_html)
                    filled = True
                if filled:
                    break
            except Exception:
                continue

        print(f"Loaded article: {article_dir}")
        print("Default is draft only. Review in browser, upload/choose cover if needed, then click Save Draft manually if the page did not auto-save.")
        if args.publish:
            print("--publish was passed, but auto mass-send is intentionally not implemented. Submit manually after review.")
        page.pause()
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
