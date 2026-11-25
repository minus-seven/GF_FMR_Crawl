#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未来资产香港基金适配器。

官网当前公开的 HK/MC 与 KFS 为宣传册/产品关键事实，并非可持续更新的单只月报；
因此本轮只接入适配器与身份页表，严格保留 10 行缺口，不把宣传册冒充月报。
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import cli_main  # noqa: E402

SHEET_NAME = "未来"


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回已核验的未来资产月报目录；无参数，本轮为空以避免误配。"""
    return []


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """建立未来资产目录索引；参数 catalog 为官网目录记录。"""
    return {}


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """保留未来资产缺口；参数 row 为页表行、index 为当前索引。"""
    return "", "", "官网当前只核验到 KFS/宣传册，未核验到单只月报"


def main(argv: list[str] | None = None) -> int:
    """运行未来资产适配器 CLI；参数 argv 支持单基金、--probe、--backfill。"""
    return cli_main(argv, description="按未来资产香港官网核验月报入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single)


if __name__ == "__main__":
    raise SystemExit(main())
