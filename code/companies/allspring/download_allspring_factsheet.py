#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 Allspring 产品页 Documents 中最新 Monthly Fact Sheet。"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import extract_report_month_from_pdf, finalize_pdf_to_monthly_path  # noqa: E402

COMPANY = "allspring"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def _find_pdf(page_url: str) -> str:
    """从 Allspring 产品页解析月报链接；参数 page_url 为产品页 URL。"""
    response = requests.get(page_url, headers=HEADERS, timeout=90)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    candidates: list[str] = []
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split()).casefold()
        href = urljoin(page_url, str(anchor["href"]))
        if ".pdf" in href.casefold() and "fact" in text:
            candidates.append(href)
    if not candidates:
        raise RuntimeError(f"Allspring 产品页未暴露 Fact Sheet（需使用浏览器获取动态 Documents）：{page_url}")
    return candidates[0]


def main(argv: list[str] | None = None) -> int:
    """下载并归档 Allspring 月报；参数 argv 为产品页 URL 与英文基金名。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise SystemExit("用法：python download_allspring_factsheet.py <产品页URL> <英文基金名>")
    page_url, fund_name = args[0], (args[1] if len(args) > 1 else "Allspring")
    pdf_url = _find_pdf(page_url)
    fd, temp_path = tempfile.mkstemp(prefix="allspring_", suffix=".pdf")
    os.close(fd)
    try:
        response = requests.get(pdf_url, headers=HEADERS, timeout=120)
        response.raise_for_status()
        if not response.content.startswith(b"%PDF-"):
            raise RuntimeError(f"Allspring 下载内容不是 PDF：{response.url}")
        with open(temp_path, "wb") as handle:
            handle.write(response.content)
        fallback = date.today().strftime("%Y-%m-%d")
        try:
            report_month = extract_report_month_from_pdf(temp_path, fallback_date=fallback)
        except Exception:
            report_month = fallback
        target, _, created = finalize_pdf_to_monthly_path(temp_path, COMPANY, re.sub(r"[^\w.-]+", "_", fund_name), fallback_date=report_month)
        print(f"[*] PDF: {pdf_url}")
        print(f"[*] {'完成' if created else '已存在'} -> {target}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
