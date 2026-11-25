# -*- coding: utf-8 -*-
"""
BEA Union Investment (BUIM) factsheet downloader.

Target page:
  https://buim.com/cht/literature-download

The page is server-rendered HTML. Fund factsheet links are listed directly in
the table as PDF links under images/data/Fundinfo/FS/TC/.

Usage:
  python download_buim_factsheet.py              # update default target funds
  python download_buim_factsheet.py ASB          # update Asian Strategic Bond Fund
  python download_buim_factsheet.py GLE          # update Global Equity Fund
  python download_buim_factsheet.py "東亞聯豐環球股票基金"
"""

import html
import os
import re
import sys
import tempfile
from urllib.parse import urljoin

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, finalize_pdf_to_monthly_path


BASE_URL = "https://buim.com/"
PAGE_URL = "https://buim.com/cht/literature-download"
COMPANY = "buim"
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

# 基金代码 ↔ 官网文献页中的繁体基金名（供 J 列用代码调用）
TARGETS = {
    "GLE": "東亞聯豐環球股票基金",
    "GLB": "東亞聯豐環球債券基金",
    "GCG": "東亞聯豐大中華增長基金",
    "ASG": "東亞聯豐亞洲策略增長基金",
    "HKG": "東亞聯豐香港增長基金",
    "ASB": "東亞聯豐亞洲策略債券基金",
    "APIG": "東亞聯豐亞太區投資級別債券基金",
    "APM": "東亞聯豐亞太區多元收益基金",
    "QB": "東亞聯豐全球優質債券基金",
    "AWF": "東亞睿智全天候基金",
    "CHA": "東亞聯豐中國A股股票基金",
    "AFA": "東亞聯豐亞太區靈活配置基金",
    "CHY": "東亞聯豐中國債券精選基金",
    "CGF": "東亞聯豐中國滙通基金",
    "GFA": "東亞聯豐環球靈活配置基金",
    "CPF": "東亞聯豐中國鳳凰基金",
    "RCB": "東亞聯豐人民幣核心債券基金",
}

LINK_RE = re.compile(
    r'<a\s+href="(?P<href>[^"]*?/FS/TC/[^"]+?\.pdf(?:\?[^"]*)?)"[^>]*>'
    r"(?P<name>.*?)</a>",
    re.I | re.S,
)


def fetch_page():
    resp = SESSION.get(PAGE_URL, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def clean_text(value):
    value = re.sub(r"<.*?>", "", value, flags=re.S)
    value = html.unescape(value)
    return re.sub(r"\s+", "", value).strip()


def safe_filename(value):
    value = re.sub(r"\s+", "", value)
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def parse_factsheets(page_html):
    docs = {}
    for match in LINK_RE.finditer(page_html):
        name = clean_text(match.group("name"))
        href = html.unescape(match.group("href"))
        if name and href:
            docs[name] = urljoin(BASE_URL, href)
    return docs


def resolve_targets(args):
    if not args:
        return list(TARGETS.values())

    selected = []
    aliases = {key.upper(): name for key, name in TARGETS.items()}
    aliases.update({name: name for name in TARGETS.values()})
    for arg in args:
        key = arg.strip()
        selected.append(aliases.get(key.upper(), aliases.get(key, key)))
    return selected


def download(url, dest):
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as resp:
        resp.raise_for_status()
        tmp = dest + ".part"
        with open(tmp, "wb") as fh:
            first = True
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    if first and not chunk.startswith(b"%PDF-"):
                        raise RuntimeError("Downloaded content is not a PDF.")
                    first = False
                    fh.write(chunk)
        os.replace(tmp, dest)
    return os.path.getsize(dest)


def main():
    targets = resolve_targets(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    print(f"[*] Fetch page: {PAGE_URL}")
    docs = parse_factsheets(fetch_page())
    if not docs:
        print("[!] No factsheet links found. Page structure may have changed.")
        sys.exit(1)

    new_count = 0
    for fund_name in targets:
        url = docs.get(fund_name)
        if not url:
            candidates = [name for name in docs if fund_name in name or name in fund_name]
            if len(candidates) == 1:
                fund_name = candidates[0]
                url = docs[fund_name]
        if not url:
            print(f"[!] Factsheet not found: {fund_name}")
            continue

        print(f"[*] {fund_name}")
        print(f"    {url}")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=SAVE_DIR) as tmp:
            tmp_path = tmp.name
        try:
            size = download(url, tmp_path)
            dest, report_month, moved = finalize_pdf_to_monthly_path(tmp_path, COMPANY, fund_name)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        if moved:
            print(f"    Updated ({size / 1024:.1f} KB) [{report_month}] -> {dest}")
            new_count += 1
        else:
            print(f"    Existing [{report_month}], skipped -> {dest}")

    if new_count == 0:
        print("[*] No files updated.")
    else:
        print(f"[*] Updated {new_count} latest file(s), saved to: {SAVE_DIR}")


if __name__ == "__main__":
    main()
