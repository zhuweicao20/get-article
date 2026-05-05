#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Publish a generated article package to WeChat Official Account via the official MP API.

Default behavior:
- Finds the newest folder under output/articles that contains article_rich.html/article.html.
- Uploads local images used in the HTML to WeChat using media/uploadimg and rewrites <img src>.
- Uploads a cover image as permanent material to obtain thumb_media_id.
- Creates a WeChat draft with draft/add.
- If --mode publish is used, submits the draft to freepublish/submit.

Required GitHub Secrets / environment variables:
- WECHAT_APPID
- WECHAT_APPSECRET

Optional:
- WECHAT_AUTHOR
- WECHAT_SOURCE_URL
- WECHAT_NEED_OPEN_COMMENT: 0 or 1
- WECHAT_ONLY_FANS_CAN_COMMENT: 0 or 1
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None

ROOT = Path(__file__).resolve().parents[1]
ARTICLE_NAME_PRIORITY = ("article_rich.html", "article-rich.html", "article.html", "index.html")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
RESULT_PATH = ROOT / "output" / "wechat_publish_result.json"


def log(msg: str) -> None:
    print(msg, flush=True)


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required secret/env: {name}")
    return value


def clean_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", BeautifulSoup(text, "lxml").get_text(" ", strip=True)).strip()


def article_html_file(article_dir: Path) -> Optional[Path]:
    for name in ARTICLE_NAME_PRIORITY:
        p = article_dir / name
        if p.exists():
            return p
    html_files = sorted([*article_dir.glob("*.html"), *article_dir.glob("*.htm")])
    html_files = [p for p in html_files if "wx_ready" not in p.name.lower()]
    return html_files[0] if html_files else None


def find_article_dirs(article_root: Path) -> List[Path]:
    if not article_root.exists():
        return []
    dirs = []
    for p in article_root.iterdir():
        if p.is_dir() and article_html_file(p):
            dirs.append(p)
    return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)


def choose_article_dir(article_root: Path, article_dir_arg: str) -> Path:
    if article_dir_arg:
        p = Path(article_dir_arg)
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        if not p.exists():
            # also allow a folder name under article_root
            alt = article_root / article_dir_arg
            if alt.exists():
                p = alt.resolve()
        if not p.exists():
            raise SystemExit(f"Article directory not found: {article_dir_arg}")
        if not article_html_file(p):
            raise SystemExit(f"No article HTML found in: {p}")
        return p
    dirs = find_article_dirs(article_root)
    if not dirs:
        raise SystemExit(f"No generated article folder found under: {article_root}")
    return dirs[0]


def load_metadata(article_dir: Path, soup: BeautifulSoup) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    paper_json = article_dir / "paper.json"
    if paper_json.exists():
        try:
            data = json.loads(paper_json.read_text(encoding="utf-8"))
            meta.update({k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))})
        except Exception:
            pass
    title = ""
    title_txt = article_dir / "title.txt"
    if title_txt.exists():
        title = title_txt.read_text(encoding="utf-8", errors="replace").strip()
    if not title:
        h1 = soup.find("h1")
        title = clean_text(str(h1)) if h1 else article_dir.name
    digest = meta.get("abstract", "") or meta.get("summary", "") or ""
    if not digest:
        ps = [clean_text(str(p)) for p in soup.find_all("p")]
        digest = " ".join([p for p in ps if len(p) > 20])[:160]
    digest = clean_text(digest)[:120]
    return {
        "title": clean_text(title)[:64] or "自动生成科研图文",
        "digest": digest,
        "source_url": os.getenv("WECHAT_SOURCE_URL", "").strip() or meta.get("link", "") or meta.get("url", ""),
        "author": os.getenv("WECHAT_AUTHOR", "").strip() or "",
    }


def get_access_token(appid: str, secret: str) -> str:
    url = "https://api.weixin.qq.com/cgi-bin/token"
    r = requests.get(url, params={"grant_type": "client_credential", "appid": appid, "secret": secret}, timeout=30)
    data = r.json()
    if "access_token" not in data:
        raise SystemExit(f"Failed to get WeChat access_token: {data}")
    return data["access_token"]


def guess_mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "image/jpeg"


def resolve_image_path(article_dir: Path, src: str) -> Optional[Path]:
    if not src or re.match(r"^(https?:)?//", src) or src.startswith("data:"):
        return None
    raw = src.split("?", 1)[0].split("#", 1)[0]
    candidates = [article_dir / raw, article_dir / Path(raw).name]
    for sub in ("images", "image", "imgs", "img"):
        candidates.append(article_dir / sub / Path(raw).name)
    for c in candidates:
        try:
            c = c.resolve()
            c.relative_to(article_dir.resolve())
        except Exception:
            continue
        if c.exists() and c.is_file():
            return c
    name = Path(raw).name.lower()
    for sub in [article_dir, article_dir / "images", article_dir / "image", article_dir / "imgs", article_dir / "img"]:
        if sub.exists():
            for p in sub.rglob("*"):
                if p.is_file() and p.name.lower() == name:
                    return p
    return None


def data_uri_to_temp(src: str) -> Optional[Path]:
    if not src.startswith("data:image/") or ";base64," not in src:
        return None
    header, encoded = src.split(",", 1)
    m = re.search(r"data:(image/[A-Za-z0-9.+-]+);base64", header)
    mime = m.group(1) if m else "image/png"
    ext = mimetypes.guess_extension(mime) or ".png"
    fd, name = tempfile.mkstemp(prefix="wechat_img_", suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(base64.b64decode(encoded))
    return Path(name)


def upload_content_image(access_token: str, image_path: Path) -> str:
    url = "https://api.weixin.qq.com/cgi-bin/media/uploadimg"
    with image_path.open("rb") as f:
        files = {"media": (image_path.name, f, guess_mime(image_path))}
        r = requests.post(url, params={"access_token": access_token}, files=files, timeout=60)
    data = r.json()
    if "url" not in data:
        raise RuntimeError(f"uploadimg failed for {image_path.name}: {data}")
    return data["url"]


def upload_cover_material(access_token: str, image_path: Path) -> str:
    url = "https://api.weixin.qq.com/cgi-bin/material/add_material"
    with image_path.open("rb") as f:
        files = {"media": (image_path.name, f, guess_mime(image_path))}
        r = requests.post(url, params={"access_token": access_token, "type": "image"}, files=files, timeout=60)
    data = r.json()
    if "media_id" not in data:
        raise RuntimeError(f"cover add_material failed for {image_path.name}: {data}")
    return data["media_id"]


def create_fallback_cover(article_dir: Path, title: str) -> Path:
    out = article_dir / "wechat_cover_auto.png"
    if Image is None:
        raise SystemExit("No image found for cover and Pillow is unavailable; cannot create cover.")
    img = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 34)
        small = ImageFont.truetype("DejaVuSans.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
        small = ImageFont.load_default()
    draw.rectangle([0, 0, 900, 500], fill=(247, 250, 252))
    draw.rectangle([0, 0, 900, 12], fill=(19, 124, 209))
    draw.text((58, 58), "Research Highlight", fill=(19, 124, 209), font=small)
    y = 130
    for line in textwrap.wrap(title, width=28)[:5]:
        draw.text((58, y), line, fill=(16, 24, 32), font=font)
        y += 50
    draw.text((58, 430), "Auto generated by GitHub Actions", fill=(110, 118, 126), font=small)
    img.save(out)
    return out


def prepare_html_and_images(access_token: str, article_dir: Path, html_path: Path, soup: BeautifulSoup) -> Tuple[str, Path, List[str]]:
    for tag in soup(["script", "style", "meta", "title"]):
        tag.decompose()
    body = soup.body or soup
    uploaded: List[str] = []
    first_local_image: Optional[Path] = None
    temp_files: List[Path] = []
    try:
        for img in body.find_all("img"):
            src = img.get("src", "")
            local = resolve_image_path(article_dir, src)
            if local is None and src.startswith("data:image/"):
                local = data_uri_to_temp(src)
                if local:
                    temp_files.append(local)
            if local is None:
                # Remote images are usually not accepted by WeChat content unless already on allowlist.
                # Keep them, but mark in result.
                continue
            if first_local_image is None:
                first_local_image = local
            wx_url = upload_content_image(access_token, local)
            img["src"] = wx_url
            img["data-src"] = wx_url
            uploaded.append(str(local))
            time.sleep(0.2)
        content = "".join(str(x) for x in body.contents)
    finally:
        for p in temp_files:
            try:
                p.unlink()
            except Exception:
                pass
    if not first_local_image:
        # Search any image file as cover even if not referenced.
        for sub in [article_dir / "images", article_dir / "image", article_dir / "imgs", article_dir / "img", article_dir]:
            if sub.exists():
                imgs = [p for p in sub.rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS]
                if imgs:
                    first_local_image = imgs[0]
                    break
    if first_local_image is None:
        title = clean_text(str(body.find("h1") or "自动生成科研图文"))
        first_local_image = create_fallback_cover(article_dir, title)
    return content, first_local_image, uploaded


def add_draft(access_token: str, article: Dict[str, Any]) -> str:
    url = "https://api.weixin.qq.com/cgi-bin/draft/add"
    r = requests.post(url, params={"access_token": access_token}, json={"articles": [article]}, timeout=60)
    data = r.json()
    if "media_id" not in data:
        raise SystemExit(f"draft/add failed: {data}")
    return data["media_id"]


def submit_publish(access_token: str, draft_media_id: str) -> Dict[str, Any]:
    url = "https://api.weixin.qq.com/cgi-bin/freepublish/submit"
    r = requests.post(url, params={"access_token": access_token}, json={"media_id": draft_media_id}, timeout=60)
    data = r.json()
    if data.get("errcode") not in (0, "0", None) or "publish_id" not in data:
        raise SystemExit(f"freepublish/submit failed: {data}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--article-root", default=str(ROOT / "output" / "articles"))
    parser.add_argument("--article-dir", default="", help="Specific article folder or folder name under article-root")
    parser.add_argument("--mode", choices=["draft", "publish"], default="draft")
    parser.add_argument("--cover", default="", help="Optional cover image path")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    article_root = Path(args.article_root)
    if not article_root.is_absolute():
        article_root = (ROOT / article_root).resolve()
    article_dir = choose_article_dir(article_root, args.article_dir)
    html_path = article_html_file(article_dir)
    assert html_path is not None
    log(f"Selected article dir: {article_dir}")
    log(f"Selected html: {html_path.name}")

    raw = html_path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "lxml")
    meta = load_metadata(article_dir, soup)

    result: Dict[str, Any] = {
        "article_dir": str(article_dir.relative_to(ROOT)) if article_dir.is_relative_to(ROOT) else str(article_dir),
        "html": html_path.name,
        "title": meta["title"],
        "mode": args.mode,
        "dry_run": args.dry_run,
    }

    if args.dry_run:
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    appid = env_required("WECHAT_APPID")
    secret = env_required("WECHAT_APPSECRET")
    access_token = get_access_token(appid, secret)

    content, auto_cover, uploaded_images = prepare_html_and_images(access_token, article_dir, html_path, soup)
    cover_path = Path(args.cover).resolve() if args.cover else auto_cover
    if not cover_path.exists():
        raise SystemExit(f"Cover image not found: {cover_path}")
    thumb_media_id = upload_cover_material(access_token, cover_path)

    need_open_comment = int(os.getenv("WECHAT_NEED_OPEN_COMMENT", "0") or "0")
    only_fans_can_comment = int(os.getenv("WECHAT_ONLY_FANS_CAN_COMMENT", "0") or "0")
    article = {
        "title": meta["title"],
        "author": meta["author"],
        "digest": meta["digest"],
        "content": content,
        "content_source_url": meta["source_url"],
        "thumb_media_id": thumb_media_id,
        "need_open_comment": need_open_comment,
        "only_fans_can_comment": only_fans_can_comment,
    }
    draft_media_id = add_draft(access_token, article)
    result.update({
        "status": "draft_created",
        "draft_media_id": draft_media_id,
        "thumb_media_id": thumb_media_id,
        "cover": str(cover_path),
        "uploaded_content_images": uploaded_images,
    })
    if args.mode == "publish":
        pub = submit_publish(access_token, draft_media_id)
        result.update({"status": "publish_submitted", "publish": pub})

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
