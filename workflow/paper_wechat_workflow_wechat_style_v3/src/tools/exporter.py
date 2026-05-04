import json
import html
import re
import shutil
from pathlib import Path


def _inline_md(text: str) -> str:
    s = html.escape(text)
    # Strong emphasis in the reference article often appears as bold + blue/orange emphasis.
    def repl(m):
        inner = m.group(1)
        # Orange for highlighted result sentences; blue for names/metadata-like emphasis.
        if re.search(r"成功|提高|降低|实现|达到|最新|关键|突破|创新|重要|Nature|Science|JACS", inner, re.I):
            return f'<strong class="em-orange">{inner}</strong>'
        return f'<strong class="em-blue">{inner}</strong>'
    s = re.sub(r"\*\*(.+?)\*\*", repl, s)
    return s


def _md_to_html_basic(md: str) -> str:
    lines = md.splitlines()
    out = []
    meta_lines = []
    for line in lines:
        s = line.strip()
        if not s:
            if meta_lines:
                out.append('<section class="meta-card">' + ''.join(meta_lines) + '</section>')
                meta_lines = []
            continue
        if s.startswith("# "):
            out.append(f"<h1>{_inline_md(s[2:])}</h1>")
        elif re.match(r"^(高分子科学前沿|作者|来源|20\d{2}年|\S+　20\d{2}年)", s):
            out.append(f'<p class="account-row">{_inline_md(s)}</p>')
        elif re.match(r"^(第一作者|通讯作者|通讯单位|论文 DOI|期刊信息)：", s):
            meta_lines.append(f"<p>{_inline_md(s)}</p>")
        elif s.startswith("!["):
            if meta_lines:
                out.append('<section class="meta-card">' + ''.join(meta_lines) + '</section>')
                meta_lines = []
            alt = s[s.find("[")+1:s.find("]")]
            path = s[s.find("(")+1:s.find(")")]
            out.append(f'<figure class="paper-figure"><img src="{html.escape(path)}" alt="{html.escape(alt)}" /></figure>')
        elif re.match(r"^图\s*\d+\s*[｜:：]", s):
            out.append(f'<p class="fig-caption">{_inline_md(s)}</p>')
        elif s.startswith("## "):
            # Keep compatibility with old markdown; render as plain bold paragraph, not decorative card.
            out.append(f'<p class="plain-heading">{_inline_md(s[3:])}</p>')
        else:
            if meta_lines:
                out.append('<section class="meta-card">' + ''.join(meta_lines) + '</section>')
                meta_lines = []
            out.append(f"<p>{_inline_md(s)}</p>")
    if meta_lines:
        out.append('<section class="meta-card">' + ''.join(meta_lines) + '</section>')
    return "\n".join(out)


def save_outputs(out_dir: Path, article: dict, figures: list, meta: dict):
    out_dir.mkdir(parents=True, exist_ok=True)
    md = article["body_markdown"]

    for name in ["article.md", "article_rich.md"]:
        (out_dir / name).write_text(md, encoding="utf-8")

    css = """
    :root { --text: #1f1f1f; --muted: #8a8a8a; --blue: #1b75d0; --orange: #ff6a00; }
    body {
      max-width: 720px;
      margin: 0 auto;
      padding: 26px 15px 48px;
      font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', Arial, sans-serif;
      color: var(--text);
      background: #fff;
      line-height: 1.82;
      letter-spacing: .2px;
    }
    h1 {
      margin: 0 0 12px;
      font-size: 22px;
      line-height: 1.35;
      font-weight: 700;
      color: #111;
    }
    .account-row {
      margin: 0 0 26px;
      color: #9a9a9a;
      font-size: 14px;
      line-height: 1.45;
    }
    .meta-card {
      margin: 0 0 18px;
      padding: 0;
      color: #666;
    }
    .meta-card p {
      margin: 0 0 3px;
      font-size: 14px;
      line-height: 1.55;
      color: #666;
    }
    p {
      margin: 0 0 17px;
      font-size: 16px;
      line-height: 1.82;
      text-align: justify;
    }
    .plain-heading {
      font-size: 17px;
      font-weight: 700;
      margin-top: 22px;
      margin-bottom: 12px;
      color: #111;
    }
    strong { font-weight: 700; }
    .em-blue { color: var(--blue); }
    .em-orange { color: var(--orange); }
    .paper-figure {
      margin: 24px 0 10px;
      text-align: center;
    }
    .paper-figure img {
      display: block;
      width: 100%;
      height: auto;
      margin: 0 auto;
      border: 0;
      border-radius: 0;
      box-shadow: none;
      background: #fff;
    }
    .fig-caption {
      margin: 0 0 20px;
      color: #333;
      font-size: 14px;
      line-height: 1.75;
      text-align: justify;
    }
    @media print {
      body { max-width: 720px; padding: 12px; }
      h1 { font-size: 22px; }
    }
    """
    html_doc = f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(article.get('title','article'))}</title><style>{css}</style></head><body>{_md_to_html_basic(md)}</body></html>"
    for name in ["article.html", "article_rich.html"]:
        (out_dir / name).write_text(html_doc, encoding="utf-8")

    try:
        from weasyprint import HTML
        HTML(string=html_doc, base_url=str(out_dir)).write_pdf(out_dir / "article.pdf")
    except Exception as exc:
        (out_dir / "pdf_export_warning.txt").write_text(f"PDF export skipped: {exc}\n", encoding="utf-8")

    (out_dir / "article.json").write_text(json.dumps({
        "meta": meta,
        "article": article,
        "figures": figures,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "selected_charts.json").write_text(json.dumps(figures, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "review_report.json").write_text(json.dumps(article["review_report"], ensure_ascii=False, indent=2), encoding="utf-8")

    # Package everything for one-click download/import.
    # Create the archive outside out_dir first; otherwise outputs.zip can be packed into itself on reruns.
    zip_target = out_dir / "outputs.zip"
    tmp_base = out_dir.parent / f".{out_dir.name}_outputs_tmp"
    if zip_target.exists():
        zip_target.unlink()
    tmp_zip = Path(shutil.make_archive(str(tmp_base), "zip", root_dir=str(out_dir)))
    tmp_zip.replace(zip_target)
