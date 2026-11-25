#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
柏瑞（PineBridge）香港基金月报爬虫补全脚本。

现状（2026-10 实测）：
  - 柏瑞香港基金经 MetLife 平台销售，基金页为 JS 渲染，且先弹免责声明门
    （requests / 无头浏览器均只能拿到 258–280KB 外壳，accept 后仍不渲染基金内容）；
  - 下载脚本 `download_pinebridge_factsheet.py` 依赖 assets.pinebridge.com 的
    直链 PDF（形如 /asset/<uuid>/PineBridge-...-Class-A-Units_HKC.PDF），
    uuid 无法从基金名推导。

2026-10-27 起改为以官方目录为准：读取 MetLife 当前“柏瑞环球基金”和
“柏瑞基金系列（标准单位）”页面内嵌的基金目录，按中文基金名匹配并生成
  I（MetLife 基金页，含 ?fund=<slug>）与 J（download_pinebridge_factsheet.py "<PDF>"）。
这样不再从爬虫表旧 I/J 反推，避免把历史错链复制到新任务。

用法：
  python pinebridge_fund_link.py 柏瑞全球债券基金
  python pinebridge_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 柏瑞
  python pinebridge_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 柏瑞
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from typing import Any
from urllib.parse import quote

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "柏瑞"
CATALOG_URLS = (
    # 该公开页同时承载 PineBridge Global Funds 当前 18 只基金的完整目录。
    "https://www.metlife.com/investments/zh-hk/funds"
    "?fund=pinebridge-global-funds",
    # 标准单位目录；公積金单位目录不发布 Fact_Sheet，故意不纳入。
    "https://www.metlife.com/content/metlife/mim/investments/zh-hk/homepage/funds"
    "?fund=pinebridge-fund-series-standard-units&lang=zh-hk.html",
)
DISCLAIMER_COOKIES = {
    "culture": "zh-hk",
    "investorType": "professional-investor",
    "disclaimer": "zh-hk/professional-investor",
}
REQUEST_TIMEOUT = 30
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
FUND_RANGE_RE = re.compile(
    r'<div[^>]+class="[^"]*pinebridge-fund-range-page[^"]*"'
    r'[^>]+data-fund-range="(?P<payload>.*?)"',
    re.I | re.S,
)
# 仅登记已由同一底层产品、同一官方月报验证过的名称别名。
FUND_NAME_ALIASES = {
    "柏瑞全球债券基金": "柏瑞环球债券基金",
    "柏瑞(香港)环球重点股票基金": "柏瑞环球重点股票基金",
}
UNSUPPORTED_REASONS = {
    "柏瑞环球新兴市场当地货币债券基金": (
        "HKEX 已标记该基金于 2026-10-08 终止，官方无现行月报"
    ),
    "柏瑞环球新兴市场债券基金": (
        "当前柏瑞官方目录无该子基金，且长期无规模记录；不映射到相似策略"
    ),
    "柏瑞欧洲新兴股票基金": (
        "当前柏瑞官方目录无该子基金，且长期无规模记录；不映射到相似策略"
    ),
    "柏瑞美国股票基金": (
        "仅见于柏瑞公積金单位目录，官方未提供 Fact_Sheet；不映射到美国研究增值策略"
    ),
    "柏瑞亚洲基金": (
        "仅见于柏瑞公積金单位目录，官方未提供 Fact_Sheet；不映射到亚洲（日本除外）策略"
    ),
}


def _cn(value: str) -> str:
    """中文核心名：简繁统一、去空格符号与“柏瑞”品牌词。"""
    text = normalize_name(value)
    for token in ("柏瑞", "PineBridge", "pinebridge"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _canonical_name(value: str) -> str:
    """返回用于目录匹配的基金全名，并应用已审计的跨系列名称别名。"""
    source = _cn(value)
    alias = next(
        (target for name, target in FUND_NAME_ALIASES.items() if _cn(name) == source),
        "",
    )
    return _cn(alias or value)


def _parse_fund_range_html(page_html: str) -> list[dict[str, Any]]:
    """解析 MetLife 基金页内嵌目录；参数 page_html 为完整响应 HTML。"""
    match = FUND_RANGE_RE.search(page_html)
    if not match:
        raise RuntimeError("MetLife 基金目录数据未加载（可能被免责声明或风控拦截）")
    try:
        payload = json.loads(html.unescape(match.group("payload")))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("MetLife 基金目录 JSON 无法解析") from exc

    range_title = str(payload.get("title") or "").strip()
    catalog: list[dict[str, Any]] = []
    for fund in payload.get("funds") or []:
        classes = [
            item
            for item in fund.get("shareClasses") or []
            if str(
                (item.get("sectionDocuments") or {}).get("Fact_Sheet") or ""
            ).strip()
        ]
        if not classes:
            continue
        selected = next(
            (item for item in classes if str(item.get("title") or "") == "A"),
            classes[0],
        )
        slug = str(fund.get("urlName") or "").strip()
        share_class = str(selected.get("title") or "").strip()
        pdf_url = str(selected["sectionDocuments"]["Fact_Sheet"]).strip()
        if not slug or not share_class:
            raise RuntimeError(
                f"MetLife 目录记录缺少 slug 或股份类别：{fund.get('zhFundName')}"
            )
        page_url = (
            "https://www.metlife.com/investments/zh-hk/funds/fund/"
            f"?fund={quote(slug, safe='')}&unit={quote(share_class, safe='')}"
        )
        catalog.append(
            {
                "fund_name": str(fund.get("zhFundName") or "").strip(),
                "english_name": str(fund.get("enFundName") or "").strip(),
                "umbrella": range_title,
                "share_class": share_class,
                "page_url": page_url,
                "run_command": (
                    f'python download_pinebridge_factsheet.py "{pdf_url}"'
                ),
                "pdf_url": pdf_url,
            }
        )
    return catalog


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取 MetLife 官方现行目录，返回带页面链接与月报直链的基金列表。"""
    session = requests.Session()
    session.trust_env = False
    session.cookies.update(DISCLAIMER_COOKIES)
    catalog: list[dict[str, Any]] = []
    for url in CATALOG_URLS:
        response = session.get(
            url,
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()
        catalog.extend(_parse_fund_range_html(response.text))
    if not catalog:
        raise RuntimeError("MetLife 官方目录未返回任何可下载月报")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """按中文核心名建立索引；参数 catalog 为官方目录记录。"""
    index: dict[str, dict[str, Any]] = {}
    for record in catalog:
        key = _canonical_name(record["fund_name"])
        current = index.get(key)
        # 同名记录优先选 A 类，其次保留目录首次出现项。
        if current is None or (
            str(record.get("share_class") or "") == "A"
            and str(current.get("share_class") or "") != "A"
        ):
            index[key] = record
    return {"key": index, "all": catalog}


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    key = _canonical_name(str(row.get("fund") or ""))
    record = index["key"].get(key)
    if not record:
        source = _cn(str(row.get("fund") or ""))
        reason = next(
            (
                detail
                for fund_name, detail in UNSUPPORTED_REASONS.items()
                if _cn(fund_name) == source
            ),
            "MetLife 现行官方目录无同名基金月报（可能已停售或仅提供公積金类别）",
        )
        return "", "", reason
    return record["page_url"], record["run_command"], ""


def _needs_refresh(link: str, run_command: str) -> bool:
    """判断旧 I/J 是否违背当前官方约束；参数 link/run_command 为表中值。"""

    page = str(link or "").strip().lower()
    command = str(run_command or "").strip().lower()
    if "metlife.com/investments/zh-hk/funds/fund/" not in page:
        return True
    if "download_pinebridge_factsheet.py" not in command or "_hkc.pdf" not in command:
        return True
    return (
        "pinebridge-latin-america-equity-fund" in page
        and "us-large-cap-research-enhanced" in command
    )


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx柏瑞表 B 列基金名查询已登记的月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        extra_check=_needs_refresh,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
