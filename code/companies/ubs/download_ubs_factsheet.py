# -*- coding: utf-8 -*-
"""
UBS Hong Kong latest fund document downloader.

Target page example:
  https://www.ubs.com/hk/tc/assetmanagement/funds/asset-class.html

The UBS page is protected from simple HTTP clients, but renders the fund table
in a browser. This script uses Playwright to read the current table, finds a
fund by ISIN or name keyword, selects the requested document label, and
downloads the linked PDF.

Usage:
  python download_ubs_factsheet.py
  python download_ubs_factsheet.py HK0000318599
  python download_ubs_factsheet.py HK0000318599 "基金月報"
  python download_ubs_factsheet.py "瑞銀(香港)中國機會股票基金" "產品資料概要"
"""

import os
import re
import shutil
import sys
import tempfile
from urllib.parse import unquote, urlparse

from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import company_data_dir, finalize_pdf_to_monthly_path


DEFAULT_PAGE_URL = "https://www.ubs.com/hk/tc/assetmanagement/funds/asset-class.html"
DEFAULT_FUND_KEY = "HK0000318599"
DEFAULT_DOC_KEYWORD = "基金月報"
COMPANY = "ubs"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
TIMEOUT_MS = 90000
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": DEFAULT_PAGE_URL,
}

def safe_filename(value):
    value = unquote(value or "").strip()
    value = re.sub(r"\s+", "_", value)
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def archive_tag(pdf_url):
    stem = os.path.splitext(os.path.basename(urlparse(pdf_url).path))[0]
    return safe_filename(stem or "latest")


def latest_document(fund_key, doc_keyword):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            locale="zh-HK",
            user_agent=HEADERS["User-Agent"],
            ignore_https_errors=True,
        )
        page.goto(DEFAULT_PAGE_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        page.wait_for_selector("#app table tbody tr", timeout=TIMEOUT_MS)

        rows = page.locator("#app table tbody tr")
        for index in range(rows.count()):
            row = rows.nth(index)
            row_text = row.inner_text(timeout=3000)
            if fund_key not in row_text:
                continue

            cells = row.locator("td")
            fund_name = cells.nth(1).inner_text(timeout=3000).strip() if cells.count() > 1 else fund_key

            available = []
            for cell_index in range(cells.count()):
                cell = cells.nth(cell_index)
                cell_text = cell.inner_text(timeout=3000).strip()
                anchor = cell.locator("a").first
                if anchor.count() == 0:
                    continue
                href = anchor.get_attribute("href") or ""
                available.append(cell_text)
                if doc_keyword in cell_text and href.lower().endswith(".pdf"):
                    browser.close()
                    return {
                        "fund": fund_name,
                        "label": doc_keyword,
                        "url": href,
                    }

            browser.close()
            raise RuntimeError(
                f"UBS fund matched '{fund_key}', but no document matched '{doc_keyword}'. "
                f"Available: {'; '.join(available)}"
            )

        browser.close()
    raise RuntimeError(f"No UBS fund row matched '{fund_key}'.")


def download_pdf(url, dest):
    tmp = dest + ".part"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            accept_downloads=True,
            locale="zh-HK",
            user_agent=HEADERS["User-Agent"],
            ignore_https_errors=True,
        )
        with page.expect_download(timeout=TIMEOUT_MS) as download_info:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS, referer=DEFAULT_PAGE_URL)
            except Exception as exc:
                if "Download is starting" not in str(exc):
                    browser.close()
                    raise
        download = download_info.value
        download.save_as(tmp)
        browser.close()

    with open(tmp, "rb") as fh:
        if not fh.read(5).startswith(b"%PDF-"):
            raise RuntimeError(f"Downloaded content is not a PDF: {url}")

    os.replace(tmp, dest)
    return os.path.getsize(dest), url


def resolve_args(args):
    fund_key = DEFAULT_FUND_KEY
    doc_keyword = DEFAULT_DOC_KEYWORD
    if len(args) > 0:
        fund_key = args[0].strip()
    if len(args) > 1:
        doc_keyword = args[1].strip()
    return fund_key, doc_keyword


def main():
    fund_key, doc_keyword = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    doc = latest_document(fund_key, doc_keyword)

    print(f"[*] Page: {DEFAULT_PAGE_URL}")
    print(f"[*] Fund key: {fund_key}")
    print(f"[*] Fund: {doc['fund']}")
    print(f"[*] Selected: {doc['label']}")
    print(f"[*] PDF: {doc['url']}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=SAVE_DIR) as tmp:
        tmp_path = tmp.name
    try:
        size, final_url = download_pdf(doc["url"], tmp_path)
        output_path, report_month, moved = finalize_pdf_to_monthly_path(
            tmp_path,
            COMPANY,
            f"{fund_key}_{doc['fund']}_{doc['label']}",
            fallback_date=doc["url"],
        )
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    print(f"[*] Final URL: {final_url}")
    if moved:
        print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month}] -> {output_path}")
    else:
        print(f"[*] Existing [{report_month}], skipped -> {output_path}")


if __name__ == "__main__":
    main()
