# -*- coding: utf-8 -*-
"""汇丰环球投资基金（HSBC GIF）—— 按年报章节切分下载脚本。

背景：HSBC GIF 伞下部分子基金在基金中心没有月报（或未上架），但官网提供
伞级年报（Audited Annual Report，实测 1,064 页，覆盖全部子基金）。
本脚本复用 `common.annual_report_split`：
  1. 下载并缓存 HSBC GIF 年报；
  2. 解析目录（英文子基金名 + 起始页，结束页取下一支基金起始页-1）；
  3. 按名称匹配子基金（去 HSBC Global Investment Funds 前缀、去括号说明）；
  4. 切出该子基金章节并落盘。

用法：
  python download_hsbc_annual_factsheet.py "HSBC Global Investment Funds - Global Corporate Bond"
  python download_hsbc_annual_factsheet.py "Global Flexible Securitised Credit Bond"
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import PROJECT_ROOT, company_data_dir, finalize_pdf_to_monthly_path  # noqa: E402
from common import annual_report_split as split_lib  # noqa: E402

try:
    import pymupdf
except ImportError:  # 兼容旧包名
    import fitz as pymupdf  # type: ignore


COMPANY = "hsbc"
ANNUAL_REPORT_URL = (
    "https://www.assetmanagement.hsbc.com.hk/api/v1/download/document/"
    "lu1823213035/hk/zh/annual%20report"
)
CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "_cache", "hsbc_annual")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TOC_PAGES = 8
_TOC_CACHE: dict[str, dict] = {}


def report_path():
    """返回年报本地缓存路径（不存在则下载）。"""
    return split_lib.download_report(
        ANNUAL_REPORT_URL, CACHE_DIR, filename="hsbc_gif_annual_report.pdf", headers=HEADERS
    )


def load_toc():
    """解析年报目录，返回 {英文子基金名: (起始页, 结束页)}（进程内缓存）。"""
    if "entries" not in _TOC_CACHE:
        path = report_path()
        document = pymupdf.open(path)
        text = "\n".join(document[i].get_text() for i in range(0, min(TOC_PAGES, document.page_count)))
        document.close()
        _TOC_CACHE["entries"] = split_lib.parse_toc_entries(text)
    return _TOC_CACHE["entries"]


def resolve_section(fund_name):
    """按基金名查年报章节，返回 (起始页, 结束页, 目录原名) 或 None。"""
    return split_lib.resolve_entry(load_toc(), fund_name)


def main():
    """命令行入口：切分指定子基金的年报章节。"""
    if len(sys.argv) < 2:
        print("用法: python download_hsbc_annual_factsheet.py <子基金英文名>")
        sys.exit(1)
    fund_name = sys.argv[1].strip()
    section = resolve_section(fund_name)
    if not section:
        print(f"[!] 年报目录中未找到子基金：{fund_name}")
        sys.exit(1)
    start, end, matched = section
    print(f"[*] 子基金：{fund_name} → 目录条目：{matched}（第 {start}-{end} 页）")

    os.makedirs(company_data_dir(COMPANY), exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=company_data_dir(COMPANY)) as tmp:
        tmp_path = tmp.name
    try:
        pages = split_lib.extract_pages(report_path(), start, end, tmp_path)
        output_path, _report_month, _moved = finalize_pdf_to_monthly_path(
            tmp_path, COMPANY, os.path.basename(fund_name), fallback_date="2026-03-31"
        )
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print(f"[*] 已切分 {pages} 页 → {output_path}")


if __name__ == "__main__":
    main()
