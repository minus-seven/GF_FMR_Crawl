# -*- coding: utf-8 -*-
"""
Foresight Fund Hong Kong latest monthly report downloader.

Target page example:
  https://www.foresightfund.com.hk/gmjj

The public page loads fund documents from the ry-api prod/report endpoint.
This script calls that API each run, selects the latest matching report by
publish date, and downloads the current PDF.

Usage:
  python download_foresight_factsheet.py
  python download_foresight_factsheet.py 1
  python download_foresight_factsheet.py 1 yb simplified
  python download_foresight_factsheet.py "https://www.foresightfund.com.hk/gmjj"
"""

import os
import re
import shutil
import sys
from datetime import datetime
from urllib.parse import unquote, urljoin, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path


DEFAULT_PAGE_URL = "https://www.foresightfund.com.hk/gmjj"
DEFAULT_PRODUCT_ID = "1"
DEFAULT_REPORT_TYPE = "yb"
DEFAULT_LANG = "simplified"
API_BASE_URL = "https://webhk.foresightfund.com.hk/ry-api/prod"
FILE_BASE_URL = "https://webhk.foresightfund.com.hk"
COMPANY = "foresight"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": DEFAULT_PAGE_URL,
    "Origin": "https://www.foresightfund.com.hk",
}

REPORT_TYPES = {
    "monthly": "yb",
    "month": "yb",
    "factsheet": "yb",
    "fs": "yb",
    "yb": "yb",
    "notice": "gg",
    "announcement": "gg",
    "gg": "gg",
}

SESSION = requests.Session()
configure_session(SESSION)


def safe_filename(value):
    value = unquote(value or "").strip()
    value = re.sub(r"\s+", "_", value)
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def parse_date(value):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            pass
    return datetime.min


def date_tag(value):
    parsed = parse_date(value)
    if parsed == datetime.min:
        return safe_filename(value or "latest")
    return parsed.strftime("%Y-%m-%d")


def normalize_report_type(value):
    key = str(value or "").strip().lower()
    return REPORT_TYPES.get(key, key or DEFAULT_REPORT_TYPE)


def fetch_json(url):
    resp = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_product_name(product_id):
    url = f"{API_BASE_URL}/prodinfo?id={product_id}"
    data = fetch_json(url).get("data") or {}
    return (
        data.get("PROD_NAME_TC")
        or data.get("PROD_NAME")
        or data.get("PROD_NAME_EN")
        or f"foresight_{product_id}"
    )


def fetch_reports(product_id, report_type, lang):
    report_type = normalize_report_type(report_type)
    url = (
        f"{API_BASE_URL}/report?id={product_id}&type={report_type}"
        f"&pageNo=1&pageSize=50&LANG={lang}"
    )
    data = fetch_json(url)
    return data.get("list") or [], report_type


def select_latest_report(reports, keyword=""):
    matches = []
    keyword = (keyword or "").strip()
    for item in reports:
        title = str(item.get("TITLE") or item.get("FILENAME") or "")
        path = str(item.get("PATH") or "")
        if keyword and keyword not in title:
            continue
        if not path.lower().endswith(".pdf"):
            continue
        matches.append(item)

    if not matches:
        raise RuntimeError(f"No Foresight PDF report matched keyword='{keyword}'.")

    matches.sort(key=lambda item: parse_date(item.get("PUBLISHDATE")), reverse=True)
    return matches[0]


def pdf_url_from_path(path):
    return urljoin(FILE_BASE_URL, path)


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
    product_id = DEFAULT_PRODUCT_ID
    report_type = DEFAULT_REPORT_TYPE
    lang = DEFAULT_LANG
    keyword = ""

    if not args:
        return page_url, product_id, report_type, lang, keyword

    first = args[0].strip()
    if first.startswith("http://") or first.startswith("https://"):
        page_url = first
        if len(args) > 1:
            product_id = args[1].strip()
        if len(args) > 2:
            report_type = args[2].strip()
        if len(args) > 3:
            lang = args[3].strip()
        if len(args) > 4:
            keyword = args[4].strip()
    else:
        product_id = first
        if len(args) > 1:
            report_type = args[1].strip()
        if len(args) > 2:
            lang = args[2].strip()
        if len(args) > 3:
            keyword = args[3].strip()

    return page_url, product_id, report_type, lang, keyword


def main():
    page_url, product_id, report_type, lang, keyword = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    print(f"[*] Page: {page_url}")
    print(f"[*] Product ID: {product_id}")
    print(f"[*] Report type: {report_type}")
    print(f"[*] Language: {lang}")

    product_name = fetch_product_name(product_id)
    reports, normalized_type = fetch_reports(product_id, report_type, lang)
    report = select_latest_report(reports, keyword)
    title = str(report.get("TITLE") or report.get("FILENAME") or normalized_type)
    publish_date = str(report.get("PUBLISHDATE") or "")
    pdf_url = pdf_url_from_path(report["PATH"])
    output_path = dated_output_path(COMPANY, f"{product_name}_{title}", publish_date)

    print(f"[*] Selected: {title} / {publish_date}")
    print(f"[*] PDF: {pdf_url}")

    size, final_url = download_pdf(pdf_url, output_path)

    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")


if __name__ == "__main__":
    main()
