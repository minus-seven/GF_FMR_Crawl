#!/usr/bin/env python
"""Combine simplified fixed-income JSON files and write Excel-friendly sheets."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill

try:
    from structured_data.combine_equity_fund import GEO_CATEGORY_MAP, _canonical_category
except ImportError:  # pragma: no cover
    from combine_equity_fund import GEO_CATEGORY_MAP, _canonical_category


MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "_structured" / "per_pdf"
DEFAULT_OUTDIR = PROJECT_ROOT / "data" / "_structured" / "combined"

HEADER_LABELS = {
    "fund_id": "基金ID",
    "source_file": "来源文件",
    "fund_name": "基金名称",
    "report_date": "报告日期",
    "fund_size_hkd_100m": "规模_亿港币",
    "portfolio_duration_years": "组合久期_年",
    "portfolio_ytm_percent": "组合到期收益率_%",
    "credit_rating": "信用评级",
    "top_holdings_total_percent": "持仓合计比例_%",
    "dividend_share_class": "派息份额",
    "dividend_currency": "派息币种",
    "dividend_frequency": "派息频率",
    "dividend_per_unit": "每单位派息",
    "annualized_dividend_yield_percent": "年度化派息率_%",
    "dividend_note": "派息备注",
    "seven_day_yield_percent": "七日年化收益率_%",
    "annualized_yield_percent": "年化收益率_%",
    "weighted_average_maturity_days": "加权平均到期期限_天",
    "weighted_average_life_days": "加权平均存续期_天",
    "daily_liquid_assets_percent": "每日流动资产_%",
    "weekly_liquid_assets_percent": "每周流动资产_%",
    "investment_objective": "投资目标",
    "investment_strategy": "投资策略",
    "portfolio_ytm_note": "YTM数据说明",
}

HOLDING_HEADERS = {
    "fund_id": "基金ID",
    "fund_name": "基金名称",
    "rank": "排名",
    "holding_name": "持仓名称",
    "weight_percent": "持仓比例_%",
}

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True)
UNDISCLOSED_FILL = PatternFill("solid", fgColor="FFF2CC")

FIXED_INCOME_GEO_MAP = {
    **GEO_CATEGORY_MAP,
    "australia": "澳洲",
    "cash": "现金及其他",
    "cash&others": "现金及其他",
    "china": "中国内地",
    "france": "法国",
    "germany": "德国",
    "hongkong": "中国香港",
    "hongkong,china": "中国香港",
    "india": "印度",
    "indonesia": "印度尼西亚",
    "ireland": "爱尔兰",
    "italy": "意大利",
    "japan": "日本",
    "otherscountryregion": "其他",
    "qatar": "卡塔尔",
    "saudiarabia": "沙特阿拉伯",
    "southkorea": "韩国",
    "spain": "西班牙",
    "switzerland": "瑞士",
    "taiwan,china": "中国台湾",
    "thailand": "泰国",
    "uk": "英国",
    "unitedkingdom": "英国",
    "unitedstates": "美国",
    "usa": "美国",
    "中国": "中国内地",
    "香港": "中国香港",
    "香港特别行政区": "中国香港",
    "其他地区": "其他",
    "现金": "现金及其他",
    "现金及其它": "现金及其他",
    "流动资金": "现金及其他",
    "南韩": "韩国",
    "印尼": "印度尼西亚",
    "澳大利亚": "澳洲",
    "沙乌地阿拉伯": "沙特阿拉伯",
}


def iter_json_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(file for file in path.glob("*.json") if not file.name.endswith(".meta.json")))
        elif path.is_file() and not path.name.endswith(".meta.json"):
            files.append(path)
    return files


def load_records(files: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for file in files:
        data = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            data = data["data"]
        holding_items = data.get("top_holdings") or []
        holding_total = sum(
            value
            for value in (_to_float(item.get("weight_percent")) for item in holding_items if isinstance(item, dict))
            if value is not None
        )
        data["top_holdings_total_percent"] = round(holding_total, 4) if holding_items else None
        data["_source_json"] = str(file)
        records.append(data)
    return records


def make_fund_id(source_file: str, fund_name: str, index: int) -> str:
    stem = Path(source_file).stem.replace(".fixed_income", "").replace(".money_market", "")
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in fund_name).strip("_")[:80] or f"fund_{index}"
    return f"{stem}__{index:03d}__{safe_name}"


def _to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _canonical_duration_bucket(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    summary_normalized = re.sub(r"[\s()（）:_\-]+", "", text.lower())
    duration_terms = ("久期", "存续期", "到期年限", "到期期限", "duration", "maturity")
    summary_terms = ("平均", "加权平均", "组合", "average", "weightedaverage", "portfolio")
    if (
        any(duration in summary_normalized for duration in duration_terms)
        and any(summary in summary_normalized for summary in summary_terms)
    ) or summary_normalized in {"durationyears", "maturityyears", "久期years", "到期年限years"}:
        return None
    normalized = (
        text.lower()
        .replace("years", "年")
        .replace("year", "年")
        .replace("yrs", "年")
        .replace("yr", "年")
        .replace("个月", "月")
        .replace("－", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("至", "-")
        .replace("～<", "-")
        .replace("~<", "-")
        .replace("～", "-")
        .replace("~", "-")
        .replace(" ", "")
    )
    if any(token in normalized for token in ["现金", "cash", "流动资产", "流动资金"]):
        return "现金及其他"
    if re.fullmatch(r"(?:10\+年|10年\+|(?:>|>=|超过)10年|10年(?:以上|以外))", normalized):
        return "10年以上"
    if re.fullmatch(r"(?:7\+年|7年\+|(?:>|>=|超过)7年|7年(?:以上|以外))", normalized):
        return "7年以上"
    if re.fullmatch(r"(?:<|<=|少于)?1年(?:以下|以内)?|0-1年", normalized):
        return "1年以下"
    if re.fullmatch(r"1-2年", normalized):
        return "1-2年"
    if re.fullmatch(r"2-3年", normalized):
        return "2-3年"
    if re.search(r"1-3年", normalized):
        return "1-3年"
    if re.search(r"3-5年", normalized):
        return "3-5年"
    if re.search(r"5-7年", normalized):
        return "5-7年"
    if re.search(r"7-10年", normalized):
        return "7-10年"
    return text


def flatten(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    summary_rows: list[dict[str, Any]] = []
    holding_rows: list[dict[str, Any]] = []
    duration_rows: list[dict[str, Any]] = []
    geography_rows: list[dict[str, Any]] = []
    money_asset_rows: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        dividend = record.get("dividend") if isinstance(record.get("dividend"), dict) else {}
        money = record.get("money_market_metrics") if isinstance(record.get("money_market_metrics"), dict) else {}
        source_file = str(record.get("_source_json") or "")
        fund_name = str(record.get("fund_name") or "")
        fund_id = make_fund_id(source_file, fund_name, index)
        summary_rows.append({
            "fund_id": fund_id,
            "source_file": source_file,
            "fund_name": fund_name,
            "report_date": record.get("report_date"),
            "listing_date": record.get("listing_date"),
            "fund_size_hkd_100m": record.get("fund_size_hkd_100m"),
            "portfolio_duration_years": record.get("portfolio_duration_years"),
            "portfolio_ytm_percent": record.get("portfolio_ytm_percent"),
            "credit_rating": record.get("credit_rating"),
            "top_holdings_total_percent": record.get("top_holdings_total_percent"),
            "dividend_share_class": dividend.get("share_class"),
            "dividend_currency": dividend.get("currency"),
            "dividend_frequency": dividend.get("frequency"),
            "dividend_per_unit": dividend.get("dividend_per_unit"),
            "annualized_dividend_yield_percent": dividend.get("annualized_dividend_yield_percent"),
            "dividend_note": dividend.get("note"),
            "seven_day_yield_percent": money.get("seven_day_yield_percent"),
            "annualized_yield_percent": money.get("annualized_yield_percent"),
            "weighted_average_maturity_days": money.get("weighted_average_maturity_days"),
            "weighted_average_life_days": money.get("weighted_average_life_days"),
            "daily_liquid_assets_percent": money.get("daily_liquid_assets_percent"),
            "weekly_liquid_assets_percent": money.get("weekly_liquid_assets_percent"),
            "investment_objective": record.get("investment_objective"),
            "investment_strategy": record.get("investment_strategy"),
            "portfolio_ytm_note": record.get("portfolio_ytm_note"),
        })
        for rank, item in enumerate(record.get("top_holdings") or [], start=1):
            if not isinstance(item, dict):
                continue
            holding_rows.append({
                "fund_id": fund_id,
                "fund_name": fund_name,
                "rank": rank,
                "holding_name": item.get("name"),
                "weight_percent": item.get("weight_percent"),
            })
        for item in record.get("duration_distribution") or []:
            if not isinstance(item, dict):
                continue
            raw_bucket = item.get("bucket")
            bucket = _canonical_duration_bucket(raw_bucket)
            if not bucket:
                continue
            duration_rows.append({
                "fund_id": fund_id,
                "fund_name": fund_name,
                "bucket": bucket,
                "bucket_original": raw_bucket if raw_bucket != bucket else None,
                "weight_percent": item.get("value") if item.get("value") is not None else item.get("weight_percent"),
                "measure_type": item.get("measure_type"),
                "unit": item.get("unit"),
            })
        for item in record.get("geographic_allocation") or []:
            if not isinstance(item, dict):
                continue
            raw_region = item.get("region")
            region = _canonical_category(raw_region, FIXED_INCOME_GEO_MAP)
            geography_rows.append({
                "fund_id": fund_id,
                "fund_name": fund_name,
                "region": region,
                "region_original": raw_region if raw_region != region else None,
                "weight_percent": item.get("weight_percent"),
            })
        for item in money.get("asset_type_allocation") or []:
            if not isinstance(item, dict):
                continue
            money_asset_rows.append({
                "fund_id": fund_id,
                "fund_name": fund_name,
                "asset_type": item.get("type"),
                "weight_percent": item.get("weight_percent"),
            })
    return summary_rows, holding_rows, duration_rows, geography_rows, money_asset_rows


def _duration_rows_for_excel(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Omit clearly incomplete fund-level duration distributions from Excel."""
    totals: dict[str, float] = {}
    for row in rows:
        fund_id = str(row.get("fund_id") or "")
        value = _to_float(row.get("weight_percent"))
        if fund_id and value is not None:
            totals[fund_id] = totals.get(fund_id, 0.0) + value
    eligible_funds = {fund_id for fund_id, total in totals.items() if total >= 20}
    return [row for row in rows if str(row.get("fund_id") or "") in eligible_funds]


def write_sheet(
    workbook: openpyxl.Workbook,
    title: str,
    rows: list[dict[str, Any]],
    header_labels: dict[str, str] | None = None,
) -> None:
    sheet = workbook.create_sheet(title)
    if not rows:
        headers = list((header_labels or {}).keys())
        sheet.append([(header_labels or {}).get(header, header) for header in headers] or ["空"])
        return
    headers = list(rows[0].keys())
    sheet.append([(header_labels or {}).get(header, header) for header in headers])
    for row in rows:
        sheet.append([row.get(header) for header in headers])
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    for column_cells in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 10), 60)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def _matrix_columns(rows: list[dict[str, Any]], category_key: str) -> list[str]:
    counts = Counter(str(row.get(category_key) or "").strip() for row in rows if row.get(category_key))
    if category_key == "bucket":
        preferred_order = {
            "1年以下": 10,
            "1-2年": 20,
            "1-3年": 25,
            "2-3年": 30,
            "3-5年": 40,
            "5-7年": 50,
            "7-10年": 60,
            "7年以上": 70,
            "10年以上": 80,
            "现金及其他": 1000,
        }
        return sorted(
            counts,
            key=lambda value: (
                preferred_order.get(value, 900),
                -counts[value],
                value,
            ),
        )
    return sorted(counts, key=lambda value: (-counts[value], value))


def _write_matrix_sheet(
    workbook: openpyxl.Workbook,
    title: str,
    summary_rows: list[dict[str, Any]],
    allocation_rows: list[dict[str, Any]],
    category_key: str,
    *,
    include_measure: bool = False,
) -> None:
    sheet = workbook.create_sheet(title)
    categories = _matrix_columns(allocation_rows, category_key)
    headers = ["基金ID", "基金名称", "报告日期"]
    if include_measure:
        headers.extend(["口径", "单位"])
    headers.extend([*categories, "合计"])
    sheet.append(headers)
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    sheet["A1"].comment = Comment("浅黄色空白表示该基金未披露对应项目。", "Codex")

    by_fund: dict[str, dict[str, float]] = {}
    measure_by_fund: dict[str, tuple[Any, Any]] = {}
    for row in allocation_rows:
        fund_id = str(row.get("fund_id") or "")
        category = str(row.get(category_key) or "").strip()
        value = _to_float(row.get("weight_percent"))
        if not fund_id or not category or value is None:
            continue
        current = by_fund.setdefault(fund_id, {})
        current[category] = round(float(current.get(category) or 0) + value, 4)
        if include_measure:
            measure_by_fund[fund_id] = (row.get("measure_type"), row.get("unit"))

    for summary in summary_rows:
        fund_id = str(summary.get("fund_id") or "")
        values = by_fund.get(fund_id, {})
        total = sum(values.values())
        prefix = [
            fund_id,
            summary.get("fund_name"),
            summary.get("report_date"),
        ]
        if include_measure:
            prefix.extend(measure_by_fund.get(fund_id, (None, None)))
        sheet.append(prefix + [
            *[values.get(category) for category in categories],
            round(total, 4) if values else None,
        ])
        row_number = sheet.max_row
        first_value_column = 6 if include_measure else 4
        for column_number in range(first_value_column, first_value_column + len(categories)):
            cell = sheet.cell(row=row_number, column=column_number)
            cell.number_format = "0.00"
            if cell.value is None:
                cell.fill = UNDISCLOSED_FILL
        total_cell = sheet.cell(row=row_number, column=first_value_column + len(categories))
        total_cell.number_format = "0.00"
        if not values:
            total_cell.fill = UNDISCLOSED_FILL

    for column_cells in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 10), 32)
    sheet.freeze_panes = "F2" if include_measure else "D2"
    sheet.auto_filter.ref = sheet.dimensions


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        headers = list(rows[0].keys())
        writer = csv.writer(handle)
        writer.writerow([HEADER_LABELS.get(header, header) for header in headers])
        for row in rows:
            writer.writerow([row.get(header) for header in headers])


def save_workbook_with_fallback(workbook: openpyxl.Workbook, path: Path) -> tuple[Path, str]:
    temporary = path.with_name(f".{path.stem}.writing{path.suffix}")
    try:
        workbook.save(temporary)
        temporary.replace(path)
        return path, ""
    except PermissionError:
        temporary.unlink(missing_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        fallback = path.with_name(f"{path.stem}_{timestamp}{path.suffix}")
        workbook.save(fallback)
        return fallback, f"{path.name} 被占用，已另存为 {fallback.name}"
    finally:
        temporary.unlink(missing_ok=True)


def write_outputs(
    records: list[dict[str, Any]],
    outdir: Path,
    *,
    output_stem: str = "fixed_income",
    excel_outdir: Path | None = None,
) -> dict[str, str]:
    outdir.mkdir(parents=True, exist_ok=True)
    excel_outdir = Path(excel_outdir) if excel_outdir else outdir
    excel_outdir.mkdir(parents=True, exist_ok=True)
    summary_rows, holding_rows, duration_rows, geography_rows, money_asset_rows = flatten(records)
    combined_json = outdir / f"{output_stem}_combined.json"
    combined_json.write_text(
        json.dumps(
            {
                "records": records,
                "funds": summary_rows,
                "top_holdings": holding_rows,
                "duration_distribution": duration_rows,
                "geographic_allocation": geography_rows,
                "money_asset_allocation": money_asset_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    write_sheet(workbook, "基金汇总", summary_rows, HEADER_LABELS)
    write_sheet(workbook, "前十大持仓", holding_rows, HOLDING_HEADERS)
    write_sheet(workbook, "货币资产类型", money_asset_rows, {
        "fund_id": "基金ID",
        "fund_name": "基金名称",
        "asset_type": "资产类型",
        "weight_percent": "比例_%",
    })
    _write_matrix_sheet(
        workbook,
        "久期分布矩阵",
        summary_rows,
        _duration_rows_for_excel(duration_rows),
        "bucket",
        include_measure=True,
    )
    _write_matrix_sheet(workbook, "地区矩阵", summary_rows, geography_rows, "region")
    xlsx_path = excel_outdir / ("fixed_income_excel.xlsx" if output_stem == "fixed_income" else f"{output_stem}.xlsx")
    saved_xlsx_path, warning = save_workbook_with_fallback(workbook, xlsx_path)

    csv_name = "fund_summary.csv" if output_stem == "fixed_income" else f"{output_stem}_fund_summary.csv"
    write_csv(outdir / csv_name, summary_rows)
    csv_prefix = "" if output_stem == "fixed_income" else f"{output_stem}_"
    write_csv(outdir / f"{csv_prefix}top_holdings.csv", holding_rows)
    write_csv(outdir / f"{csv_prefix}duration_distribution.csv", duration_rows)
    write_csv(outdir / f"{csv_prefix}geographic_allocation.csv", geography_rows)
    write_csv(outdir / f"{csv_prefix}money_asset_allocation.csv", money_asset_rows)
    outputs = {
        "combined_json": str(combined_json),
        "excel": str(saved_xlsx_path),
        "fund_count": str(len(summary_rows)),
        "source_count": str(len(records)),
    }
    if warning:
        outputs["warning"] = warning
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Combine fixed-income extraction JSON files into JSON/CSV/XLSX outputs.")
    parser.add_argument("inputs", nargs="*", type=Path, default=[DEFAULT_INPUT])
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--output-stem", default="fixed_income")
    parser.add_argument("--excel-outdir", type=Path)
    args = parser.parse_args()

    files = iter_json_files(args.inputs)
    records = load_records(files)
    outputs = write_outputs(
        records,
        args.outdir,
        output_stem=args.output_stem,
        excel_outdir=args.excel_outdir,
    )
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
