#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真实浏览器从瑞士百达香港产品页定位并归档最新 Monthly Factsheet。"""

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
from common.browser_fetch import download_via_browser, launch_browser, new_context  # noqa: E402
from common_paths import configure_session, extract_report_month_from_pdf, finalize_pdf_to_monthly_path  # noqa: E402


COMPANY = "pictet"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
SESSION = configure_session(requests.Session())


def _find_pdf(page_url: str, html: str) -> str | None:
    """从产品页 HTML 选择 Monthly Factsheet PDF；参数为产品页 URL 与 HTML。"""

    for href, label in re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html, flags=re.I | re.S):
        blob = f"{href} {re.sub(r'<[^>]+>', ' ', label)}".casefold()
        if "fact" in blob and ".pdf" in blob:
            return urljoin(page_url, href)
    return None


def _browser_download(page_url: str, target: str) -> None:
    """浏览器加载产品页并下载 Factsheet；参数为页面 URL 与临时目标。"""

    with sync_playwright() as playwright:
        browser = launch_browser(playwright, headless=True)
        try:
            context = new_context(browser, locale="en-HK")
            try:
                page = context.new_page()
                page.goto(page_url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(8000)
                links = page.locator("a").evaluate_all("""els => els.map(a => ({href:a.href||'', text:(a.innerText||a.textContent||'').trim()}))""")
                candidates = [str(x.get("href") or "") for x in links if ".pdf" in str(x.get("href") or "").casefold() and "fact" in str(x.get("text") or "").casefold()]
                if not candidates:
                    candidates = [str(x.get("href") or "") for x in links if ".pdf" in str(x.get("href") or "").casefold()]
                if not candidates:
                    raise RuntimeError(f"Pictet 产品页未找到 Factsheet：{page_url}")
                download_via_browser(page, candidates[0], target, timeout_ms=90000)
            finally:
                context.close()
        finally:
            browser.close()


def main() -> int:
    """解析命令行并归档；参数从 sys.argv 读取。"""

    page_url = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    fund_name = str(sys.argv[2] if len(sys.argv) > 2 else "Pictet Fund").strip()
    if not page_url:
        raise SystemExit("用法：python download_pictet_factsheet.py <香港产品页> [基金英文名]")
    fd, temp_path = tempfile.mkstemp(prefix="pictet_", suffix=".pdf")
    os.close(fd)
    try:
        response = SESSION.get(page_url, headers=HEADERS, timeout=60)
        pdf_url = _find_pdf(page_url, response.text) if response.ok else None
        if pdf_url:
            pdf_response = SESSION.get(pdf_url, headers={**HEADERS, "Referer": page_url}, timeout=60)
            pdf_response.raise_for_status()
            if not pdf_response.content.startswith(b"%PDF-"):
                raise RuntimeError(f"Pictet 直连内容不是 PDF：{pdf_url}")
            open(temp_path, "wb").write(pdf_response.content)
        else:
            _browser_download(page_url, temp_path)
        fallback = date.today().strftime("%Y-%m-%d")
        try:
            report_month = extract_report_month_from_pdf(temp_path, fallback_date=fallback)
        except Exception:
            report_month = fallback
        target, _, created = finalize_pdf_to_monthly_path(temp_path, COMPANY, fund_name, fallback_date=report_month)
        print(f"[*] 产品页: {page_url}")
        print(f"[*] {'完成' if created else '已存在'} -> {target}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
