"""Turn report/report.md into report/report.html and report/report.pdf (A4).

    .venv\\Scripts\\python report\\build_report.py

Edit report.md (plain Markdown), then run this again. It prints the page count
(the hackathon limit is 6 pages).
"""
from __future__ import annotations

import re
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent

FONT_CSS = ""  # the PDF engine uses its built-in Helvetica; unsupported symbols are replaced below

CSS = """
@page { size: A4; margin: 1.5cm 1.6cm 1.5cm 1.6cm;
        @frame footer { -pdf-frame-content: footer; bottom: 0.6cm; margin-left: 1.6cm; margin-right: 1.6cm; height: 0.6cm; } }
body { font-family: Body, Helvetica, Arial, sans-serif; font-size: 9.4pt; line-height: 1.32; color: #1b1f24; }
h1 { font-size: 17pt; margin: 0 0 2pt 0; color: #7a2e0c; }
h2 { font-size: 11.5pt; margin: 9pt 0 3pt 0; color: #7a2e0c; border-bottom: 0.6pt solid #d9c7bb; padding-bottom: 1pt; }
p { margin: 0 0 4pt 0; }
ul, ol { margin: 0 0 4pt 0; padding-left: 14pt; }
li { margin: 0 0 1.5pt 0; }
table { border-collapse: collapse; width: 100%; margin: 3pt 0 6pt 0; }
th, td { border: 0.5pt solid #c9ced6; padding: 2pt 3pt; font-size: 8.2pt; text-align: left; vertical-align: top; }
th { background-color: #f3eee9; }
img { width: 100%; }
code { font-family: Courier; font-size: 8.4pt; }
.cap { font-size: 7.8pt; color: #5b6470; margin: 1pt 0 6pt 0; }
"""


def build():
    md = (HERE / "report.md").read_text(encoding="utf-8")
    # Markdown needs an empty line before a list; add it when the writer forgot
    lines, out = md.splitlines(), []
    is_item = lambda t: re.match(r"^\s*(- |\d+\. )", t) is not None  # noqa: E731
    for i, line in enumerate(lines):
        if re.match(r"^  (- |\d+\. )", line):  # 2-space sub-items -> 4 spaces (Markdown needs 4 to nest)
            line = "  " + line
        if is_item(line) and out and out[-1].strip() and not is_item(out[-1]) and not out[-1].startswith("  "):
            out.append("")
        out.append(line)
    md = "\n".join(out)
    body = markdown.markdown(md, extensions=["tables"])
    # images: give them a caption line from their alt text and a fixed width for the PDF

    def figure(m):
        alt, src = m.group(1), m.group(2)
        width = "11.5cm" if "sitrep_map" in src else "17.5cm"
        return f'<p style="text-align:center"><img src="{src}" style="width:{width}"></p><p class="cap">{alt}</p>'
    body = re.sub(r'<p><img alt="([^"]*)" src="([^"]+)" ?/?></p>', figure, body)
    html = (f"<!doctype html><html><head><meta charset='utf-8'><title>Flood Damage from Space - report</title>"
            f"<style>{FONT_CSS}{CSS}</style></head><body>{body}"
            f"<div id='footer' style='font-size:7pt;color:#777'>Flood Damage from Space · Multimodal AI Hackathon 2026 · Track B</div>"
            f"</body></html>")
    (HERE / "report.html").write_text(html, encoding="utf-8")
    # the PDF engine's built-in font only has the Windows-1252 characters: write the rest in plain text
    plain = {"≥": "&gt;=", "≤": "&lt;=", "→": "-&gt;", "−": "-", "Δ": "d", "…": "...", "✓": "", "≈": "~"}
    for ch in set(html):
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            html = html.replace(ch, plain.get(ch, ""))

    from xhtml2pdf import pisa

    def link_callback(uri, rel):
        p = (HERE / uri).resolve()
        return str(p) if p.exists() else uri

    with open(HERE / "report.pdf", "wb") as f:
        result = pisa.CreatePDF(html, dest=f, link_callback=link_callback, encoding="utf-8")
    if result.err:
        raise SystemExit("PDF failed")
    from pypdf import PdfReader
    pages = len(PdfReader(HERE / "report.pdf").pages)
    print(f"report.pdf: {pages} pages" + ("  (OK, limit is 6)" if pages <= 6 else "  (TOO LONG - limit is 6)"))


if __name__ == "__main__":
    build()
