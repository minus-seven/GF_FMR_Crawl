#!/usr/bin/env python3
"""拉取 case 中资货币基金分币种名单的 Bloomberg 日点位并计算年化收益率/波动率。

运行前提
--------
1. 运行机器安装 Bloomberg Terminal/载体，并已登录；Desktop API 默认监听
   localhost:8194。该脚本使用 Bloomberg Desktop API，不是网页爬虫。
2. 安装依赖：``pip install blpapi pandas``（pandas 会安装 numpy）。
3. 任务名单、份额类别和 Bloomberg ticker 已内置在本脚本中，因此可以只复制
   这一个 `.py` 文件运行。若同目录（项目模式下为 `case/`）同时存在两个 CSV，
   则优先读取 CSV，便于后续替换基金名单或份额；不能只用基金名称猜 ticker。

默认计算的是 2025-11-01 至 2026-10-31 的一年期年化结果。可以通过
``--start-date`` 改为任意起点；``--start-date inception`` 会使用拉取到的第一
个有效点位并按实际天数计算 CAGR。

收益率序列优先级：
``TOT_RETURN_INDEX_NET_DVDS`` -> ``TOT_RETURN_INDEX_GROSS_DVDS`` ->
``FUND_NET_ASSET_VAL`` -> ``PX_LAST``。如果退化到 NAV/PX_LAST，结果会标记为
``price_return``，不能当作含分派的总回报。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover - Bloomberg 环境外的清晰错误提示
    pd = None
    _PANDAS_IMPORT_ERROR = exc

try:
    import blpapi
except ImportError as exc:  # pragma: no cover - Bloomberg 环境外的清晰错误提示
    blpapi = None
    _BLPAPI_IMPORT_ERROR = exc


SCRIPT_DIR = Path(__file__).resolve().parent
# 项目内脚本位于 case/；单独复制后则把脚本所在目录作为数据和输出目录。
PROJECT_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == "case" else SCRIPT_DIR
DATA_DIR = PROJECT_ROOT / "case" if (PROJECT_ROOT / "case").is_dir() else SCRIPT_DIR
# 新范围：中资美元前 10、中资港元前 15、中资人民币前 10（按主数据快照规模）。
# 旧的 01-money-market-top15.csv 和 mapping 文件保留作历史 case 复现，不再作为默认输入。
DEFAULT_UNIVERSE = DATA_DIR / "01-money-market-bloomberg-scope.csv"
DEFAULT_MAPPING = DATA_DIR / "bloomberg_money_market_identifiers_2026_scope.csv"
DEFAULT_OUTPUT = DATA_DIR / "bloomberg_money_market_output"

FIELDS = (
    "TOT_RETURN_INDEX_NET_DVDS",
    "TOT_RETURN_INDEX_GROSS_DVDS",
    "FUND_NET_ASSET_VAL",
    "PX_LAST",
)
SERIES_PRIORITY = (
    ("TOT_RETURN_INDEX_NET_DVDS", "total_return_net"),
    ("TOT_RETURN_INDEX_GROSS_DVDS", "total_return_gross"),
    ("FUND_NET_ASSET_VAL", "nav"),
    ("PX_LAST", "px_last"),
)


@dataclass(frozen=True)
class Security:
    rank: str
    code: str
    name: str
    currency: str
    bbg_ticker: str
    isin: str
    share_class: str
    notes: str
    scope_group: str = ""
    rank_within_currency: str = ""
    scale_hkd_100m: str = ""
    manager: str = ""


# 由 case/01-money-market-bloomberg-scope.csv 与
# case/bloomberg_money_market_identifiers_2026_scope.csv 合并而来。保留在脚本内，保证单文件
# 拷贝到 Bloomberg 终端后仍能运行；若两个 CSV 同时存在，load_securities 会优先读 CSV。
EMBEDDED_SECURITIES: tuple[dict[str, str], ...] = (
    {"rank": "USD-01", "scope_group": "中资美元货基前10", "rank_within_currency": "1", "code": "BSG677", "name": "博时美元货币市场基金", "manager": "博时", "currency": "USD", "scale_hkd_100m": "716.48772", "bbg_ticker": "BOUMMIU HK EQUITY", "isin": "", "share_class": "I USD ACC", "notes": "附件货币基金-美元；名称唯一匹配"},
    {"rank": "USD-02", "scope_group": "中资美元货基前10", "rank_within_currency": "2", "code": "BSR551", "name": "华夏精选美元货币基金", "manager": "华夏", "currency": "USD", "scale_hkd_100m": "616.75146", "bbg_ticker": "IUSDERW HK Equity", "isin": "", "share_class": "I USD INC", "notes": "附件货币基金-美元；名称唯一匹配"},
    {"rank": "USD-03", "scope_group": "中资美元货基前10", "rank_within_currency": "3", "code": "BPJ877", "name": "高腾微金美元货币基金", "manager": "高腾", "currency": "USD", "scale_hkd_100m": "448.32138", "bbg_ticker": "GTWVIUS HK Equity", "isin": "", "share_class": "I USD ACC", "notes": "附件货币基金-美元；名称唯一匹配"},
    {"rank": "USD-04", "scope_group": "中资美元货基前10", "rank_within_currency": "4", "code": "BUE512", "name": "招银国际美元货币市场基金", "manager": "招银国际", "currency": "USD", "scale_hkd_100m": "411.70194", "bbg_ticker": "CMBUMNI HK Equity", "isin": "", "share_class": "I HKD ACC", "notes": "附件份额栏写作 I HKD ACC；终端复核份额币种"},
    {"rank": "USD-05", "scope_group": "中资美元货基前10", "rank_within_currency": "5", "code": "BSG589", "name": "泰康开泰美元货币基金", "manager": "泰康", "currency": "USD", "scale_hkd_100m": "387.75828", "bbg_ticker": "TADMMIU HK EQUITY", "isin": "", "share_class": "I USD ACC", "notes": "附件货币基金-美元；名称唯一匹配"},
    {"rank": "USD-06", "scope_group": "中资美元货基前10", "rank_within_currency": "6", "code": "BQN919", "name": "平安货币基金", "manager": "平安资管", "currency": "USD", "scale_hkd_100m": "338.38818", "bbg_ticker": "PIAMIUS HK EQUITY", "isin": "", "share_class": "CLASS I USD ACC", "notes": "附件货币基金-美元；名称唯一匹配"},
    {"rank": "USD-07", "scope_group": "中资美元货基前10", "rank_within_currency": "7", "code": "BTM337", "name": "中银香港全天候美元货币市场基金", "manager": "中银香港", "currency": "USD", "scale_hkd_100m": "319.95288", "bbg_ticker": "BCWTMKI HK Equity", "isin": "", "share_class": "I1 USD ACC", "notes": "附件货币基金-美元；名称唯一匹配"},
    {"rank": "USD-08", "scope_group": "中资美元货基前10", "rank_within_currency": "8", "code": "BJK348", "name": "易方达(香港)美元货币市场基金", "manager": "易方达", "currency": "USD", "scale_hkd_100m": "184.32804", "bbg_ticker": "EFUMIAU HK EQUITY", "isin": "", "share_class": "I USD ACC", "notes": "附件货币基金-美元；名称唯一匹配"},
    {"rank": "USD-09", "scope_group": "中资美元货基前10", "rank_within_currency": "9", "code": "BTE502", "name": "广发美元货币市场基金", "manager": "广发国际", "currency": "USD", "scale_hkd_100m": "107.82174", "bbg_ticker": "GFMFIUA HK Equity", "isin": "", "share_class": "I USD ACC", "notes": "附件货币基金-美元；名称唯一匹配"},
    {"rank": "USD-10", "scope_group": "中资美元货基前10", "rank_within_currency": "10", "code": "BTF715", "name": "汇添富美元货币市场基金", "manager": "汇添富", "currency": "USD", "scale_hkd_100m": "105.30156", "bbg_ticker": "CUASMMA HK Equity", "isin": "", "share_class": "I USD ACC", "notes": "附件货币基金-美元；名称唯一匹配"},
    {"rank": "HKD-01", "scope_group": "中资港元货基前15", "rank_within_currency": "1", "code": "BNW892", "name": "高腾微财货币基金", "manager": "高腾", "currency": "HKD", "scale_hkd_100m": "372.99522", "bbg_ticker": "GTWMIHK HK EQUITY", "isin": "", "share_class": "I HKD ACC", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-02", "scope_group": "中资港元货基前15", "rank_within_currency": "2", "code": "BNY627", "name": "华夏精选货币基金", "manager": "华夏", "currency": "HKD", "scale_hkd_100m": "197.30490", "bbg_ticker": "CHMMIHI HK EQUITY", "isin": "", "share_class": "I HKD INC", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-03", "scope_group": "中资港元货基前15", "rank_within_currency": "3", "code": "BRG187", "name": "泰康开泰港元货币基金", "manager": "泰康", "currency": "HKD", "scale_hkd_100m": "165.23676", "bbg_ticker": "TADMMIH HK EQUITY", "isin": "", "share_class": "I HKD ACC", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-04", "scope_group": "中资港元货基前15", "rank_within_currency": "4", "code": "BSV019", "name": "中银香港全天候港元货币市场基金", "manager": "中银香港", "currency": "HKD", "scale_hkd_100m": "136.36350", "bbg_ticker": "BKAWHME HK EQUITY", "isin": "", "share_class": "I1 HKD INC", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-05", "scope_group": "中资港元货基前15", "rank_within_currency": "5", "code": "BTE097", "name": "博时港元货币市场ETF", "manager": "博时", "currency": "HKD", "scale_hkd_100m": "86.53944", "bbg_ticker": "3152 HK Equity", "isin": "", "share_class": "HKD ACC", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-06", "scope_group": "中资港元货基前15", "rank_within_currency": "6", "code": "BMV635", "name": "南方东英港元货币市场ETF", "manager": "南方", "currency": "HKD", "scale_hkd_100m": "77.95320", "bbg_ticker": "3053 HK Equity", "isin": "", "share_class": "A HKD INC", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-07", "scope_group": "中资港元货基前15", "rank_within_currency": "7", "code": "BNO281", "name": "易方达(香港)港元货币市场基金", "manager": "易方达", "currency": "HKD", "scale_hkd_100m": "73.15620", "bbg_ticker": "EFHDMIH HK EQUITY", "isin": "", "share_class": "I HKD ACC", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-08", "scope_group": "中资港元货基前15", "rank_within_currency": "8", "code": "BVJ945", "name": "招银国际港元货币市场基金", "manager": "招银国际", "currency": "HKD", "scale_hkd_100m": "53.29194", "bbg_ticker": "CMHKIAC HK EQUITY", "isin": "", "share_class": "I ACCUMULATION", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-09", "scope_group": "中资港元货基前15", "rank_within_currency": "9", "code": "BTF710", "name": "国泰君安港元货币市场基金", "manager": "国泰君安", "currency": "HKD", "scale_hkd_100m": "31.99404", "bbg_ticker": "GUJHKMA HK Equity", "isin": "", "share_class": "A2", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-10", "scope_group": "中资港元货基前15", "rank_within_currency": "10", "code": "BTS953", "name": "华泰港元货币市场基金", "manager": "华泰", "currency": "HKD", "scale_hkd_100m": "31.75380", "bbg_ticker": "HUAMRFI HK Equity", "isin": "", "share_class": "I HKD UNITS", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-11", "scope_group": "中资港元货基前15", "rank_within_currency": "11", "code": "BSX133", "name": "招商货币市场基金", "manager": "招证资管", "currency": "HKD", "scale_hkd_100m": "21.09822", "bbg_ticker": "CMSCMMI HK EQUITY", "isin": "", "share_class": "I HKD INC", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-12", "scope_group": "中资港元货基前15", "rank_within_currency": "12", "code": "BUM340", "name": "平安港元货币基金", "manager": "平安资管", "currency": "HKD", "scale_hkd_100m": "10.86930", "bbg_ticker": "PANMRKI HK Equity", "isin": "", "share_class": "I", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-13", "scope_group": "中资港元货基前15", "rank_within_currency": "13", "code": "BWE554", "name": "广发港元货币市场基金", "manager": "广发国际", "currency": "HKD", "scale_hkd_100m": "10.40598", "bbg_ticker": "GFKFIHA HK Equity", "isin": "", "share_class": "I HKD ACCUMULATION", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-14", "scope_group": "中资港元货基前15", "rank_within_currency": "14", "code": "BVW984", "name": "工银资管（全球）港元货币市场基金", "manager": "工银资管", "currency": "HKD", "scale_hkd_100m": "8.44974", "bbg_ticker": "ICBCHMI HK Equity", "isin": "", "share_class": "I", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "HKD-15", "scope_group": "中资港元货基前15", "rank_within_currency": "15", "code": "BVQ026", "name": "华夏港元数字货币基金", "manager": "华夏", "currency": "HKD", "scale_hkd_100m": "7.67520", "bbg_ticker": "CHIHMAH HK EQUITY", "isin": "", "share_class": "A HKD UNITS INC", "notes": "附件货币基金-港元；名称唯一匹配"},
    {"rank": "CNY-01", "scope_group": "中资人民币货基前10", "rank_within_currency": "1", "code": "BTP458", "name": "华夏人民币货币ETF", "manager": "华夏", "currency": "CNY", "scale_hkd_100m": "51.45114", "bbg_ticker": "83161 HK Equity", "isin": "", "share_class": "人民币柜台", "notes": "附件货币基金-人民币；名称唯一匹配"},
    {"rank": "CNY-02", "scope_group": "中资人民币货基前10", "rank_within_currency": "2", "code": "BSH785", "name": "博时人民币货币市场ETF", "manager": "博时", "currency": "CNY", "scale_hkd_100m": "36.79026", "bbg_ticker": "83192 HK Equity", "isin": "", "share_class": "人民币柜台", "notes": "附件货币基金-人民币；名称唯一匹配"},
    {"rank": "CNY-03", "scope_group": "中资人民币货基前10", "rank_within_currency": "3", "code": "BUK159", "name": "中银香港全天候人民币货币市场基金", "manager": "中银香港", "currency": "CNY", "scale_hkd_100m": "18.60066", "bbg_ticker": "BCAWRA1 HK EQUITY", "isin": "", "share_class": "类别A1 (人民币)", "notes": "附件货币基金-人民币；名称唯一匹配"},
    {"rank": "CNY-04", "scope_group": "中资人民币货基前10", "rank_within_currency": "4", "code": "BWH820", "name": "招商永隆人民币货币市场基金", "manager": "招商永隆", "currency": "CNY", "scale_hkd_100m": "6.11442", "bbg_ticker": "CMWLRMA HK Equity", "isin": "", "share_class": "A类人民币基金单位（累积）", "notes": "附件货币基金-人民币；名称唯一匹配"},
    {"rank": "CNY-05", "scope_group": "中资人民币货基前10", "rank_within_currency": "5", "code": "BDO182", "name": "南方东英人民币货币市场ETF", "manager": "南方", "currency": "CNY", "scale_hkd_100m": "2.90550", "bbg_ticker": "HK0000591419 Equity", "isin": "", "share_class": "非上市类别A-人民币", "notes": "附件货币基金-人民币；名称唯一匹配"},
    {"rank": "CNY-06", "scope_group": "中资人民币货基前10", "rank_within_currency": "6", "code": "BWG919", "name": "华夏人民币数字货币基金", "manager": "华夏", "currency": "CNY", "scale_hkd_100m": "2.65122", "bbg_ticker": "83461 HK Equity", "isin": "", "share_class": "人民币柜台", "notes": "附件货币基金-人民币；名称唯一匹配"},
    {"rank": "CNY-07", "scope_group": "中资人民币货基前10", "rank_within_currency": "7", "code": "BVC695", "name": "泰康开泰人民币货币基金", "manager": "泰康", "currency": "CNY", "scale_hkd_100m": "1.82520", "bbg_ticker": "TKRMMRM HK Equity", "isin": "", "share_class": "M类人民币", "notes": "附件货币基金-人民币；名称唯一匹配"},
    {"rank": "CNY-08", "scope_group": "中资人民币货基前10", "rank_within_currency": "8", "code": "BVO540", "name": "华泰人民币货币市场基金", "manager": "华泰", "currency": "CNY", "scale_hkd_100m": "0.78000", "bbg_ticker": "HURMMSR HK Equity", "isin": "", "share_class": "S类人民币", "notes": "附件货币基金-人民币；名称唯一匹配"},
    {"rank": "CNY-09", "scope_group": "中资人民币货基前10", "rank_within_currency": "9", "code": "BVB640", "name": "东方红人民币货币市场基金", "manager": "东方资管", "currency": "CNY", "scale_hkd_100m": "0.29172", "bbg_ticker": "OSRMMFC HK EQUITY", "isin": "", "share_class": "C CNY ACC", "notes": "附件货币基金-人民币；名称唯一匹配"},
    {"rank": "CNY-10", "scope_group": "中资人民币货基前10", "rank_within_currency": "10", "code": "BXW016", "name": "广发人民币货币市场基金", "manager": "广发国际", "currency": "CNY", "scale_hkd_100m": "0.23868", "bbg_ticker": "GFRFIRA HK Equity", "isin": "", "share_class": "I ACC 人民币", "notes": "附件货币基金-人民币；名称唯一匹配"},
)


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"日期必须为 YYYY-MM-DD：{value}") from exc


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def ticker_key(value: Any) -> str:
    """生成用于匹配 Bloomberg 返回值的标准键。

    Bloomberg 对基金证券有时把请求中的 ``HK Equity`` 规范化为 ``HK``，
    或者改变大小写。证券本身没有变化，因此匹配时忽略大小写和可选的
    ``Equity`` 后缀，但输出仍保留 Bloomberg 实际返回的 ticker。
    """

    text = " ".join(clean_text(value).split()).casefold()
    if text.endswith(" equity"):
        text = text[: -len(" equity")].rstrip()
    return text


def load_securities(universe_path: Path, mapping_path: Path) -> list[Security]:
    # 单文件模式：两个外部 CSV 都不存在时使用内置数据。只存在其中一个时，
    # 视为路径配置错误，避免半套名单和半套 ticker 被意外混用。
    universe_exists = universe_path.exists()
    mapping_exists = mapping_path.exists()
    if not universe_exists and not mapping_exists:
        return [Security(**row) for row in EMBEDDED_SECURITIES]
    if not universe_exists:
        raise FileNotFoundError(f"找不到基金名单：{universe_path}")
    if not mapping_exists:
        raise FileNotFoundError(f"找不到 Bloomberg 标识符映射：{mapping_path}")

    with universe_path.open("r", encoding="utf-8-sig", newline="") as handle:
        universe = list(csv.DictReader(handle))
    with mapping_path.open("r", encoding="utf-8-sig", newline="") as handle:
        mapping_rows = list(csv.DictReader(handle))
    mapping_codes = [clean_text(row.get("code")) for row in mapping_rows]
    duplicate_mapping_codes = sorted({code for code in mapping_codes if code and mapping_codes.count(code) > 1})
    if duplicate_mapping_codes:
        raise ValueError("Bloomberg 映射文件存在重复基金代码：" + ", ".join(duplicate_mapping_codes))
    mappings = {clean_text(row.get("code")): row for row in mapping_rows}
    universe_codes = {clean_text(row.get("code")) for row in universe}
    orphan_mapping_codes = sorted(code for code in mappings if code and code not in universe_codes)
    if orphan_mapping_codes:
        raise ValueError("Bloomberg 映射文件包含名单外基金代码：" + ", ".join(orphan_mapping_codes))

    securities: list[Security] = []
    for row in universe:
        code = clean_text(row.get("code"))
        mapping = mappings.get(code, {})
        securities.append(
            Security(
                rank=clean_text(row.get("rank")),
                code=code,
                name=clean_text(row.get("name")),
                currency=clean_text(mapping.get("currency") or row.get("currency")),
                bbg_ticker=clean_text(mapping.get("bbg_ticker")),
                isin=clean_text(mapping.get("isin")),
                share_class=clean_text(mapping.get("share_class")),
                notes=clean_text(mapping.get("notes")),
                scope_group=clean_text(row.get("scope_group")),
                rank_within_currency=clean_text(row.get("rank_within_currency")),
                scale_hkd_100m=clean_text(row.get("scale_hkd_100m")),
                manager=clean_text(row.get("manager")),
            )
        )
    return securities


def validate_securities(securities: list[Security]) -> None:
    """在请求 Bloomberg 前校验基金代码与证券代码的一一对应关系。

    HKEX 基金代码（如 BSG677）和 Bloomberg 证券代码（如 BOUMMIU HK Equity）
    是两个不同命名空间。这里拒绝重复代码、重复 ticker 和空名称，避免把一只
    基金的行情写到另一只基金上；缺 ticker 仍由 ``--strict-identifiers`` 控制，
    以便历史名单可在非严格模式下诊断。
    """

    seen_codes: dict[str, Security] = {}
    seen_tickers: dict[str, Security] = {}
    problems: list[str] = []
    for item in securities:
        if not item.code:
            problems.append("存在空基金代码")
        elif item.code in seen_codes:
            problems.append(f"基金代码重复：{item.code}")
        else:
            seen_codes[item.code] = item
        if not item.name:
            problems.append(f"{item.code or '<空代码>'} 缺少基金名称")
        if item.bbg_ticker:
            key = ticker_key(item.bbg_ticker)
            previous = seen_tickers.get(key)
            if previous is not None and previous.code != item.code:
                problems.append(
                    f"Bloomberg ticker 重复：{item.bbg_ticker} 同时映射 {previous.code} 与 {item.code}"
                )
            else:
                seen_tickers[key] = item
    if problems:
        raise ValueError("标识符映射校验失败：" + "；".join(problems))


def start_session(host: str, port: int):
    if blpapi is None:
        raise RuntimeError(
            "当前 Python 环境没有 blpapi。请在安装 Bloomberg Desktop API 的环境中执行 "
            "pip install blpapi。"
        ) from _BLPAPI_IMPORT_ERROR
    options = blpapi.SessionOptions()
    options.setServerHost(host)
    options.setServerPort(port)
    session = blpapi.Session(options)
    if not session.start():
        raise RuntimeError(f"无法连接 Bloomberg Desktop API：{host}:{port}")
    if not session.openService("//blp/refdata"):
        session.stop()
        raise RuntimeError("无法打开 Bloomberg //blp/refdata 服务")
    return session


def get_datetime(element: Any) -> date | None:
    if element is None or element.isNull():
        return None
    try:
        value = element.getValueAsDatetime()
    except Exception:
        value = element.getValue()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def get_optional(element: Any, name: str) -> float | None:
    if not element.hasElement(name):
        return None
    child = element.getElement(name)
    if child.isNull():
        return None
    try:
        value = child.getValue()
    except Exception:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def request_history(
    session: Any,
    securities: Iterable[Security],
    start_date: date,
    end_date: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """一次请求全部标识符，返回 (daily_rows, errors)。"""

    service = session.getService("//blp/refdata")
    request = service.createRequest("HistoricalDataRequest")
    active = [item for item in securities if item.bbg_ticker]
    for item in active:
        request.getElement("securities").appendValue(item.bbg_ticker)
    for field in FIELDS:
        request.getElement("fields").appendValue(field)
    request.set("startDate", start_date.strftime("%Y%m%d"))
    request.set("endDate", end_date.strftime("%Y%m%d"))
    request.set("periodicitySelection", "DAILY")
    request.set("periodicityAdjustment", "ACTUAL")
    request.set("adjustmentNormal", True)
    request.set("adjustmentAbnormal", True)
    request.set("adjustmentSplit", True)
    request.set("returnEids", False)
    request.set("maxDataPoints", 10000)

    by_ticker = {ticker_key(item.bbg_ticker): item for item in active}
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_tickers: set[str] = set()
    session.sendRequest(request)

    while True:
        event = session.nextEvent(30000)
        for message in event:
            if message.hasElement("responseError"):
                errors.append({"security": "*", "error": str(message.getElement("responseError"))})
                continue
            if not message.hasElement("securityData"):
                continue
            data = message.getElement("securityData")
            ticker = clean_text(data.getElementAsString("security"))
            response_key = ticker_key(ticker)
            seen_tickers.add(response_key)
            security = by_ticker.get(response_key)
            if security is None:
                errors.append({"security": ticker, "error": "返回了未请求的标识符"})
                continue
            if data.hasElement("securityError"):
                errors.append({"security": ticker, "error": str(data.getElement("securityError"))})
                continue
            if data.hasElement("fieldExceptions"):
                exceptions = data.getElement("fieldExceptions")
                for index in range(exceptions.numValues()):
                    exception = exceptions.getValueAsElement(index)
                    errors.append({"security": ticker, "error": str(exception)})
            field_data = data.getElement("fieldData")
            for index in range(field_data.numValues()):
                point = field_data.getValueAsElement(index)
                point_date = get_datetime(point.getElement("date") if point.hasElement("date") else None)
                if point_date is None:
                    continue
                row: dict[str, Any] = {
                    "rank": security.rank,
                    "scope_group": security.scope_group,
                    "rank_within_currency": security.rank_within_currency,
                    "code": security.code,
                    "name": security.name,
                    "manager": security.manager,
                    "currency": security.currency,
                    "scale_hkd_100m": security.scale_hkd_100m,
                    "bbg_ticker": ticker,
                    "isin": security.isin,
                    "share_class": security.share_class,
                    "date": point_date.isoformat(),
                }
                for field in FIELDS:
                    row[field] = get_optional(point, field)
                rows.append(row)
        if event.eventType() == blpapi.Event.RESPONSE:
            break

    missing = [item for item in securities if not item.bbg_ticker]
    errors.extend(
        {"security": item.code, "error": "缺少 bbg_ticker，未发起 Bloomberg 请求"}
        for item in missing
    )
    errors.extend(
        {"security": item.bbg_ticker, "error": "Bloomberg 未返回 securityData"}
        for item in active
        if ticker_key(item.bbg_ticker) not in seen_tickers
    )
    return rows, errors


def choose_series(frame: pd.DataFrame) -> tuple[pd.Series, str, str]:
    for field, label in SERIES_PRIORITY:
        if field in frame and frame[field].notna().sum() >= 2:
            series = frame[["date", field]].dropna().drop_duplicates("date").sort_values("date")
            if len(series) >= 2:
                return series.set_index("date")[field].astype(float), label, field
    raise ValueError("没有至少两个可用点位；无法计算收益率/波动率")


def calculate_summary(rows: list[dict[str, Any]], start_date: date | str, end_date: date, annualization_days: int) -> list[dict[str, Any]]:
    if pd is None:
        raise RuntimeError("当前 Python 环境没有 pandas，请执行 pip install pandas") from _PANDAS_IMPORT_ERROR
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    results: list[dict[str, Any]] = []
    for (code, ticker), group in frame.groupby(["code", "bbg_ticker"], dropna=False):
        group = group.sort_values("date")
        if start_date == "inception":
            sample = group[group["date"] <= end_date].copy()
        else:
            sample = group[(group["date"] >= start_date) & (group["date"] <= end_date)].copy()
        base = group.iloc[0]
        result: dict[str, Any] = {
            "rank": base.get("rank"),
            "scope_group": base.get("scope_group"),
            "rank_within_currency": base.get("rank_within_currency"),
            "code": code,
            "name": base.get("name"),
            "manager": base.get("manager"),
            "currency": base.get("currency"),
            "scale_hkd_100m": base.get("scale_hkd_100m"),
            "bbg_ticker": ticker,
            "isin": base.get("isin"),
            "share_class": base.get("share_class"),
            "requested_start": start_date if isinstance(start_date, str) else start_date.isoformat(),
            "requested_end": end_date.isoformat(),
            "observations": 0,
            "first_observation": None,
            "last_observation": None,
            "series_used": None,
            "return_type": None,
            "annualized_return_percent": None,
            "annualized_volatility_percent": None,
            "status": "ok",
            "error": None,
        }
        try:
            series, return_type, field = choose_series(sample)
            if len(series) < 2 or float(series.iloc[0]) <= 0 or float(series.iloc[-1]) <= 0:
                raise ValueError("点位数量不足或首尾点位非正数")
            daily_returns = series.pct_change().dropna()
            if daily_returns.empty:
                raise ValueError("无法形成日收益率")
            first_date = series.index[0]
            last_date = series.index[-1]
            elapsed_days = max((last_date - first_date).days, 1)
            cagr = (float(series.iloc[-1]) / float(series.iloc[0])) ** (365.25 / elapsed_days) - 1
            volatility = float(daily_returns.std(ddof=1) * math.sqrt(annualization_days))
            result.update(
                observations=len(series),
                first_observation=first_date.isoformat(),
                last_observation=last_date.isoformat(),
                series_used=field,
                return_type="total_return" if return_type.startswith("total_return") else "price_return",
                annualized_return_percent=cagr * 100,
                annualized_volatility_percent=volatility * 100,
            )
        except (ValueError, TypeError) as exc:
            result["status"] = "insufficient_data"
            result["error"] = str(exc)
        results.append(result)
    return results


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start-date", default="2025-11-01", help="YYYY-MM-DD 或 inception")
    parser.add_argument("--end-date", type=parse_date, default=date(2026, 10, 31))
    parser.add_argument("--annualization-days", type=int, default=252)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8194)
    parser.add_argument("--strict-identifiers", action="store_true", help="有缺 ticker 时直接退出")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.start_date != "inception":
        start_date: date | str = parse_date(args.start_date)
        if start_date > args.end_date:
            raise SystemExit("--start-date 不能晚于 --end-date")
    else:
        start_date = "inception"
    securities = load_securities(args.universe, args.mapping)
    validate_securities(securities)
    embedded_mode = not args.universe.exists() and not args.mapping.exists()
    missing = [item.code for item in securities if not item.bbg_ticker]
    if missing and args.strict_identifiers:
        raise SystemExit("以下基金没有 bbg_ticker：" + ", ".join(missing))

    session = start_session(args.host, args.port)
    try:
        query_start = date(1900, 1, 1) if start_date == "inception" else start_date
        rows, errors = request_history(session, securities, query_start, args.end_date)
    finally:
        session.stop()

    output_dir = args.output_dir
    write_csv(output_dir / "daily_points.csv", rows)
    summary = calculate_summary(rows, start_date, args.end_date, args.annualization_days)
    write_csv(output_dir / "summary.csv", summary)
    write_csv(output_dir / "errors.csv", errors)
    metadata = {
        "universe": str(args.universe),
        "mapping": str(args.mapping),
        "data_source": "embedded" if embedded_mode else "csv",
        "requested_start": start_date if isinstance(start_date, str) else start_date.isoformat(),
        "requested_end": args.end_date.isoformat(),
        "annualization_days": args.annualization_days,
        "series_priority": [field for field, _ in SERIES_PRIORITY],
        "missing_identifiers": missing,
        "security_count": len(securities),
        "scope_counts": {
            group: sum(1 for item in securities if item.scope_group == group)
            for group in sorted({item.scope_group for item in securities if item.scope_group})
        },
        "daily_rows": len(rows),
        "error_rows": len(errors),
        "note": "Bloomberg historical data is licensed; verify share class and adjustment settings before publication.",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    if errors:
        print("\nBloomberg 错误明细（完整记录见 errors.csv）：")
        for item in errors:
            print(f"- {item.get('security', '*')}: {item.get('error', '')}")
    return 0 if not errors else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit("已取消")
