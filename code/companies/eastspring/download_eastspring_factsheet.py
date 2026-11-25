#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 Eastspring 香港官方 Factsheet PDF 并按报告月份归档。"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, extract_report_month_from_pdf, finalize_pdf_to_monthly_path  # noqa: E402


COMPANY = "eastspring"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
SESSION = configure_session(requests.Session())


def main() -> int:
    """解析命令行并下载归档；参数从 sys.argv 读取。"""

    pdf_url = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    fund_name = str(sys.argv[2] if len(sys.argv) > 2 else "Eastspring Fund").strip()
    if not pdf_url:
        raise SystemExit("用法：python download_eastspring_factsheet.py <Factsheet PDF> [基金英文名]")
    fd, temp_path = tempfile.mkstemp(prefix="eastspring_", suffix=".pdf")
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
                        raise RuntimeError(f"Eastspring 下载内容不是 PDF：{response.url}")
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
