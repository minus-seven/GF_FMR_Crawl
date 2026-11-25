#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载并归档 Nomura Funds Ireland 官方 Factsheet。"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import date

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import extract_report_month_from_pdf, finalize_pdf_to_monthly_path  # noqa: E402

COMPANY = "nomura"


def main(argv: list[str] | None = None) -> int:
    """下载并归档 PDF；参数 argv 为 PDF URL 与英文基金名。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise SystemExit("用法：python download_nomura_factsheet.py <PDF URL> <英文基金名>")
    url, fund_name = args[0], (args[1] if len(args) > 1 else "Nomura")
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF-"):
        raise RuntimeError(f"Nomura 下载内容不是 PDF：{response.url}")
    fd, temp_path = tempfile.mkstemp(prefix="nomura_", suffix=".pdf")
    os.close(fd)
    try:
        with open(temp_path, "wb") as handle:
            handle.write(response.content)
        fallback = date.today().strftime("%Y-%m-%d")
        try:
            report_month = extract_report_month_from_pdf(temp_path, fallback_date=fallback)
        except Exception:
            report_month = fallback
        target, _, created = finalize_pdf_to_monthly_path(temp_path, COMPANY, re.sub(r"[^\w.-]+", "_", fund_name), fallback_date=report_month)
        print(f"[*] PDF: {url}")
        print(f"[*] {'完成' if created else '已存在'} -> {target}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
