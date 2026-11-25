#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""霸菱（Barings）香港产品页月报入口适配器。

霸菱香港英文站的 sitemap 提供当前公开基金产品页。这里按官网产品名与
产品页 slug 精确映射；下载器运行时再从产品页的 Factsheet 文档入口取得
最新文件。不把历史名称或相近策略映射到其他产品。
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "霸菱"
BASE_URL = "https://www.barings.com/en-hk/individual/funds"
RUN_COMMAND = 'python download_barings_factsheet.py "{page_url}" "{english}"'


def _key(value: str) -> str:
    """归一化英文基金名；参数 value 为工作簿中的英文基金名。"""

    text = normalize_name(str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


# slug 与香港官网 sitemap（2026-11-25）逐项核验；值为 (产品分类, slug)。
SLUGS: dict[str, tuple[str, str]] = {
    "Barings Developed and Emerging Markets High Yield Bond Fund": ("public-fixed-income", "barings-developed-and-emerging-markets-high-yield-bond-fund"),
    "Barings Emerging Markets Sovereign Debt Fund": ("public-fixed-income", "barings-emerging-markets-sovereign-debt-fund"),
    "Barings Global Bond Fund": ("public-fixed-income", "barings-global-bond-fund"),
    "Barings Global High Yield Bond Fund": ("public-fixed-income", "barings-global-high-yield-bond-fund"),
    "Barings Global Investment Grade Credit Fund": ("public-fixed-income", "barings-global-investment-grade-credit-fund"),
    "Barings Global Senior Secured Bond Fund": ("public-fixed-income", "barings-global-senior-secured-bond-fund"),
    "Barings Income Navigator Fund": ("public-fixed-income", "barings-income-navigator-fund"),
    "Barings USD Liquidity Fund": ("public-fixed-income", "barings-usd-liquidity-fund"),
    "Barings ASEAN Frontiers Fund": ("public-equities", "barings-asean-frontiers-fund"),
    "Barings Asia Growth Fund": ("public-equities", "barings-asia-growth-fund"),
    "Barings Australia Fund": ("public-equities", "barings-australia-fund"),
    "Barings Eastern Europe Fund": ("public-equities", "barings-eastern-europe-fund"),
    "Barings Eastern Europe (SP) Fund": ("public-equities", "barings-eastern-europe-sp-fund"),
    "Barings Eastern Trust": ("public-equities", "barings-eastern-trust"),
    "Barings Europa Fund": ("public-equities", "barings-europa-fund"),
    "Barings European Growth Trust": ("public-equities", "barings-european-growth-trust"),
    "Barings Europe Select Trust": ("public-equities", "barings-europe-select-trust"),
    "Barings German Growth Trust": ("public-equities", "barings-german-growth-trust"),
    "Barings Global Agriculture Fund": ("public-equities", "barings-global-agriculture-fund"),
    "Barings Global Emerging Markets Fund": ("public-equities", "barings-global-emerging-markets-fund"),
    "Barings Global Leaders Fund": ("public-equities", "barings-global-leaders-fund"),
    "Barings Global Resources Fund": ("public-equities", "barings-global-resources-fund"),
    "Barings Hong Kong China Fund": ("public-equities", "barings-hong-kong-china-fund"),
    "Barings Korea Feeder Fund": ("public-equities", "barings-korea-feeder-fund"),
    "Barings Korea Trust": ("public-equities", "barings-korea-trust"),
    "Barings Latin America Fund": ("public-equities", "barings-latin-america-fund"),
    "Barings Global Balanced Fund": ("multi-asset", "barings-global-balanced-fund"),
}

UNSUPPORTED_REASONS = {
    "Baring Global Aggregate Bond Fund": "当前霸菱香港 sitemap 未列出同名产品页；保留为待人工核验，不映射相近债券基金",
    "Barings European High Yield Bond Fund": "霸菱香港英文产品路径返回 404；仅核验到其他地区页面，不冒充香港月报入口",
}


def _build_index() -> dict[str, tuple[str, str]]:
    """建立归一化官网产品名索引；本函数无网络参数。"""

    return {_key(name): value for name, value in SLUGS.items()}


def _resolve_single(row: dict[str, str], index: dict[str, tuple[str, str]]) -> tuple[str, str, str]:
    """解析单只霸菱月报入口；参数 row 为页表行、index 为官网产品索引。"""

    english = str(row.get("english") or row.get("fund") or "").strip()
    item = index.get(_key(english))
    if not item:
        reason = next(
            (detail for name, detail in UNSUPPORTED_REASONS.items() if _key(name) == _key(english)),
            "当前未核验到 Barings 香港官网同名产品页；不映射相近策略",
        )
        return "", "", reason
    category, slug = item
    page_url = f"{BASE_URL}/{category}/{slug}"
    return page_url, RUN_COMMAND.format(page_url=page_url, english=english), ""


def _catalog_loader() -> list[dict[str, str]]:
    """返回由官网 sitemap 核验的产品目录；无参数、无运行时目录请求。"""

    return [{"name": name, "category": category, "slug": slug} for name, (category, slug) in SLUGS.items()]


def main(argv: list[str] | None = None) -> int:
    """运行霸菱 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按 Barings 香港官方产品页补全霸菱 Factsheet 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_catalog_loader,
        index_builder=lambda catalog: _build_index(),
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
