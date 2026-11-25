# -*- coding: utf-8 -*-
"""华夏基金（香港）报告下载器。

月报复用 ChinaAMC 官网文档接口；半年报/年报使用 HKEX IFP 财务报告接口，
按正文 ``docType`` 精确选择，避免把公告或通知送入解析链路。
"""

from __future__ import annotations

import os
import re
import sys

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path, finalize_pdf_to_monthly_path

COMPANY = "chinaamc"
SAVE_DIR = company_data_dir(COMPANY)
CHINAAMC_DOC_API = "https://www.chinaamc.com.hk/jeecg-boot/mainWebDailyDocument/tMainWebDailyDocument/listByFundId"
CHINAAMC_FILE_BASE = "https://www.chinaamc.com.hk/jeecg-boot/sys/common/static/"
IFP_API = "https://ifp.hkex.com.hk/fund-repository/ifp/api/v1/fund/getFundFileList"
IFP_PDF_BASE = "https://ifp.hkex.com.hk/fund-repository/ifp/api/v1/fund/"
TIMEOUT = 60
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept": "application/json, text/plain, */*"}
SESSION = requests.Session()
configure_session(SESSION)
FACTSHEET_CATEGORY = {"SC": "基金月报", "TC": "基金月報", "EN": "Factsheet"}


def parse_date(value: str) -> tuple[int, int, int]:
    """解析 ChinaAMC 日期；参数 value 形如 DD/MM/YYYY。"""
    match = re.match(r"(\d{2})/(\d{2})/(\d{4})", value or "")
    return (int(match.group(3)), int(match.group(2)), int(match.group(1))) if match else (0, 0, 0)


def fetch_documents(fund_id: str) -> list[dict]:
    """读取官网文档列表；参数 fund_id 为 ChinaAMC 官网基金 ID。"""
    response = SESSION.get(CHINAAMC_DOC_API, headers=HEADERS, params={"fundId": fund_id}, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError(payload.get("message", "ChinaAMC 文档接口返回失败"))
    return payload.get("result") or []


def list_ifp_reports(fund_code: str) -> list[dict]:
    """读取 HKEX IFP 财务报告；参数 fund_code 为 HKEX 基金代码。"""
    response = SESSION.get(
        IFP_API,
        params={"fundId": fund_code, "docTypeParent": "FINANCIAL_REPORT", "page": 1, "size": 100, "lang": "zh_HK"},
        headers={**HEADERS, "x-requested-lang": "zh_HK", "Referer": f"https://ifp.hkex.com.hk/fund-repository/zh-HK/fund/{fund_code}"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return sorted(response.json().get("data") or [], key=lambda item: str(item.get("docDate") or ""), reverse=True)


def select_ifp_report(records: list[dict], report_kind: str) -> dict[str, str]:
    """严格选择半年报/年报正文；参数 records 为 IFP 记录。"""
    target = {"semiannual": "SEMIANNUAL", "annual": "ANNUAL"}.get(report_kind)
    if not target:
        raise ValueError("report_kind 必须为 semiannual 或 annual")
    for record in records:
        for item in record.get("fundFileVOList") or []:
            if str(item.get("docType") or "").upper() == target:
                return {"url": IFP_PDF_BASE + str(item.get("docUrl") or ""), "date": str(record.get("docDate") or ""), "label": str(item.get("fileName") or item.get("displayDocName") or "")}
    raise RuntimeError(f"HKEX IFP 没有匹配的{report_kind}正文")


def download_pdf(url: str, destination: str) -> tuple[int, str]:
    """下载并校验 PDF；参数 url 为地址，destination 为路径。"""
    temporary = destination + ".part"
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as response:
        response.raise_for_status()
        first = True
        with open(temporary, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"ChinaAMC 下载内容不是 PDF：{response.url}")
                first = False
                handle.write(chunk)
        final_url = response.url
    os.replace(temporary, destination)
    return os.path.getsize(destination), final_url


def main(argv: list[str] | None = None) -> None:
    """命令行入口；参数为官网基金 ID 或 HKEX 代码、报告类型和语言。"""
    args = list(sys.argv[1:] if argv is None else argv)
    positional = [arg for arg in args if not arg.startswith("--")]
    if not positional:
        raise SystemExit("用法：python download_chinaamc_reports.py <官网fundId或HKEX代码> [--kind monthly|semiannual|annual] [--lang EN]")
    identifier = positional[0].strip()
    kind = "monthly"
    if "--kind" in args:
        kind = args[args.index("--kind") + 1].lower()
    lang = "EN"
    if "--lang" in args:
        lang = args[args.index("--lang") + 1].upper()
    os.makedirs(SAVE_DIR, exist_ok=True)
    if kind == "monthly":
        docs = [doc for doc in fetch_documents(identifier) if doc.get("ssLanguage") == lang and doc.get("ssCategory") == FACTSHEET_CATEGORY.get(lang, "Factsheet")]
        if not docs:
            raise RuntimeError(f"ChinaAMC 官网没有 {identifier} 的 {lang} 月报")
        latest = sorted(docs, key=lambda doc: parse_date(str(doc.get("ssFileDate") or "")), reverse=True)[0]
        tag = "-".join(str(value).zfill(2) for value in parse_date(str(latest.get("ssFileDate") or "")))
        url = CHINAAMC_FILE_BASE + str(latest.get("ssFileFormat") or "").lstrip("/")
        destination = dated_output_path(COMPANY, f"{identifier}_{lang}", tag if tag != "00-00-0000" else "latest")
        fallback_date = tag
    else:
        selected = select_ifp_report(list_ifp_reports(identifier), kind)
        url = selected["url"]
        destination = os.path.join(SAVE_DIR, f".chinaamc_{os.getpid()}.pdf")
        fallback_date = selected["date"] or "latest"
    print(f"[*] identifier: {identifier}; kind: {kind}; URL: {url}")
    if kind == "monthly" and os.path.exists(destination):
        print(f"[*] 已存在，跳过：{destination}")
        return
    try:
        size, final_url = download_pdf(url, destination)
        if kind != "monthly":
            output_path, report_month, _ = finalize_pdf_to_monthly_path(destination, COMPANY, re.sub(r"[^\w.-]+", "_", identifier), fallback_date=fallback_date)
            print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month or fallback_date}] -> {output_path}")
        else:
            print(f"[*] Updated ({size / 1024:.1f} KB) -> {destination}")
        print(f"[*] Final URL: {final_url}")
    finally:
        if kind != "monthly" and os.path.exists(destination):
            os.remove(destination)


if __name__ == "__main__":
    main()
