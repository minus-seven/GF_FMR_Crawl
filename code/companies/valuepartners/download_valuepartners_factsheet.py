# -*- coding: utf-8 -*-
"""
惠理（Value Partners）www.valuepartners-group.com —— 基金月刊(月报)自动下载

针对基金页，例如：
  https://www.valuepartners-group.com/sc/investment-solutions/institutional-funds/
  value-partners-high-dividend-stocks-fund-sc/?country=hk&type=hkri&tab=docs

为什么用无头浏览器：
  该页的"文件"下拉框是点开后由 JS 动态加载的。直接抓静态 HTML 只会拿到一份很旧的
  回退列表(2017年)。所以脚本用 Playwright 渲染页面，读"基金月刊"下拉框里最新一项
  (第一项)的真实 PDF 地址，再用浏览器的请求上下文下载(规避该站偶发的 SSL 证书链问题)。

  月报文件名规律(简体)：
    /ftp/files/reports/<code>/001_fact_sheet/sc/<code>_fact_sheet_YYYY-MM-01_zh-Hans.pdf

用法：
  python download_valuepartners_factsheet.py "<基金页URL>"
  python download_valuepartners_factsheet.py "<基金页URL>" --doc 季度评论   # 换其它文件类别
"""

import os
import re
import sys
from datetime import date

from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import company_data_dir, dated_output_path

COMPANY = "valuepartners"
SAVE_DIR = company_data_dir(COMPANY)
DEFAULT_DOC = "基金月刊"          # 月报
MIN_ACCEPTABLE_DOC_YEAR = 2020
DOCUMENT_WAIT_SECONDS = 90


def ensure_docs_tab(url):
    """保证 URL 打开的是"文件"页(tab=docs)。"""
    if "tab=" in url:
        url = re.sub(r"tab=[^&]*", "tab=docs", url)
    else:
        url += ("&" if "?" in url else "?") + "tab=docs"
    return url


def name_from_pdf_url(pdf_url):
    """从 PDF 地址提取 基金代码 与 日期 做文件名，如 vphy_fact_sheet_2026-05-01。"""
    m = re.search(r"/reports/([^/]+)/.*?/([^/]+?)\.pdf", pdf_url)
    fname = m.group(2) if m else "factsheet"
    return fname + ".pdf"


def parse_doc_date(label, url):
    text = f"{label or ''} {url or ''}"
    match = re.search(r"(20\d{2})[-_/](\d{1,2})(?:[-_/](\d{1,2}))?", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3) or 1))
    match = re.search(r"(20\d{2})年\s*(\d{1,2})月", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), 1)
    return date.min


def language_score(url):
    lowered = (url or "").lower()
    if "/sc/" in lowered or "zh-hans" in lowered:
        return 2
    if "/tc/" in lowered or "zh-hant" in lowered:
        return 1
    return 0


def select_latest_document(candidates, doc_name):
    cleaned = []
    seen = set()
    for item in candidates or []:
        url = (item.get("url") or "").strip()
        label = (item.get("label") or "").strip()
        if not url or ".pdf" not in url.lower() or url in seen:
            continue
        seen.add(url)
        cleaned.append({"label": label, "url": url, "date": parse_doc_date(label, url)})

    if not cleaned:
        raise RuntimeError(f"该类别下没有可下载 PDF: {doc_name}")

    cleaned.sort(key=lambda item: (item["date"], language_score(item["url"]), item["url"]), reverse=True)
    latest = cleaned[0]
    return {"label": latest["label"], "url": latest["url"], "date": latest["date"], "candidate_count": len(cleaned)}


def main():
    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        print('用法: python download_valuepartners_factsheet.py "<基金页URL>" [--doc 文件类别]')
        sys.exit(1)
    url = ensure_docs_tab(positional[0])
    doc_name = DEFAULT_DOC
    if "--doc" in args:
        doc_name = args[args.index("--doc") + 1]

    os.makedirs(SAVE_DIR, exist_ok=True)
    print(f"[*] 渲染页面(读取最新{doc_name}): {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="zh-HK")
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(1000)

        # The page keeps an old fallback table in the DOM and later injects the
        # current document table. Collect every matching row and pick newest by
        # date, otherwise the first fallback option can be years out of date.
        last_error = ""
        info = None
        for _ in range(DOCUMENT_WAIT_SECONDS):
            candidates = page.evaluate(
                """(docName) => {
                    const panel = document.querySelector('#panel-documents');
                    if (!panel) return {err: '找不到文件面板', candidates: []};
                    const candidates = [];
                    for (const tr of panel.querySelectorAll('tr')) {
                        const td0 = tr.querySelector('td');
                        if (!td0 || td0.innerText.trim() !== docName) continue;
                        for (const opt of tr.querySelectorAll('option')) {
                            candidates.push({label: opt.textContent.trim(), url: opt.value});
                        }
                        for (const anchor of tr.querySelectorAll('a[href]')) {
                            candidates.push({label: anchor.innerText.trim(), url: anchor.href});
                        }
                    }
                    if (!candidates.length) return {err: '没有找到文件类别: ' + docName, candidates: []};
                    return {candidates};
                }""",
                doc_name,
            )

            if candidates.get("err"):
                last_error = candidates["err"]
                page.wait_for_timeout(1000)
                continue

            try:
                info = select_latest_document(candidates.get("candidates"), doc_name)
            except RuntimeError as exc:
                last_error = str(exc)
                page.wait_for_timeout(1000)
                continue

            if info["date"].year >= MIN_ACCEPTABLE_DOC_YEAR:
                break
            last_error = (
                f"动态文件列表尚未加载，当前只看到旧文件: {info['label']} "
                f"({info['url']})"
            )
            info = None
            page.wait_for_timeout(1000)

        if not info:
            print(f"[!] {last_error or '没有找到可用的最新文件'}")
            browser.close()
            sys.exit(1)

        pdf_url, label = info["url"], info["label"]
        dest = dated_output_path(COMPANY, name_from_pdf_url(pdf_url), info["date"])
        print(f"[*] 最新一期: {label} | 日期: {info['date'].isoformat()} | 候选PDF: {info.get('candidate_count')}")
        print(f"[*] 链接: {pdf_url}")
        if os.path.exists(dest):
            print(f"[*] 已存在，跳过: {dest}")
            browser.close()
            return

        # 用浏览器请求上下文下载(规避 Python 端 SSL 证书链问题)
        resp = ctx.request.get(pdf_url, timeout=120000)
        if not resp.ok:
            print(f"[!] 下载失败 HTTP {resp.status}")
            browser.close()
            sys.exit(1)
        body = resp.body()
        if body[:4] != b"%PDF":
            print("[!] 下载到的不是 PDF，页面结构可能已变化。")
            browser.close()
            sys.exit(1)
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, dest)
        print(f"[*] 完成 ({len(body)/1024:.1f} KB) -> {dest}")
        browser.close()


if __name__ == "__main__":
    main()

