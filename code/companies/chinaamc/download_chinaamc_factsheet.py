# -*- coding: utf-8 -*-
"""
华夏基金（香港）www.chinaamc.com.hk —— 基金月报(Factsheet)自动下载

针对基金页，例如：
  https://www.chinaamc.com.hk/detail?fundId=HKGIGBF&fundName=ChinaAMC-Global-Investment-Grade-Bond-Fund

原理：
  页面是 Vue SPA，数据走 jeecg-boot 后端 API。文档列表接口：
    /jeecg-boot/mainWebDailyDocument/tMainWebDailyDocument/listByFundId?fundId=<fundId>
  返回每个文档的 ssCategory(类别)/ssLanguage(SC/TC/EN)/ssFileDate/ssFileFormat(相对路径)。
  月报的类别名： SC=基金月报, TC=基金月報, EN=Factsheet。
  下载地址 = https://www.chinaamc.com.hk/jeecg-boot/sys/common/static/<ssFileFormat>

用法：
  python download_chinaamc_factsheet.py HKGIGBF                # 下载最新月报(简体)
  python download_chinaamc_factsheet.py <基金页URL>            # 也可直接丢基金页链接(自动提fundId)
  python download_chinaamc_factsheet.py HKGIGBF --lang EN      # 英文版
"""

import os
import re
import sys

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path

API = "https://www.chinaamc.com.hk/jeecg-boot/mainWebDailyDocument/tMainWebDailyDocument/listByFundId"
FILE_BASE = "https://www.chinaamc.com.hk/jeecg-boot/sys/common/static/"
COMPANY = "chinaamc"
SAVE_DIR = company_data_dir(COMPANY)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
           "Accept": "application/json"}
TIMEOUT = 30
SESSION = requests.Session()
configure_session(SESSION)

# 各语言下"月报"的类别名
FACTSHEET_CATEGORY = {"SC": "基金月报", "TC": "基金月報", "EN": "Factsheet"}


def extract_fund_id(arg):
    m = re.search(r"fundId=([A-Za-z0-9]+)", arg)
    if m:
        return m.group(1)
    return arg.strip()


def parse_date(s):
    """ssFileDate 形如 29/05/2026 -> (年,月,日) 用于排序；解析失败返回 (0,0,0)。"""
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s or "")
    if not m:
        return (0, 0, 0)
    dd, mm, yy = m.groups()
    return (int(yy), int(mm), int(dd))


def date_tag(s):
    y, m, d = parse_date(s)
    return f"{y:04d}-{m:02d}-{d:02d}" if y else "nodate"


def fetch_documents(fund_id):
    r = SESSION.get(API, headers=HEADERS, params={"fundId": fund_id}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(data.get("message", "API 返回失败"))
    return data.get("result", [])


def download(url, dest):
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as r:
        r.raise_for_status()
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        os.replace(tmp, dest)
    return os.path.getsize(dest)


def main():
    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        print("用法: python download_chinaamc_factsheet.py <fundId或基金页URL> [--lang SC|TC|EN]")
        sys.exit(1)
    fund_id = extract_fund_id(positional[0])
    lang = "SC"
    if "--lang" in args:
        lang = args[args.index("--lang") + 1].upper()
    category = FACTSHEET_CATEGORY.get(lang, "基金月报")

    os.makedirs(SAVE_DIR, exist_ok=True)
    print(f"[*] fundId: {fund_id} | 语言: {lang} | 月报类别: {category}")

    docs = fetch_documents(fund_id)
    factsheets = [d for d in docs if d.get("ssLanguage") == lang and d.get("ssCategory") == category]
    if not factsheets:
        cats = sorted({(d.get("ssLanguage"), d.get("ssCategory")) for d in docs})
        print(f"[!] 没找到月报。该基金可用(语言,类别): {cats}")
        sys.exit(1)

    factsheets.sort(key=lambda d: parse_date(d.get("ssFileDate")), reverse=True)
    latest = factsheets[0]
    tag = date_tag(latest.get("ssFileDate"))
    rel = latest.get("ssFileFormat", "")
    url = FILE_BASE + rel.lstrip("/")
    dest = dated_output_path(COMPANY, f"{fund_id}_{lang}", tag)

    print(f"[*] 最新一期: {latest.get('ssFileDate')} | {latest.get('ssSubject')}")
    print(f"[*] 链接: {url}")
    if os.path.exists(dest):
        print(f"[*] 已存在，跳过: {os.path.basename(dest)}")
        return
    size = download(url, dest)
    print(f"[*] 完成 ({size/1024:.1f} KB) -> {os.path.basename(dest)}")
    print(f"[*] 保存目录: {SAVE_DIR}")


if __name__ == "__main__":
    main()
