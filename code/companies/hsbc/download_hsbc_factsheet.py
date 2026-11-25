# -*- coding: utf-8 -*-
"""
HSBC factsheet/monthly report downloader.

Preferred source: HSBC Asset Management Hong Kong fund-centre pages, e.g.
  https://www.assetmanagement.hsbc.com.hk/zh/intermediary/fund-centre/hk0000317518?t=5

Those pages expose a stable PDF API:
  /api/v1/download/document/<isin>/hk/zh/factsheet

Fallback source: HSBC China QuickRank pages, e.g.
  https://fundsresearch.investments.hsbc.com.cn/rbwm/QuickRank.aspx?fund=...

The QuickRank page exposes a monthly report button whose href points to
Casdocument.aspx?documentid=...; that intermediate page embeds the real PDF in
a Morningstar iframe.

Usage:
  python download_hsbc_factsheet.py hk0000317518
  python download_hsbc_factsheet.py "https://www.assetmanagement.hsbc.com.hk/zh/intermediary/fund-centre/hk0000349503?t=5"
  python download_hsbc_factsheet.py "https://www.assetmanagement.hsbc.co.uk/en/individual-investor/funds/gb0000469741"
  python download_hsbc_factsheet.py "<QuickRank URL>"
  python download_hsbc_factsheet.py
"""

import html
import os
import re
import shutil
import sys
import tempfile
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, finalize_pdf_to_monthly_path


DEFAULT_URL = "https://www.assetmanagement.hsbc.com.hk/zh/intermediary/fund-centre/hk0000317518?t=5"
HK_API_TMPL = "https://www.assetmanagement.hsbc.com.hk/api/v1/download/document/{isin}/hk/zh/factsheet"
UK_API_TMPL = "https://www.assetmanagement.hsbc.co.uk/api/v1/download/document/{isin}/gb/en/factsheet"
COMPANY = "hsbc"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

SESSION = requests.Session()
configure_session(SESSION)

MONTHLY_LINK_RE = re.compile(
    r'<a\b(?=[^>]*\bclass="[^"]*\blink_hsbcreport\b[^"]*")[^>]*\bhref="(?P<href>[^"]*documentid=\d+[^"]*)"',
    re.I,
)
IFRAME_RE = re.compile(r'<iframe\b[^>]*\bsrc="(?P<src>[^"]+)"', re.I)
DOC_ID_RE = re.compile(r"documentid=(\d+)", re.I)
HK_ISIN_RE = re.compile(r"\b(hk\d{10})\b", re.I)
# 汇丰月报接口对任意注册地的 ISIN 都可用（LU/IE/CNE 等 2026-10 实测可下载）
ANY_ISIN_RE = re.compile(r"\b([A-Za-z]{2}[A-Za-z0-9]{9}\d)\b")
FILENAME_RE = re.compile(r'filename="?([^";]+)"?', re.I)


def fetch_text(url):
    resp = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def safe_filename(value):
    value = re.sub(r"\s+", "_", value.strip())
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def is_hk_source(value):
    """判断是否走汇丰香港月报接口：HK 站 URL 或任意 ISIN 均可。"""
    value_lower = value.lower()
    return (
        "assetmanagement.hsbc.com.hk" in value_lower
        or bool(HK_ISIN_RE.search(value_lower))
        or bool(ANY_ISIN_RE.search(value))
    )


def is_uk_source(value):
    """判断是否走汇丰英国月报接口。

    参数 value：英国基金页/月报 URL 或裸 ISIN；返回是否为英国官网来源。
    """
    return "assetmanagement.hsbc.co.uk" in str(value or "").lower()


def extract_hk_isin(value):
    """从 URL 或裸 ISIN 中提取 ISIN（含 LU/IE/CNE 等非 HK 注册地）。"""
    match = ANY_ISIN_RE.search(value) or HK_ISIN_RE.search(value)
    if not match:
        raise RuntimeError(f"HSBC ISIN not found: {value}")
    return match.group(1).lower()


def fund_slug_from_quickrank(page_url):
    fund = parse_qs(urlparse(page_url).query).get("fund", ["hsbc_fund"])[0]
    return safe_filename(unquote(fund) or "hsbc_fund")


def filename_from_headers(resp, fallback):
    cd = resp.headers.get("Content-Disposition", "")
    match = FILENAME_RE.search(cd)
    return safe_filename(match.group(1)) if match else fallback


def download_pdf(url, dest):
    tmp = dest + ".part"
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as resp:
        resp.raise_for_status()
        official_name = filename_from_headers(resp, os.path.basename(dest))
        with open(tmp, "wb") as fh:
            first = True
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError("Downloaded content is not a PDF.")
                first = False
                fh.write(chunk)
    os.replace(tmp, dest)
    return os.path.getsize(dest), official_name


def download_hk_factsheet(arg, api_template=HK_API_TMPL, market="HK"):
    """下载汇丰香港或英国基金月报并归档。

    参数 arg：基金 URL 或 ISIN；api_template：对应市场月报 API；
    market：日志中的市场标识。
    """
    isin = extract_hk_isin(arg)
    api_url = api_template.format(isin=isin)

    print(f"[*] HSBC {market} ISIN: {isin}")
    print(f"[*] API: {api_url}")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=SAVE_DIR) as tmp:
        tmp_path = tmp.name
    try:
        size, official_name = download_pdf(api_url, tmp_path)
        output_path, report_month, moved = finalize_pdf_to_monthly_path(tmp_path, COMPANY, isin, fallback_date=official_name)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print(f"[*] Official filename: {official_name}")
    if moved:
        print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month}] -> {output_path}")
    else:
        print(f"[*] Existing [{report_month}], skipped -> {output_path}")


def extract_quickrank_monthly_link(page_url):
    page_html = fetch_text(page_url)
    match = MONTHLY_LINK_RE.search(page_html)
    if not match:
        raise RuntimeError("Monthly report link not found on QuickRank page.")

    href = html.unescape(match.group("href"))
    doc_url = urljoin(page_url, href)
    doc_id_match = DOC_ID_RE.search(doc_url)
    doc_id = doc_id_match.group(1) if doc_id_match else "unknown"
    return doc_url, doc_id


def extract_quickrank_pdf_url(casdocument_url):
    doc_html = fetch_text(casdocument_url)
    match = IFRAME_RE.search(doc_html)
    if not match:
        raise RuntimeError("Morningstar iframe not found on Casdocument page.")
    return html.unescape(match.group("src"))


def download_quickrank_monthly_report(page_url):
    casdocument_url, doc_id = extract_quickrank_monthly_link(page_url)
    pdf_url = extract_quickrank_pdf_url(casdocument_url)
    slug = fund_slug_from_quickrank(page_url)

    print("[*] HSBC China QuickRank fallback")
    print(f"[*] Current documentid: {doc_id}")
    print(f"[*] Casdocument: {casdocument_url}")
    print(f"[*] PDF: {pdf_url}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=SAVE_DIR) as tmp:
        tmp_path = tmp.name
    try:
        size, official_name = download_pdf(pdf_url, tmp_path)
        output_path, report_month, moved = finalize_pdf_to_monthly_path(
            tmp_path,
            COMPANY,
            f"{slug}_{doc_id}",
            fallback_date=f"{pdf_url} {official_name}",
        )
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    if moved:
        print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month}] -> {output_path}")
    else:
        print(f"[*] Existing [{report_month}], skipped -> {output_path}")


def main():
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else DEFAULT_URL
    os.makedirs(SAVE_DIR, exist_ok=True)

    print(f"[*] Input: {arg}")
    if is_uk_source(arg):
        download_hk_factsheet(arg, api_template=UK_API_TMPL, market="UK")
    elif is_hk_source(arg):
        download_hk_factsheet(arg)
    else:
        download_quickrank_monthly_report(arg)
    print(f"[*] Save dir: {SAVE_DIR}")


if __name__ == "__main__":
    main()
