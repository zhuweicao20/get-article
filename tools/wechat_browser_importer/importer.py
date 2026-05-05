#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = ROOT / ".local_wechat_profile"
ARTICLE_NAMES = ("article_wx_ready.html", "article_rich.html", "article.html", "index.html")


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
    return sorted([p for p in article_root.iterdir() if p.is_dir() and find_html(p)], key=lambda p: p.stat().st_mtime, reverse=True)


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
    path = article_dir / "title.txt"
    if path.exists():
        title = path.read_text(encoding="utf-8", errors="replace").strip()
        if title:
            return title[:64]
    for name in ("paper.json", "article.json"):
        path = article_dir / name
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                title = str(data.get("title") or data.get("article", {}).get("title") or data.get("meta", {}).get("title") or "").strip()
                if title:
                    return title[:64]
            except Exception:
                pass
    h1 = soup.find("h1")
    return (clean_text(str(h1)) if h1 else article_dir.name)[:64]


def simplify_html(raw: str) -> str:
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "meta", "title"]):
        tag.decompose()
    return str(soup.body or soup)


def logged_in(page) -> bool:
    return any(page.locator(f"text={marker}").count() for marker in ("首页", "草稿箱", "新的创作", "图文消息", "素材库"))


def wait_for_login(page, timeout_ms: int) -> None:
    page.goto("https://mp.weixin.qq.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    if logged_in(page):
        print("WeChat login state is available.")
        return
    print("Scan QR code once. Login state will stay in .local_wechat_profile/.")
    try:
        page.wait_for_function(
            "() => document.body && !/扫码|登录|二维码|Scan/i.test(document.body.innerText)",
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(3000)


def open_editor(page) -> None:
    for url in (
        "https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=10&lang=zh_CN",
        "https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=10",
    ):
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        if "login" not in page.url.lower():
            return


def fill_first(page, selectors: list[str], value: str) -> bool:
    for selector in selectors:
        loc = page.locator(selector).first
        if not loc.count():
            continue
        try:
            loc.fill(value, timeout=3000)
            return True
        except Exception:
            try:
                loc.evaluate("(el, value) => { el.innerText = value; el.value = value; el.dispatchEvent(new Event('input', {bubbles:true})); }", value)
                return True
            except Exception:
                pass
    return False


def fill_editor(page, html: str) -> bool:
    for selector in ("#ueditor_0", ".ProseMirror", ".ql-editor", '[contenteditable="true"]'):
        loc = page.locator(selector).last
        if not loc.count():
            continue
        try:
            loc.evaluate(
                "(el, html) => { el.innerHTML = html; el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertHTML', data: html})); el.dispatchEvent(new Event('change', {bubbles:true})); }",
                html,
            )
            return True
        except Exception:
            pass
    for frame in page.frames:
        try:
            frame.locator("body").evaluate("(el, html) => { el.innerHTML = html; el.dispatchEvent(new Event('input', {bubbles:true})); }", html)
            return True
        except Exception:
            pass
    return False


def click_save_draft(page) -> bool:
    for selector in ("text=保存为草稿", "text=保存草稿", "text=存为草稿", "text=保存"):
        loc = page.locator(selector).last
        if not loc.count():
            continue
        try:
            loc.click(timeout=3000)
            page.wait_for_timeout(3000)
            return True
        except Exception:
            pass
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--article-root", default=str(ROOT / "output" / "articles"))
    parser.add_argument("--article-dir", default="")
    parser.add_argument("--login-only", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--publish", action="store_true", help="Reserved; automatic mass sending is not implemented")
    args = parser.parse_args()

    article_dir: Optional[Path] = None
    title = ""
    content_html = ""
    if not args.login_only:
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
        context = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False, viewport={"width": 1400, "height": 900})
        page = context.pages[0] if context.pages else context.new_page()
        wait_for_login(page, 180_000)
        if args.login_only:
            print("Login profile is ready. Close the browser when the MP dashboard is visible.")
            page.pause()
            context.close()
            return 0
        print("Opening draft editor.")
        open_editor(page)
        title_ok = fill_first(page, ['input[placeholder*="标题"]', 'textarea[placeholder*="标题"]', '[contenteditable="true"][placeholder*="标题"]'], title)
        body_ok = fill_editor(page, content_html)
        save_ok = False if args.no_save else click_save_draft(page)
        print(f"Loaded article: {article_dir}")
        print(f"title_filled={title_ok} body_filled={body_ok} draft_save_clicked={save_ok}")
        if args.publish:
            print("--publish was passed, but automatic mass sending is intentionally not implemented.")
        if not (title_ok and body_ok and save_ok):
            page.pause()
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
