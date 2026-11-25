# -*- coding: utf-8 -*-
"""
Fidelity Taiwan latest monthly report downloader.

Target page example:
  https://www.fidelity.com.tw/fund-and-service/fundui/funds/Fidelity-Funds-Global-Focus-Fund-A-ACC-USD/documents

The Taiwan site renders the document table with JavaScript. The latest monthly
report is the first document-table row and currently points to an HFFS PDF.
The top monthly-report button is a rendered button without an href, so this
script uses it as a page-readiness check and downloads the table link.

站点由 Akamai 保护：Playwright 自带 Chromium 会 403，因此这里改用真实浏览器
渠道（Chrome/Edge）+ Chrome UA，并在命中拦截页时立即失败，避免长时间空等。

Usage:
  python download_fidelity_tw_factsheet.py "<Fidelity Taiwan documents page URL>"
  python download_fidelity_tw_factsheet.py
"""

import os
import re
import sys
import tempfile
from urllib.parse import urlparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.browser_fetch import (  # noqa: E402
    download_via_browser,
    is_access_denied,
    launch_browser,
    new_context,
)
from common_paths import company_data_dir, finalize_pdf_to_monthly_path  # noqa: E402
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


DEFAULT_URL = (
    "https://www.fidelity.com.tw/fund-and-service/fundui/funds/"
    "Fidelity-Funds-Global-Focus-Fund-A-ACC-USD/documents"
)
COMPANY = "fidelity_tw"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT_MS = 60000

CTA_SELECTOR = "#fil-general-info__cta-monthly-report > button"
TABLE_LINK_SELECTOR = (
    "#fil-fund-detail-document-table > div > div.fil-table--body > "
    "div:nth-child(1) > div.fil-table--body-row-cell.text-right."
    "fil-fund-detail__document-table-link > div > span > a"
)


def safe_filename(value):
    """把名称转成安全文件名。"""
    value = re.sub(r"\s+", "_", value.strip())
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def slug_from_url(page_url):
    """从文档页 URL 中提取基金 slug，用于输出文件名。"""
    path = urlparse(page_url).path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    if "funds" in parts:
        idx = parts.index("funds")
        if idx + 1 < len(parts):
            return safe_filename(parts[idx + 1])
    return "fidelity_tw_fund"


def find_latest_report(page, page_url):
    """在已打开的页面中读取最新月报链接。"""
    response = page.goto(page_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    if is_access_denied(page, response):
        raise RuntimeError(
            "富达台湾站点返回 Access Denied：请确认本机已安装 Chrome/Edge 后重试"
        )

    cta_count = page.locator(CTA_SELECTOR).count()
    if cta_count == 0:
        print("[!] Top monthly-report button not found; trying document table.")

    href = ""
    label = ""
    try:
        page.wait_for_selector(TABLE_LINK_SELECTOR, timeout=TIMEOUT_MS)
        link = page.locator(TABLE_LINK_SELECTOR).first
        href = link.get_attribute("href") or ""
        label = (link.inner_text() or "").strip()
    except PlaywrightTimeoutError:
        print("[!] Document table link not found; scanning rendered PDF links.")

    pdf_links = page.locator("a[href]").evaluate_all(
        """links => links
            .map(a => ({href: a.href || a.getAttribute('href') || '', text: (a.innerText || '').trim()}))
            .filter(item => item.href.toLowerCase().includes('.pdf'))"""
    )
    hffs_links = [item for item in pdf_links if "hffs." in item["href"].lower()]
    hffs_count = len(hffs_links)
    if not href and hffs_links:
        href = hffs_links[0]["href"]
        label = hffs_links[0]["text"] or "HFFS PDF"
    if not href:
        raise RuntimeError("No Fidelity Taiwan monthly-report PDF link was found after rendering.")

    if ".pdf" not in href.lower():
        raise RuntimeError(f"Latest document link is not a PDF: {href}")
    if "hffs." not in href.lower():
        print(f"[!] First document link is not an HFFS link; verify manually: {href}")
    if hffs_count == 0:
        print("[!] No HFFS PDF link found during cross-check.")

    return {"url": href, "label": label or "download", "hffs_count": hffs_count}


def main():
    """命令行入口：渲染台湾文档页并下载最新月报。"""
    page_url = sys.argv[1].strip() if len(sys.argv) > 1 else DEFAULT_URL
    os.makedirs(SAVE_DIR, exist_ok=True)
    print(f"[*] Page: {page_url}")

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = new_context(browser, locale="zh-TW")
        page = context.new_page()
        try:
            report = find_latest_report(page, page_url)
            fund_slug = slug_from_url(page_url)

            print(f"[*] Latest monthly report link: {report['url']}")
            print(
                f"[*] Link text: {report['label']} | HFFS links on page: {report['hffs_count']}"
            )
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".pdf", dir=SAVE_DIR
            ) as tmp:
                tmp_path = tmp.name
            try:
                size = download_via_browser(
                    page, report["url"], tmp_path, timeout_ms=TIMEOUT_MS
                )
                dest, report_month, moved = finalize_pdf_to_monthly_path(
                    tmp_path, COMPANY, fund_slug
                )
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            if moved:
                print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month}] -> {dest}")
            else:
                print(f"[*] Existing [{report_month}], skipped -> {dest}")
            print(f"[*] Save dir: {SAVE_DIR}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
