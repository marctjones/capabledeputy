#!/usr/bin/env python3
"""Fetch the Process Mechanics CORE / PRO + Model Monster reference pages,
strip site chrome, and render clean PDFs.

Used with the author's (Van Lindberg) permission to retain reference
copies of the CORE+PRO framework material. Outputs land in
docs/vendor/process-mechanics/ as both cleaned .html and .pdf.

Run:  python3 scripts/fetch_core_pro.py
Deps: system python3 (bs4 + lxml), curl, weasyprint.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

# weasyprint's pyenv shim only resolves under the version that has it
# installed; prefer the explicit binary, fall back to PATH.
WEASYPRINT = next(
    (
        p
        for p in (
            str(Path.home() / ".pyenv/versions/3.12.9/bin/weasyprint"),
            shutil.which("weasyprint") or "",
        )
        if p and Path(p).exists()
    ),
    "weasyprint",
)

OUT = Path("docs/vendor/process-mechanics")
UA = "Mozilla/5.0 (X11; Linux x86_64) reference-archival"

PAGES = [
    ("https://processmechanics.com/core/", "core"),
    ("https://modelmonster.ai/", "modelmonster-home"),
    ("https://modelmonster.ai/glossary/", "modelmonster-glossary"),
    ("https://modelmonster.ai/risk-registry/", "modelmonster-risk-registry"),
]

# Classes/ids whose subtrees are site chrome, not content.
CHROME_HINTS = (
    "masthead",
    "footer",
    "nav",
    "menu",
    "cookie",
    "sidebar",
    "breadcrumb",
    "skip-link",
    "site-header",
    "site-footer",
)

PRINT_CSS = """
@page { size: A4; margin: 18mm 16mm; @bottom-center {
  content: "Process Mechanics — reference copy · " counter(page); font-size: 8pt; color:#888; } }
body { font: 11pt/1.5 'DejaVu Serif', Georgia, serif; color:#1a1a1a; max-width: 100%; }
h1 { font-size: 20pt; border-bottom: 2px solid #333; padding-bottom: 4pt; }
h2 { font-size: 15pt; margin-top: 18pt; color:#222; }
h3 { font-size: 12.5pt; color:#333; }
h1,h2,h3,h4 { font-family:'DejaVu Sans', Helvetica, sans-serif; page-break-after: avoid; }
code,pre { font-family:'DejaVu Sans Mono', monospace; font-size:9.5pt; background:#f4f4f4; }
pre { padding:8pt; border-radius:4pt; white-space:pre-wrap; }
table { border-collapse: collapse; width:100%; font-size:9.5pt; }
th,td { border:1px solid #bbb; padding:4pt 6pt; text-align:left; vertical-align:top; }
th { background:#eee; }
a { color:#0b5; text-decoration:none; }
.src-banner { font-family:'DejaVu Sans',sans-serif; font-size:9pt; color:#666;
  border:1px solid #ddd; background:#fafafa; padding:8pt; border-radius:4pt; margin-bottom:14pt; }
img { max-width:100%; }
"""


def clean(raw: str, url: str) -> str:
    soup = BeautifulSoup(raw, "lxml")
    title = (soup.title.string if soup.title and soup.title.string else url).strip()

    for tag in soup(["script", "style", "noscript", "svg", "form", "button", "iframe"]):
        tag.decompose()
    for tag in soup.find_all(["nav", "header", "footer"]):
        tag.decompose()
    for tag in soup.find_all(attrs={"class": True}):
        cls = " ".join(tag.get("class", [])).lower()
        if any(h in cls for h in CHROME_HINTS):
            tag.decompose()
    for tag in soup.find_all(attrs={"id": True}):
        if any(h in str(tag.get("id", "")).lower() for h in CHROME_HINTS):
            tag.decompose()

    main = soup.find("main") or soup.body or soup
    body_html = main.decode_contents() if hasattr(main, "decode_contents") else str(main)

    banner = (
        f'<div class="src-banner"><b>Reference copy.</b> Source: '
        f'<a href="{url}">{url}</a><br>Retrieved {date.today().isoformat()} · '
        f"Process Mechanics PLLC / Van Lindberg · retained with permission for internal reference.</div>"  # noqa: E501
    )
    return (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title><style>{PRINT_CSS}</style></head><body>"
        f"<h1>{title}</h1>{banner}{body_html}</body></html>"
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ok = 0
    for url, slug in PAGES:
        try:
            raw = subprocess.run(
                ["curl", "-sSL", "-A", UA, "--max-time", "45", url],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            if len(raw) < 500:
                print(f"  ! {slug}: short response ({len(raw)}B) — skipping")
                continue
            html_path = OUT / f"{slug}.html"
            pdf_path = OUT / f"{slug}.pdf"
            html_path.write_text(clean(raw, url), encoding="utf-8")
            subprocess.run(
                [WEASYPRINT, str(html_path), str(pdf_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            kb = pdf_path.stat().st_size // 1024
            print(f"  ✓ {slug}: {html_path}  +  {pdf_path} ({kb} KB)")
            ok += 1
        except subprocess.CalledProcessError as e:
            print(f"  ✗ {slug}: {e.stderr[:300] if e.stderr else e}")
    print(f"done: {ok}/{len(PAGES)} pages")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
