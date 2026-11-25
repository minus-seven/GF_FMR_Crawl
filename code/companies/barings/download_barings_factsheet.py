#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打开霸菱香港官方产品页，下载最新英文 Factsheet 并归档。"""

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


COMPANY = "barings"
TIMEOUT = 120
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
}
SESSION = configure_session(requests.Session())


def _find_pdf_from_html(page_url: str, html: str) -> str | None:
    """从产品页 HTML 选择 Factsheet PDF；参数为产品页 URL 与 HTML。"""

    pattern = re.compile(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
    candidates: list[str] = []
    for href, label in pattern.findall(html):
        text = re.sub(r"<[^>]+>", " ", label)
        lower = f"{text} {href}".casefold()
        if "factsheet" not in lower or not (".pdf" in lower or "documents.barings.com" in lower):
            continue
        if any(token in lower for token in ("kfs", "key facts", "prospectus", "semi annual", "annual report")):
            continue
        candidates.append(urljoin(page_url, href))
    return candidates[0] if candidates else None


def _browser_find_and_download(page_url: str, target: str) -> str:
    """用浏览器加载动态文档链接并下载 Factsheet；参数为产品页和临时文件。"""

    with sync_playwright() as playwright:
        browser = launch_browser(playwright, headless=True)
        try:
            context = new_context(browser, locale="en-HK")
            try:
                page = context.new_page()
                page.goto(page_url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(4000)
                links = page.locator("a").evaluate_all(
                    """els => els.map(a => ({href: a.href || '', text: (a.innerText || a.textContent || '').trim()}))"""
                )
                candidates = [
                    str(item.get("href") or "") for item in links
                    if "factsheet" in str(item.get("text") or "").casefold()
                    and "documents.barings.com" in str(item.get("href") or "").casefold()
                    and not any(t in str(item.get("text") or "").casefold() for t in ("kfs", "key facts", "prospectus"))
                ]
                if not candidates:
                    content = page.content()
                    candidates = re.findall(r"https?://[^\"'<> ]+documents\.barings\.com[^\"'<> ]+\.pdf[^\"'<> ]*", content, flags=re.I)
                    candidates = [url for url in candidates if "factsheet" in url.casefold()]
                if not candidates:
                    raise RuntimeError(f"霸菱产品页未找到 Factsheet 下载链接：{page_url}")
                download_via_browser(page, candidates[0], target, timeout_ms=90000)
            finally:
                context.close()
        finally:
            browser.close()
    with open(target, "rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise RuntimeError(f"霸菱浏览器下载内容不是 PDF：{page_url}")
    return target


def _download(page_url: str) -> str:
    """下载产品页最新 Factsheet；参数 page_url 为官方产品页。"""

    fd, temp_path = tempfile.mkstemp(prefix="barings_", suffix=".pdf")
    os.close(fd)
    try:
        response = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        pdf_url = _find_pdf_from_html(page_url, response.text)
        if pdf_url:
            pdf_response = SESSION.get(pdf_url, headers={**HEADERS, "Referer": page_url}, timeout=TIMEOUT, stream=True)
            pdf_response.raise_for_status()
            first = True
            with open(temp_path, "wb") as handle:
                for chunk in pdf_response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    if first and not chunk.startswith(b"%PDF-"):
                        raise RuntimeError(f"霸菱下载内容不是 PDF：{pdf_url}")
                    first = False
                    handle.write(chunk)
            pdf_response.close()
            if not first:
                return temp_path
        return _browser_find_and_download(page_url, temp_path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def main() -> int:
    """解析命令行并归档 Factsheet；参数从 sys.argv 读取。"""

    page_url = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    fund_name = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    if not page_url or not fund_name:
        raise SystemExit("用法：python download_barings_factsheet.py <官方产品页> <英文基金名>")
    temp_path: str | None = None
    try:
        temp_path = _download(page_url)
        try:
            report_month = extract_report_month_from_pdf(temp_path, fallback_date=date.today().strftime("%Y-%m-%d"))
        except Exception:
            report_month = date.today().strftime("%Y-%m-%d")
        target, _, created = finalize_pdf_to_monthly_path(temp_path, COMPANY, fund_name, fallback_date=report_month)
        print(f"[*] 产品页: {page_url}")
        print(f"[*] {'完成' if created else '已存在'} -> {target}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
