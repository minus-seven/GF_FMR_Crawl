#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路博迈（Neuberger Berman）香港 UCITS 月报入口适配器。

路博迈官网产品页提供按基金划分的 Fund Literature，Factsheet 会由页面动态
加载。这里维护与官方产品页一一对应的 slug，不把相近策略互相替代；J 列
下载器运行时打开官方产品页，定位最新英文 Factsheet，再下载并归档。
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "路博迈"
BASE_URL = "https://www.nb.com/products/ucits-funds/"
RUN_COMMAND = 'python download_neuberger_factsheet.py "{page_url}" "{english}"'


def _key(value: str) -> str:
    """归一化英文基金名；参数 value 为页表名称。"""

    text = normalize_name(str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


SLUGS = {
    "Neuberger Strategic Income Fund": "strategic-income-fund",
    "Neuberger Short Duration Emerging Markets Debt Fund": "short-duration-emerging-market-debt-fund",
    "Neuberger Emerging Markets Debt - Hard Currency Fund": "emerging-market-debt-hard-currency-fund",
    "Neuberger Corporate Hybrid Bond Fund": "corporate-hybrid-bond-fund",
    "Neuberger Next Generation Connectivity Fund": "next-generation-connectivity-fund",
    "Neuberger High Yield Bond Fund": "high-yield-bond-fund",
    "Neuberger European High Yield Bond Fund": "european-high-yield-bond-fund",
    "Neuberger Global Equity Megatrends Fund": "global-equity-megatrends-fund",
    "Neuberger US Large Cap Value Fund": "us-large-cap-value-fund",
    "Neuberger Emerging Markets Debt - Local Currency Fund": "emerging-market-debt-local-currency-fund",
    "Neuberger US Small Cap Intrinsic Value Fund": "us-small-cap-intrinsic-value-fund",
    "Neuberger Next Generation Space Economy Fund": "next-generation-space-economy-fund",
    "Neuberger Emerging Markets Debt Blend Fund": "emerging-market-debt-blend-fund",
    "Neuberger Global Flexible Credit Income Fund": "global-flexible-credit-income-fund",
    "Neuberger Global Investment Grade Credit Fund": "global-investment-grade-credit-fund",
    "Neuberger US Small Cap Fund": "us-small-cap-fund",
    "Neuberger Short Duration High Yield Engagement Fund": "short-duration-high-yield-engagement-fund",
    "Neuberger Japan Equity Engagement Fund": "japan-equity-engagement-fund",
    "Neuberger US Multi Cap Opportunities Fund": "us-multi-cap-opportunities-fund",
    "Neuberger US Equity Premium Fund": "us-equity-premium-fund",
    "Neuberger US Real Estate Securities Fund": "us-real-estate-securities-fund",
    "Neuberger Next Generation Mobility Fund": "next-generation-mobility-fund",
    "Neuberger Berman Emerging Markets Equity Fund": "emerging-markets-equity-fund",
    "Neuberger EMD Corporate - Social and Environmental Transition Fund* (This is not classified as an ESG fund in Hong Kong)": "emd-corporate-social-and-environmental-transition-fund",
    "Neuberger Global Bond Fund": "global-bond-fund",
    "Neuberger Short Duration Income Fund": "short-duration-income-fund",
    "Neuberger China Equity Fund": "china-equity-fund",
    "Neuberger Asia Responsible Transition Bond Fund": "asia-responsible-transition-bond-fund",
    "Neuberger Berman China Bond Fund": "china-bond-fund",
}

UNSUPPORTED_REASONS = {
    "Neuberger European Opportunities (LUX)": "当前官方产品目录未核验到该历史产品页；不映射到其他欧洲策略",
}


def _build_index() -> dict[str, str]:
    """建立官方产品页 slug 索引；本函数无网络参数。"""

    return {_key(name): slug for name, slug in SLUGS.items()}


def _resolve_single(row: dict[str, str], index: dict[str, str]) -> tuple[str, str, str]:
    """解析单只路博迈月报入口；参数 row 为页表行、index 为产品页索引。"""

    english = str(row.get("english") or row.get("fund") or "").strip()
    slug = index.get(_key(english))
    if not slug:
        reason = next(
            (detail for name, detail in UNSUPPORTED_REASONS.items() if _key(name) == _key(english)),
            "当前未核验到 Neuberger 官方同名 UCITS 产品页；不映射相近策略",
        )
        return "", "", reason
    page_url = BASE_URL + slug
    return page_url, RUN_COMMAND.format(page_url=page_url, english=english), ""


def _catalog_loader() -> list[dict[str, str]]:
    """返回由官方产品页逐项核验的目录；无参数、无运行时目录请求。"""

    return [{"name": name, "slug": slug} for name, slug in SLUGS.items()]


def main(argv: list[str] | None = None) -> int:
    """运行路博迈 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按 Neuberger 官方 UCITS 产品页补全路博迈香港 Factsheet 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_catalog_loader,
        index_builder=lambda catalog: _build_index(),
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
