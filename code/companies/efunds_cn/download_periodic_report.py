# -*- coding: utf-8 -*-
"""
易方达（内地站）最新「定期报告」下载脚本。

适用：内地公募基金没有「基金月度资料」时，退而下载最新定期公告，
      例如“易方达安心回报债券型证券投资基金2026年中期报告”。

页面：https://www.efunds.com.cn/fund/<code>.shtml （服务端渲染）
解析：页面“最新公告”区域按时间倒序排列，取第一条标题含“报告”的 PDF 链接
      （中期报告/年度报告/季度报告），排除招募说明书、产品资料概要等。

用法：
  python download_periodic_report.py 110027
  python download_periodic_report.py 110027 --all   # 下载页面上全部报告类文件
"""

import os
import re
import sys
import html

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import dated_output_path  # noqa: E402
from download_monthly_report import (  # noqa: E402
    ANCHOR_RE,
    COMPANY,
    PAGE_URL_TMPL,
    SAVE_DIR,
    download,
    fetch_page,
)

REPORT_TYPES = ("年度报告", "中期报告", "季度报告")
YEAR_RE = re.compile(r"(\d{4})\s*年")
QUARTER_RE = re.compile(r"第\s*([1-4])\s*季度")


def parse_periodic_reports(page_html):
    """解析页面中的定期报告条目，按页面顺序（最新在前）返回。"""
    reports = []
    seen = set()
    for match in ANCHOR_RE.finditer(page_html):
        url = match.group("url")
        title = html.unescape(match.group("title")).strip()
        if not any(kind in title for kind in REPORT_TYPES):
            continue
        year_match = YEAR_RE.search(title)
        year = int(year_match.group(1)) if year_match else 0
        quarter_match = QUARTER_RE.search(title)
        quarter = int(quarter_match.group(1)) if quarter_match else 0
        key = (title, url)
        if key in seen:
            continue
        seen.add(key)
        reports.append(
            {"title": title, "url": url, "year": year, "quarter": quarter}
        )
    return reports


def main():
    """命令行入口：下载指定基金最新一期定期报告。"""
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print("用法: python download_periodic_report.py <基金代码> [--all]")
        sys.exit(1)
    code = args[0]
    download_all = "--all" in sys.argv[1:]
    os.makedirs(SAVE_DIR, exist_ok=True)

    page_url = PAGE_URL_TMPL.format(code=code)
    print(f"[*] 基金代码: {code}")
    print(f"[*] 抓取页面: {page_url}")

    reports = parse_periodic_reports(fetch_page(code))
    if not reports:
        print("[!] 页面未找到定期报告链接（页面结构可能已变化）。")
        sys.exit(1)
    print(f"[*] 发现 {len(reports)} 条定期报告，最新为：{reports[0]['title']}")

    targets = reports if download_all else reports[:1]
    new_count = 0
    for report in targets:
        tag = f"{report['year']}-{report['quarter'] or 0}" if report["year"] else "latest"
        dest = dated_output_path(COMPANY, f"{code}_{report['title'][:40]}", f"{report['year'] or 0}-01-01")
        if os.path.exists(dest):
            print(f"    - 已存在，跳过: {os.path.basename(dest)}")
            continue
        print(f"    - 下载中: {report['title'][:60]} -> {os.path.basename(dest)}")
        size = download(report, dest)
        print(f"      完成 ({size/1024:.1f} KB)")
        new_count += 1

    if new_count == 0:
        print("[*] 本地已是最新，无需下载。")
    else:
        print(f"[*] 本次新增下载 {new_count} 个文件，保存目录: {SAVE_DIR}")


if __name__ == "__main__":
    main()
