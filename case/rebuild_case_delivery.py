#!/usr/bin/env python3
"""Build the three-sheet money-market case workbook from the exported snapshot."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "case"
INPUTS = CASE / "case_inputs.json"
OVERRIDES = CASE / "case_manual_overrides.json"
OUT = CASE / "01-money-market-case-delivery.xlsx"
AUDIT = CASE / "01-money-market-case-delivery.audit.json"

TARGET = "2026-06-30"
SCALE_DATE = "2026-08-31"

NAVY = "1F4E78"
LIGHT_BLUE = "EEF5FB"
BLUE = "D9EAF7"
GREEN = "E2F0D9"
PURPLE = "E4DFEC"
AMBER = "FFF2CC"
GREY = "E7E6E6"
RED = "F8CBAD"
BORDER = "C9D5E2"
WHITE = "FFFFFF"
DARK = "1F2937"
MID = "44546A"

thin = Side(style="thin", color=BORDER)
ALL_BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)

CONF_RANK = {
    "source_table": 5,
    "high": 4,
    "bloomberg": 4,
    "review": 3,
    "medium": 2,
    "low": 1,
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


DATA = load_json(INPUTS)
# DB-only build: do not substitute PDF-extracted values for database fields.
MANUAL = {}
FUNDS = DATA["funds"]
METRICS = DATA["metrics"]
HOLDINGS = DATA["holdings"]
ALLOCATIONS = DATA["allocations"]


def as_date(value) -> str | None:
    if value is None:
        return None
    return str(value)[:10]


def doc_label(document_type: str | None) -> str:
    return {
        "semiannual": "半年报",
        "semiannual_report": "半年报",
        "annual": "年报",
        "monthly": "月报",
        "bloomberg_historical_market_data": "Bloomberg",
    }.get(str(document_type or ""), str(document_type or "报告"))


def metric_source(row: dict | None) -> str:
    if not row:
        return "未披露"
    label = doc_label(row.get("document_type"))
    period = as_date(row.get("report_period"))
    page = row.get("source_page")
    text = f"{label}（{period}）" if period else label
    if page:
        text += f" P.{page}"
    return text


def pick_metric(code: str, name: str):
    rows = [
        m
        for m in METRICS
        if m["fund_code"] == code
        and m["metric_name"] == name
        and (m.get("numeric_value") is not None or m.get("text_value"))
    ]
    if not rows:
        return None, "missing"

    def period(row):
        return as_date(row.get("report_period")) or "0000-00-00"

    def doc_rank(row):
        return {
            "semiannual": 3,
            "semiannual_report": 3,
            "monthly": 2,
            "annual": 1,
        }.get(str(row.get("document_type") or ""), 0)

    same = [r for r in rows if period(r) == TARGET]
    if same:
        same.sort(
            key=lambda r: (doc_rank(r), CONF_RANK.get(str(r.get("confidence")), 0)),
            reverse=True,
        )
        return same[0], "target"

    rows.sort(
        key=lambda r: (period(r), doc_rank(r), CONF_RANK.get(str(r.get("confidence")), 0)),
        reverse=True,
    )
    return rows[0], "fallback"


def fill_for_time(time_value: str | None, inferred: bool = False) -> str:
    if inferred:
        return AMBER
    if not time_value:
        return GREY
    t = str(time_value)[:10]
    if t == TARGET:
        return BLUE
    if t > TARGET:
        return GREEN
    return AMBER


def number(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt_pct(value, digits=2):
    v = number(value)
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def fmt_scale(value):
    v = number(value)
    if v is None:
        return "—"
    return f"{v:,.2f}"


EQUITY_ALLOC = re.compile(r"股票|權益|权益|equity|stock", re.I)
MONEY_ALLOC = re.compile(
    r"存款|現金|现金|貨幣|货币|債券|债券|债务|固定收益|deposit|cash|money\s+market|bond|debt",
    re.I,
)


def infer_equity_debt(code: str):
    """Infer 0:100 only when DB asset_type shows money-market assets and no equity."""
    rows = [
        a
        for a in ALLOCATIONS
        if a["fund_code"] == code and a["dimension"] == "asset_type"
    ]
    periods = sorted({as_date(a.get("report_period")) for a in rows if a.get("report_period")}, reverse=True)
    for period in periods:
        categories = [
            (str(a.get("category") or ""), number(a.get("weight")) or 0.0)
            for a in rows
            if as_date(a.get("report_period")) == period
        ]
        if not categories or any(EQUITY_ALLOC.search(cat) for cat, _ in categories):
            continue
        if any(MONEY_ALLOC.search(cat) for cat, _ in categories):
            return period, categories
    return None, []


# ---------------------------------------------------------------------------
# Holdings selection and classification
# ---------------------------------------------------------------------------

ASSET_RULES = [
    ("存款证", re.compile(r"CERTIFICATE\s+OF\s+DEPOSIT|\bCD\b|存款證|存款证|FXCD|\bECD\b", re.I)),
    ("定期存款", re.compile(r"DEPOSIT|定期存款|定存|TIME\s+DEPOSIT|(?:^|[^證证])存款", re.I)),
    ("货币市场基金/ETF", re.compile(r"MONEY\s+MARKET\s+FUND|\bMMF\b|\bETF\b|貨幣市場基金|货币市场基金|MN\s+MK", re.I)),
    ("债务证券（永续债）", re.compile(r"\bPERP\b|PERPETUAL", re.I)),
    ("债务证券（伊斯兰债券）", re.compile(r"SUKUK", re.I)),
    ("商业票据", re.compile(r"COMMERCIAL\s+PAPER|\bCP\b", re.I)),
    ("回购", re.compile(r"REPO|回購", re.I)),
    ("未具名货币市场工具", re.compile(r"货币市场工具|貨幣市場工具", re.I)),
]

ISSUER_RULES = [
    (
        re.compile(
            r"MIZUHO|MUFG|INTESA|RIYAD|SAUDI\s+NATIONAL|SAUDI\s+NTL|STANDARD\s+CHARTERED|"
            r"COMMONWEALTH\s+BANK|BANK\s+OF\s+CHINA|BCHINA|ICBC|CCB|BOSHAI|BCOM|AGRBK|"
            r"BCLMHK|NCBKSG|RJHIAB|QNBK|QNB\s+FINANCE|SNB\s+FUNDING|AL\s+RAJHI|MASHREQ|"
            r"EMIRATES\s+NBD|STATE\s+BANK\s+OF\s+INDIA|NORBK|卡塔爾國家銀行|卡塔尔国家银行|"
            r"永丰银行|永豐銀行|台新银行|台新銀行|平安银行|平安銀行|上海浦东发展银行|上海浦東發展銀行|"
            r"MASQUH|C\s+FLOAT|GS\s+FLOAT|BOHAIB|BCDHGR",
            re.I,
        ),
        "银行",
    ),
    (re.compile(r"GOVERNMENT|TREASURY|PUBLIC\s+AUTHORITY|MULTILATERAL", re.I), "政府/公共机构"),
    (
        re.compile(
            r"SECURITIES|CAPITAL|FINANCE|FINANCIAL|ASSET\s+MANAGEMENT|INVESTMENT|"
            r"INSURANCE|HOLDINGS|LEASE|FUTURES|BROKER|CICC|GF\s+GLOBAL|GFGCHK|GUOTJU|"
            r"HTIFIH|CICCHK|CSCIF|CCAMCL|ORIEAS|FAR\s+EAST\s+HORIZON|HUATAI|MIRAE\s+ASSET|"
            r"HANA\s+SECURITIES|CHINAAMC|TAIKANG|GAOTENG|HRINTH|CITICS|CSFCO|HANFGI|"
            r"MIRSHK|MITHCC|MITSUBISHI",
            re.I,
        ),
        "非银行金融机构",
    ),
]


def classify(name: str, holding_type: str = "") -> tuple[str, str]:
    n = str(name or "")
    t = str(holding_type or "")
    asset = None
    for label, rx in ASSET_RULES:
        if rx.search(n):
            asset = label
            break
    if asset is None:
        if re.search(r"%|FLOAT|EMTN|BOND|NOTE|\b\d{2}[A-Z]{3}\d{4}\b|\b\d{8}\b", n, re.I):
            asset = "债务证券"
        else:
            asset = "待复核"

    issuer = None
    for rx, label in ISSUER_RULES:
        if rx.search(n):
            issuer = label
            break
    if issuer is None:
        if re.search(r"银行|銀行|BANK|\bBK\b", n, re.I):
            issuer = "银行"
        elif re.search(r"存款|定存|DEPOSIT", n, re.I):
            issuer = "未识别（存款）"
        elif re.search(r"货币市场工具|貨幣市場工具", n):
            issuer = "未识别（未具名）"
        else:
            issuer = "非银行企业/其他"
    return issuer, asset


BAD_GROUP_NAMES = {
    "BQN919": [re.compile(r"GAOTENG", re.I)],
    "BUE512": [re.compile(r"\bDAIL\b|RESLIF|GRNCH|MTRC|BCDHGR|CHJMAO|NORBK|HRINTH", re.I)],
}

DERIVATIVE = re.compile(
    r"NET\s+FINANCIAL\s+DERIVATIVE|GROSS\s+FINANCIAL\s+DERIVATIVE", re.I
)


def group_holdings(code: str):
    groups = defaultdict(list)
    for h in HOLDINGS:
        if h["fund_code"] != code:
            continue
        weight = number(h.get("weight"))
        if weight is None or weight <= 0:
            continue
        name = str(h.get("security_name") or "")
        key = (
            as_date(h.get("report_period")),
            str(h.get("document_type") or ""),
            str(h.get("dv_id") or ""),
        )
        groups[key].append(h)
    return groups


def aggregate_items(rows):
    agg = OrderedDict()
    for h in rows:
        name = re.sub(r"\s+", " ", str(h.get("security_name") or "")).strip()
        key = name.upper()
        if key not in agg:
            agg[key] = {
                "name": name,
                "weight": 0.0,
                "holding_type": str(h.get("holding_type") or ""),
                "count": 0,
            }
        agg[key]["weight"] += number(h.get("weight")) or 0
        if not agg[key]["holding_type"] and h.get("holding_type"):
            agg[key]["holding_type"] = str(h["holding_type"])
        agg[key]["count"] += 1
    items = list(agg.values())
    items.sort(key=lambda x: x["weight"], reverse=True)
    return items


def pick_holdings(code: str):
    manual = MANUAL.get(code, {}).get("holdings")
    if manual:
        items = [
            {
                "name": name,
                "weight": float(weight),
                "holding_type": holding_type,
                "count": 1,
            }
            for name, weight, holding_type in manual["items"]
        ]
        return items, manual["report_period"], manual["source"], manual.get("note", "")

    groups = group_holdings(code)
    if not groups:
        if code == "BTE502":
            return (
                [],
                TARGET,
                f"{TARGET} 半年报",
                "数据库该半年报仅记录衍生品敞口，无逐项证券持仓",
            )
        return [], None, None, "数据库未取得可核验持仓"

    def doc_rank(document_type):
        return {
            "semiannual": 4,
            "semiannual_report": 4,
            "monthly": 3,
            "annual": 2,
        }.get(document_type, 1)

    keys = sorted(
        groups,
        key=lambda k: (k[0] or "0000-00-00", doc_rank(k[1]), len(groups[k])),
        reverse=True,
    )
    target_semi = [k for k in keys if k[0] == TARGET and k[1] in ("semiannual", "semiannual_report")]
    target_monthly = [k for k in keys if k[0] == TARGET and k[1] == "monthly"]
    latest_monthly = [k for k in keys if k[1] == "monthly"]

    if target_semi:
        pick, kind = target_semi[0], "target_semiannual"
    elif target_monthly:
        pick, kind = target_monthly[0], "target_monthly"
    elif latest_monthly:
        pick, kind = latest_monthly[0], "fallback_monthly"
    else:
        pick, kind = keys[0], "fallback_other"

    items = aggregate_items(groups[pick])
    raw_count = len(groups[pick])
    label = doc_label(pick[1])
    note = {
        "target_semiannual": f"{TARGET} {label}，原始{raw_count}项",
        "target_monthly": f"无{TARGET}半年报持仓，采用{TARGET}月报（{raw_count}项）",
        "fallback_monthly": f"无{TARGET}半年报持仓，采用{pick[0]}月报（{raw_count}项）",
        "fallback_other": f"无月报持仓，采用{pick[0]} {label}（{raw_count}项）",
    }[kind]
    source = f"{pick[0]} {label}"
    return items, pick[0], source, note


def classify_items(items):
    return [
        {**item, "issuer": "", "asset": str(item.get("holding_type") or "")}
        for item in items
    ]


def holdings_text(items, limit=10):
    lines = []
    for index, item in enumerate(items[:limit], start=1):
        lines.append(f"{index}. {item['name']}｜{item['weight']:.2f}%")
    return "\n".join(lines)


def issuer_text(items, limit=10):
    selected = items[:limit]
    if not selected or not any(item["issuer"] for item in selected):
        return ""
    return "\n".join(
        f"{index}. {item['issuer']}".rstrip()
        for index, item in enumerate(selected, start=1)
    )


def asset_text(items, limit=10):
    selected = items[:limit]
    if not selected or not any(item["asset"] for item in selected):
        return ""
    return "\n".join(
        f"{index}. {item['asset']}".rstrip()
        for index, item in enumerate(selected, start=1)
    )


def classification_summary(items, limit=10):
    selected = items[:limit]
    totals = defaultdict(float)
    for item in selected:
        if item["asset"]:
            totals[item["asset"]] += item["weight"]
    total = sum(totals.values())
    if total <= 0:
        return ""
    parts = [
        f"{label} {weight / total * 100:.1f}%"
        for label, weight in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return "数据库资产类型：" + "｜".join(parts)


# ---------------------------------------------------------------------------
# Row assembly
# ---------------------------------------------------------------------------


def merge_notes(*groups):
    out = []
    for group in groups:
        for note in group:
            if note and note not in out:
                out.append(note)
    return "；".join(out)


def concise_notes(
    *,
    code,
    daily,
    weekly,
    daily_time,
    weekly_time,
    inception_value,
    items,
    holdings_period,
    equity_debt,
):
    parts = []
    missing = []
    if daily is None:
        missing.append("日流动资产")
    if weekly is None:
        missing.append("周流动资产")
    if not inception_value:
        missing.append("成立时间")
    if not items and code != "BTE502":
        missing.append("前十大持仓")
    if equity_debt in (None, ""):
        missing.append("股债比")
    if items and all(not item["asset"] for item in items):
        missing.append("资产类型")
    if missing:
        parts.append("数据库未披露：" + "、".join(missing))

    if daily is not None and daily_time and daily_time > TARGET:
        if daily_time == "2026-09-03":
            parts.append("日流动资产记录日期2026-09-03，非月末，需复核")
        else:
            parts.append(f"日流动资产采用{daily_time}月报")
    if weekly is not None and weekly_time and weekly_time > TARGET:
        parts.append(f"周流动资产采用{weekly_time}月报")

    if items and holdings_period and holdings_period > TARGET:
        parts.append(f"持仓采用{holdings_period}月报，共{len(items)}项")
    elif items and len(items) < 10:
        parts.append(f"持仓仅{len(items)}项")
    if items and any(item.get("count", 1) > 1 for item in items):
        parts.append("同名多笔已合并")

    if code == "BUE512":
        parts.append("持仓疑似混入同伞子基金，原样列示，待复核")
    elif code == "BQN919":
        parts.append("持仓为脱敏/汇总名称，分类待复核")
    elif code == "BTE502":
        parts.append("半年报仅记录衍生品敞口，无逐项证券持仓")

    return "；".join(parts)


def build_rows():
    rows = []
    for fund in sorted(FUNDS, key=lambda x: x["rank"]):
        code = fund["fund_code"]
        manual = MANUAL.get(code, {})
        notes = []

        # Return
        ret_row, ret_status = pick_metric(code, "annualized_return_percent")
        ret_value = number(ret_row.get("numeric_value")) if ret_row else None
        ret_source = metric_source(ret_row)
        if ret_status == "fallback":
            notes.append(f"收益率采用{as_date(ret_row.get('report_period'))}数据")
        if ret_value is None:
            notes.append("过去一年年化收益率未披露")

        # Liquidity
        liquid = manual.get("liquid", {})
        liquid_rows = {}
        for key in ("daily", "weekly"):
            metric_name = f"{key}_liquid_assets_percent"
            if key in liquid:
                value = liquid.get(key)
                value = number(value)
                source = liquid.get("source") or "人工补录"
                time_value = liquid.get("time")
                if liquid.get("note"):
                    notes.append(liquid["note"])
            else:
                row, status = pick_metric(code, metric_name)
                value = number(row.get("numeric_value")) if row else None
                source = metric_source(row) if row else "未披露"
                time_value = as_date(row.get("report_period")) if row else None
                if row and status == "fallback":
                    notes.append(
                        f"{'日' if key == 'daily' else '周'}流动资产采用{time_value}月报"
                    )
                    if time_value and time_value[8:10] not in ("28", "29", "30", "31"):
                        notes.append("该数据库记录日期非月末，需复核是否为正式月报")
            liquid_rows[key] = {
                "value": value,
                "source": source,
                "time": time_value,
                "fill": fill_for_time(time_value) if value is not None else GREY,
            }
            if value is None:
                notes.append(f"{'日' if key == 'daily' else '周'}流动资产未披露")

        # Inception
        row, status = pick_metric(code, "inception_date")
        inception_value = str(row.get("text_value") or "")[:10] if row else None
        inception_source = metric_source(row) if row else "未披露"
        inception_time = as_date(row.get("report_period")) if row else None
        if not inception_value:
            inception_value = None
            inception_source = "未披露"
            inception_time = None
            notes.append("数据库未披露成立时间，留空")

        # Holdings
        items, holdings_period, holdings_source, holdings_note = pick_holdings(code)
        if holdings_note:
            notes.append(holdings_note)
        items = classify_items(items)
        items = items[:10]
        if items:
            if code == "BQN919":
                notes.append("数据库持仓为脱敏/汇总名称，分类待复核")
            if code == "BUE512":
                notes.append("数据库持仓疑似混入同伞子基金，原样列示，待复核")
            notes.append("银行/金融机构分类：数据库未提供，留空")
            if any(not item["asset"] for item in items):
                notes.append("部分持仓资产类型：数据库未提供，留空")
            count_note = f"前十大持仓按持仓权重排序"
            if len(items) < 10:
                count_note = f"持仓仅披露{len(items)}项，前十大不完整"
            notes.append(count_note)
            if any(item.get("count", 1) > 1 for item in items):
                notes.append("同名多笔持仓已合并计算")
        else:
            if not holdings_note:
                notes.append("未取得可核验的前十大持仓")

        # Equity / debt
        eq_row, eq_status = pick_metric(code, "equity_weight_percent")
        debt_row, debt_status = pick_metric(code, "debt_weight_percent")
        eq_period = as_date(eq_row.get("report_period")) if eq_row else None
        debt_period = as_date(debt_row.get("report_period")) if debt_row else None
        if (
            eq_row
            and debt_row
            and eq_period == debt_period
            and (eq_period == TARGET or str(eq_row.get("document_type")) == "monthly")
        ):
            equity = number(eq_row.get("numeric_value"))
            debt = number(debt_row.get("numeric_value"))
            eq_text = f"{equity:.0f}:{debt:.0f}" if equity is not None and debt is not None else None
            eq_source = metric_source(eq_row)
            eq_time = eq_period
            basis = "disclosed"
            eq_fill = fill_for_time(eq_time)
            if eq_period != TARGET:
                notes.append(f"股债比采用{eq_period}数据")
        else:
            eq_text = None
            eq_source = "未披露"
            eq_time = None
            basis = "missing"
            eq_fill = GREY
            notes.append("数据库未披露股债比，留空")

        row = {
            "rank": fund["rank"],
            "code": code,
            "name": fund["name_cn"],
            "manager": fund.get("manager_short") or fund.get("manager_name"),
            "scale": number(fund.get("fund_size")),
            "scale_date": SCALE_DATE,
            "currency": fund.get("base_currency") or "—",
            "return": ret_value,
            "return_source": ret_source,
            "daily": liquid_rows["daily"]["value"],
            "daily_source": liquid_rows["daily"]["source"],
            "daily_time": liquid_rows["daily"]["time"],
            "daily_fill": liquid_rows["daily"]["fill"],
            "weekly": liquid_rows["weekly"]["value"],
            "weekly_source": liquid_rows["weekly"]["source"],
            "weekly_time": liquid_rows["weekly"]["time"],
            "weekly_fill": liquid_rows["weekly"]["fill"],
            "inception": inception_value,
            "inception_source": inception_source or "—",
            "inception_time": inception_time,
            "holdings_period": holdings_period,
            "holdings_source": holdings_source or "—",
            "items": items,
            "holdings_text": holdings_text(items),
            "issuer_text": issuer_text(items),
            "asset_text": asset_text(items),
            "summary_text": classification_summary(items),
            "equity_debt": eq_text,
            "equity_debt_source": eq_source,
            "equity_debt_time": eq_time,
            "equity_debt_basis": basis,
            "equity_debt_fill": eq_fill,
            "notes": concise_notes(
                code=code,
                daily=liquid_rows["daily"]["value"],
                weekly=liquid_rows["weekly"]["value"],
                daily_time=liquid_rows["daily"]["time"],
                weekly_time=liquid_rows["weekly"]["time"],
                inception_value=inception_value,
                items=items,
                holdings_period=holdings_period,
                equity_debt=eq_text,
            ),
        }
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Workbook styling helpers
# ---------------------------------------------------------------------------


def fill(color):
    return PatternFill("solid", fgColor=color)


def style_cell(cell, *, bold=False, size=9.5, color=DARK, bg=WHITE, wrap=True, align="left"):
    cell.font = Font(name="Aptos", size=size, bold=bold, color=color)
    cell.fill = fill(bg)
    cell.alignment = Alignment(
        horizontal=align, vertical="center", wrap_text=wrap
    )
    cell.border = ALL_BORDER


def style_range(ws, cell_range, *, bold=False, size=9.5, color=DARK, bg=WHITE, align="left"):
    for row in ws[cell_range]:
        for cell in row:
            style_cell(cell, bold=bold, size=size, color=color, bg=bg, align=align)


def section_header(ws, row, text, last_col):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=last_col)
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = Font(name="Aptos", size=11, bold=True, color=NAVY)
    cell.fill = fill("DCE6F1")
    cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[row].height = 24


def write_table(ws, start_row, headers, rows, widths=None, header_bg=NAVY):
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=start_row, column=col, value=header)
        style_cell(cell, bold=True, size=9.5, color=WHITE, bg=header_bg, align="center")
    ws.row_dimensions[start_row].height = 30
    for r_idx, row in enumerate(rows, start=start_row + 1):
        for c_idx, value in enumerate(row, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=value)
            style_cell(cell, size=9, align="left")
    if widths:
        for idx, width in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = width


def merge_table_tail(ws, first_row, last_row, first_col, last_col):
    """Merge the unused trailing columns of a compact table and restore its border."""
    for row in range(first_row, last_row + 1):
        ws.merge_cells(
            start_row=row,
            start_column=first_col,
            end_row=row,
            end_column=last_col,
        )
        cell = ws.cell(row=row, column=first_col)
        cell.border = ALL_BORDER
        cell.alignment = Alignment(
            horizontal="left" if row > first_row else "center",
            vertical="center",
            wrap_text=True,
        )


def display_width(value) -> int:
    text = str(value or "")
    return sum(
        2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text
    )


def autofit_rows(
    ws,
    first_row,
    last_row,
    widths,
    *,
    font_size=8.5,
    min_height=54,
    max_height=205,
):
    """Approximate wrapped-text height so long holdings and notes are not clipped."""
    line_height = font_size * 1.36
    for row in range(first_row, last_row + 1):
        needed_lines = 1
        for col, width in widths.items():
            value = ws[f"{col}{row}"].value
            if value is None:
                continue
            chars_per_line = max(width * 1.02, 8)
            lines = sum(
                max(1, int((display_width(part) + chars_per_line - 1) // chars_per_line))
                for part in str(value).split("\n")
            )
            needed_lines = max(needed_lines, lines)
        ws.row_dimensions[row].height = min(
            max_height, max(min_height, needed_lines * line_height + 7)
        )


def configure_print(
    ws,
    *,
    paper_size,
    fit_width,
    title_rows=None,
    title_cols=None,
    header_left=None,
    print_start_row=1,
    print_end_row=None,
    footer_left=None,
):
    ws.page_setup.paperSize = paper_size
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = fit_width
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = 0.25
    ws.page_margins.right = 0.25
    ws.page_margins.top = 0.45
    ws.page_margins.bottom = 0.45
    ws.page_margins.header = 0.2
    ws.page_margins.footer = 0.2
    end_row = print_end_row or ws.max_row
    ws.print_area = (
        f"A{print_start_row}:{get_column_letter(ws.max_column)}{end_row}"
    )
    if title_rows:
        ws.print_title_rows = title_rows
    if title_cols:
        ws.print_title_cols = title_cols
    ws.oddHeader.left.text = header_left or ws.title
    ws.oddHeader.left.size = 9
    ws.oddHeader.left.color = MID
    ws.oddHeader.right.text = "&A"
    ws.oddHeader.right.size = 9
    ws.oddHeader.right.color = MID
    ws.oddFooter.center.text = "第 &P 页 / 共 &N 页"
    ws.oddFooter.center.size = 8.5
    ws.oddFooter.center.color = MID
    if footer_left:
        ws.oddFooter.left.text = footer_left
        ws.oddFooter.left.size = 7.5
        ws.oddFooter.left.color = MID


def add_hyperlink(cell, url, label=None):
    cell.value = label or url
    cell.hyperlink = url
    cell.font = Font(name="Aptos", size=8.5, color="0563C1", underline="single")


# ---------------------------------------------------------------------------
# Sheet 1
# ---------------------------------------------------------------------------


def build_sheet1(wb, rows):
    ws = wb.active
    ws.title = "口径与颜色"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = NAVY

    ws.merge_cells("A1:E1")
    c = ws["A1"]
    c.value = "Case 01｜香港中资货币基金 Top15 数据口径与颜色说明"
    c.font = Font(name="Aptos Display", size=16, bold=True, color=WHITE)
    c.fill = fill(NAVY)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 34

    ws.merge_cells("A2:E2")
    c = ws["A2"]
    c.value = (
        f"生成日期：{datetime.now().strftime('%Y-%m-%d')}｜"
        f"数据库快照：{DATA['snapshot']['snapshot_id']}（{str(DATA['snapshot']['published_at'])[:19]}）｜"
        f"数据源：数据库当前发布快照（无 PDF 人工补录）｜规模排名：按 case brief 固定为 HKEX {SCALE_DATE} 快照｜"
        f"其余字段优先 {TARGET}，缺失回退最新月报"
    )
    c.font = Font(name="Aptos", size=9.5, color=DARK)
    c.fill = fill(LIGHT_BLUE)
    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 30

    section_header(ws, 4, "一、字段与取数规则", 5)
    defs = [
        ("字段", "口径", "优先来源/时间点", "缺失/回退规则", "注意事项"),
        (
            "规模与排名",
            "亿港币；按基金规模降序，取中资货币基金前15名",
            "HKEX 2026-08-31 规模快照（数据库当前发布批次）",
            "无规模记录则不进入排名",
            "规模日期与报告日期不同，不作同日资产规模比较",
        ),
        (
            "币种",
            "基金主数据 base_currency",
            "数据库基金主表",
            "缺失写“—”",
            "不同币种规模不直接相加作收益比较",
        ),
        (
            "过去一年年化收益率",
            "2025-06-30 至 2026-06-30，Bloomberg 总回报指数净分红序列年化",
            "Bloomberg 历史数据（2026-06-30）",
            "缺失则写“—”并在备注说明",
            "不是七日年化收益率，也不是累计回报；不同币种/份额类别不可直接横比",
        ),
        (
            "日/周流动资产",
            "报告披露的 daily / weekly liquid assets 占资产净值比例",
            "2026-06-30 半年报；无则 2026-06-30 月报；再无则最新月报",
            "无可靠披露则留空，备注说明",
            "日/周流动资产不等于 WAM/WAL，也不等于压力测试后的可用流动性",
        ),
        (
            "成立时间",
            "基金或首个份额类别成立/发行日期",
            "数据库 inception_date 指标；2026-06-30 优先，否则最新记录",
            "数据库未披露则留空，不再以 SFC 认可日期替代",
            "SFC 认可日期不是法律成立日，本版不使用",
        ),
        (
            "前十大持仓",
            "按数据库持仓记录权重排序，最多10项；同名多笔合并",
            "数据库持仓；2026-06-30 半年报优先，否则 2026-06-30 月报，否则最新月报",
            "数据库无持仓则留空，备注说明报告期与原因",
            "部分月报仅披露前5项；不把其他期间持仓混入同列",
        ),
        (
            "银行/金融机构分类",
            "逐项展示数据库可用的发行人类型字段",
            "数据库 fund_holdings 字段",
            "数据库没有该字段，全部留空；不做名称规则推断",
            "留空原因是数据库未提供银行/金融机构分类，不代表无银行敞口",
        ),
        (
            "资产类型分类",
            "直接照搬数据库 holding_type 原文（如 Certificate of Deposit、掛牌債券、非上市存款證等）",
            "数据库 fund_holdings.holding_type",
            "数据库未提供则留空",
            "不把货币市场工具改写成普通债券，也不补造分类",
        ),
        (
            "股债比",
            "股票权重 : 债务证券权重；货币基金通常为 0:100",
            "数据库 equity_weight_percent / debt_weight_percent 直接披露值",
            "数据库未披露则留空，不按月报资产类别推断",
            "本版不生成推断值",
        ),
        (
            "备注",
            "逐基金说明回退期间、数据缺口、异常与人工复核事项",
            "生成脚本按规则自动汇总",
            "—",
            "备注优先于单元格颜色；颜色只表示数据时间点",
        ),
    ]
    write_table(ws, 5, defs[0], defs[1:], widths=[16, 38, 28, 26, 42])
    for r in range(6, 6 + len(defs) - 1):
        ws.row_dimensions[r].height = 46

    start = 6 + len(defs) - 1 + 2
    section_header(ws, start, "二、颜色图例（Sheet2 单元格底色代表的数据时间点）", 5)

    legend = [
        ("色块", "颜色含义", "数据时间点", "适用字段", "说明"),
        (BLUE, "2026-06-30 口径", "2026-06-30", "流动性、成立时间、持仓、股债比", "半年报或该日可得的月报；同一天优先半年报"),
        (GREEN, "最新月报/规模快照", "2026-07-01 至 2026-09-30", "流动性、持仓、成立时间、规模", "无 2026-06-30 披露时回退；规模固定为 2026-08-31"),
        (PURPLE, "Bloomberg 年化收益", "2025-06-30 至 2026-06-30", "过去一年年化收益率", "区间收益，不是时点值；颜色按区间终点标识"),
        (AMBER, "待复核/异常（保留色）", "—", "—", "本版不生成替代或推断值；异常持仓在备注列提示"),
        (GREY, "缺失", "—", "任意字段", "报告未披露或数据不可用；原因写在备注列"),
        ("FFFFFF", "基础信息", "静态/非时间序列", "币种、管理人、代码", "不参与时间点比较"),
    ]
    r0 = start + 1
    for offset, (color, meaning, time_text, fields, note) in enumerate(legend[1:], start=1):
        row = r0 + offset
        swatch = ws.cell(row=row, column=1, value="")
        swatch.fill = fill(color)
        swatch.border = ALL_BORDER
        for col, value in enumerate((meaning, time_text, fields, note), start=2):
            cell = ws.cell(row=row, column=col, value=value)
            style_cell(cell, size=9, align="left")
        ws.row_dimensions[row].height = 30
    for col, header in enumerate(legend[0], start=1):
        cell = ws.cell(row=r0, column=col, value=header)
        style_cell(cell, bold=True, size=9.5, color=WHITE, bg=NAVY, align="center")
    ws.row_dimensions[r0].height = 26

    start2 = r0 + len(legend)
    section_header(ws, start2, "三、数据覆盖摘要（本表 15 只基金）", 5)
    semi = sum(
        1
        for r in rows
        if r["items"]
        and r["holdings_period"] == TARGET
        and "半年报" in str(r["holdings_source"])
    )
    monthly_target = sum(
        1
        for r in rows
        if r["items"]
        and r["holdings_period"] == TARGET
        and "月报" in str(r["holdings_source"])
    )
    monthly_latest = sum(
        1 for r in rows if r["items"] and r["holdings_period"] and r["holdings_period"] > TARGET
    )
    missing_holdings = sum(1 for r in rows if not r["items"])
    liquid_ok = sum(1 for r in rows if r["daily"] is not None and r["weekly"] is not None)
    eq_disclosed = sum(1 for r in rows if r["equity_debt_basis"] == "disclosed")
    eq_missing = sum(1 for r in rows if r["equity_debt_basis"] == "missing")
    coverage = [
        ("项目", "数量/结果", "说明", "", ""),
        ("基金总数", f"{len(rows)} 只", "中资货币基金规模前15名；广发排名第13，故取15名", "", ""),
        ("2026-06-30 半年报持仓", f"{semi} 只", "按半年报原始持仓排序取前十大", "", ""),
        ("2026-06-30 月报持仓", f"{monthly_target} 只", "该日无半年报持仓，但月报披露持仓", "", ""),
        ("最新月报持仓回退", f"{monthly_latest} 只", "采用 2026-08-31 或 2026-09-30 月报", "", ""),
        ("无持仓", f"{missing_holdings} 只", "报告未披露逐项持仓或结构化不可用", "", ""),
        ("日/周流动资产均可得", f"{liquid_ok} 只", "含半年报、月报与回退值；其余见备注", "", ""),
        ("股债比直接披露", f"{eq_disclosed} 只", "数据库明确披露股票/债务权重", "", ""),
        ("股债比缺失", f"{eq_missing} 只", "数据库未披露，留空，不推断", "", ""),
        ("过去一年年化收益率", f"{sum(1 for r in rows if r['return'] is not None)} 只", "Bloomberg 2025-06-30 至 2026-06-30", "", ""),
    ]
    write_table(ws, start2 + 1, coverage[0], coverage[1:], widths=[16, 38, 28, 26, 42])
    for r in range(start2 + 2, start2 + 2 + len(coverage) - 1):
        ws.row_dimensions[r].height = 28
    merge_table_tail(ws, start2 + 1, start2 + len(coverage), 3, 5)

    start3 = start2 + 2 + len(coverage) - 1 + 1
    section_header(ws, start3, "四、中资货基市场容量（2026-08-31，按基金基础货币）", 5)
    market = DATA.get("market_capacity", [])
    total_size = sum(number(m.get("total_size")) or 0 for m in market)
    market_rows = [("币种", "基金数", "规模合计（亿港币）", "占比", "说明")]
    for m in market:
        size = number(m.get("total_size")) or 0
        market_rows.append(
            (
                m.get("base_currency") or "—",
                int(m.get("fund_count") or 0),
                f"{size:,.2f}",
                f"{size / total_size * 100:.1f}%" if total_size else "—",
                "中资、资产类别一级分类=货币",
            )
        )
    market_rows.append(
        (
            "合计",
            int(sum(int(m.get("fund_count") or 0) for m in market)),
            f"{total_size:,.2f}",
            "100.0%",
            "市场容量不等于Top15合计",
        )
    )
    write_table(ws, start3 + 1, market_rows[0], market_rows[1:], widths=[16, 38, 28, 26, 42])

    start4 = start3 + 1 + len(market_rows) + 1
    section_header(ws, start4, "五、来源、版本与限制", 5)
    notes = [
        ("来源/限制", "说明"),
        (
            "数据库",
            f"PostgreSQL fund_research，当前发布快照 {DATA['snapshot']['snapshot_id']}，"
            f"发布版本 {DATA['snapshot'].get('release_version')}，"
            f"发布时间 {str(DATA['snapshot'].get('published_at'))[:19]}。"
            "基金身份、分类、规模、指标、持仓与分配均来自该快照。",
        ),
        (
            "HKEX 主数据",
            "基金代码、名称、管理人、基础货币与 2026-08-31 规模快照。"
            "规模单位为亿港币；排名仅用于研究，不构成投资建议。",
        ),
        (
            "Bloomberg",
            "过去一年年化收益率来自 case/summary.csv 与 bloomberg_money_market_stats.py，"
            "区间 2025-06-30 至 2026-06-30，序列为 TOT_RETURN_INDEX_NET_DVDS。",
        ),
        (
            "基金报告",
            "基金指标、持仓与分类均取自数据库当前发布快照的结构化结果；"
            "报告期与页码见 Sheet2 来源列，缺失、回退与异常见备注列。",
        ),
        (
            "SFC 规则",
            "Sheet3 阈值为 SFC《单位信托及互惠基金守则》2026年11月版第8.2条（2026-11-01生效）"
            "及 24EC64 通函；正式合规判断应以基金适用版本、过渡安排和完整持仓为准。",
        ),
        (
            "数据限制",
            "银行/金融机构分类：数据库未提供该字段，整列留空，不做名称规则推断；"
            "资产类型直接照搬数据库 holding_type，未提供则留空；"
            "部分基金仅披露前5项或1项持仓，同名多笔已合并；"
            "BUE512 持仓疑似混入同伞子基金、BQN919 为脱敏名称，均原样列示并在备注提示；"
            "流动性、成立时间、股债比缺失的，一律留空不推断；数据库个别值与报告原文可能存在差异，建议以报告复核。",
        ),
        (
            "复现",
            "运行 case/export_case_inputs.py 导出当前快照，再运行 case/rebuild_case_delivery.py 生成；"
            "本版不使用 PDF 人工补录。",
        ),
    ]
    write_table(ws, start4 + 1, notes[0], notes[1:], widths=[16, 38, 28, 26, 42])
    for r in range(start4 + 2, start4 + 2 + len(notes) - 1):
        ws.row_dimensions[r].height = 52
    merge_table_tail(ws, start4 + 1, start4 + len(notes), 2, 5)

    for col, width in zip("ABCDE", [16, 38, 28, 26, 42]):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A5"
    configure_print(
        ws,
        paper_size=ws.PAPERSIZE_A4,
        fit_width=1,
        header_left="Case 01｜中资货基 Top15 数据口径",
    )


# ---------------------------------------------------------------------------
# Sheet 2
# ---------------------------------------------------------------------------


def build_sheet2(wb, rows):
    ws = wb.create_sheet("中资货基Top15")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "548235"

    headers = [
        "排名",
        "代码",
        "基金名称",
        "管理人",
        "规模\n（亿港币）",
        "规模日期",
        "币种",
        "过去一年\n年化收益率\n（%）",
        "收益率来源/期间",
        "日流动资产\n（%）",
        "日流动资产来源/日期",
        "周流动资产\n（%）",
        "周流动资产来源/日期",
        "成立时间",
        "成立时间来源",
        "持仓来源/报告期",
        "前十大持仓（名称｜占比）",
        "银行/金融机构分类\n（数据库未提供，留空）",
        "资产类型分类\n（数据库 holding_type 原值）",
        "数据库资产类型汇总\n（按前十大权重）",
        "股债比",
        "股债比来源/日期",
        "备注",
    ]
    last_col = len(headers)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    c = ws.cell(row=1, column=1, value="香港中资货币基金规模 Top15｜收益、流动性、成立时间、前十大持仓与股债比")
    c.font = Font(name="Aptos Display", size=16, bold=True, color=WHITE)
    c.fill = fill(NAVY)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 34

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
    c = ws.cell(
        row=2,
        column=1,
        value=(
            f"数据源：数据库当前发布快照｜规模排序：HKEX {SCALE_DATE} 快照｜"
            f"结构字段：{TARGET} 优先，缺失回退最新月报｜"
            "颜色=数据时间点（蓝=2026-06-30，绿=最新月报/规模快照，紫=Bloomberg一年区间，琥珀=待复核/异常，灰=缺失）"
        ),
    )
    c.font = Font(name="Aptos", size=9.5, color=DARK)
    c.fill = fill(LIGHT_BLUE)
    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 32

    groups = [
        (1, 7, "基础信息（规模快照 2026-08-31）", "DCE6F1"),
        (8, 9, "收益（Bloomberg 2025-06-30 → 2026-06-30）", "E4DFEC"),
        (10, 13, "流动性（优先 2026-06-30）", "D9EAF7"),
        (14, 15, "成立时间", "E2F0D9"),
        (16, 20, "持仓与分类（优先 2026-06-30 半年报）", "FFF2CC"),
        (21, 23, "股债比与备注", "DCE6F1"),
    ]
    for start, end, label, color in groups:
        ws.merge_cells(start_row=3, start_column=start, end_row=3, end_column=end)
        cell = ws.cell(row=3, column=start, value=label)
        style_cell(cell, bold=True, size=9, color=NAVY, bg=color, align="center")
    ws.row_dimensions[3].height = 22

    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col, value=header)
        style_cell(cell, bold=True, size=9, color=WHITE, bg=NAVY, align="center")
    ws.row_dimensions[4].height = 42

    for idx, row in enumerate(rows, start=5):
        values = [
            row["rank"],
            row["code"],
            row["name"],
            row["manager"] or "—",
            row["scale"],
            row["scale_date"],
            row["currency"],
            row["return"],
            row["return_source"],
            row["daily"],
            row["daily_source"],
            row["weekly"],
            row["weekly_source"],
            row["inception"],
            row["inception_source"],
            row["holdings_source"],
            row["holdings_text"],
            row["issuer_text"],
            row["asset_text"],
            row["summary_text"],
            row["equity_debt"],
            row["equity_debt_source"],
            row["notes"],
        ]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=idx, column=col, value=value)
            style_cell(cell, size=8.5, align="left")
            if col in (1, 2, 5, 6, 7, 8, 10, 12, 14, 21):
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            if col == 5:
                cell.number_format = "#,##0.00"
            if col in (8, 10, 12):
                cell.number_format = "0.00"
        # time-point colors
        for col in (5, 6):
            ws.cell(row=idx, column=col).fill = fill(GREEN)
        ws.cell(row=idx, column=7).fill = fill(WHITE)
        for col in (8, 9):
            ws.cell(row=idx, column=col).fill = fill(PURPLE)
        ws.cell(row=idx, column=10).fill = fill(row["daily_fill"])
        ws.cell(row=idx, column=11).fill = fill(row["daily_fill"])
        ws.cell(row=idx, column=12).fill = fill(row["weekly_fill"])
        ws.cell(row=idx, column=13).fill = fill(row["weekly_fill"])
        inception_fill = (
            AMBER
            if "替代" in str(row["inception_source"])
            else fill_for_time(row["inception_time"])
        )
        ws.cell(row=idx, column=14).fill = fill(inception_fill)
        ws.cell(row=idx, column=15).fill = fill(inception_fill)
        holdings_fill = fill_for_time(row["holdings_period"])
        for col in (16, 17, 18, 19, 20):
            ws.cell(row=idx, column=col).fill = fill(holdings_fill if row["items"] else GREY)
        ws.cell(row=idx, column=21).fill = fill(row["equity_debt_fill"])
        ws.cell(row=idx, column=22).fill = fill(row["equity_debt_fill"])
        ws.cell(row=idx, column=23).fill = fill(WHITE)

    widths = {
        "A": 5.5,
        "B": 9,
        "C": 24,
        "D": 11,
        "E": 11,
        "F": 10,
        "G": 7,
        "H": 11,
        "I": 25,
        "J": 9,
        "K": 25,
        "L": 9,
        "M": 25,
        "N": 11,
        "O": 23,
        "P": 25,
        "Q": 58,
        "R": 21,
        "S": 17,
        "T": 34,
        "U": 9,
        "V": 30,
        "W": 62,
    }
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    autofit_rows(ws, 5, 4 + len(rows), widths)

    ws.freeze_panes = "C5"
    ws.auto_filter.ref = f"A4:{get_column_letter(last_col)}{4 + len(rows)}"
    ws.sheet_view.zoomScale = 75

    note_row = 5 + len(rows) + 1
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=last_col)
    c = ws.cell(
        row=note_row,
        column=1,
        value=(
            "说明：规模单位为亿港币；收益率、流动资产为百分比。颜色仅表示数据时间点，"
            "不代表数据质量；回退期间、缺失原因、人工修正与待复核事项见备注列。"
            "“分类汇总”仅按数据库 holding_type 与权重分组；未提供的留空，不是信用评级或合规结论。"
        ),
    )
    c.font = Font(name="Aptos", size=9, color=MID)
    c.fill = fill(LIGHT_BLUE)
    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[note_row].height = 34

    configure_print(
        ws,
        paper_size=ws.PAPERSIZE_A3,
        fit_width=2,
        title_rows="4:4",
        title_cols="A:B",
        header_left="香港中资货币基金规模 Top15",
        print_start_row=4,
        print_end_row=note_row - 1,
        footer_left="颜色仅表示数据时间点；回退、缺失与人工判断见备注列",
    )


# ---------------------------------------------------------------------------
# Sheet 2 (wide layout: ten holding columns, ten asset-type columns)
# ---------------------------------------------------------------------------


def build_sheet2_wide(wb, rows):
    ws = wb.create_sheet("中资货基Top15")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "548235"

    base_headers = [
        "排名",
        "代码",
        "基金名称",
        "管理人",
        "规模\n（亿港币）",
        "币种",
        "过去一年\n年化收益率\n（%）",
        "日流动资产\n（%）",
        "周流动资产\n（%）",
        "成立时间",
    ]
    holding_headers = [f"第{i}大\n持仓" for i in range(1, 11)]
    type_headers = [f"类型{i}" for i in range(1, 11)]
    tail_headers = [
        "银行/金融机构\n分类（未提供）",
        "股债比",
        "备注",
    ]
    headers = base_headers + holding_headers + type_headers + tail_headers
    last_col = len(headers)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    c = ws.cell(
        row=1,
        column=1,
        value="香港中资货币基金规模 Top15｜收益、流动性、成立时间、前十大持仓与股债比",
    )
    c.font = Font(name="Aptos Display", size=16, bold=True, color=WHITE)
    c.fill = fill(NAVY)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 34

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
    c = ws.cell(
        row=2,
        column=1,
        value=(
            f"数据源：数据库当前发布快照｜规模排序：HKEX {SCALE_DATE} 快照｜"
            "颜色=数据时间点（蓝=2026-06-30，绿=最新月报/规模快照，紫=Bloomberg一年区间，"
            "灰=缺失或无数据，白=静态信息）"
        ),
    )
    c.font = Font(name="Aptos", size=9.5, color=DARK)
    c.fill = fill(LIGHT_BLUE)
    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 32

    groups = [
        (1, 6, "基础信息（规模快照 2026-08-31）", "DCE6F1"),
        (7, 7, "收益（Bloomberg 2025-06-30 → 2026-06-30）", "E4DFEC"),
        (8, 9, "流动性（优先 2026-06-30）", "D9EAF7"),
        (10, 10, "成立时间", "E2F0D9"),
        (11, 20, "前十大持仓（名称｜占比，2026-06-30 优先）", "FFF2CC"),
        (21, 30, "资产类型（数据库 holding_type 原值）", "FFF2CC"),
        (31, 31, "银行分类（数据库未提供）", "E7E6E6"),
        (32, 33, "股债比与备注", "DCE6F1"),
    ]
    for start, end, label, color in groups:
        if start != end:
            ws.merge_cells(
                start_row=3, start_column=start, end_row=3, end_column=end
            )
        cell = ws.cell(row=3, column=start, value=label)
        style_cell(cell, bold=True, size=8.5, color=NAVY, bg=color, align="center")
    ws.row_dimensions[3].height = 22

    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col, value=header)
        style_cell(cell, bold=True, size=8.5, color=WHITE, bg=NAVY, align="center")
    ws.row_dimensions[4].height = 42

    for idx, row in enumerate(rows, start=5):
        items = row["items"][:10]
        values = [
            row["rank"],
            row["code"],
            row["name"],
            row["manager"] or "",
            row["scale"],
            row["currency"],
            row["return"],
            row["daily"],
            row["weekly"],
            row["inception"],
        ]
        values += [
            f"{item['name']}｜{item['weight']:.2f}%" if pos < len(items) else ""
            for pos, item in enumerate(items[:10])
        ]
        values += [""] * (10 - len(items))
        values += [
            (items[pos]["asset"] if pos < len(items) else "")
            for pos in range(10)
        ]
        values += ["", row["equity_debt"] or "", row["notes"]]

        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=idx, column=col, value=value if value != "" else None)
            style_cell(cell, size=8.5, align="left")
            if col in (1, 2, 5, 6, 7, 8, 9, 10, 31, 32):
                cell.alignment = Alignment(
                    horizontal="center", vertical="center", wrap_text=True
                )
            if col == 5:
                cell.number_format = "#,##0.00"
            if col in (7, 8, 9):
                cell.number_format = "0.00"

        ws.cell(row=idx, column=5).fill = fill(GREEN)
        ws.cell(row=idx, column=7).fill = fill(PURPLE)
        ws.cell(row=idx, column=8).fill = fill(row["daily_fill"])
        ws.cell(row=idx, column=9).fill = fill(row["weekly_fill"])
        ws.cell(row=idx, column=10).fill = fill(
            fill_for_time(row["inception_time"])
        )

        holdings_fill = fill_for_time(row["holdings_period"])
        for pos in range(10):
            ws.cell(row=idx, column=11 + pos).fill = fill(
                holdings_fill if pos < len(items) else GREY
            )
            type_value = items[pos]["asset"] if pos < len(items) else ""
            ws.cell(row=idx, column=21 + pos).fill = fill(
                holdings_fill if type_value else GREY
            )
        ws.cell(row=idx, column=31).fill = fill(GREY)
        ws.cell(row=idx, column=32).fill = fill(row["equity_debt_fill"])
        ws.cell(row=idx, column=33).fill = fill(WHITE)

    widths = {
        "A": 5.5,
        "B": 9,
        "C": 24,
        "D": 11,
        "E": 11,
        "F": 7,
        "G": 11,
        "H": 9,
        "I": 9,
        "J": 11,
    }
    for pos in range(10):
        widths[get_column_letter(11 + pos)] = 22
    for pos in range(10):
        widths[get_column_letter(21 + pos)] = 14
    widths["AE"] = 14
    widths["AF"] = 9
    widths["AG"] = 60
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    autofit_rows(ws, 5, 4 + len(rows), widths, min_height=42, max_height=180)

    ws.freeze_panes = "C5"
    ws.auto_filter.ref = f"A4:{get_column_letter(last_col)}{4 + len(rows)}"
    ws.sheet_view.zoomScale = 65

    note_row = 5 + len(rows) + 1
    ws.merge_cells(
        start_row=note_row, start_column=1, end_row=note_row, end_column=last_col
    )
    c = ws.cell(
        row=note_row,
        column=1,
        value=(
            "说明：规模单位为亿港币；收益率、流动资产为百分比。颜色仅表示数据时间点；"
            "缺失、回退与异常只写在备注列。前十大持仓按数据库记录权重排序；"
            "“资产类型”直接照搬 holding_type；“银行分类”数据库未提供，整列留空。"
        ),
    )
    c.font = Font(name="Aptos", size=9, color=MID)
    c.fill = fill(LIGHT_BLUE)
    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[note_row].height = 34

    configure_print(
        ws,
        paper_size=ws.PAPERSIZE_A3,
        fit_width=2,
        title_rows="4:4",
        title_cols="A:B",
        header_left="香港中资货币基金规模 Top15",
        print_start_row=4,
        print_end_row=note_row - 1,
        footer_left="颜色仅表示数据时间点；缺失、回退与异常见备注列",
    )


# ---------------------------------------------------------------------------
# Sheet 3
# ---------------------------------------------------------------------------


def build_sheet3(wb, rows):
    ws = wb.create_sheet("SFC监管要求")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "7F6000"

    ws.merge_cells("A1:F1")
    c = ws["A1"]
    c.value = "香港证监会（SFC）货币市场基金风险管理要求｜2026年11月版"
    c.font = Font(name="Aptos Display", size=16, bold=True, color=WHITE)
    c.fill = fill(NAVY)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 34

    ws.merge_cells("A2:F2")
    c = ws["A2"]
    c.value = (
        "依据：SFC《单位信托及互惠基金守则》2026年11月版第8.2条（2026-11-01生效）；"
        "SFC 24EC64 通函（2024-12-17）及 16EC29 通函（2016-07-04）。"
        "本表为研究与数据复核参考，不是法律意见、合规审计或对任何基金的认证。"
    )
    c.font = Font(name="Aptos", size=9.5, color=DARK)
    c.fill = fill(AMBER)
    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 42

    headers = ["主题", "要求/管理要点", "定量阈值", "适用对象/状态", "在本 Case 中的用法", "官方来源"]
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=4, column=col, value=header)
        style_cell(cell, bold=True, size=9.5, color=WHITE, bg=NAVY, align="center")
    ws.row_dimensions[4].height = 30

    reqs = [
        (
            "投资范围",
            "主要投资于短期存款、高质量货币市场工具，以及符合条件的货币市场基金；"
            "管理人需建立审慎的信用质量评估程序，不应过度依赖外部评级。",
            "—",
            "所有 SFC 认可货币市场基金",
            "Sheet2 的资产类型列直接展示数据库 holding_type；银行分类因数据库无该字段而留空，不等同信用评级。",
            "UT Code 8.2(e) 及 Note(2)",
        ),
        (
            "加权平均到期日（WAM）",
            "组合 WAM 用于衡量对货币市场利率变动的敏感度；按各工具相对持仓加权计算至到期日。",
            "≤ 60 天",
            "硬性数值要求",
            "Sheet2 未单列 WAM；本次可取得的 WAM 披露值均低于 60 天，缺失项需回报告补核。",
            "UT Code 8.2(f) 及 Note(1)",
        ),
        (
            "加权平均存续期（WAL）",
            "组合 WAL 用于衡量信用风险与流动性风险；按各证券剩余存续期加权计算，"
            "可变利率票据的利率重置一般不得缩短 WAL。",
            "≤ 120 天",
            "硬性数值要求",
            "WAL 与 WAM 不能混为一项；本次可取得的 WAL 披露值均低于 120 天，缺失项需回报告补核。",
            "UT Code 8.2(f) 及 Notes(2)(3)",
        ),
        (
            "单一工具剩余期限",
            "不得购买剩余期限超过上限的货币市场工具；政府及其他公共证券适用较长上限。",
            "一般 ≤ 397 天；政府/其他公共证券 ≤ 2 年",
            "硬性数值要求（例外需核对）",
            "可用半年报持仓的到期日做异常期限筛查；本表未逐项计算全部持仓期限。",
            "UT Code 8.2(f)",
        ),
        (
            "每日流动资产（一般 MMF）",
            "现金、可在一个营业日内转换为现金的工具/证券，以及一个营业日内无条件到期的应收款。",
            "≥ 资产净值的 7.5%",
            "所有 SFC 认可货币市场基金",
            "Sheet2 的“日流动资产”列；蓝色为 2026-06-30，绿色为最新月报，灰色为未披露。",
            "UT Code 8.2(n) 及 Note(1)",
        ),
        (
            "每周流动资产（一般 MMF）",
            "现金、可在五个营业日内转换为现金的工具/证券，以及五个营业日内无条件到期的应收款。",
            "≥ 资产净值的 15%",
            "所有 SFC 认可货币市场基金",
            "Sheet2 的“周流动资产”列；周流动资产通常应不低于日流动资产。",
            "UT Code 8.2(n) 及 Note(2)",
        ),
        (
            "压力测试与流动性加码",
            "管理人应定期进行压力测试，并根据市场状况与预期赎回提高日/周流动资产水平。",
            "—（监管期望）",
            "持续管理要求",
            "Sheet2 的静态披露值不等于压力测试后的流动性；不得用规模或收益率推断流动性安全。",
            "UT Code 8.2(n) Note(3)；24EC64",
        ),
        (
            "固定/稳定净值 MMF：资产范围",
            "采用固定/稳定资产净值或摊余成本会计的基金，须至少 99.5% 资产净值投资于"
            "政府、公共当局或多边机构发行/担保的流动性高质量货币市场工具、相关逆回购及现金/短期存款。",
            "≥ 99.5% 资产净值",
            "仅适用于固定/稳定净值 MMF；须 SFC 逐案认可",
            "本次 Top15 未识别为固定/稳定净值型；如适用，应单独核对招募说明书与 SFC 认可条件。",
            "UT Code 8.2(o) 及 Note(2)(a)",
        ),
        (
            "固定/稳定净值 MMF：流动性",
            "固定/稳定净值 MMF 适用更高的日/周流动资产要求。",
            "日 ≥ 15%；周 ≥ 50%",
            "仅适用于固定/稳定净值 MMF",
            "与一般 MMF 的 7.5%/15% 区分；Sheet2 未对基金净值类型做自动判定。",
            "UT Code 8.2(o) Note(2)(b)",
        ),
        (
            "单一实体集中度",
            "单一实体发行/存款工具合计不得超过资产净值 10%；符合条件的大型金融机构可提高至 25%，"
            "政府/其他公共证券同一发行可至 30%；小于 100 万美元的存款有例外。",
            "一般 ≤ 10%；例外 25% / 30%",
            "硬性数值要求（需结合例外条件）",
            "Sheet2 未提供银行/发行人分类，无法用于集中度初筛，需回报告补取。",
            "UT Code 8.2(g)",
        ),
        (
            "同一集团集中度",
            "同一集团内实体的工具与存款合计不得超过资产净值 20%；小额存款有例外。",
            "≤ 20%",
            "硬性数值要求",
            "需要集团层级映射；本表仅按持仓名称做发行人初筛，未做集团合并。",
            "UT Code 8.2(g)(a)",
        ),
        (
            "借款",
            "仅可为满足赎回或支付费用等临时目的借款，且不得超过资产净值 10%。",
            "≤ 10% 资产净值（临时）",
            "硬性数值要求",
            "本表不采集借款数据；需查基金财务报表。",
            "UT Code 8.2(h)",
        ),
        (
            "回购/逆回购",
            "出售及回购交易的现金合计、以及向同一交易对手提供的逆回购现金均设上限；"
            "担保品限于现金、高质量货币市场工具及符合条件的政府证券。",
            "回购现金合计 ≤ 10%；同一对手逆回购 ≤ 15%",
            "硬性数值要求",
            "本表不采集回购数据；需查基金财务报表与持仓明细。",
            "UT Code 8.2(i)-(k)",
        ),
        (
            "衍生品",
            "货币市场基金仅可为对冲目的使用金融衍生工具。",
            "仅对冲",
            "硬性要求",
            "BTE502 半年报的衍生品敞口行不作为持仓；Sheet2 已剔除 NET/GROSS derivative exposure。",
            "UT Code 8.2(l)",
        ),
        (
            "货币风险",
            "非基础货币计价的重大货币风险应适当对冲。",
            "—（原则性要求）",
            "持续管理要求",
            "本表不采集对冲比率；需查基金策略与财务报表。",
            "UT Code 8.2(m)",
        ),
        (
            "反稀释流动性管理工具",
            "至少应有一种反稀释 LMT，将赎回成本分配给赎回投资者；应校准阈值并动态启用。",
            "至少 1 种 LMT",
            "硬性要求（2026年11月版）",
            "本表不采集 LMT 类型与阈值；需查招募说明书/管理人政策。",
            "UT Code 8.2(p)",
        ),
        (
            "流动性风险管理框架",
            "建立并执行有效的流动性风险管理政策与程序，考虑投资策略、投资者基础、"
            "资产流动性、负债/赎回义务与赎回政策；定期压力测试并准备行动计划。",
            "—（监管期望/良好实践）",
            "持续管理要求",
            "Sheet2 的日/周流动资产只是报告披露指标，不等于完整的流动性风险评估。",
            "24EC64；16EC29",
        ),
        (
            "高质量信用评估",
            "管理人应同时考虑信用质量与流动性，建立审慎的内部评估程序；"
            "一般不投资无评级或低投资级货币市场工具。",
            "—（监管期望）",
            "持续管理要求",
            "Sheet2 的分类字段直接来自数据库，不是信用评级；不得据此判断发行人信用质量。",
            "24EC64；UT Code 8.2(e) Note(2)",
        ),
        (
            "信息披露",
            "货币市场基金应在财务报告/附录披露组合 WAM、WAL，以及日/周流动资产等关键指标。",
            "—（披露要求）",
            "财务报告披露",
            "Sheet2 的来源列逐项标注报告期与页码；缺失项在备注说明。",
            "UT Code 附录；24EC64",
        ),
        (
            "版本与过渡",
            "2026年11月版 UT Code 于 2026-11-01 生效；现有 SFC 认可基金通常有过渡安排，"
            "正式合规判断应核对适用版本、过渡期与补充指引。",
            "截至 2026-11-03",
            "版本/过渡",
            "本表按 2026年11月版整理；不替代法律意见或 SFC 个案解释。",
            "SFC Codes（2026-11-01）；UT Code 2026年11月版",
        ),
    ]

    for idx, req in enumerate(reqs, start=5):
        for col, value in enumerate(req, start=1):
            cell = ws.cell(row=idx, column=col, value=value)
            style_cell(cell, size=9, align="left")
        ws.row_dimensions[idx].height = 58
    for idx in range(5, 9):
        for col in range(1, 7):
            ws.cell(row=idx, column=col).fill = fill(BLUE)
    for idx in range(9, 11):
        for col in range(1, 7):
            ws.cell(row=idx, column=col).fill = fill(GREEN)

    widths = [18, 55, 25, 22, 48, 42]
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    link_row = 5 + len(reqs) + 1
    ws.merge_cells(start_row=link_row, start_column=1, end_row=link_row, end_column=6)
    c = ws.cell(row=link_row, column=1, value="官方链接")
    c.font = Font(name="Aptos", size=9.5, bold=True, color=NAVY)
    c.fill = fill("DCE6F1")
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[link_row].height = 22

    links = [
        (
            "SFC Handbook for Unit Trusts and Mutual Funds（2026年11月版）",
            "https://www.sfc.hk/-/media/EN/assets/components/codes/files-current/web/codes/sfc-handbook-for-unit-trusts-and-mutual-funds/sfc-handbook-for-unit-trusts-and-mutual-funds.pdf",
        ),
        (
            "SFC 通函 24EC64：货币市场基金流动性风险管理（2024-12-17）",
            "https://apps.sfc.hk/edistributionWeb/api/circular/list-content/circular/doc?lang=EN&refNo=24EC64",
        ),
        (
            "SFC 通函 16EC29：SFC 认可基金流动性风险管理（2016-07-04）",
            "https://apps.sfc.hk/edistributionWeb/gateway/EN/circular/doc?refNo=16EC29",
        ),
        (
            "SFC Codes and guidelines 总入口",
            "https://www.sfc.hk/en/Rules-and-standards/Codes-and-guidelines/Codes",
        ),
    ]
    for offset, (label, url) in enumerate(links, start=1):
        row = link_row + offset
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        cell = ws.cell(row=row, column=1)
        add_hyperlink(cell, url, f"{label}：{url}")
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.row_dimensions[row].height = 26

    note_row = link_row + len(links) + 2
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=6)
    c = ws.cell(
        row=note_row,
        column=1,
        value=(
            "数据可用性说明：本次 Top15 中可取得的 WAM 披露值均 ≤60 天、WAL 披露值均 ≤120 天，"
            "但部分基金未披露 WAM/WAL 或日/周流动资产；Sheet2 不对缺失值做推断。"
            "若需逐基金合规结论，应以完整持仓、报告期和适用过渡条款重新计算。"
        ),
    )
    c.font = Font(name="Aptos", size=9, color=MID)
    c.fill = fill(LIGHT_BLUE)
    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[note_row].height = 44

    ws.freeze_panes = "A5"
    configure_print(
        ws,
        paper_size=ws.PAPERSIZE_A3,
        fit_width=1,
        title_rows="4:4",
        header_left="SFC 货币市场基金风险管理要求",
    )


def main():
    rows = build_rows()
    wb = Workbook()
    build_sheet1(wb, rows)
    build_sheet2_wide(wb, rows)
    build_sheet3(wb, rows)
    wb.properties.title = "香港中资货币基金 Top15 数据交付件"
    wb.properties.subject = "规模、收益、流动性、持仓与 SFC 监管要求"
    wb.properties.creator = "Case 01 rebuild"
    wb.properties.description = (
        "基于 fund_research 当前发布快照生成；规模排名为 2026-08-31 HKEX 快照，"
        "结构字段优先 2026-06-30。"
    )
    wb.properties.created = datetime.now()
    wb.save(OUT)

    audit = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot": DATA["snapshot"],
        "scale_date": SCALE_DATE,
        "data_source": "database_snapshot_only",
        "manual_overrides_used": False,
        "inference_used": False,
        "classification_rule": "fund_holdings.holding_type verbatim; blank when null",
        "rows": [
            {
                "rank": r["rank"],
                "code": r["code"],
                "name": r["name"],
                "currency": r["currency"],
                "scale": r["scale"],
                "return": r["return"],
                "daily": r["daily"],
                "daily_source": r["daily_source"],
                "weekly": r["weekly"],
                "weekly_source": r["weekly_source"],
                "inception": r["inception"],
                "inception_source": r["inception_source"],
                "holdings_source": r["holdings_source"],
                "holdings": [
                    {"name": i["name"], "weight": i["weight"], "issuer": i["issuer"], "asset": i["asset"]}
                    for i in r["items"]
                ],
                "equity_debt": r["equity_debt"],
                "equity_debt_source": r["equity_debt_source"],
                "equity_debt_basis": r["equity_debt_basis"],
                "notes": r["notes"],
            }
            for r in rows
        ],
    }
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=1))
    print(f"saved {OUT}")
    print(f"saved {AUDIT}")


if __name__ == "__main__":
    main()
