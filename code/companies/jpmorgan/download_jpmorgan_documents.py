# -*- coding: utf-8 -*-
"""
摩根资产管理（香港）am.jpmorgan.com —— 基金文件自动下载脚本

针对产品页，例如：
  https://am.jpmorgan.com/hk/zh/asset-management/per/products/
  jpmorgan-global-investment-grade-bond-acc-usd-hk0000914694#/documents

原理：
  该页面是 AEM 单页应用，但文档清单直接内嵌在 HTML 的 JS 变量里：
      documents = {"<ISIN>":[{documentType, docURL, title, onDate, ...}, ...]};
  脚本抓页面 -> 正则取出这段 JSON -> 找到目标文档类型 -> 下载 PDF。
  摩根的 PDF 是固定路径（每月覆盖同一文件），所以用文档的 onDate 发布日期命名，
  保证每期都能在本地留档；已存在则跳过。

用法：
  python download_jpmorgan_documents.py <产品页URL>           # 下载最新【基金報告/月报】
  python download_jpmorgan_documents.py <URL> --type "Annual Report"   # 指定文档类型
  python download_jpmorgan_documents.py <URL> --all          # 下载该基金所有文档
  python download_jpmorgan_documents.py                      # 用内置默认基金(环球投资级别债券)

文档类型(documentType)常见取值：
  Fact Sheet (US EMEA)      基金報告（月报，默认）
  Semi-Annual Report        中期報告
  Annual Report             年度報告
  Offering Document (APAC)  銷售文件及產品資料概要
"""

import os
import re
import sys
import json

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
DEFAULT_URL = ("https://am.jpmorgan.com/hk/zh/asset-management/per/products/"
               "jpmorgan-global-investment-grade-bond-acc-usd-hk0000914694")
DEFAULT_DOC_TYPE = "Fact Sheet (US EMEA)"   # 基金報告 = 月报
COMPANY = "jpmorgan"
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

# 页面里内嵌的文档清单： documents= {"ISIN":[ ... ]};
# 注意页面里还有个空的 documents= {}; 默认分支，必须要求紧跟 {"key":[ 才是真正的清单
DOCS_RE = re.compile(r'documents\s*=\s*(\{\s*"[^"]+"\s*:\s*\[.*?\]\s*\})\s*;', re.S)
ISIN_RE = re.compile(r"(HK\d{10}|[A-Z]{2}\d{10}|[A-Z0-9]{12})")  # 从 URL 兜底提取 ISIN


def clean_url(url):
    """去掉 #/documents 之类的前端路由片段。"""
    return url.split("#")[0].rstrip("/")


def fetch_page(url):
    resp = SESSION.get(clean_url(url), headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def parse_documents(page_html):
    """返回 (isin, [{documentType, title, docURL, onDate}, ...])"""
    m = DOCS_RE.search(page_html)
    if not m:
        return None, []
    data = json.loads(m.group(1))
    # data 形如 {"HK0000914694": [ {...}, ... ]}
    isin = next(iter(data.keys()), None)
    docs = data.get(isin, []) if isin else []
    # 只保留 PDF
    docs = [d for d in docs if str(d.get("docURL", "")).lower().endswith(".pdf")]
    return isin, docs


def date_tag(on_date):
    """onDate 形如 2026-05-27T21:55:00.000-0400 -> 2026-05-27"""
    if not on_date:
        return "nodate"
    return str(on_date)[:10]


def safe(name):
    name = re.sub(r"\s+", "_", str(name).strip())
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def local_filename(isin, doc):
    """例如 HK0000914694_FactSheet_2026-05-27.pdf"""
    dtype = safe(doc.get("documentType", "doc")).replace("(", "").replace(")", "")
    return f"{isin}_{dtype}_{date_tag(doc.get('onDate'))}.pdf"


def download(url, dest_path):
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as r:
        r.raise_for_status()
        tmp = dest_path + ".part"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        os.replace(tmp, dest_path)
    return os.path.getsize(dest_path)


def main():
    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("--")]
    url = positional[0] if positional else DEFAULT_URL

    download_all = "--all" in args
    doc_type = DEFAULT_DOC_TYPE
    if "--type" in args:
        i = args.index("--type")
        if i + 1 < len(args):
            doc_type = args[i + 1]

    os.makedirs(SAVE_DIR, exist_ok=True)
    print(f"[*] 产品页: {clean_url(url)}")

    page_html = fetch_page(url)
    isin, docs = parse_documents(page_html)
    if not docs:
        print("[!] 没在页面里解析到文档清单，页面结构可能已变化。")
        sys.exit(1)
    if not isin:
        m = ISIN_RE.search(url.upper())
        isin = m.group(1) if m else "UNKNOWN"

    print(f"[*] ISIN: {isin}，共发现 {len(docs)} 个文档:")
    for d in docs:
        print(f"      - {d.get('documentType')} | {d.get('title')} | {date_tag(d.get('onDate'))}")

    if download_all:
        targets = docs
    else:
        targets = [d for d in docs if d.get("documentType") == doc_type]
        if not targets:
            types = ", ".join(sorted({d.get("documentType", "") for d in docs}))
            print(f"[!] 没找到类型 '{doc_type}'。可选: {types}")
            sys.exit(1)

    new_count = 0
    for d in targets:
        fname = local_filename(isin, d)
        dest = dated_output_path(COMPANY, f"{isin}_{d.get('documentType')}", d.get("onDate"))
        if os.path.exists(dest):
            print(f"    - 已存在，跳过: {fname}")
            continue
        print(f"    - 下载中: {d.get('documentType')} ({date_tag(d.get('onDate'))}) -> {fname}")
        size = download(d["docURL"], dest)
        print(f"      完成 ({size/1024:.1f} KB)")
        new_count += 1

    if new_count == 0:
        print("[*] 本地已是最新，无需下载。")
    else:
        print(f"[*] 本次新增下载 {new_count} 个文件，保存目录: {SAVE_DIR}")


if __name__ == "__main__":
    main()
