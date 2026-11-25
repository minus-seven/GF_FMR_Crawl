#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 First Sentier 香港文学页中指定品牌的最新 Monthly Factsheet。"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import date
from urllib.parse import urljoin

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, extract_report_month_from_pdf, finalize_pdf_to_monthly_path  # noqa: E402


COMPANY = "firstsentier_global"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
SESSION = configure_session(requests.Session())


def _find_pdf(page_url: str, keyword: str) -> str:
    """从文学页找到包含关键词的 PDF；参数为页面 URL 与英文基金关键词。"""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(locale="en-HK", user_agent=HEADERS["User-Agent"])
        page.goto(page_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(8000)
        links = page.locator("a").evaluate_all("""els => els.map(a => ({href:a.href||'', text:(a.innerText||a.textContent||'').trim(), row:(a.closest('tr')||{}).innerText||''}))""")
        browser.close()
    key = re.sub(r"[^a-z0-9]+", "", keyword.casefold())
    for item in links:
        href = urljoin(page_url, str(item.get("href") or ""))
        blob = f"{href} {item.get('text') or ''} {item.get('row') or ''}".casefold()
        blob_key = re.sub(r"[^a-z0-9]+", "", blob)
        if href.lower().endswith(".pdf") and key in blob_key and "fundfactsheet" in href.lower():
            return href
    for item in links:
        href = urljoin(page_url, str(item.get("href") or ""))
        blob = f"{href} {item.get('text') or ''} {item.get('row') or ''}".casefold()
        if href.lower().endswith(".pdf") and key in re.sub(r"[^a-z0-9]+", "", blob):
            return href
    raise RuntimeError(f"First Sentier 文学页未找到关键词对应 Factsheet：{keyword}")


def main() -> int:
    """解析命令行并归档；参数为页面 URL、关键词、基金英文名。"""

    page_url = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    keyword = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    fund_name = str(sys.argv[3] if len(sys.argv) > 3 else keyword).strip()
    if not page_url or not keyword:
        raise SystemExit("用法：python download_firstsentier_global_factsheet.py <文学页> <关键词> [基金英文名]")
    pdf_url = _find_pdf(page_url, keyword)
    fd, temp_path = tempfile.mkstemp(prefix="firstsentier_global_", suffix=".pdf")
    os.close(fd)
    try:
        with SESSION.get(pdf_url, headers=HEADERS, timeout=120, stream=True) as response:
            response.raise_for_status()
            with open(temp_path, "wb") as handle:
                first = True
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    if first and not chunk.startswith(b"%PDF-"):
                        raise RuntimeError(f"First Sentier 下载内容不是 PDF：{response.url}")
                    first = False
                    handle.write(chunk)
        fallback = date.today().strftime("%Y-%m-%d")
        try:
            report_month = extract_report_month_from_pdf(temp_path, fallback_date=fallback)
        except Exception:
            report_month = fallback
        target, _, created = finalize_pdf_to_monthly_path(temp_path, COMPANY, fund_name, fallback_date=report_month)
        print(f"[*] PDF: {pdf_url}")
        print(f"[*] {'完成' if created else '已存在'} -> {target}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
