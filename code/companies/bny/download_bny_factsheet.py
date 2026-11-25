#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载纽约梅隆香港官方 Factsheet PDF 并归档为月报。"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import extract_report_month_from_pdf, finalize_pdf_to_monthly_path  # noqa: E402

COMPANY = "bny"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/pdf,*/*"}


def main() -> int:
    """下载命令行传入的官方 PDF；参数为 url、英文基金名。"""

    url = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    fund_name = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    if not url or not fund_name:
        raise SystemExit("用法：python download_bny_factsheet.py <官方PDF> <英文基金名>")
    fd, temp_path = tempfile.mkstemp(prefix="bny_", suffix=".pdf")
    os.close(fd)
    try:
        response = requests.get(url, headers=HEADERS, timeout=120)
        response.raise_for_status()
        if not response.content.startswith(b"%PDF-"):
            raise RuntimeError(f"纽约梅隆官方链接返回内容不是 PDF：{response.url}")
        with open(temp_path, "wb") as handle:
            handle.write(response.content)
        fallback = date.today().strftime("%Y-%m-%d")
        try:
            report_month = extract_report_month_from_pdf(temp_path, fallback_date=fallback)
        except Exception:
            report_month = fallback
        target, _, created = finalize_pdf_to_monthly_path(temp_path, COMPANY, fund_name, fallback_date=report_month)
        print(f"[*] {'完成' if created else '已存在'} -> {target}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
