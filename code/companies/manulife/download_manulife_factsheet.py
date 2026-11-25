#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载并归档宏利香港单只 Factsheet（月报）。"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.browser_fetch import download_via_browser, launch_browser, new_context  # noqa: E402
from common_paths import (  # noqa: E402
    configure_session,
    extract_report_month_from_pdf,
    finalize_pdf_to_monthly_path,
)


COMPANY = "manulife"
TIMEOUT = 120
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
}
SESSION = configure_session(requests.Session())


def _download(url: str, referer: str) -> str:
    """下载并校验 PDF；参数 url 为 Factsheet 地址、referer 为官方文件页。"""

    response = SESSION.get(url, headers={**HEADERS, "Referer": referer}, timeout=TIMEOUT, stream=True)
    response.raise_for_status()
    fd, temp_path = tempfile.mkstemp(prefix="manulife_", suffix=".pdf")
    os.close(fd)
    first = True
    try:
        with open(temp_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"宏利下载内容不是 PDF：{response.url}")
                first = False
                handle.write(chunk)
        if first:
            raise RuntimeError(f"宏利下载内容为空：{response.url}")
        return temp_path
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
    finally:
        response.close()


def _download_browser(url: str, referer: str) -> str:
    """在真实 Chrome/Edge 渠道下载被 Akamai 拦截的 PDF；参数同 ``_download``。"""

    fd, temp_path = tempfile.mkstemp(prefix="manulife_browser_", suffix=".pdf")
    os.close(fd)
    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright, headless=True)
            try:
                context = new_context(browser, locale="en-HK")
                try:
                    page = context.new_page()
                    page.goto(referer, wait_until="domcontentloaded", timeout=60000)
                    download_via_browser(page, url, temp_path, timeout_ms=60000)
                finally:
                    context.close()
            finally:
                browser.close()
        return temp_path
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def main() -> int:
    """解析命令行并归档文件；本函数无参数，参数从 sys.argv 读取。"""

    source = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    fund_name = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    pdf_url = str(sys.argv[3] if len(sys.argv) > 3 else "").strip()
    if not source or not fund_name or not pdf_url:
        raise SystemExit("用法：python download_manulife_factsheet.py <官方文件页> <英文基金名> <Factsheet URL>")
    try:
        temp_path = _download(pdf_url, source)
    except requests.HTTPError as direct_error:
        # 宏利资产文件对纯 requests 返回 403；用项目统一的真实浏览器渠道重试。
        print(f"[!] requests 下载被拒绝，切换真实浏览器渠道：{direct_error}")
        temp_path = _download_browser(pdf_url, source)
    try:
        report_month = extract_report_month_from_pdf(
            temp_path, fallback_date=date.today().strftime("%Y-%m-%d")
        )
    except Exception:
        report_month = date.today().strftime("%Y-%m-%d")
    target, _, created = finalize_pdf_to_monthly_path(
        temp_path, COMPANY, fund_name, fallback_date=report_month
    )
    print(f"[*] 基金页: {source}")
    print(f"[*] Factsheet: {pdf_url}")
    print(f"[*] {'完成' if created else '已存在'} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
