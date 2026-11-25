# -*- coding: utf-8 -*-
"""
Fullgoal Hong Kong latest monthly report downloader.

Target page examples:
  https://www.fullgoal.com.hk/fundDetail/selectInvestmentGradeBondFund/index.html
  https://www.fullgoal.com.hk/fundDetail/smallMid/index.html

The document list is rendered by the page. The latest monthly report is the
first anchor whose visible text contains "基金月報".

Usage:
  python download_fullgoal_factsheet.py "<fund detail page URL>"
"""

import os
import re
import shutil
import sys
from urllib.parse import urljoin, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_URL = "https://www.fullgoal.com.hk/fundDetail/selectInvestmentGradeBondFund/index.html"
COMPANY = "fullgoal"
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
configure_session(SESSION)


def safe_filename(value):
    value = re.sub(r"\s+", "_", value.strip())
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def slug_from_page_url(page_url):
    parts = [part for part in urlparse(page_url).path.split("/") if part]
    if "fundDetail" in parts:
        idx = parts.index("fundDetail")
        if idx + 1 < len(parts):
            return safe_filename(parts[idx + 1])
    return "fullgoal_fund"


def date_from_text(text):
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text or "")
    return match.group(1) if match else "latest"


def latest_monthly_report(page_url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(ignore_https_errors=True, user_agent=HEADERS["User-Agent"])
        page.goto(page_url, wait_until="networkidle", timeout=TIMEOUT_MS)
        try:
            page.wait_for_selector("a[href$='.pdf']", timeout=TIMEOUT_MS)
        except PlaywrightTimeoutError:
            browser.close()
            raise RuntimeError("No PDF links found after rendering Fullgoal page.")

        links = page.locator("a[href]")
        for index in range(links.count()):
            link = links.nth(index)
            href = link.get_attribute("href") or ""
            text = (link.inner_text(timeout=1000) or "").strip()
            if "基金月報" in text and href.lower().endswith(".pdf"):
                browser.close()
                return {
                    "url": urljoin(page_url, href),
                    "date": date_from_text(text),
                    "text": text,
                }

        browser.close()
    raise RuntimeError("Latest 基金月報 link not found on Fullgoal page.")


def archive_tag(pdf_url):
    name = os.path.basename(urlparse(pdf_url).path)
    return safe_filename(os.path.splitext(name)[0] or "latest")


def download_pdf(url, dest):
    tmp = dest + ".part"
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as resp:
        resp.raise_for_status()
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
    return os.path.getsize(dest)


def main():
    page_url = sys.argv[1].strip() if len(sys.argv) > 1 else DEFAULT_URL
    os.makedirs(SAVE_DIR, exist_ok=True)

    report = latest_monthly_report(page_url)
    slug = slug_from_page_url(page_url)
    output_path = dated_output_path(COMPANY, slug, report["date"])

    print(f"[*] Page: {page_url}")
    print(f"[*] Latest monthly report: {report['date']}")
    print(f"[*] PDF: {report['url']}")

    size = download_pdf(report["url"], output_path)

    print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")


if __name__ == "__main__":
    main()
