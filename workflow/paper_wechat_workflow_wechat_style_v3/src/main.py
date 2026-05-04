import argparse
from pathlib import Path

from tools.pdf_tool import extract_text
from tools.figure_extractor import extract_figures
from tools.article_writer import build_article
from tools.exporter import save_outputs


def main():
    parser = argparse.ArgumentParser(description="Paper PDF to reference-WeChat-push style article v4")
    parser.add_argument("--pdf", required=True, help="Input PDF path")
    parser.add_argument("--out", default="outputs", help="Output directory")
    parser.add_argument("--word-count", type=int, default=1800)
    parser.add_argument("--max-figures", type=int, default=8)
    args = parser.parse_args()

    pdf_path = Path(args.pdf).resolve()
    out_dir = Path(args.out).resolve()
    images_dir = out_dir / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    raw_text, meta = extract_text(pdf_path)
    figures = extract_figures(pdf_path, images_dir, max_figures=args.max_figures)
    article = build_article(raw_text=raw_text, meta=meta, figures=figures, word_count=args.word_count)
    save_outputs(out_dir=out_dir, article=article, figures=figures, meta=meta)

    print(f"Done. Outputs written to: {out_dir}")
    print(f"Packaged zip: {out_dir / 'outputs.zip'}")
    print(f"Figures extracted: {len(figures)}")
    for fig in figures:
        print(f"  - Figure {fig['number']}: {fig['image_path']}  [{fig['method']}]")

if __name__ == "__main__":
    main()
