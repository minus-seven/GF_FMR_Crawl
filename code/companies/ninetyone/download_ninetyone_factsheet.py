#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载并归档晋达（Ninety One）官方 GSF 月度 Factsheet。"""

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


COMPANY = "ninetyone"
TIMEOUT = 120
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
}
SESSION = configure_session(requests.Session())


def _download(url: str) -> str:
    """下载并校验 PDF；参数 url 为 Ninety One 官方媒体地址。"""

    response = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
    response.raise_for_status()
    fd, temp_path = tempfile.mkstemp(prefix="ninetyone_", suffix=".pdf")
    os.close(fd)
    first = True
    try:
        with open(temp_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"晋达下载内容不是 PDF：{response.url}")
                first = False
                handle.write(chunk)
        if first:
            raise RuntimeError(f"晋达下载内容为空：{response.url}")
        return temp_path
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
    finally:
        response.close()


def _download_browser(url: str) -> str:
    """用真实浏览器处理 Cloudflare/站点拦截；参数 url 为官方 PDF。"""

    fd, temp_path = tempfile.mkstemp(prefix="ninetyone_browser_", suffix=".pdf")
    os.close(fd)
    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright, headless=True)
            try:
                context = new_context(browser, locale="en-HK")
                try:
                    page = context.new_page()
                    download_via_browser(page, url, temp_path, timeout_ms=90000)
                finally:
                    context.close()
            finally:
                browser.close()
        with open(temp_path, "rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise RuntimeError(f"浏览器下载内容不是 PDF：{url}")
        return temp_path
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def main() -> int:
    """解析命令行并归档月报；参数从 sys.argv 读取。"""

    url = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    fund_name = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    if not url or not fund_name:
        raise SystemExit("用法：python download_ninetyone_factsheet.py <Factsheet URL> <英文基金名>")
    temp_path: str | None = None
    try:
        try:
            temp_path = _download(url)
        except requests.HTTPError as direct_error:
            if direct_error.response is None or direct_error.response.status_code not in {401, 403, 429}:
                raise
            print(f"[!] requests 下载被拒绝，切换真实浏览器渠道：{direct_error}")
            temp_path = _download_browser(url)
        try:
            report_month = extract_report_month_from_pdf(temp_path, fallback_date=date.today().strftime("%Y-%m-%d"))
        except Exception:
            report_month = date.today().strftime("%Y-%m-%d")
        target, _, created = finalize_pdf_to_monthly_path(
            temp_path, COMPANY, fund_name, fallback_date=report_month
        )
        print(f"[*] Factsheet: {url}")
        print(f"[*] {'完成' if created else '已存在'} -> {target}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
