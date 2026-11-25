# -*- coding: utf-8 -*-
"""博时基金财报下载器。

月报仍由 :mod:`download_bosera_factsheet` 从博时产品 API 动态发现；本脚本
专门处理半年报和年报，使用 HKEX IFP 的 ``docType`` 精确选择正文，避免
公告、通知等同名文件误入 MinerU/结构化流水线。
"""

from __future__ import annotations

import os
import re
import sys

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, finalize_pdf_to_monthly_path

COMPANY = "bosera"
SAVE_DIR = company_data_dir(COMPANY)
IFP_API = "https://ifp.hkex.com.hk/fund-repository/ifp/api/v1/fund/getFundFileList"
IFP_PDF_BASE = "https://ifp.hkex.com.hk/fund-repository/ifp/api/v1/fund/"
TIMEOUT = 60
SESSION = requests.Session()
configure_session(SESSION)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


def list_ifp_reports(fund_id: str) -> list[dict]:
    """读取 HKEX IFP 财务报告；参数 fund_id 为 HKEX 基金代码。"""
    response = SESSION.get(
        IFP_API,
        params={"fundId": fund_id, "docTypeParent": "FINANCIAL_REPORT", "page": 1, "size": 100, "lang": "zh_HK"},
        headers={**HEADERS, "x-requested-lang": "zh_HK", "Referer": f"https://ifp.hkex.com.hk/fund-repository/zh-HK/fund/{fund_id}"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return sorted(response.json().get("data") or [], key=lambda item: str(item.get("docDate") or ""), reverse=True)


def select_ifp_report(records: list[dict], report_kind: str) -> dict[str, str]:
    """按 ``SEMIANNUAL``/``ANNUAL`` 严格选择正文文件。"""
    target = {"semiannual": "SEMIANNUAL", "annual": "ANNUAL"}.get(report_kind.lower())
    if not target:
        raise ValueError("report_kind 必须为 semiannual 或 annual")
    for record in records:
        for item in record.get("fundFileVOList") or []:
            if str(item.get("docType") or "").upper() == target and item.get("docUrl"):
                return {
                    "url": IFP_PDF_BASE + str(item["docUrl"]).lstrip("/"),
                    "date": str(record.get("docDate") or ""),
                    "label": str(item.get("fileName") or item.get("displayDocName") or ""),
                }
    raise RuntimeError(f"HKEX IFP 没有匹配的{report_kind}正文")


def download_pdf(url: str, destination: str, fund_id: str) -> tuple[int, str]:
    """下载并校验 PDF 魔数；参数 url 为文件地址，destination 为临时路径。"""
    temporary = destination + ".part"
    with SESSION.get(url, headers={**HEADERS, "x-requested-lang": "zh_HK"}, timeout=TIMEOUT, stream=True) as response:
        response.raise_for_status()
        first = True
        with open(temporary, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"博时财报下载内容不是 PDF：{response.url}")
                first = False
                handle.write(chunk)
        final_url = response.url
    os.replace(temporary, destination)
    return os.path.getsize(destination), final_url


def main(argv: list[str] | None = None) -> None:
    """命令行入口：``<HKEX代码> --kind semiannual|annual --lang EN``。"""
    args = list(sys.argv[1:] if argv is None else argv)
    positional = [arg for arg in args if not arg.startswith("--")]
    if not positional:
        raise SystemExit("用法：python download_bosera_reports.py <HKEX代码> --kind semiannual|annual [--lang EN]")
    fund_id = positional[0].strip().upper()
    kind = "semiannual"
    if "--kind" in args and args.index("--kind") + 1 < len(args):
        kind = args[args.index("--kind") + 1].lower()
    if kind == "monthly":
        raise ValueError("月报请使用 download_bosera_factsheet.py；本脚本只处理半年报/年报")
    selected = select_ifp_report(list_ifp_reports(fund_id), kind)
    os.makedirs(SAVE_DIR, exist_ok=True)
    temporary = os.path.join(SAVE_DIR, f".bosera_{fund_id}_{os.getpid()}.pdf")
    print(f"[*] Fund: {fund_id}; kind: {kind}")
    print(f"[*] Selected: {selected['label']}; PDF: {selected['url']}")
    try:
        size, final_url = download_pdf(selected["url"], temporary, fund_id)
        output_path, report_month, _ = finalize_pdf_to_monthly_path(
            temporary, COMPANY, re.sub(r"[^\w.-]+", "_", fund_id), fallback_date=selected["date"] or "latest"
        )
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month or selected['date']}] -> {output_path}")


if __name__ == "__main__":
    main()
