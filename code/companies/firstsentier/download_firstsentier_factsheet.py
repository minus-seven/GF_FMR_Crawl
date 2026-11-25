# -*- coding: utf-8 -*-
"""
First Sentier Investors Hong Kong latest factsheet downloader.

Target literature page:
  https://www.firstsentierinvestors.com/hk/zh/retail/performance/literature.html

The factsheet table contains stable PDF links that are overwritten monthly.
This script locates the target factsheet link from the table, downloads the PDF,
and extracts the factsheet date from the PDF text for local archiving.

Usage:
  python download_firstsentier_factsheet.py
  python download_firstsentier_factsheet.py "FSSA-China-Growth-Fund"
  python download_firstsentier_factsheet.py "https://www.firstsentierinvestors.com/hk/zh/retail/performance/literature.html" "FSSA-China-Growth-Fund"
  python download_firstsentier_factsheet.py "https://www.firstsentierinvestors.com/.../FSSA-China-Growth-Fund-USD-I-zh-hk-IE0008368742.pdf"
"""

import os
import re
import sys
import tempfile
from urllib.parse import urljoin, urlparse

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, finalize_pdf_to_monthly_path


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")


COMPANY = "firstsentier"
DEFAULT_PAGE_URL = "https://www.firstsentierinvestors.com/hk/zh/retail/performance/literature.html"
DEFAULT_KEYWORD = "FSSA-China-Growth-Fund"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
TIMEOUT_MS = 90000
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

SESSION = requests.Session()
configure_session(SESSION)

DATE_RE = re.compile(r"(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月\s*([0-3]?\d)\s*日")
MONTH_RE = re.compile(r"(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月")


def safe_filename(value):
    value = re.sub(r"\s+", "_", str(value or "").strip())
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def slug_from_pdf(url):
    name = os.path.splitext(os.path.basename(urlparse(url).path))[0]
    name = re.sub(r"-zh-hk-[A-Z0-9]+$", "", name, flags=re.I)
    return safe_filename(name or DEFAULT_KEYWORD)


def date_from_pdf(pdf_path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return None

    text = "\n".join((page.extract_text() or "") for page in PdfReader(pdf_path).pages[:2])
    match = DATE_RE.search(text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = MONTH_RE.search(text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-01"
    return None


def find_pdf_from_page(page_url, keyword):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="zh-HK", user_agent=HEADERS["User-Agent"], ignore_https_errors=True)
        page.goto(page_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        page.wait_for_timeout(10000)
        links = page.evaluate(
            """() => Array.from(document.querySelectorAll('.factsheets-table a[href], a[href*="PdfFundFactsheets"]')).map(a => {
                const row = a.closest('tr');
                return {
                    href: a.href,
                    text: (a.innerText || a.textContent || '').trim(),
                    rowText: (row && row.innerText || '').trim()
                };
            })"""
        )
        browser.close()

    candidates = []
    keyword_lower = keyword.lower()
    for link in links:
        href = urljoin(page_url, link.get("href") or "")
        blob = f"{href} {link.get('text') or ''} {link.get('rowText') or ''}".lower()
        if href.lower().endswith(".pdf") and keyword_lower in blob:
            candidates.append(href)
    if not candidates:
        raise RuntimeError(f"No First Sentier factsheet PDF matched keyword '{keyword}' on {page_url}")
    return candidates[0]


def resolve_args(args):
    page_url = DEFAULT_PAGE_URL
    keyword = DEFAULT_KEYWORD
    if not args:
        return page_url, keyword

    first = args[0].strip()
    if first.lower().endswith(".pdf"):
        return first, None
    if first.startswith("http://") or first.startswith("https://"):
        page_url = first
        if len(args) > 1:
            keyword = args[1].strip()
    else:
        keyword = first
    return page_url, keyword


def download_pdf(url, tmp_path):
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=True) as resp:
        resp.raise_for_status()
        final_url = resp.url
        with open(tmp_path, "wb") as fh:
            first_chunk = True
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first_chunk and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"Downloaded content is not a PDF: {final_url}")
                first_chunk = False
                fh.write(chunk)
    return os.path.getsize(tmp_path), final_url


def main():
    source, keyword = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    pdf_url = source if keyword is None else find_pdf_from_page(source, keyword)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=SAVE_DIR) as tmp:
        tmp_path = tmp.name
    try:
        size, final_url = download_pdf(pdf_url, tmp_path)
        tag = date_from_pdf(tmp_path) or slug_from_pdf(final_url)
        output_path, report_month, moved = finalize_pdf_to_monthly_path(
            tmp_path,
            COMPANY,
            slug_from_pdf(final_url),
            fallback_date=tag,
        )
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    print(f"[*] Source: {source}")
    print(f"[*] Keyword: {keyword or '(direct PDF)'}")
    print(f"[*] PDF: {pdf_url}")
    print(f"[*] Final URL: {final_url}")
    print(f"[*] Date tag: {tag}")
    if moved:
        print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month}] -> {output_path}")
    else:
        print(f"[*] Existing [{report_month}], skipped -> {output_path}")


if __name__ == "__main__":
    main()
