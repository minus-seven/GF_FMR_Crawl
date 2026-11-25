#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""瑞士百达香港基金产品页适配器。

香港站产品页采用 ``基金 slug/ISIN`` 路由且有访问门槛。页表没有 ISIN，故本
适配器固定生成同名官方产品 slug 入口，下载器再用真实浏览器从产品页文档
组件选择最新 Monthly Factsheet；不把全球/瑞士页面当作香港月报。
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "瑞士百达"
BASE_URL = "https://am.pictet.com/hk/en/individuals/funds"
RUN_COMMAND = 'python download_pictet_factsheet.py "{page_url}" "{english}"'

PRODUCTS = {
    "Pictet - Robotics": "pictet-robotics",
    "Pictet - Global Megatrend Selection": "pictet-global-megatrend-selection",
    "Pictet - Water": "pictet-water",
    "Pictet - Clean Energy Transition": "pictet-clean-energy-transition",
    "Pictet - Security": "pictet-security",
    "Pictet - Global Environmental Opportunities": "pictet-global-environmental-opportunities",
    "Pictet - Digital": "pictet-digital",
    "Pictet Strategic Income": "pictet-strategic-income",
    "Pictet - Emerging Local Currency Debt": "pictet-emerging-local-currency-debt",
    "Pictet - Global Emerging Debt": "pictet-global-emerging-debt",
    "Pictet - Premium Brands": "pictet-premium-brands",
    "Pictet - Japanese Equity Opportunities": "pictet-japanese-equity-opportunities",
    "Pictet - Global Sustainable Credit": "pictet-global-sustainable-credit",
    "Pictet - Emerging Corporate Bonds": "pictet-emerging-corporate-bonds",
    "Pictet - Asian Equities Ex Japan": "pictet-asian-equities-ex-japan",
    "Pictet - Russian Equities": "pictet-russian-equities",
    "Pictet - SmartCity": "pictet-smartcity",
    "Pictet - Global Thematic Opportunities": "pictet-global-thematic-opportunities",
    "Pictet - Japanese Equity Selection": "pictet-japanese-equity-selection",
    "Pictet - Nutrition": "pictet-nutrition",
    "Pictet - Timber": "pictet-timber",
    "Pictet - Emerging Markets": "pictet-emerging-markets",
    "Pictet Global Income Quality": "pictet-global-income-quality",
    "Pictet - Chinese Local Currency Debt": "pictet-chinese-local-currency-debt",
    "Pictet - Asian Local Currency Debt": "pictet-asian-local-currency-debt",
    "Pictet Asian Bond Income": "pictet-asian-bond-income",
    "Pictet - Global Multi Asset Themes": "pictet-global-multi-asset-themes",
}


def _key(value: str) -> str:
    """归一化英文基金名；参数 value 为工作簿或官网基金名称。"""

    return re.sub(r"[^a-z0-9]+", "", normalize_name(str(value or "")).casefold())


def _catalog_loader() -> list[dict[str, str]]:
    """返回已按香港站产品目录核验的静态产品名与 slug；无参数。"""

    return [{"name": name, "slug": slug} for name, slug in PRODUCTS.items()]


def _build_index(catalog: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """建立归一化基金名索引；参数 catalog 为产品目录。"""

    return {_key(item["name"]): item for item in catalog}


def _resolve_single(row: dict[str, str], index: dict[str, dict[str, str]]) -> tuple[str, str, str]:
    """解析单只瑞士百达月报入口；参数 row 为页表行、index 为产品索引。"""

    english = str(row.get("english") or "").strip()
    item = index.get(_key(english))
    if not item:
        return "", "", "Pictet 香港产品目录未配置该同名基金"
    page_url = f"{BASE_URL}/{item['slug']}"
    return page_url, RUN_COMMAND.format(page_url=page_url, english=english), ""


def main(argv: list[str] | None = None) -> int:
    """运行 Pictet CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按 Pictet 香港产品目录补全瑞士百达 Factsheet 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_catalog_loader,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
