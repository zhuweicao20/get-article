from pathlib import Path
import re
import fitz


def _clean_text(text: str) -> str:
    text = text.replace("\u0000", "")
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(pdf_path: Path):
    doc = fitz.open(str(pdf_path))
    page_texts = []
    for i, page in enumerate(doc, start=1):
        txt = page.get_text("text")
        page_texts.append(f"\n\n[Page {i}]\n{txt}")

    raw = _clean_text("\n".join(page_texts))

    title = ""
    # Most ACS papers have title in first 15 lines.
    for line in raw.splitlines()[:30]:
        s = line.strip()
        if len(s) > 20 and not s.startswith("[Page") and not s.startswith("Cite This"):
            title = s
            break

    abstract = ""
    m = re.search(r"ABSTRACT:\s*(.*?)(?:\n\s*■\s*INTRODUCTION|\n\s*INTRODUCTION)", raw, flags=re.S | re.I)
    if m:
        abstract = re.sub(r"\s+", " ", m.group(1)).strip()

    return raw, {
        "title": title,
        "abstract": abstract,
        "page_count": len(doc),
        "source_pdf": str(pdf_path),
    }
