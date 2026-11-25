# -*- coding: utf-8 -*-
"""
FSMOne Hong Kong latest fund factsheet downloader.

Target page example:
  https://www.fsmone.com.hk/funds/fund-info/factsheet/...?...&fund=HKHFCEF

FSMOne exposes the latest factsheet at a stable URL:
  https://www.fsmone.com.hk/admin/buy/factsheet/ch/factsheet<FUND>.pdf

The stable PDF is overwritten when the fund factsheet is updated, so this script
downloads the current PDF and extracts the document month from the PDF text for
local archiving.

Usage:
  python download_fsmone_factsheet.py HKHFCEF
  python download_fsmone_factsheet.py "https://www.fsmone.com.hk/funds/fund-info/factsheet/...?...&fund=HKHFCEF"
"""

import os
import re
import sys
import tempfile
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlparse

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, finalize_pdf_to_monthly_path


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")


COMPANY = "fsmone"
DEFAULT_FUND = "HKHFCEF"
PDF_TEMPLATE = "https://www.fsmone.com.hk/admin/buy/factsheet/ch/factsheet{fund}.pdf"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*;q=0.8",
}

SESSION = requests.Session()
configure_session(SESSION)

FUND_RE = re.compile(r"^[A-Z0-9]{3,20}$", re.I)
CHINESE_MONTH_RE = re.compile(r"(20\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月")
COMPACT_MONTH_RE = re.compile(r"(20\d{2})(0[1-9]|1[0-2])")


def fund_from_arg(arg):
    value = (arg or "").strip()
    if not value:
        return DEFAULT_FUND
    if value.startswith("http://") or value.startswith("https://"):
        query = parse_qs(urlparse(value).query)
        fund = (query.get("fund") or query.get("Fund") or [""])[0].strip()
        if not fund:
            raise RuntimeError(f"No fund= code found in FSMOne URL: {value}")
        return fund.upper()
    if FUND_RE.match(value):
        return value.upper()
    raise RuntimeError(f"Unsupported input. Use FSMOne fund code or factsheet URL: {value}")


def factsheet_url(fund):
    return PDF_TEMPLATE.format(fund=fund.upper())


def response_date_tag(response):
    last_modified = response.headers.get("last-modified")
    if not last_modified:
        return None
    try:
        return parsedate_to_datetime(last_modified).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def pdf_month_tag(pdf_path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return None

    reader = PdfReader(pdf_path)
    text = "\n".join((page.extract_text() or "") for page in reader.pages[:2])
    tags = []
    for pattern in (CHINESE_MONTH_RE, COMPACT_MONTH_RE):
        for match in pattern.finditer(text):
            tags.append(f"{match.group(1)}-{int(match.group(2)):02d}-01")
    return max(tags) if tags else None


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
        return os.path.getsize(tmp_path), final_url, resp


def main():
    fund = fund_from_arg(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FUND)
    os.makedirs(SAVE_DIR, exist_ok=True)

    url = factsheet_url(fund)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=SAVE_DIR) as tmp:
        tmp_path = tmp.name

    try:
        size, final_url, response = download_pdf(url, tmp_path)
        tag = pdf_month_tag(tmp_path) or response_date_tag(response) or fund
        output_path, report_month, moved = finalize_pdf_to_monthly_path(
            tmp_path,
            COMPANY,
            fund,
            fallback_date=tag,
        )
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    print(f"[*] Fund: {fund}")
    print(f"[*] PDF: {url}")
    print(f"[*] Final URL: {final_url}")
    print(f"[*] Date tag: {tag}")
    if moved:
        print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month}] -> {output_path}")
    else:
        print(f"[*] Existing [{report_month}], skipped -> {output_path}")


if __name__ == "__main__":
    main()
