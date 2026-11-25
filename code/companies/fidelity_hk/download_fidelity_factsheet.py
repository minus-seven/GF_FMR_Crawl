# -*- coding: utf-8 -*-
"""
Fidelity Hong Kong factsheet downloader.

Target page example:
  https://www.fidelity.com.hk/zh/funds/factsheet/CFGE/H/tab-documents

The rendered page calls FundDocuments.json. The factsheet is returned as
PACKET_FUND_CLASS_DOC_HFFS / 基金報告 with a dated document entry.

站点由 Akamai 保护：纯 requests 与 Playwright 自带 Chromium 都会 403，
因此这里统一用“真实浏览器渠道（Chrome/Edge）+ Chrome UA”：
  - 接口 JSON：在页面内 fetch（同源，实测 200）；
  - PDF 文件：切到文件所在域后用浏览器取字节落盘（实测 200）。

Usage:
  python download_fidelity_factsheet.py CFGE/H
  python download_fidelity_factsheet.py "https://www.fidelity.com.hk/zh/funds/factsheet/CFGE/H/tab-documents"
"""

import os
import re
import sys
from urllib.parse import urlencode

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.browser_fetch import (  # noqa: E402
    download_via_browser,
    fetch_json,
    is_access_denied,
    launch_browser,
    new_context,
)
from common_paths import company_data_dir, dated_output_path  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


API = "https://www.fidelity.com.hk/api/ce/fdh/FundDocuments.json"
FUND_PAGE = "https://www.fidelity.com.hk/zh/funds"
COMPANY = "fidelity_hk"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT_MS = 60000
DOC_KEY = "PACKET_FUND_CLASS_DOC_HFFS"
DIRECT_PDF_FALLBACKS = {
    "CFGE/H": "https://www.fidelityinternational.com/legal/documents/HK-zh_en/hffs.HK-zh_en.HK.H-CFGE.pdf",
}


def extract_fund_id(arg):
    """从 fundId 或产品页 URL 中提取 fundId（形如 CFGE/H）。"""
    match = re.search(r"/factsheet/([^/]+)/([^/#?]+)", arg)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return arg.strip()


def fetch_documents(page, fund_id):
    """在页面内请求 FundDocuments 接口，返回 JSON。"""
    params = {
        "isins": fund_id,
        "ids": fund_id,
        "countries": "hk",
        "country": "hk",
        "languages": "zh,en",
        "language": "zh",
        "channels": "ce.private-investor",
        "channel": "ce.private-investor",
    }
    return fetch_json(page, f"{API}?{urlencode(params)}", timeout_ms=TIMEOUT_MS)


def pick_factsheet(data, fund_id):
    """从接口返回中挑选基金报告（HFFS）条目。"""
    fund = data.get(fund_id)
    if not fund:
        raise RuntimeError(f"Fund not found in API response: {fund_id}")

    doc = fund.get("documents", {}).get(DOC_KEY)
    if not doc:
        raise RuntimeError(f"Factsheet document key not found: {DOC_KEY}")

    translations = doc.get("translations", {})
    for lang in ("zh_en", "zh", "en"):
        item = translations.get(lang)
        if isinstance(item, dict) and item.get("url"):
            return {
                "title": doc.get("title") or "factsheet",
                "language": item.get("language") or lang,
                "date": item.get("date") or "nodate",
                "url": item["url"],
                "fileSize": item.get("fileSize"),
            }

    raise RuntimeError("Factsheet exists, but no downloadable translation URL was found.")


def fallback_factsheet(fund_id):
    """接口不可用时的直链兜底（PDF 仍由浏览器下载）。"""
    url = DIRECT_PDF_FALLBACKS.get(fund_id)
    if not url:
        raise RuntimeError(f"No direct PDF fallback configured for Fidelity fund: {fund_id}")
    return {
        "title": "基金報告",
        "language": "direct_pdf_fallback",
        "date": "nodate",
        "url": url,
        "fileSize": None,
    }


def main():
    """命令行入口：按 fundId 下载最新基金月报。"""
    args = sys.argv[1:]
    if not args:
        print("Usage: python download_fidelity_factsheet.py <fundId or Fidelity factsheet URL>")
        sys.exit(1)

    fund_id = extract_fund_id(args[0])
    os.makedirs(SAVE_DIR, exist_ok=True)
    print(f"[*] fundId: {fund_id}")

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = new_context(browser, locale="zh-HK")
        page = context.new_page()
        try:
            page.goto(FUND_PAGE, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            if is_access_denied(page):
                raise RuntimeError(
                    "富达香港站点返回 Access Denied：请确认本机已安装 Chrome/Edge 后重试"
                )
            try:
                factsheet = pick_factsheet(fetch_documents(page, fund_id), fund_id)
            except Exception as exc:  # noqa: BLE001 - 接口不可用时走直链兜底
                if fund_id not in DIRECT_PDF_FALLBACKS:
                    raise
                print(f"[!] FundDocuments API 不可用（{exc}）；使用直链兜底。")
                factsheet = fallback_factsheet(fund_id)

            date = factsheet["date"]
            dest = dated_output_path(COMPANY, fund_id, date)
            print(
                f"[*] Latest: {factsheet['title']} | {date} | "
                f"{factsheet['language']} | {factsheet.get('fileSize')}"
            )
            print(f"[*] URL: {factsheet['url']}")
            if os.path.exists(dest):
                print(f"[*] Exists, skip: {os.path.basename(dest)}")
                return
            print(f"[*] Downloading via browser...")
            size = download_via_browser(page, factsheet["url"], dest, timeout_ms=TIMEOUT_MS)
            print(f"[*] Done ({size / 1024:.1f} KB) -> {dest}")
            print(f"[*] Save dir: {SAVE_DIR}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
