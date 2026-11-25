# -*- coding: utf-8 -*-
"""
东方汇理（Amundi）香港 www.amundi.com.hk —— 基金月报(Monthly Factsheet)自动下载

针对产品页，例如：
  https://www.amundi.com.hk/zh_retail/product/view/HK0000319415

原理：
  产品页的数据由前端组件渲染，但月报 PDF 有一个**确定性下载地址**：
    https://www.amundi.com.hk/dl/doc/monthly-factsheet/<ISIN>/<LANG>/HKG/RETAIL/AMUNDI/<YYYYMMDD>
  其中 <YYYYMMDD> 是月报的数据截止日(每月最后一天)，LANG = ZHO(中文) / ENG(英文)。
  脚本从当前月份往前逐月探测，找到最新一期(返回 PDF)就下载。
  月报 PDF 内含：基金规模、十大持仓、地区分布、行业分布、派息记录等。

用法：
  python download_amundi_factsheet.py HK0000319415              # 下载最新中文月报
  python download_amundi_factsheet.py HK0000319415 --lang ENG   # 英文版
  python download_amundi_factsheet.py <产品页URL>               # 也可直接丢产品页链接(自动提ISIN)
"""

import os
import re
import sys
import datetime

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path

BASE = "https://www.amundi.com.hk/dl/doc/monthly-factsheet/{isin}/{lang}/HKG/RETAIL/AMUNDI/{date}"
COMPANY = "amundi"
SAVE_DIR = company_data_dir(COMPANY)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
TIMEOUT = 30
SESSION = requests.Session()
configure_session(SESSION)
LOOKBACK_MONTHS = 6          # 最多往前找几个月
ISIN_RE = re.compile(r"([A-Z]{2}[A-Z0-9]{9}\d)")


def month_end(year, month):
    """返回某年某月最后一天的 date。"""
    if month == 12:
        nxt = datetime.date(year + 1, 1, 1)
    else:
        nxt = datetime.date(year, month + 1, 1)
    return nxt - datetime.timedelta(days=1)


def recent_month_ends(n):
    """从本月往前 n 个月，依次返回各月最后一天(YYYYMMDD)，最新在前。"""
    today = datetime.date.today()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(month_end(y, m).strftime("%Y%m%d"))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


def extract_isin(arg):
    m = ISIN_RE.search(arg.upper())
    return m.group(1) if m else arg


def try_download(isin, lang, date_str, dest):
    url = BASE.format(isin=isin, lang=lang, date=date_str)
    r = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT)
    ctype = r.headers.get("content-type", "")
    if r.status_code == 200 and "pdf" in ctype.lower() and r.content[:4] == b"%PDF":
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(r.content)
        os.replace(tmp, dest)
        return len(r.content)
    return None


def main():
    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        print("用法: python download_amundi_factsheet.py <ISIN或产品页URL> [--lang ZHO|ENG]")
        sys.exit(1)
    isin = extract_isin(positional[0])
    lang = "ZHO"
    if "--lang" in args:
        lang = args[args.index("--lang") + 1].upper()

    os.makedirs(SAVE_DIR, exist_ok=True)
    print(f"[*] ISIN: {isin} | 语言: {lang}")

    for date_str in recent_month_ends(LOOKBACK_MONTHS):
        dest = dated_output_path(COMPANY, f"{isin}_{lang}", date_str)
        if os.path.exists(dest):
            print(f"[*] 最新一期({date_str})已存在，跳过: {os.path.basename(dest)}")
            return
        print(f"    - 尝试 {date_str} ...", end=" ")
        size = try_download(isin, lang, date_str, dest)
        if size:
            print(f"命中并下载完成 ({size/1024:.1f} KB) -> {os.path.basename(dest)}")
            print(f"[*] 保存目录: {SAVE_DIR}")
            return
        print("无")

    print(f"[!] 往前 {LOOKBACK_MONTHS} 个月都没找到月报，请检查 ISIN 或语言代码。")
    sys.exit(1)


if __name__ == "__main__":
    main()
