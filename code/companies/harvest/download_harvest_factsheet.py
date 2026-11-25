# -*- coding: utf-8 -*-
"""
嘉实（Harvest Global Investments 香港）基金月报下载脚本。

目标页示例：
  https://www.harvestglobal.com.hk/hgi/index.php/funds/active/HCEF

原理：
  站点先访问 /hgi/index.php?landing 拿到 ReVisit 放行 cookie，之后基金页会
  把文档列表以 HTML 转义 JSON 内嵌在页面里（fund_file_title /
  fund_file_media_pdf_info.media_url / fund_file_launch_date）。本脚本据此挑出
  最新的“月报类”文档（Monthly Factsheet / 基金月報 / Monthly Return Report），
  同月份优先港元份额，再下载当前 PDF。

用法：
  python download_harvest_factsheet.py "https://www.harvestglobal.com.hk/hgi/index.php/funds/active/HCEF"
  python download_harvest_factsheet.py HCEF            # 代码形式需能推断路径，建议直接用页面 URL
"""

import html
import os
import re
import sys
import tempfile
from datetime import datetime
from urllib.parse import unquote, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, finalize_pdf_to_monthly_path


LANDING_URL = "https://www.harvestglobal.com.hk/hgi/index.php?landing"
BASE_URL = "https://www.harvestglobal.com.hk"
COMPANY = "harvest"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 40
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
# 月报类文档标题：英文 Monthly Factsheet / 中文 基金月報 / ETF Monthly Return Report
MONTHLY_TITLE_RE = re.compile(r"(Monthly[\s\S]{0,20}Factsheet|Monthly Return Report|月報|月报)", re.I)
TITLE_RE = re.compile(r'"fund_file_title":"([^"]{1,60})"')
MEDIA_RE = re.compile(r'"media_url":"([^"]+)"')
DATE_RE = re.compile(r'"fund_file_launch_date":"([^"]+)"')
STATE_RE = re.compile(r'"fund_file_state":"([^"]+)"')
CURRENCY_RE = re.compile(r'"fund_cate_currency":"([^"]+)"')
CURRENCY_RANK = {"HKD": 0, "港元": 0, "USD": 1, "美元": 1}

SESSION = requests.Session()
configure_session(SESSION)


def fetch_page(url):
    """抓取页面（先过一次 landing 拿放行 cookie），返回解码后的 HTML。"""
    SESSION.get(LANDING_URL, headers=HEADERS, timeout=TIMEOUT)
    resp = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return html.unescape(resp.text)


def parse_date(value):
    """解析 'Jun 2026' 形式的日期，失败返回 datetime.min。"""
    for fmt in ("%b %Y", "%B %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except (TypeError, ValueError):
            continue
    return datetime.min


def parse_monthly_documents(page_html):
    """解析页面内嵌 JSON，返回 [{title, url, date, currency}]（仅月报类文档）。"""
    documents = []
    matches = list(TITLE_RE.finditer(page_html))
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        if not MONTHLY_TITLE_RE.search(title):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(page_html)
        window = page_html[match.start():end]
        media = MEDIA_RE.search(window)
        date = DATE_RE.search(window)
        currency = CURRENCY_RE.search(window)
        state = STATE_RE.search(window)
        if not media or (state and state.group(1) != "published"):
            continue
        documents.append(
            {
                "title": title,
                "url": media.group(1),
                "date": parse_date(date.group(1) if date else ""),
                "currency": (currency.group(1) if currency else ""),
            }
        )
    return documents


def select_latest(documents):
    """挑最新一期月报；同月按 港元 > 美元 > 其他 选份额。"""
    if not documents:
        raise RuntimeError("页面未找到任何月报类文档（Monthly Factsheet / 基金月報）")
    ranked = sorted(
        documents,
        key=lambda item: (
            item["date"],
            -CURRENCY_RANK.get(str(item["currency"]).upper(), 2),
        ),
        reverse=True,
    )
    best_date = ranked[0]["date"]
    same_period = [item for item in ranked if item["date"] == best_date]
    same_period.sort(key=lambda item: CURRENCY_RANK.get(str(item["currency"]).upper(), 2))
    return same_period[0]


def download_pdf(url, dest):
    """下载 PDF 到 dest，返回 (字节数, 官方文件名)。"""
    tmp = dest + ".part"
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as fh:
            first = True
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"Downloaded content is not a PDF: {url}")
                first = False
                fh.write(chunk)
    os.replace(tmp, dest)
    return os.path.getsize(dest), os.path.basename(unquote(urlparse(url).path))


def resolve_page_url(arg):
    """把参数解析成基金页 URL（已是 URL 则直接用，否则按代码拼 active 路径）。"""
    if arg.startswith("http://") or arg.startswith("https://"):
        return arg
    return f"{BASE_URL}/hgi/index.php/funds/active/{arg.strip()}"


def main():
    """命令行入口：下载指定基金页的最新月报。"""
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    if not arg:
        print("用法: python download_harvest_factsheet.py <基金页URL|基金代码>")
        sys.exit(1)
    page_url = resolve_page_url(arg)
    os.makedirs(SAVE_DIR, exist_ok=True)
    print(f"[*] Page: {page_url}")

    documents = parse_monthly_documents(fetch_page(page_url))
    document = select_latest(documents)
    print(f"[*] Selected: {document['title']} / {document['date']:%Y-%m} / {document['currency']}")
    print(f"[*] PDF: {document['url']}")

    fund_label = urlparse(page_url).path.rstrip("/").rsplit("/", 1)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=SAVE_DIR) as tmp:
        tmp_path = tmp.name
    try:
        size, official_name = download_pdf(document["url"], tmp_path)
        output_path, report_month, moved = finalize_pdf_to_monthly_path(
            tmp_path, COMPANY, fund_label, fallback_date=document["date"].strftime("%Y-%m")
        )
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    if moved:
        print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month}] -> {output_path}")
    else:
        print(f"[*] Existing [{report_month}], skipped -> {output_path}")


if __name__ == "__main__":
    main()
