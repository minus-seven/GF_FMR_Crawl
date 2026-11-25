# -*- coding: utf-8 -*-
"""
GaoTeng latest fund document downloader.

Target page example:
  https://www.gaotengasset.com/zh-HK/product-services/fund-details?fund_id=10001#3

The fund detail page is a React app. It loads fund documents from the public
reportListNoToken API. This script calls that API each run, selects the latest
matching document, and downloads the current PDF URL.

Usage:
  python download_gaoteng_factsheet.py
  python download_gaoteng_factsheet.py 10001
  python download_gaoteng_factsheet.py "https://www.gaotengasset.com/zh-HK/product-services/fund-details?fund_id=10001#3"
  python download_gaoteng_factsheet.py 10001 monthly zh-hk
"""

import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, finalize_pdf_to_monthly_path


DEFAULT_PAGE_URL = "https://www.gaotengasset.com/zh-HK/product-services/fund-details?fund_id=10001#3"
DEFAULT_FUND_ID = "10001"
DEFAULT_DOC_TYPE = "monthly"
DEFAULT_LANG = "zh-hk"
WEB_BASE_URL = "https://www.gaotengasset.com"
API_BASE_URL = "https://api.gaotengasset.com/gt/website"
COMPANY = "gaoteng"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Origin": WEB_BASE_URL,
    "Referer": WEB_BASE_URL + "/",
}

REPORT_TYPES = {
    "finance": 1,
    "financial": 1,
    "report": 1,
    "sale": 2,
    "sales": 2,
    "offering": 2,
    "monthly": 3,
    "month": 3,
    "factsheet": 3,
    "fs": 3,
    "notice": 4,
    "close": 5,
}

SESSION = requests.Session()
configure_session(SESSION)


def safe_filename(value):
    value = unquote(value or "").strip()
    value = re.sub(r"\s+", "_", value)
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def parse_date(value):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
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


def report_type_value(doc_type):
    key = str(doc_type or "").strip().lower()
    if key.isdigit():
        return int(key)
    if key not in REPORT_TYPES:
        choices = ", ".join(sorted(REPORT_TYPES))
        raise RuntimeError(f"Unknown GaoTeng document type '{doc_type}'. Choices: {choices}, or numeric 1-5.")
    return REPORT_TYPES[key]


def lang_from_url(page_url):
    parts = [part for part in urlparse(page_url).path.split("/") if part]
    for part in parts:
        if part.lower() in ("zh-hk", "zh-cn", "en-us"):
            return part.lower()
    return DEFAULT_LANG


def fund_id_from_url(page_url):
    query = parse_qs(urlparse(page_url).query)
    return (query.get("fund_id") or query.get("fundId") or [""])[0]


def fetch_json(url):
    resp = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != 1000:
        raise RuntimeError(f"GaoTeng API returned {data.get('status')}: {data.get('message')}")
    return data.get("data") or {}


def fetch_fund_name(fund_id, lang):
    url = f"{API_BASE_URL}/productService?lang={lang}&fund_id={fund_id}"
    items = fetch_json(url).get("list") or []
    for item in items:
        if str(item.get("fundId")) == str(fund_id):
            return str(item.get("fundName") or "").strip()
    return ""


def fetch_report_list(fund_id, doc_type, lang):
    type_value = report_type_value(doc_type)
    url = f"{API_BASE_URL}/reportListNoToken?lang={lang}&fundId={fund_id}&type={type_value}"
    return fetch_json(url).get("reportList") or [], type_value


def select_latest_report(reports, keyword=""):
    matches = []
    keyword = (keyword or "").strip()
    for report in reports:
        name = str(report.get("name") or "")
        if keyword and keyword not in name:
            continue
        if not str(report.get("url") or "").lower().endswith(".pdf"):
            continue
        matches.append(report)

    if not matches:
        raise RuntimeError(f"No GaoTeng PDF report matched keyword='{keyword}'.")

    matches.sort(key=lambda item: parse_date(item.get("date")), reverse=True)
    return matches[0]


def normalize_pdf_url(pdf_url):
    return urljoin(WEB_BASE_URL, pdf_url)


def archive_tag(pdf_url, report_date):
    path = urlparse(pdf_url).path
    file_id = os.path.splitext(os.path.basename(path))[0]
    if file_id:
        return safe_filename(file_id)
    return date_tag(report_date)


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
    fund_id = DEFAULT_FUND_ID
    doc_type = DEFAULT_DOC_TYPE
    lang = DEFAULT_LANG
    keyword = ""

    if not args:
        return page_url, fund_id, doc_type, lang, keyword

    first = args[0].strip()
    if first.startswith("http://") or first.startswith("https://"):
        page_url = first
        fund_id = fund_id_from_url(first) or DEFAULT_FUND_ID
        lang = lang_from_url(first)
        if len(args) > 1:
            doc_type = args[1].strip()
        if len(args) > 2:
            lang = args[2].strip().lower()
        if len(args) > 3:
            keyword = args[3].strip()
    else:
        fund_id = first
        page_url = f"https://www.gaotengasset.com/{lang}/product-services/fund-details?fund_id={fund_id}#3"
        if len(args) > 1:
            doc_type = args[1].strip()
        if len(args) > 2:
            lang = args[2].strip().lower()
            page_url = f"https://www.gaotengasset.com/{lang}/product-services/fund-details?fund_id={fund_id}#3"
        if len(args) > 3:
            keyword = args[3].strip()

    return page_url, fund_id, doc_type, lang, keyword


def main():
    page_url, fund_id, doc_type, lang, keyword = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    print(f"[*] Page: {page_url}")
    print(f"[*] Fund ID: {fund_id}")
    print(f"[*] Document type: {doc_type}")
    print(f"[*] Language: {lang}")

    fund_name = fetch_fund_name(fund_id, lang)
    reports, type_value = fetch_report_list(fund_id, doc_type, lang)
    report = select_latest_report(reports, keyword)
    pdf_url = normalize_pdf_url(report["url"])
    report_name = str(report.get("name") or f"type_{type_value}")
    report_date = str(report.get("date") or "")
    fund_label = "_".join(part for part in [fund_id, fund_name, report_name] if part)

    print(f"[*] Selected: {report_name} / {report_date}")
    print(f"[*] PDF: {pdf_url}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=SAVE_DIR) as tmp:
        tmp_path = tmp.name
    try:
        size, final_url = download_pdf(pdf_url, tmp_path)
        output_path, report_month, moved = finalize_pdf_to_monthly_path(
            tmp_path,
            COMPANY,
            fund_label,
            fallback_date=report_date,
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
