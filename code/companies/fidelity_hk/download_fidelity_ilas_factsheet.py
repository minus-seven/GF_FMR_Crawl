# -*- coding: utf-8 -*-
"""
富达环球投资基金（FGIF / ILAS 伞）—— 按子基金章节切分下载脚本。

背景：
  FGIF 是 ILAS/MPF 平台的伞子基金，官网没有逐支基金产品页，只发布伞级
  半年度/年度报告（例如 fidelity.com.cn 的
  /media/Fund_Report/MRF/fgif-semi-annual-report-2026-mrf.pdf，353 页）。

做法：
  1. 下载伞级报告（本地缓存，避免 31 次重复下载）；
  2. 解析第 2–3 页目录，得到每个子基金的中文名与起止页码
     （目录页码与 PDF 物理页索引一一对应，已实测）；
  3. 按子基金页码范围切出独立 PDF，再用 common_paths 的归档逻辑落盘。

用法：
  python download_fidelity_ilas_factsheet.py "增长基金"
  python download_fidelity_ilas_factsheet.py "富达环球投资基金 - 增长基金"
  python download_fidelity_ilas_factsheet.py "储蓄易2040基金"
  python download_fidelity_ilas_factsheet.py --refresh-links   # 把页表 ILAS 行 I 列刷新为最新报告 URL
"""

import os
import re
import sys
import tempfile
from datetime import datetime

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import PROJECT_ROOT, company_data_dir, finalize_pdf_to_monthly_path  # noqa: E402
from common import annual_report_split as split_lib  # noqa: E402

try:
    import pymupdf
except ImportError:  # 兼容旧包名
    import fitz as pymupdf  # type: ignore


COMPANY = "fidelity_hk"
DEFAULT_REPORT_URL = (
    "https://www.fidelity.com.cn/media/Fund_Report/MRF/fgif-semi-annual-report-2026-mrf.pdf"
)
REPORT_URL = os.environ.get("FGIF_REPORT_URL", DEFAULT_REPORT_URL)
REPORT_URL_TMPL = "https://www.fidelity.com.cn/media/Fund_Report/MRF/{name}"
# 官方命名规律：半年度 fgif-semi-annual-report-YYYY-mrf.pdf；年度 fgif-annual-report-YYYY-mrf.pdf
REPORT_CANDIDATES = (
    ("semi", "fgif-semi-annual-report-{year}-mrf.pdf", (6, 30)),
    ("annual", "fgif-annual-report-{year}-mrf.pdf", (12, 31)),
)
REPORT_PROBE_YEARS = 5
CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "_cache", "fidelity_ilas")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 180
# 目录里的子基金名与页表名的差异（按需补充）
NAME_ALIASES = {
    "美国股票基金": "美洲股票基金",
    "美国股票基金(强积金)": "美洲股票基金(强积金)",
}
TOC_TEXT_SKIP = re.compile(
    r"目录|页码|份额持有人|资产净值表|现金流量表|投资组合|金融衍生工具|"
    r"管理及行政|投资管理人报告|半年度报告及账目"
)


def normalize_name(value):
    """名称归一化：简繁/引号/括号/空格/品牌词统一，便于目录匹配。

    参数 value：原始名称；返回归一化字符串。
    """
    text = str(value or "")
    for src, dst in (
        ("（", "("), ("）", ")"), (" ", ""), ("\u3000", ""),
        ("—", "-"), ("–", "-"), ("－", "-"),
        ("投資", "投资"), ("環", "环"), ("增長", "增长"), ("財", "财"),
        ("“", ""), ("”", ""), ("「", ""), ("」", ""), ("《", ""), ("》", ""),
        ("富达", ""), ("富達", ""),
    ):
        text = text.replace(src, dst)
    text = re.sub(r"^环球投资基金-?", "", text)
    return text


def download_report(url=None):
    """下载伞级报告到本地缓存，返回本地路径（已存在则直接复用）。

    参数 url：报告 PDF 地址（None 时自动探测最新一期）；返回缓存文件路径。
    """
    if not url:
        url = REPORT_URL if os.environ.get("FGIF_REPORT_URL") else resolve_latest_report_url()[0]
    return split_lib.download_report(url, CACHE_DIR, headers=HEADERS, timeout=TIMEOUT)


def _probe_url(url):
    """探测报告 URL 是否存在（HEAD，失败再退回流式 GET）。"""
    try:
        response = requests.head(url, headers=HEADERS, timeout=30, allow_redirects=True)
        if response.status_code == 200:
            return True
        response = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        ok = response.status_code == 200 and "pdf" in (response.headers.get("content-type") or "").lower()
        response.close()
        return ok
    except Exception:  # noqa: BLE001
        return False


def resolve_latest_report_url():
    """按官方命名规律探测最近数年的半年度/年度报告，返回最新一期 URL。

    无参数；返回 (url, 期间末日期 datetime)。找不到时回退 DEFAULT_REPORT_URL。
    """
    today = datetime.now()
    best = None
    for year in range(today.year, today.year - REPORT_PROBE_YEARS, -1):
        for _kind, template, (month, day) in REPORT_CANDIDATES:
            url = REPORT_URL_TMPL.format(name=template.format(year=year))
            if not _probe_url(url):
                continue
            period_end = datetime(year, month, day)
            if best is None or period_end > best[1]:
                best = (url, period_end)
    if best:
        return best
    return DEFAULT_REPORT_URL, datetime(2026, 6, 30)


def parse_toc(report_path):
    """解析报告目录，返回 {归一化子基金名: (起始页, 结束页)}。

    参数 report_path：伞级报告本地路径；页码与 PDF 页索引一致（0 基）。
    """
    document = pymupdf.open(report_path)
    text = "\n".join(document[i].get_text() for i in range(1, min(4, document.page_count)))
    document.close()
    raw_entries = split_lib.parse_toc_entries(text)
    return {normalize_name(name): span for name, span in raw_entries.items()}


def resolve_section(report_path, fund_name):
    """返回子基金的 (起始页, 结束页, 目录名)；找不到返回 None。

    参数 report_path：报告路径；fund_name：页表基金名或简称。
    """
    entries = parse_toc(report_path)
    core = re.sub(r"^富达环球投资基金\s*[-—–－]\s*", "", str(fund_name or "").strip())
    core = NAME_ALIASES.get(core, core)
    key = normalize_name(core)
    if key in entries:
        return (*entries[key], core)
    # 兜底：含“储蓄易”等写法差异时做包含匹配
    for name, span in entries.items():
        if key and (key in name or name in key):
            return (*span, name)
    return None


def extract_section(report_path, start, end, dest_path):
    """把报告的 [start, end] 页（含端点）切分写入 dest_path。

    参数 report_path：源报告；start/end：页码；dest_path：输出 PDF。
    """
    return split_lib.extract_pages(report_path, start, end, dest_path)


def main():
    """命令行入口：按子基金名切分下载最新伞级报告章节。"""
    if len(sys.argv) < 2:
        print("用法: python download_fidelity_ilas_factsheet.py <子基金名> | --refresh-links")
        sys.exit(1)
    if sys.argv[1].strip() == "--refresh-links":
        refresh_sheet_links()
        return
    fund_name = sys.argv[1].strip()
    report_path = download_report()
    section = resolve_section(report_path, fund_name)
    if not section:
        print(f"[!] 报告中未找到子基金：{fund_name}")
        sys.exit(1)
    start, end, matched = section
    print(f"[*] 子基金：{fund_name} → 目录条目：{matched}（第 {start}-{end} 页）")

    os.makedirs(company_data_dir(COMPANY), exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=company_data_dir(COMPANY)) as tmp:
        tmp_path = tmp.name
    try:
        pages = extract_section(report_path, start, end, tmp_path)
        output_path, report_month, moved = finalize_pdf_to_monthly_path(
            tmp_path, COMPANY, fund_name, fallback_date="2026-06-30"
        )
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print(f"[*] 已切分 {pages} 页 → {output_path}")


def refresh_sheet_links():
    """把「富达」页表 ILAS 行的 I 列刷新为当前最新报告 URL（保留 J 不变）。"""
    import openpyxl

    latest_url, period_end = resolve_latest_report_url()
    workbook_path = os.path.join(PROJECT_ROOT, "爬虫list.xlsx")
    workbook = openpyxl.load_workbook(workbook_path)
    sheet = workbook["富达"]
    header = [str(c.value).strip() if c.value is not None else "" for c in sheet[1]]
    umbrella_col = header.index("伞") + 1
    link_col = header.index("月报链接") + 1
    changed = 0
    for row in range(2, sheet.max_row + 1):
        if str(sheet.cell(row, umbrella_col).value or "").strip() != "富达环球投资基金":
            continue
        if not str(sheet.cell(row, 2).value or "").strip():
            continue
        if sheet.cell(row, link_col).value != latest_url:
            sheet.cell(row, link_col).value = latest_url
            changed += 1
    workbook.save(workbook_path)
    print(f"[*] 最新 FGIF 报告：{latest_url}（期间截至 {period_end:%Y-%m-%d}）")
    print(f"[*] 已刷新 ILAS 行 {changed} 条")


if __name__ == "__main__":
    main()
