# -*- coding: utf-8 -*-
"""
99Fund Hong Kong latest monthly report downloader.

Target page example:
  https://www.99fund.com.hk/main/hkweb_new/hk/products/888888/fundgl.shtml

The fund page contains a "fund_monthly" document table in the HTML. This script
fetches the page each run, selects the newest monthly report by publish date,
and downloads the current PDF.

Usage:
  python download_99fund_factsheet.py
  python download_99fund_factsheet.py 888888
  python download_99fund_factsheet.py "https://www.99fund.com.hk/main/hkweb_new/hk/products/888888/fundgl.shtml"
"""

import os
import re
import shutil
import sys
from datetime import datetime
from html import unescape
from urllib.parse import unquote, urljoin, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path


DEFAULT_PRODUCT_ID = "888888"
DEFAULT_PAGE_URL = f"https://www.99fund.com.hk/main/hkweb_new/hk/products/{DEFAULT_PRODUCT_ID}/fundgl.shtml"
COMPANY = "99fund"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.99fund.com.hk/",
}

SESSION = requests.Session()
configure_session(SESSION)


def safe_filename(value):
    value = unquote(value or "").strip()
    value = re.sub(r"\s+", "_", value)
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def decode_page(content):
    for encoding in ("gb18030", "gbk", "utf-8"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            pass
    return content.decode("gb18030", errors="replace")


def fetch_page(page_url):
    resp = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return decode_page(resp.content)


def strip_tags(html):
    text = re.sub(r"<script\b.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def parse_date(value):
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
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


def product_id_from_url(page_url):
    match = re.search(r"/products/([^/]+)/", urlparse(page_url).path)
    return safe_filename(match.group(1) if match else DEFAULT_PRODUCT_ID)


def page_url_from_arg(value):
    value = (value or "").strip()
    if not value:
        return DEFAULT_PAGE_URL
    if value.startswith(("http://", "https://")):
        return value
    return f"https://www.99fund.com.hk/main/hkweb_new/hk/products/{value}/fundgl.shtml"


def extract_fund_name(html):
    match = re.search(
        r"<td[^>]*>\s*基金全稱\s*</td>\s*<td[^>]*>\s*(.*?)\s*</td>",
        html,
        flags=re.I | re.S,
    )
    if match:
        return strip_tags(match.group(1))

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    if title_match:
        return strip_tags(title_match.group(1))

    return "99fund"


def extract_monthly_section(html):
    match = re.search(
        r'<div[^>]*class=["\'][^"\']*\bfund_monthly\b[^"\']*["\'][^>]*>(.*?)</div>',
        html,
        flags=re.I | re.S,
    )
    if not match:
        raise RuntimeError("No 99Fund fund_monthly section found on the page.")
    return match.group(1)


def latest_monthly_report(page_url):
    html = fetch_page(page_url)
    section = extract_monthly_section(html)
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", section, flags=re.I | re.S)
    reports = []

    for row in rows:
        href_match = re.search(r'<a\b[^>]*href=["\']([^"\']+\.pdf[^"\']*)["\']', row, flags=re.I | re.S)
        if not href_match:
            continue

        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, flags=re.I | re.S)
        row_text = strip_tags(row)
        title = strip_tags(cells[0]) if cells else "基金月報"
        date_match = re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", row_text)
        publish_date = date_match.group(0).replace("/", "-") if date_match else ""

        reports.append(
            {
                "title": title,
                "date": publish_date,
                "url": urljoin(page_url, href_match.group(1)),
            }
        )

    if not reports:
        raise RuntimeError("No monthly report PDF link found in 99Fund fund_monthly section.")

    reports.sort(key=lambda item: parse_date(item["date"]), reverse=True)
    latest = reports[0]
    latest["fund_name"] = extract_fund_name(html)
    return latest


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


def main():
    page_url = page_url_from_arg(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PAGE_URL
    os.makedirs(SAVE_DIR, exist_ok=True)

    report = latest_monthly_report(page_url)
    product_id = product_id_from_url(page_url)
    output_path = dated_output_path(COMPANY, f"{product_id}_{report['fund_name']}", report["date"])

    print(f"[*] Page: {page_url}")
    print(f"[*] Fund: {report['fund_name']}")
    print(f"[*] Selected: {report['title']} / {report['date']}")
    print(f"[*] PDF: {report['url']}")

    size, final_url = download_pdf(report["url"], output_path)

    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")


if __name__ == "__main__":
    main()
