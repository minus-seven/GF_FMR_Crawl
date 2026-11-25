# -*- coding: utf-8 -*-
"""
易方达（香港）基金 —— 月度资料（月报）自动下载脚本

用法：
    python download_monthly_report.py                # 下载默认基金(968118)的最新月报
    python download_monthly_report.py 968118         # 指定基金代码
    python download_monthly_report.py 968118 --all   # 把网页上列出的所有月报都补全到本地

原理：
    基金详情页 https://www.efunds.com.cn/fund/<code>.shtml 是服务端渲染的，
    "基金月度资料" 那张表格里直接写了每期月报的 PDF 链接（cdn.efunds.com.cn）。
    脚本解析这些链接，按标题里的"YYYY年MM月份"取最新一期下载到本地。
    已经下载过的文件会跳过，所以每次运行只会补上新出的月报。
"""

import os
import re
import sys
import html
from urllib.parse import unquote

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
DEFAULT_FUND_CODE = "968118"
PAGE_URL_TMPL = "https://www.efunds.com.cn/fund/{code}.shtml"
COMPANY = "efunds_cn"
SAVE_DIR = company_data_dir(COMPANY)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
SESSION = requests.Session()
configure_session(SESSION)

# 匹配页面里的 PDF 锚点： <a href="....pdf" ...>标题</a>
ANCHOR_RE = re.compile(r'<a\s+href="(?P<url>https?://[^"]+?\.pdf)"[^>]*>(?P<title>.*?)</a>', re.I | re.S)
# 从标题里抽取年月，例如 "2026年04月份月度资料" / "2025年12月份月度资料"
MONTH_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")


def fetch_page(code):
    """抓取基金详情页 HTML（保持 UTF-8 解码）。"""
    url = PAGE_URL_TMPL.format(code=code)
    resp = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def parse_monthly_reports(page_html):
    """
    解析出所有"月度资料"条目。
    返回按年月降序排列的列表：[{year, month, title, url}, ...]
    """
    reports = []
    seen = set()
    for m in ANCHOR_RE.finditer(page_html):
        url = m.group("url")
        title = html.unescape(m.group("title")).strip()
        # 标题里可能是编码后的链接文字，统一只认"月度资料"这类月报
        # （排除年报、招募说明书、净值公告等其它公告）
        decoded_url = unquote(url)
        if "月度资料" not in title and "月度资料" not in decoded_url:
            continue
        ym = MONTH_RE.search(title) or MONTH_RE.search(decoded_url)
        if not ym:
            continue
        year, month = int(ym.group(1)), int(ym.group(2))
        key = (year, month)
        if key in seen:
            continue
        seen.add(key)
        reports.append({"year": year, "month": month, "title": title, "url": url})

    reports.sort(key=lambda r: (r["year"], r["month"]), reverse=True)
    return reports


def local_filename(code, report):
    """生成规范的本地文件名，例如 968118_2026-04_月度资料.pdf"""
    return f"{code}_{report['year']}-{report['month']:02d}_月度资料.pdf"


def download(report, dest_path):
    """下载单个 PDF 到 dest_path。"""
    with SESSION.get(report["url"], headers=HEADERS, timeout=TIMEOUT, stream=True) as r:
        r.raise_for_status()
        tmp_path = dest_path + ".part"
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        os.replace(tmp_path, dest_path)  # 原子替换，避免下到一半的残缺文件
    return os.path.getsize(dest_path)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    code = args[0] if args else DEFAULT_FUND_CODE
    download_all = "--all" in sys.argv[1:]

    os.makedirs(SAVE_DIR, exist_ok=True)
    print(f"[*] 基金代码: {code}")
    print(f"[*] 抓取页面: {PAGE_URL_TMPL.format(code=code)}")

    page_html = fetch_page(code)
    reports = parse_monthly_reports(page_html)
    if not reports:
        print("[!] 没有在页面上找到任何月度资料链接，页面结构可能已变化。")
        sys.exit(1)

    print(f"[*] 页面共发现 {len(reports)} 期月度资料，最新一期为 "
          f"{reports[0]['year']}年{reports[0]['month']:02d}月")

    targets = reports if download_all else reports[:1]
    new_count = 0
    for rep in targets:
        dest = dated_output_path(COMPANY, code, f"{rep['year']}-{rep['month']:02d}-01")
        if os.path.exists(dest):
            print(f"    - 已存在，跳过: {os.path.basename(dest)}")
            continue
        print(f"    - 下载中: {rep['year']}年{rep['month']:02d}月 -> {os.path.basename(dest)}")
        size = download(rep, dest)
        print(f"      完成 ({size/1024:.1f} KB)")
        new_count += 1

    if new_count == 0:
        print("[*] 本地已是最新，无需下载。")
    else:
        print(f"[*] 本次新增下载 {new_count} 个文件，保存目录: {SAVE_DIR}")


if __name__ == "__main__":
    main()
