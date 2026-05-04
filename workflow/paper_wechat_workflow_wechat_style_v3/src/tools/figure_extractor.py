from pathlib import Path
import re
import fitz


FIG_RE = re.compile(r"^(?:Figure|Fig\.)\s*(\d+)[\.\s:]", re.I)


def _block_text(block):
    if block.get("type") != 0:
        return ""
    parts = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            parts.append(span.get("text", ""))
        parts.append(" ")
    return "".join(parts).strip()


def _merge_rects(rects):
    if not rects:
        return None
    r = fitz.Rect(rects[0])
    for x in rects[1:]:
        r |= fitz.Rect(x)
    return r


def _enlarge(rect, page_rect, margin=8):
    r = fitz.Rect(rect)
    r.x0 = max(page_rect.x0, r.x0 - margin)
    r.y0 = max(page_rect.y0, r.y0 - margin)
    r.x1 = min(page_rect.x1, r.x1 + margin)
    r.y1 = min(page_rect.y1, r.y1 + margin)
    return r


def _caption_blocks(page):
    caps = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        s = _block_text(b)
        m = FIG_RE.search(s)
        if m:
            caps.append({
                "number": int(m.group(1)),
                "bbox": fitz.Rect(b["bbox"]),
                "caption": s,
            })
    return caps


def _image_blocks(page):
    return [fitz.Rect(b["bbox"]) for b in page.get_text("dict")["blocks"] if b.get("type") == 1]


def _crop_by_image_blocks(page, caption_bbox):
    """Prefer exact embedded image blocks above a caption."""
    imgs = _image_blocks(page)
    if not imgs:
        return None

    # Candidate images above the figure caption.
    candidates = []
    for r in imgs:
        # Usually figure image is immediately above caption.
        if r.y1 <= caption_bbox.y0 + 8:
            # Avoid tiny publisher logos / footer images.
            area = r.width * r.height
            if area > 5000 and r.width > page.rect.width * 0.20:
                candidates.append(r)

    if not candidates:
        return None

    # Take images whose bottom is nearest to caption top.
    candidates = sorted(candidates, key=lambda r: abs(caption_bbox.y0 - r.y1))
    nearest = candidates[0]
    selected = [nearest]

    # Merge sibling image blocks aligned with nearest, useful for multi-panel figures.
    for r in candidates[1:]:
        vertical_close = abs(r.y1 - nearest.y1) < 120 or abs(r.y0 - nearest.y0) < 120
        overlaps_band = not (r.y1 < nearest.y0 - 80 or r.y0 > nearest.y1 + 80)
        if vertical_close or overlaps_band:
            selected.append(r)

    return _merge_rects(selected)


def _fallback_crop(page, caption_bbox):
    """Fallback for vector figures where PyMuPDF reports no image blocks."""
    page_rect = page.rect
    top_margin = max(page_rect.y0 + 45, caption_bbox.y0 - page_rect.height * 0.50)
    # Crop figure area only; caption itself is not included.
    r = fitz.Rect(page_rect.x0 + 45, top_margin, page_rect.x1 - 45, caption_bbox.y0 - 5)
    if r.height < 60:
        return None
    return r


def extract_figures(pdf_path: Path, images_dir: Path, max_figures=8, dpi=220):
    doc = fitz.open(str(pdf_path))
    figures = []
    seen = set()

    for page_index, page in enumerate(doc):
        caps = _caption_blocks(page)
        if not caps:
            continue

        for cap in caps:
            n = cap["number"]
            if n in seen:
                continue

            rect = _crop_by_image_blocks(page, cap["bbox"])
            method = "image-block"
            if rect is None:
                rect = _fallback_crop(page, cap["bbox"])
                method = "fallback-page-crop"

            if rect is None:
                continue

            rect = _enlarge(rect, page.rect, margin=8)
            out_path = images_dir / f"figure_{n}.png"
            matrix = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=matrix, clip=rect, alpha=False)
            pix.save(str(out_path))

            figures.append({
                "number": n,
                "page": page_index + 1,
                "image_path": str(out_path),
                "relative_image_path": f"images/{out_path.name}",
                "caption": cap["caption"],
                "bbox": [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)],
                "method": method,
            })
            seen.add(n)

            if len(figures) >= max_figures:
                return sorted(figures, key=lambda x: x["number"])

    return sorted(figures, key=lambda x: x["number"])
