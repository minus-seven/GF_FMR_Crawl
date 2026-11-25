# -*- coding: utf-8 -*-
"""
GF Fund Hong Kong monthly report downloader.

Target page example:
  http://www.gffunds.com.hk/ch/product/funds/202205/t20220511_374730.shtml

The page builds its "資料下載" list from inline JavaScript records. This script
fetches the page each run, extracts all exposed document records, filters the
requested fund's monthly reports, and downloads every matching PDF. If the page
later exposes more historical monthly reports, they will be downloaded too.

Usage:
  python download_gffunds_factsheet.py
  python download_gffunds_factsheet.py "http://www.gffunds.com.hk/ch/product/funds/202205/t20220511_374730.shtml"
  python download_gffunds_factsheet.py "廣發全球精選股票基金" "月報"
"""

import html
import os
import re
import shutil
import sys
from datetime import datetime
from urllib.parse import unquote, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path


DEFAULT_PAGE_URL = "http://www.gffunds.com.hk/ch/product/funds/202205/t20220511_374730.shtml"
DEFAULT_FUND_KEYWORD = "廣發全球精選股票基金"
DEFAULT_DOC_KEYWORD = "月報"
COMPANY = "gffunds"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": DEFAULT_PAGE_URL,
}

SESSION = requests.Session()
configure_session(SESSION)


def safe_filename(value):
    value = unquote(value or "").strip()
    value = re.sub(r"\s+", "_", value)
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def parse_datetime(value):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            pass
    return datetime.min


def date_tag(value):
    parsed = parse_datetime(value)
    if parsed == datetime.min:
        return safe_filename(value or "latest")
    return parsed.strftime("%Y-%m-%d")


def fetch_page(page_url):
    resp = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.content.decode("utf-8", errors="replace")


def normalize_js_html(value):
    value = value.replace("' +", "")
    value = value.replace("+'", "")
    value = value.replace("'", "")
    value = re.sub(r"\s+", " ", value)
    return html.unescape(value)


def extract_documents(page_html):
    pattern = re.compile(
        r"var \$insert = \$\(\s*'(.*?)'\s*\)\s*var crtime = '([^']+)'",
        re.S,
    )
    docs = []
    for block, crtime in pattern.findall(page_html):
        fragment = normalize_js_html(block)
        href_match = re.search(r'href="([^"]+)"', fragment)
        title_match = re.search(r'title="([^"]+)"', fragment)
        if not href_match or not title_match:
            continue
        docs.append(
            {
                "title": title_match.group(1),
                "url": href_match.group(1),
                "crtime": crtime,
            }
        )
    return docs


def select_monthly_reports(docs, fund_keyword, doc_keyword):
    matches = []
    for doc in docs:
        title = doc["title"]
        if fund_keyword not in title:
            continue
        if doc_keyword not in title:
            continue
        if not doc["url"].lower().endswith(".pdf"):
            continue
        matches.append(doc)

    matches.sort(key=lambda item: parse_datetime(item["crtime"]), reverse=True)
    return matches


def archive_tag(pdf_url):
    stem = os.path.splitext(os.path.basename(urlparse(pdf_url).path))[0]
    return safe_filename(stem or "latest")


def download_pdf(url, dest):
    tmp = dest + ".part"
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=True) as resp:
        resp.raise_for_status()
        final_url = resp.url
        with open(tmp, "wb") as fh:
            first = True
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"Downloaded content is not a PDF: {final_url}")
                first = False
                fh.write(chunk)
    os.replace(tmp, dest)
    return os.path.getsize(dest), final_url


def resolve_args(args):
    page_url = DEFAULT_PAGE_URL
    fund_keyword = DEFAULT_FUND_KEYWORD
    doc_keyword = DEFAULT_DOC_KEYWORD

    if not args:
        return page_url, fund_keyword, doc_keyword

    first = args[0].strip()
    if first.startswith(("http://", "https://")):
        page_url = first
        if len(args) > 1:
            fund_keyword = args[1].strip()
        if len(args) > 2:
            doc_keyword = args[2].strip()
    else:
        fund_keyword = first
        if len(args) > 1:
            doc_keyword = args[1].strip()
        if len(args) > 2:
            page_url = args[2].strip()

    return page_url, fund_keyword, doc_keyword


def main():
    page_url, fund_keyword, doc_keyword = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    print(f"[*] Page: {page_url}")
    print(f"[*] Fund keyword: {fund_keyword}")
    print(f"[*] Document keyword: {doc_keyword}")

    docs = extract_documents(fetch_page(page_url))
    reports = select_monthly_reports(docs, fund_keyword, doc_keyword)
    if not reports:
        available = "; ".join(doc["title"] for doc in docs if fund_keyword in doc["title"])
        raise RuntimeError(f"No GF Fund monthly reports matched. Available fund docs: {available}")

    print(f"[*] Matched reports: {len(reports)}")
    for index, report in enumerate(reports):
        archive_path = dated_output_path(COMPANY, report["title"], report["crtime"])

        print(f"[*] Report: {report['title']} / {report['crtime']}")
        print(f"[*] PDF: {report['url']}")
        size, final_url = download_pdf(report["url"], archive_path)
        print(f"[*] Saved ({size / 1024:.1f} KB) -> {os.path.basename(archive_path)}")
        print(f"[*] Final URL: {final_url}")

    print(f"[*] Save dir: {SAVE_DIR}")


if __name__ == "__main__":
    main()
