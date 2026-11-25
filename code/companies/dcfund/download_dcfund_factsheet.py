# -*- coding: utf-8 -*-
"""
DC Fund Hong Kong latest monthly factsheet downloader.

Target page:
  https://www.dcfund.com.hk/cn/DCCSEF/resource/?cid=33

The resource page lists fund document download endpoints such as
`/cn/down/?t=Article&id=2623`. This script parses the current list, keeps
monthly-report items, selects the latest month from the title, and downloads
the linked PDF. It does not depend on a fixed PDF URL.

Usage:
  python download_dcfund_factsheet.py
  python download_dcfund_factsheet.py "https://www.dcfund.com.hk/cn/DCCSEF/resource/?cid=33"
"""

import html
import os
import re
import sys
from urllib.parse import urljoin

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path


COMPANY = "dcfund"
DEFAULT_PAGE_URL = "https://www.dcfund.com.hk/cn/DCCSEF/resource/?cid=33"
DEFAULT_FUND_KEY = "DCCSEF"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
configure_session(SESSION)

ITEM_RE = re.compile(r"<li\b[^>]*>(.*?)</li>", re.I | re.S)
LINK_RE = re.compile(r"<a\b[^>]*\bhref=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<body>.*?)</a>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
MONTH_RE = re.compile(r"(20\d{2})\s*[-./年]?\s*(0?[1-9]|1[0-2])")


def safe_filename(value):
    value = re.sub(r"\s+", "_", str(value or "").strip())
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def fetch_text(url):
    resp = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def text_from_html(value):
    text = TAG_RE.sub(" ", value or "")
    return " ".join(html.unescape(text).split())


def month_tag(title):
    match = MONTH_RE.search(title or "")
    if not match:
        return None
    return f"{match.group(1)}-{int(match.group(2)):02d}-01"


def monthly_reports(page_url):
    page_html = fetch_text(page_url)
    reports = []
    for item_match in ITEM_RE.finditer(page_html):
        link_match = LINK_RE.search(item_match.group(1))
        if not link_match:
            continue
        title = text_from_html(link_match.group("body"))
        if not any(keyword in title for keyword in ("基金月報", "基金月报", "月報", "月报")):
            continue
        date_tag = month_tag(title)
        if not date_tag:
            continue
        reports.append(
            {
                "title": title,
                "date": date_tag,
                "url": urljoin(page_url, html.unescape(link_match.group("href"))),
            }
        )
    reports.sort(key=lambda item: item["date"], reverse=True)
    return reports


def download_pdf(url, dest):
    tmp = dest + ".part"
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=True) as resp:
        resp.raise_for_status()
        final_url = resp.url
        with open(tmp, "wb") as fh:
            first_chunk = True
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first_chunk and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"Downloaded content is not a PDF: {final_url}")
                first_chunk = False
                fh.write(chunk)
    os.replace(tmp, dest)
    return os.path.getsize(dest), final_url


def main():
    page_url = sys.argv[1].strip() if len(sys.argv) > 1 else DEFAULT_PAGE_URL
    os.makedirs(SAVE_DIR, exist_ok=True)

    reports = monthly_reports(page_url)
    if not reports:
        raise RuntimeError(f"No monthly report found on page: {page_url}")

    latest = reports[0]
    output_path = dated_output_path(COMPANY, DEFAULT_FUND_KEY, latest["date"])

    print(f"[*] Page: {page_url}")
    print(f"[*] Latest monthly report: {latest['title']} ({latest['date']})")
    print(f"[*] Download URL: {latest['url']}")

    size, final_url = download_pdf(latest["url"], output_path)
    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")


if __name__ == "__main__":
    main()
