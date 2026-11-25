# -*- coding: utf-8 -*-
"""
CCB International latest fund document downloader.

Target page example:
  https://www.ccbintl.com.hk/Big5/management3.html

The page contains a "pdfdown" list. This script re-reads the page each run,
finds the requested document label, follows the current down.html link, and
downloads the returned PDF.

Usage:
  python download_ccbintl_factsheet.py
  python download_ccbintl_factsheet.py "基金月刊"
  python download_ccbintl_factsheet.py "https://www.ccbintl.com.hk/Big5/management3.html" "基金月刊"
"""

import os
import re
import shutil
import sys
import urllib3
from html import unescape
from urllib.parse import unquote, urljoin, urlparse, parse_qs

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path


DEFAULT_PAGE_URL = "https://www.ccbintl.com.hk/Big5/management3.html"
DEFAULT_LABEL = "基金月刊"
COMPANY = "ccbintl"
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
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def safe_filename(value):
    value = unquote(value or "").strip()
    value = re.sub(r"\s+", "_", value)
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def strip_tags(html):
    text = re.sub(r"<script\b.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def fetch_page(page_url):
    resp = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT, verify=False)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def extract_pdfdown_section(html):
    match = re.search(
        r'<div[^>]*class=["\'][^"\']*\bpdfdown\b[^"\']*["\'][^>]*>(.*?)</div>',
        html,
        flags=re.I | re.S,
    )
    if not match:
        raise RuntimeError("No CCBI pdfdown section found on the page.")
    return match.group(1)


def select_document(page_url, label):
    html = fetch_page(page_url)
    section = extract_pdfdown_section(html)
    links = []
    for match in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', section, flags=re.I | re.S):
        text = strip_tags(match.group(2))
        href = urljoin(page_url, match.group(1))
        links.append({"label": text, "url": href})

    matches = [item for item in links if label in item["label"]]
    if not matches:
        available = "; ".join(item["label"] for item in links)
        raise RuntimeError(f"No CCBI document matched label='{label}'. Available: {available}")

    return matches[0]


def archive_tag(url):
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    doc_id = (params.get("id") or ["latest"])[0]
    return safe_filename(doc_id)


def download_pdf(url, dest):
    tmp = dest + ".part"
    headers = dict(HEADERS)
    headers["Referer"] = DEFAULT_PAGE_URL
    with SESSION.get(url, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=True, verify=False) as resp:
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
    label = DEFAULT_LABEL

    if not args:
        return page_url, label

    first = args[0].strip()
    if first.startswith(("http://", "https://")):
        page_url = first
        if len(args) > 1:
            label = args[1].strip()
    else:
        label = first

    return page_url, label


def main():
    page_url, label = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    doc = select_document(page_url, label)
    output_path = dated_output_path(COMPANY, doc["label"], archive_tag(doc["url"]))

    print(f"[*] Page: {page_url}")
    print(f"[*] Selected: {doc['label']}")
    print(f"[*] Download URL: {doc['url']}")

    size, final_url = download_pdf(doc["url"], output_path)

    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")


if __name__ == "__main__":
    main()
