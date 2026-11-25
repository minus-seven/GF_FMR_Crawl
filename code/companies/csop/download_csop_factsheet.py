# -*- coding: utf-8 -*-
"""
CSOP Hong Kong fund monthly newsletter and portfolio data crawler.

Target page example:
  https://www.csopasset.com/tc/products/us-dollar-bond

The CSOP API rejects plain requests from this environment, so this script opens
the product page with Playwright and calls the same JSON APIs from the browser
context. It downloads the latest Monthly Newsletter and saves the portfolio
data exposed on the product page.

Usage:
  python download_csop_factsheet.py
  python download_csop_factsheet.py "https://www.csopasset.com/tc/products/china-new-balance" "China New Balance Opportunity Fund" monthly
  python download_csop_factsheet.py "https://www.csopasset.com/tc/products/money-market-etf" "CSOP Hong Kong Dollar Money Market ETF" semiannual
"""

import json
import os
import re
import shutil
import sys
import base64
from datetime import datetime
from urllib.parse import urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path
from common.browser_fetch import launch_browser, new_context
from playwright.sync_api import sync_playwright


DEFAULT_PAGE_URL = "https://www.csopasset.com/tc/products/china-new-balance"
DEFAULT_PRODUCT_NAME = "China New Balance Opportunity Fund"
DEFAULT_REPORT_KIND = "monthly"
API_BASE = "https://website-api.csopasset.com"
COMPANY = "csop"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
TIMEOUT_MS = 60000
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

SESSION = requests.Session()
configure_session(SESSION)

REPORT_FILE_TYPES = {
    "monthly": ("基金月報", "基金月刊", "Monthly Newsletter"),
    "semiannual": ("基金半年報", "基金中期報告", "中期報告", "Interim Report"),
    "annual": ("基金年報", "年度報告", "年報", "Annual Report"),
}
REPORT_TITLE_MARKERS = {
    "monthly": ("月報", "月刊", "monthly", "newsletter", "factsheet"),
    "semiannual": ("半年報", "中期報告", "interim", "semi-annual", "semiannual"),
    "annual": ("年報", "年度報告", "annual report", "annual"),
}


def safe_filename(value):
    value = re.sub(r"\s+", "_", value.strip())
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def today_tag():
    return datetime.now().strftime("%Y%m%d")


def api_fetch(page, path, payload=None, method="POST"):
    url = API_BASE + path
    result = page.evaluate(
        """async ({url, method, payload}) => {
            const options = {
                method,
                headers: {
                    "content-type": "application/json",
                    "accept": "application/json, text/plain, */*"
                }
            };
            if (payload !== null) {
                options.body = JSON.stringify(payload);
            }
            const response = await fetch(url, options);
            const buffer = await response.arrayBuffer();
            let binary = "";
            const bytes = new Uint8Array(buffer);
            for (let i = 0; i < bytes.byteLength; i++) {
                binary += String.fromCharCode(bytes[i]);
            }
            return {status: response.status, body_b64: btoa(binary)};
        }""",
        {"url": url, "method": method, "payload": payload},
    )
    text = base64.b64decode(result["body_b64"]).decode("utf-8", errors="replace")
    if result["status"] != 200:
        raise RuntimeError(f"CSOP API {path} returned HTTP {result['status']}: {text[:300]}")
    data = json.loads(text)
    if isinstance(data, dict) and data.get("code") not in (None, 200):
        raise RuntimeError(f"CSOP API {path} returned error: {data}")
    return data


def _doc_date(item):
    """返回报告日期排序键；参数 item 为 CSOP 文件 API 返回的记录。"""
    return str(
        item.get("reportDate")
        or item.get("asOfDate")
        or item.get("createDate")
        or item.get("launchDate")
        or ""
    )


def _title_text(item):
    """拼接中英文文件标题；参数 item 为 CSOP 文件 API 返回的记录。"""
    return " ".join(
        str(item.get(key) or "")
        for key in ("titleTc", "titleSc", "titleEn", "nameTc", "nameEn", "fileName")
    ).strip()


def latest_report(page, product_name, report_kind="monthly"):
    """读取最新报告；参数 page 为浏览器页、product_name 为官网产品名、report_kind 为月报/半年报/年报。"""
    kind = str(report_kind or DEFAULT_REPORT_KIND).lower()
    if kind not in REPORT_FILE_TYPES:
        raise ValueError("report_kind must be monthly, semiannual, or annual")
    payload_base = {
        "page": 1,
        "rows": 50,
        "sort": "createDate",
        "order": "desc",
        "productName": product_name,
    }
    candidates = []
    for file_type in REPORT_FILE_TYPES[kind]:
        payload = {**payload_base, "fundFileType": file_type}
        try:
            data = api_fetch(page, "/cmsApi/fundFile/list", payload)
        except Exception:  # noqa: BLE001 - 某些站点版本不接受英文/繁体类型名
            continue
        records = data.get("data", {}).get("data", []) if isinstance(data, dict) else []
        if records:
            candidates.extend(records)
    # 旧版 API 不支持类型枚举时，尝试无类型查询并按标题筛选。
    if not candidates:
        try:
            data = api_fetch(page, "/cmsApi/fundFile/list", payload_base)
            records = data.get("data", {}).get("data", []) if isinstance(data, dict) else []
            markers = tuple(marker.lower() for marker in REPORT_TITLE_MARKERS[kind])
            candidates = [
                item for item in records
                if any(marker in _title_text(item).lower() for marker in markers)
            ]
        except Exception:  # noqa: BLE001 - 页面仍可作为稳定入口保存
            candidates = []
    if not candidates:
        return None
    unique = {str(item.get("id") or item.get("pdfTcUrl") or item.get("pdfEnUrl")): item for item in candidates}
    return sorted(unique.values(), key=_doc_date, reverse=True)[0]


def latest_monthly_newsletter(page, product_name):
    """兼容旧调用方的月报读取函数；参数 page/product_name 同 latest_report。"""
    return latest_report(page, product_name, "monthly")


def holdings_data(page, product_name):
    data = api_fetch(page, "/cmsApi/Holdings/product/MF", {"productName": product_name})
    return data.get("data", {})


def normalize_rows(rows, name_keys, value_key):
    output = []
    for row in rows or []:
        item = {key: row.get(key) for key in name_keys if row.get(key) is not None}
        item["value"] = row.get(value_key)
        item["as_of"] = row.get("AsOfDate")
        item["as_of_tc"] = row.get("AsOfDateTc")
        output.append(item)
    return output


def summarize_holdings(raw):
    characteristics = {}
    for row in raw.get("Portfolio Characteristics", []) or []:
        key = row.get("TypeEn") or row.get("TypeTC")
        if key:
            characteristics[key] = {
                "value": row.get("Exposure"),
                "label_tc": row.get("TypeTC"),
                "as_of": row.get("AsOfDate"),
                "as_of_tc": row.get("AsOfDateTc"),
            }

    return {
        "as_of": next(
            (
                row.get("AsOfDate")
                for rows in raw.values()
                if isinstance(rows, list)
                for row in rows
                if isinstance(row, dict) and row.get("AsOfDate")
            ),
            None,
        ),
        "portfolio_characteristics": characteristics,
        "requested_metrics": {
            "portfolio_modified_duration": characteristics.get("Modified Duration"),
            "average_yield_to_maturity": characteristics.get("Average Yield to Maturity"),
            "number_of_holdings": characteristics.get("Number of Holdings"),
            "credit_rating_allocation": normalize_rows(
                raw.get("Credit Rating Allocation"),
                ["TypeEn", "TypeTC"],
                "Exposure",
            ),
            "duration_distribution": None,
            "duration_distribution_note": "Not exposed as a separate field in the CSOP product page Holdings API.",
            "holding_distribution_yield": None,
            "holding_distribution_yield_note": (
                "The page exposes distribution history per share, but no standalone "
                "holding/distribution-yield field was found in the Holdings API."
            ),
        },
        "allocations": {
            "credit_rating": normalize_rows(raw.get("Credit Rating Allocation"), ["TypeEn", "TypeTC"], "Exposure"),
            "sector": normalize_rows(raw.get("Sector Allocation"), ["ItemEn", "ItemTC"], "Exposure"),
            "country": normalize_rows(raw.get("Country Allocation"), ["NameEn", "NameTC"], "Percentage"),
            "currency": normalize_rows(raw.get("Currency Allocation"), ["CurrencyEn", "CurrencyTC"], "Exposure"),
        },
        "top_5_holdings": [
            {
                "security_name_en": row.get("SecurityNameEn"),
                "security_name_tc": row.get("SecurityNameTC"),
                "nav_percent": row.get("NavPercent"),
                "as_of": row.get("AsOfDate"),
                "as_of_tc": row.get("AsOfDateTc"),
            }
            for row in raw.get("Top 5 Holdings", []) or []
        ],
        "raw": raw,
    }


def download_pdf(url, dest):
    tmp = dest + ".part"
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=True) as resp:
        resp.raise_for_status()
        final_url = resp.url
        with open(tmp, "wb") as fh:
            first = True
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"Downloaded content is not a PDF: {final_url}")
                first = False
                fh.write(chunk)
    os.replace(tmp, dest)
    return os.path.getsize(dest), final_url


def resolve_args(args):
    """解析命令行参数；参数 args 依次为产品页、产品名和报告类型。"""
    page_url = DEFAULT_PAGE_URL
    product_name = DEFAULT_PRODUCT_NAME
    report_kind = DEFAULT_REPORT_KIND

    if not args:
        return page_url, product_name

    first = args[0].strip()
    if first.startswith("http://") or first.startswith("https://"):
        page_url = first
        if len(args) > 1:
            product_name = args[1].strip()
        if len(args) > 2:
            report_kind = args[2].strip().lower()
    else:
        product_name = first
        if len(args) > 1:
            report_kind = args[1].strip().lower()

    return page_url, product_name, report_kind


def main():
    """下载指定产品最新报告；命令行参数由 resolve_args 解析。"""
    page_url, product_name, report_kind = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    print(f"[*] Page: {page_url}")
    print(f"[*] Product: {product_name}")
    print(f"[*] Report kind: {report_kind}")

    with sync_playwright() as p:
        browser = launch_browser(p, headless=True)
        context = new_context(browser, locale="zh-HK")
        page = context.new_page()
        page.goto(page_url, wait_until="networkidle", timeout=TIMEOUT_MS)

        doc = latest_report(page, product_name, report_kind)
        raw_holdings = holdings_data(page, product_name) if report_kind == "monthly" else {}
        browser.close()

    summary = {
        "product_name": product_name,
        "page_url": page_url,
        "report_kind": report_kind,
        "report": doc,
        "holdings": summarize_holdings(raw_holdings),
    }

    json_archive = dated_output_path(COMPANY, product_name, today_tag(), ".json")
    with open(json_archive, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print(f"[*] Data -> {json_archive}")

    if doc:
        pdf_url = doc.get("pdfTcUrl") or doc.get("pdfEnUrl")
        date_tag = safe_filename(doc.get("createDate") or today_tag())
        title = safe_filename(doc.get("titleTc") or doc.get("titleEn") or "monthly_newsletter")
        archive_pdf = dated_output_path(COMPANY, product_name, date_tag)
        print(f"[*] Latest {report_kind}: {_title_text(doc)} ({_doc_date(doc)})")
        print(f"[*] PDF: {pdf_url}")
        size, final_url = download_pdf(pdf_url, archive_pdf)
        print(f"[*] Final URL: {final_url}")
        print(f"[*] Updated ({size / 1024:.1f} KB) -> {archive_pdf}")
    else:
        print("[!] No Monthly Newsletter document returned by CSOP fundFile/list.")

    if report_kind == "monthly":
        metrics = summary["holdings"]["requested_metrics"]
        print("[*] Extracted page/API metrics:")
        print(f"    Modified Duration: {metrics['portfolio_modified_duration']}")
        print(f"    Average Yield to Maturity: {metrics['average_yield_to_maturity']}")
        print(f"    Credit Rating rows: {len(metrics['credit_rating_allocation'])}")
    print(f"[*] Save dir: {SAVE_DIR}")


if __name__ == "__main__":
    main()
