#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""晋达（Ninety One / 原 Investec）香港 GSF 月报入口适配器。

晋达官网的香港基金目录由 Cloudflare 保护，无法依赖一个稳定的 JSON API。
因此这里维护一份由官方检索结果核验过的月度 Factsheet 目录：只认领同名且
可定位到 Ninety One 官方 PDF 的产品，其余行明确输出 SKIP，不把相近策略或
其他管理人的文件误当作月报。官方目录入口仍作为 I 列，J 列负责下载直接 PDF。
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "晋达"
HONG_KONG_URL = "https://ninetyone.com/en/hong-kong/funds-literature"
RUN_COMMAND = 'python download_ninetyone_factsheet.py "{pdf_url}" "{english}"'
MEDIA_BASE = "https://ninetyone.com/-/media/documents/factsheet/"


def _key(value: str) -> str:
    """归一化英文基金名；参数 value 为工作簿或官方名称。"""

    return re.sub(r"[^a-z0-9]+", "", normalize_name(str(value or "")).casefold())


# URL 均来自 Ninety One 官方网站的 Factsheet 搜索结果或香港基金页面；不做
# “名称相似”猜测。未列出的产品会在回填日志中保留可审计的缺口原因。
CATALOG: dict[str, str] = {
    "Global Franchise Fund": "91-hk-gsf-global-franchise-fund-a-usd-factsheet-en.pdf",
    "Asian Equity Fund": "91-hk-gsf-asian-equity-fund-a-usd-factsheet-en.pdf",
    "Emerging Markets Equity Fund": "91-hk-gsf-emerging-markets-equity-fund-a-usd-factsheet-en.pdf",
    "Emerging Markets Corporate Debt Fund": "91-hk-gsf-emerging-markets-corporate-debt-fund-a-usd-factsheet-en.pdf",
    "US Dollar Money Fund": "91-hk-gsf-us-dollar-money-fund-a-usd-factsheet-en.pdf",
    "Global Managed Income Fund": "91-hk-gsf-global-managed-income-fund-a-usd-factsheet-en.pdf",
    "Global Gold Fund": "91-hk-gsf-global-gold-fund-a-usd-factsheet-en.pdf",
    "Global Credit Income Fund": "91-hk-gsf-global-credit-income-fund-a-usd-factsheet-en.pdf",
    "Emerging Markets Local Currency Debt Fund": "91-hk-gsf-emerging-markets-local-currency-debt-fund-a-usd-factsheet-en.pdf",
    # 以下三份为 Ninety One 官方 GSF Factsheet（香港页面可见相同产品）；
    # PDF 本身不带 hk 前缀，但内容和产品名称已由官方页面核验。
    "All China Equity Fund": "91-gsf-all-china-equity-fund-a-usd-factsheet-en.pdf",
    "European Equity Fund": "91-gsf-european-equity-fund-c-usd-factsheet-en.pdf",
    "All China Bond Fund": "91-gsf-all-china-bond-fund-a-usd-factsheet-en.pdf",
}

UNSUPPORTED_REASONS = {
    "Global Natural Resources Fund": "官方香港页面可访问产品信息，但当前检索未定位到同名 HK Factsheet PDF；暂不以全球/新加坡版本替代",
    "Global Strategic Equity Fund": "只核验到新加坡 Factsheet，未确认香港月报入口",
    "Global Equity Fund": "当前 Ninety One 香港目录未核验到同名产品；不映射到 Global Strategic Equity",
    "Emerging Markets Local Currency Dynamic Debt Fund": "香港页面有产品页，但当前未核验到可下载的 HK Factsheet PDF",
    "Latin American Corporate Debt Fund": "只核验到全球/新加坡 Factsheet，未确认香港月报入口",
    "Global Strategic Managed Fund": "只核验到 Key Facts，未核验到香港月报 Factsheet",
    "Emerging Markets Multi-Asset Fund": "当前未核验到同名香港月报 Factsheet",
    "Global Macro Allocation Fund": "当前未核验到同名香港月报 Factsheet",
    "Emerging Markets Investment Grade Corporate Debt Fund": "当前未核验到同名香港月报 Factsheet",
    "Global Environment Fund": "只核验到 Minimum Disclosure Document，非香港月报 Factsheet",
    "Global Quality Dividend Growth Fund": "当前未核验到同名香港月报 Factsheet",
    "Sterling Money Fund": "当前未核验到可下载的香港月报 Factsheet",
    "Emerging Markets Blended Debt Fund": "当前未核验到同名香港月报 Factsheet",
    "Asia Pacific Equity Opportunities Fund": "当前未核验到同名香港月报 Factsheet",
    "China A Shares Fund": "当前未核验到同名香港月报 Factsheet",
    "American Franchise Fund": "当前未核验到同名香港月报 Factsheet",
    "UK Alpha Fund": "当前未核验到同名香港月报 Factsheet",
    "Emerging Markets Hard Currency Debt Fund": "当前未核验到同名香港月报 Factsheet",
}


def _catalog_loader() -> list[dict[str, Any]]:
    """返回人工核验的官方目录；无参数、无外部网络调用。"""

    return [
        {"fund_name": name, "pdf_url": f"{MEDIA_BASE}{filename}"}
        for name, filename in CATALOG.items()
    ]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """按归一化英文名建立目录索引；参数 catalog 为官方目录。"""

    return {_key(str(item.get("fund_name") or "")): item for item in catalog}


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str]:
    """解析单只晋达基金的入口；参数 row 为页表行、index 为官方目录。"""

    english = str(row.get("english") or row.get("fund") or "").strip()
    item = index.get(_key(english))
    if not item:
        reason = next(
            (detail for name, detail in UNSUPPORTED_REASONS.items() if _key(name) == _key(english)),
            "当前未核验到 Ninety One 香港同名月报 Factsheet；不映射相近策略",
        )
        return "", "", reason
    pdf_url = str(item["pdf_url"])
    return HONG_KONG_URL, RUN_COMMAND.format(pdf_url=pdf_url, english=english), ""


def main(argv: list[str] | None = None) -> int:
    """运行晋达 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按 Ninety One 官方 Factsheet 目录补全晋达香港月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_catalog_loader,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
