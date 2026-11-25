# -*- coding: utf-8 -*-
"""
PineBridge Hong Kong fund report downloader.

Preferred input is the PDF URL behind the "基金滙報" button, for example:
  https://assets.pinebridge.com/asset/.../PineBridge-Global-Bond-Fund-Class-A-Units_HKC.PDF

The public fund page can redirect to a country-site disclaimer before the fund
content is available. This script still tries to parse the page for a fund
report link, but the direct PDF URL is the reliable reusable input.

Usage:
  python download_pinebridge_factsheet.py
  python download_pinebridge_factsheet.py "https://www.pinebridge.com/zh-hk/funds/pinebridge-global-bond-fund/a"
  python download_pinebridge_factsheet.py "https://assets.pinebridge.com/asset/.../PineBridge-..._HKC.PDF"
"""

import html
import os
import re
import shutil
import sys
import tempfile
from urllib.parse import urljoin, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import company_data_dir, finalize_pdf_to_monthly_path
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_PAGE_URL = "https://www.pinebridge.com/zh-hk/funds/pinebridge-global-bond-fund/a"
DEFAULT_PDF_URL = (
    "https://assets.pinebridge.com/asset/e6bb0a42-08cb-4123-a2ee-6f3c2985e92f/"
    "PineBridge-Global-Bond-Fund-Class-A-Units_HKC.PDF"
)
COMPANY = "pinebridge"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
TIMEOUT_MS = 60000
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

SESSION = requests.Session()
SESSION.trust_env = False
for _cookie_name, _cookie_value in {
    "culture": "zh-hk",
    "investorType": "professional-investor",
    "disclaimer": "zh-hk/professional-investor",
}.items():
    SESSION.cookies.set(
        _cookie_name,
        _cookie_value,
        domain="www.metlife.com",
        path="/",
    )

PDF_LINK_RE = re.compile(r'href="(?P<href>[^"]+?\.pdf)"', re.I)
ASSET_ID_RE = re.compile(r"/asset/([^/]+)/", re.I)


def safe_filename(value):
    value = re.sub(r"\s+", "_", value.strip())
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def is_pdf_url(value):
    return urlparse(value).path.lower().endswith(".pdf")


def fetch_text(url):
    resp = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.url, resp.text


def find_report_pdf(page_url):
    final_url, page_html = fetch_text(page_url)
    candidates = []
    for match in PDF_LINK_RE.finditer(page_html):
        href = html.unescape(match.group("href"))
        absolute = urljoin(final_url, href)
        lower = absolute.lower()
        if "assets.pinebridge.com" in lower and ("_hkc.pdf" in lower or "fund" in lower):
            candidates.append(absolute)

    if candidates:
        return candidates[0]

    browser_url = find_report_pdf_with_browser(page_url)
    if browser_url:
        return browser_url

    if page_url.rstrip("/") == DEFAULT_PAGE_URL.rstrip("/"):
        print("[!] Fund page redirected to disclaimer or did not expose PDF; using known report PDF for default fund.")
        return DEFAULT_PDF_URL

    raise RuntimeError("No PineBridge fund report PDF found. Pass the direct assets.pinebridge.com PDF URL.")


def find_report_pdf_with_browser(page_url):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(ignore_https_errors=True, user_agent=HEADERS["User-Agent"])
            page.goto(page_url, wait_until="networkidle", timeout=TIMEOUT_MS)

            if "/disclaimer" in page.url:
                buttons = page.locator("button")
                for index in range(buttons.count()):
                    button = buttons.nth(index)
                    text = (button.inner_text(timeout=1000) or "").strip()
                    if "接受" not in text:
                        continue
                    try:
                        button.click(timeout=10000)
                        page.wait_for_timeout(3000)
                        page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
                    except PlaywrightTimeoutError:
                        pass
                    if "/disclaimer" not in page.url:
                        break

            links = page.locator("a[href]")
            candidates = []
            for index in range(links.count()):
                link = links.nth(index)
                href = link.get_attribute("href") or ""
                lower = href.lower()
                if "assets.pinebridge.com" in lower and lower.endswith(".pdf") and "_hkc.pdf" in lower:
                    candidates.append(href)
            browser.close()
            if candidates:
                return candidates[0]
    except Exception as exc:
        print(f"[!] Browser fallback failed: {exc}")
    return None


def slug_from_pdf_url(pdf_url):
    name = os.path.splitext(os.path.basename(urlparse(pdf_url).path))[0]
    return safe_filename(name or "pinebridge_fund_report")


def archive_tag(pdf_url):
    asset_match = ASSET_ID_RE.search(pdf_url)
    if asset_match:
        return safe_filename(asset_match.group(1))
    return slug_from_pdf_url(pdf_url)


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
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else DEFAULT_PAGE_URL
    os.makedirs(SAVE_DIR, exist_ok=True)

    pdf_url = arg if is_pdf_url(arg) else find_report_pdf(arg)
    slug = slug_from_pdf_url(pdf_url)

    print(f"[*] PDF: {pdf_url}")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=SAVE_DIR) as tmp:
        tmp_path = tmp.name
    try:
        size, final_url = download_pdf(pdf_url, tmp_path)
        output_path, report_month, moved = finalize_pdf_to_monthly_path(
            tmp_path,
            COMPANY,
            slug,
            fallback_date=pdf_url,
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
