#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
东亚联丰（BUIM）香港基金月报爬虫补全脚本。

以“爬虫list.xlsx”东亚联丰表的 B 列基金名（结合 C 伞）为索引，在 BUIM 官网
文献下载页找到对应基金月报，输出要回填的 I（文献页链接）与 J（运行方式）：
  I = https://buim.com/cht/literature-download
  J = python download_buim_factsheet.py <基金代码>（代码取自 PDF 文件名
      BEAUI_FS_<代码>_TC.pdf，例如 GLE / ASB）

通用流程（Excel 扫描、进度条、probe/backfill、命令行入口）都在
code/common/fund_link_backfill.py；本文件只保留 BUIM 官网特有的
文献页解析（复用 download_buim_factsheet 的解析函数）与名称归一化。

用法：
  python buim_fund_link.py 东亚联丰环球股票基金
  python buim_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 东亚联丰
  python buim_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 东亚联丰
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    normalize_name,
)
from download_buim_factsheet import fetch_page, parse_factsheets  # noqa: E402


SHEET_NAME = "东亚联丰"
PAGE_URL = "https://buim.com/cht/literature-download"
CODE_RE = re.compile(r"BEAUI_FS_([A-Za-z0-9]+)_TC\.pdf")


def _norm(value: str) -> str:
    """名称归一化：在公共规则上再统一“匯/滙/汇”、“級/级”等异体字。"""
    text = normalize_name(value)
    for src, dst in (("匯", "汇"), ("滙", "汇"), ("級", "级"), ("(", ""), (")", "")):
        text = text.replace(src, dst)
    return text


def _fetch_catalog() -> list[dict[str, Any]]:
    """抓取 BUIM 文献页，返回每支可下载月报的基金与其代码。"""
    docs = parse_factsheets(fetch_page())
    catalog: list[dict[str, Any]] = []
    for name, url in docs.items():
        match = CODE_RE.search(url)
        catalog.append(
            {
                "fund_name": str(name).strip(),
                "code": match.group(1).upper() if match else "",
                "url": url,
            }
        )
    if not catalog:
        raise RuntimeError("BUIM 文献页未解析到月报链接，请检查页面结构是否变更")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按归一化基金名建立索引。"""
    index: dict[str, dict[str, Any]] = {}
    for record in catalog:
        index.setdefault(_norm(record["fund_name"]), record)
    return index


def _resolve_single(
    row: dict[str, Any], index: dict[str, dict[str, Any]]
) -> tuple[str, str, str]:
    """解析一支基金，返回 (I文献页链接, J运行方式, 跳过原因)。"""
    fund_name = str(row.get("fund") or "")
    umbrella = str(row.get("umbrella") or "")
    key = _norm(fund_name)
    record = index.get(key)
    if record is None:
        candidates = [name for name in index if name and (key in name or name in key)]
        if len(candidates) == 1:
            record = index[candidates[0]]
    if record is None or not record.get("code"):
        return "", "", "官网文献页暂无该基金月报，跳过"
    code = record["code"]
    return PAGE_URL, f"python download_buim_factsheet.py {code}", ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx东亚联丰表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
