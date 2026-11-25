#!/usr/bin/env python
"""Combine equity fund JSON files and write Excel-friendly sheets."""

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


MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "_structured_equity" / "per_pdf"
DEFAULT_OUTDIR = PROJECT_ROOT / "data" / "_structured_equity" / "combined"

SUMMARY_HEADERS = {
    "fund_id": "基金ID",
    "source_file": "来源文件",
    "fund_name": "基金名称",
    "report_date": "报告日期",
    "fund_size_hkd_100m": "规模_亿港币",
    "top_holdings_total_percent": "持仓合计比例_%",
    "dividend_share_class": "派息份额",
    "dividend_currency": "派息币种",
    "dividend_frequency": "派息频率",
    "dividend_per_unit": "每单位派息",
    "annualized_dividend_yield_percent": "年度化派息率_%",
}

EMPTY_SHEET_HEADERS = {
    "前十大持仓": ["fund_id", "fund_name", "rank", "ticker", "holding_name", "weight_percent"],
}

HOLDING_HEADERS = {
    "fund_id": "基金ID",
    "fund_name": "基金名称",
    "rank": "排名",
    "ticker": "Ticker",
    "holding_name": "持仓名称",
    "weight_percent": "持仓比例_%",
}


GEO_CATEGORY_MAP = {
    "usa": "美国",
    "unitedstates": "美国",
    "美国": "美国",
    "美国股票": "美国",
    "japan": "日本",
    "日本": "日本",
    "日本股票": "日本",
    "taiwan": "中国台湾",
    "台湾": "中国台湾",
    "中国台湾": "中国台湾",
    "香港": "中国香港",
    "中国香港": "中国香港",
    "香港股票": "中国香港",
    "china": "中国内地",
    "中国": "中国内地",
    "中国大陆": "中国内地",
    "中国内地": "中国内地",
    "内地": "中国内地",
    "中国在岸股票": "中国内地",
    "中国a股": "中国内地",
    "a股": "中国内地",
    "southkorea": "韩国",
    "korea": "韩国",
    "韩国": "韩国",
    "南韩": "韩国",
    "singapore": "新加坡",
    "新加坡": "新加坡",
    "uk": "英国",
    "unitedkingdom": "英国",
    "英国": "英国",
    "canada": "加拿大",
    "加拿大": "加拿大",
    "germany": "德国",
    "德国": "德国",
    "netherlands": "荷兰",
    "荷兰": "荷兰",
    "france": "法国",
    "法国": "法国",
    "brazil": "巴西",
    "巴西": "巴西",
    "australia": "澳洲",
    "澳大利亚": "澳洲",
    "澳洲": "澳洲",
    "india": "印度",
    "印度": "印度",
    "indonesia": "印度尼西亚",
    "印度尼西亚": "印度尼西亚",
    "印尼": "印度尼西亚",
    "thailand": "泰国",
    "泰国": "泰国",
    "malaysia": "马来西亚",
    "马来西亚": "马来西亚",
    "otherandcashequivalent": "现金及其他",
    "现金": "现金及其他",
    "现金及其他": "现金及其他",
    "现金及其它": "现金及其他",
    "现金及现金等值": "现金及其他",
    "其他现金及现金等值": "现金及其他",
    "其他、现金及现金等值": "现金及其他",
    "流动资金": "现金及其他",
    "净流动资产": "现金及其他",
    "净流动资金": "现金及其他",
    "其他国家": "其他",
    "其他": "其他",
    "北美洲": "北美",
    "北美": "北美",
    "美洲": "美洲",
    "欧洲(英国除外)": "欧洲",
    "欧洲（英国除外）": "欧洲",
    "其他-欧洲": "欧洲",
    "其他－欧洲": "欧洲",
    "其他欧洲": "欧洲",
    "欧洲股票": "欧洲",
    "亚洲(日本及中国香港除外)": "亚洲",
    "亚洲（日本及中国香港除外）": "亚洲",
    "其他-亚洲": "亚洲",
    "其他－亚洲": "亚洲",
    "其他亚洲": "亚洲",
    "亚太股票": "亚洲",
    "亚洲": "亚洲",
    "欧洲、中东和非洲": "欧洲、中东及非洲",
    "欧洲中东和非洲": "欧洲、中东及非洲",
}

SECTOR_CATEGORY_MAP = {
    "technology": "信息技术",
    "informationtechnology": "信息技术",
    "资讯科技": "信息技术",
    "信息技术": "信息技术",
    "科技": "信息技术",
    "communicationservices": "通讯服务",
    "telecommunicationservices": "通讯服务",
    "通讯服务": "通讯服务",
    "通信服务": "通讯服务",
    "电信服务": "通讯服务",
    "电讯": "通讯服务",
    "financials": "金融",
    "financialservices": "金融",
    "金融": "金融",
    "金融服务": "金融",
    "银行": "金融",
    "保险": "金融",
    "其他金融": "金融",
    "healthcare": "医疗保健",
    "医疗保健": "医疗保健",
    "健康护理": "医疗保健",
    "卫生保健": "医疗保健",
    "医疗": "医疗保健",
    "健康护理及药业": "医疗保健",
    "materials": "材料",
    "原材料": "材料",
    "材料": "材料",
    "物料": "材料",
    "基础材料": "材料",
    "基本物料": "材料",
    "realestate": "房地产",
    "房地产": "房地产",
    "地产": "房地产",
    "utilities": "公用事业",
    "公用事业": "公用事业",
    "公共事业": "公用事业",
    "公用": "公用事业",
    "consumerstaples": "必需消费品",
    "必需消费品": "必需消费品",
    "必需消费": "必需消费品",
    "日常消费品": "必需消费品",
    "主要消费品": "必需消费品",
    "消费者常用品": "必需消费品",
    "消费必需品": "必需消费品",
    "核心消费": "必需消费品",
    "consumerdiscretionary": "非必需消费品",
    "非必需消费品": "非必需消费品",
    "非必需消费": "非必需消费品",
    "可选消费品": "非必需消费品",
    "非日常生活消费品": "非必需消费品",
    "消费者非必需品": "非必需消费品",
    "非核心消费": "非必需消费品",
    "energy": "能源",
    "能源": "能源",
    "能源相关": "能源",
    "industrials": "工业",
    "工业": "工业",
    "现金": "现金及其他",
    "流动资金": "现金及其他",
    "现金及其他": "现金及其他",
    "现金及其它": "现金及其他",
    "现金及衍生产品": "现金及其他",
    "现金及衍生品": "现金及其他",
    "其他及现金": "现金及其他",
    "净流动资产": "现金及其他",
    "净流动资金": "现金及其他",
    "基金": "其他",
    "其他": "其他",
    "其他-交易所买卖基金": "其他",
    "其他－交易所买卖基金": "其他",
}

# 模型有时会同时保留图表的英文和中文标签，例如
# ``Consumer Discretionary 非必需消费品`` 或 ``US 美国``。矩阵层统一这些
# 展示差异；原始模型输出仍保留在逐 PDF JSON 中。
GEO_CATEGORY_MAP.update({
    "us": "美国",
    "美国us": "美国",
    "us美国": "美国",
    "cn": "中国内地",
    "mainlandchina": "中国内地",
    "mainlandchina中国内地": "中国内地",
    "hk": "中国香港",
    "hkchina": "中国香港",
    "hongkong": "中国香港",
    "hongkong香港": "中国香港",
    "japan日本": "日本",
    "cashandothers": "现金及其他",
    "cashandother": "现金及其他",
    "现金及现金等价物": "现金及其他",
    "现金及其它": "现金及其他",
    "其他及现金": "现金及其他",
    "其他和现金": "现金及其他",
    "h股": "中国H股",
    "美国上市股票": "美国上市",
    # 部分月报会把英文、中文和脚注一起作为同一个图表标签返回。
    "americasequities": "美国",
    "americasequities美国股票": "美国",
    "asiapacificequities": "亚洲",
    "asiapacificequities亚太股票": "亚洲",
    "cashothers": "现金及其他",
    "cashothers现金及其他": "现金及其他",
    "chinaonshoreequities": "中国内地",
    "chinaonshoreequities中国在岸股票": "中国内地",
    "emergingmarketsequities": "新兴市场",
    "emergingmarketsequities新兴市场股票": "新兴市场",
    "新兴市场股票": "新兴市场",
    "europeequities": "欧洲",
    "europeequities欧洲股票": "欧洲",
    "hongkongequities": "中国香港",
    "hongkongequities香港股票": "中国香港",
    "japanequities": "日本",
    "japanequities日本股票": "日本",
})

SECTOR_CATEGORY_MAP.update({
    "informationtechnology资讯技术": "信息技术",
    "informationtechnology信息技术": "信息技术",
    "technology科技": "信息技术",
    "资讯技术": "信息技术",
    "信息科技": "信息技术",
    "communicationservices通讯服务": "通讯服务",
    "telecommunications电讯": "通讯服务",
    "telecommunications电讯服务": "通讯服务",
    "通讯": "通讯服务",
    "电讯服务": "通讯服务",
    "financials金融": "金融",
    "healthcare医疗": "医疗保健",
    "healthcare健康护理": "医疗保健",
    "materials材料": "材料",
    "basicmaterials基本物料": "材料",
    "原料": "材料",
    "资源": "材料",
    "consumerstaples必需消费品": "必需消费品",
    "consumerstaples主要消费品": "必需消费品",
    "基本消费品": "必需消费品",
    "必需性消费": "必需消费品",
    "consumerdiscretionary非必需消费品": "非必需消费品",
    "consumerdiscretionary非必需消费": "非必需消费品",
    "可选消费": "非必需消费品",
    "非必需消费": "非必需消费品",
    "非必需性消费": "非必需消费品",
    "非日常消费": "非必需消费品",
    "非日常消费品": "非必需消费品",
    "非必须消费": "非必需消费品",
    "非必须消费品": "非必需消费品",
    "energy能源": "能源",
    "industrials工业": "工业",
    "realestate房地产": "房地产",
    "utilities公用事业": "公用事业",
    "others其他": "其他",
    "etfs": "其他",
    "交易所买卖基金": "其他",
    "现金及现金等价物": "现金及其他",
    "cashandothers": "现金及其他",
    "cashandother": "现金及其他",
    "cashothers": "现金及其他",
    "cashothers现金及其他": "现金及其他",
    "cashothers现金及其它": "现金及其他",
    "医药": "医疗保健",
})


def _category_key(value: Any) -> str:
    # 去除语言标签之间的空格、逗号、星号和脚注符号，使同义的双语标题
    # 生成同一个稳定键；字母、数字和中日韩文字本身均保留。
    return re.sub(r"[^\w\u3400-\u9fff]+", "", str(value or "").strip(), flags=re.UNICODE).replace("_", "").lower()


def _canonical_category(value: Any, mapping: dict[str, str]) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    key = _category_key(text)
    direct = mapping.get(key)
    if direct is not None:
        return direct
    # 历史映射表中有些别名自身带逗号、连字符或脚注。键和值两边使用
    # 同一标准化规则，避免 ``Hong Kong, China`` 一类标签漏配。
    normalized_mapping = {_category_key(alias): canonical for alias, canonical in mapping.items()}
    return normalized_mapping.get(key, text)


def _to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def iter_json_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(file for file in path.glob("*.json") if not file.name.endswith(".meta.json")))
        elif path.is_file() and path.suffix.lower() == ".json" and not path.name.endswith(".meta.json"):
            files.append(path)
    return files


def load_records(files: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for file in files:
        data = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            data = data["data"]
        data["_source_json"] = str(file)
        records.append(data)
    return records


def make_fund_id(source_file: str, fund_name: str, index: int) -> str:
    stem = Path(source_file).stem.replace(".equity_fund", "")
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in fund_name).strip("_")[:80] or f"fund_{index}"
    return f"{stem}__{index:03d}__{safe_name}"


def flatten(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    summary_rows: list[dict[str, Any]] = []
    holding_rows: list[dict[str, Any]] = []
    geography_rows: list[dict[str, Any]] = []
    sector_rows: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        source_file = str(record.get("_source_json") or "")
        fund_name = str(record.get("fund_name") or "")
        fund_id = make_fund_id(source_file, fund_name, index)
        dividend = record.get("dividend") if isinstance(record.get("dividend"), dict) else {}
        summary_rows.append({
            "fund_id": fund_id,
            "source_file": source_file,
            "fund_name": fund_name,
            "report_date": record.get("report_date"),
            "fund_size_hkd_100m": record.get("fund_size_hkd_100m"),
            "top_holdings_total_percent": record.get("top_holdings_total_percent"),
            "dividend_share_class": dividend.get("share_class"),
            "dividend_currency": dividend.get("currency"),
            "dividend_frequency": dividend.get("frequency"),
            "dividend_per_unit": dividend.get("dividend_per_unit"),
            "annualized_dividend_yield_percent": dividend.get("annualized_dividend_yield_percent"),
        })
        for rank, item in enumerate(record.get("top_holdings") or [], start=1):
            if not isinstance(item, dict):
                continue
            holding_rows.append({
                "fund_id": fund_id,
                "fund_name": fund_name,
                "rank": rank,
                "ticker": item.get("ticker"),
                "holding_name": item.get("name"),
                "weight_percent": item.get("weight_percent"),
            })
        for item in record.get("geographic_allocation") or []:
            if isinstance(item, dict):
                raw_region = item.get("region")
                region = _canonical_category(raw_region, GEO_CATEGORY_MAP)
                geography_rows.append({
                    "fund_id": fund_id,
                    "fund_name": fund_name,
                    "region": region,
                    "region_original": raw_region if raw_region != region else None,
                    "weight_percent": item.get("weight_percent"),
                })
        for item in record.get("sector_allocation") or []:
            if isinstance(item, dict):
                raw_sector = item.get("sector")
                sector = _canonical_category(raw_sector, SECTOR_CATEGORY_MAP)
                sector_rows.append({
                    "fund_id": fund_id,
                    "fund_name": fund_name,
                    "sector": sector,
                    "sector_original": raw_sector if raw_sector != sector else None,
                    "weight_percent": item.get("weight_percent"),
                })
    return summary_rows, holding_rows, geography_rows, sector_rows


def write_sheet(workbook: openpyxl.Workbook, title: str, rows: list[dict[str, Any]], header_labels: dict[str, str] | None = None) -> None:
    sheet = workbook.create_sheet(title)
    if not rows:
        headers = list((header_labels or {}).keys()) or EMPTY_SHEET_HEADERS.get(title, [])
        sheet.append([(header_labels or {}).get(header, header) for header in headers])
        for column_cells in sheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 10), 60)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        return
    headers = list(rows[0].keys())
    sheet.append([(header_labels or {}).get(header, header) for header in headers])
    for row in rows:
        sheet.append([row.get(header) for header in headers])
    for column_cells in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 10), 60)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def _matrix_columns(rows: list[dict[str, Any]], category_key: str) -> list[str]:
    counts = Counter(str(row.get(category_key) or "").strip() for row in rows if row.get(category_key))
    return sorted(counts, key=lambda value: (-counts[value], value))


def _write_matrix_sheet(
    workbook: openpyxl.Workbook,
    title: str,
    summary_rows: list[dict[str, Any]],
    allocation_rows: list[dict[str, Any]],
    category_key: str,
) -> None:
    sheet = workbook.create_sheet(title)
    categories = _matrix_columns(allocation_rows, category_key)
    headers = ["基金ID", "基金名称", "报告日期", *categories, "合计_%"]
    sheet.append(headers)
    by_fund: dict[str, dict[str, Any]] = {}
    for row in allocation_rows:
        fund_id = str(row.get("fund_id") or "")
        category = str(row.get(category_key) or "").strip()
        if not fund_id or not category:
            continue
        value = _to_float(row.get("weight_percent"))
        if value is None:
            continue
        current = by_fund.setdefault(fund_id, {})
        current[category] = round(float(current.get(category) or 0) + value, 4)
    dates = {str(row.get("fund_id") or ""): row.get("report_date") for row in summary_rows}
    names = {str(row.get("fund_id") or ""): row.get("fund_name") for row in summary_rows}
    for summary in summary_rows:
        fund_id = str(summary.get("fund_id") or "")
        values = by_fund.get(fund_id, {})
        total = sum(float(value) for value in values.values() if isinstance(value, (int, float)))
        sheet.append([
            fund_id,
            names.get(fund_id),
            dates.get(fund_id),
            *[values.get(category) for category in categories],
            round(total, 4) if values else None,
        ])
    for column_cells in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 10), 32)
    sheet.freeze_panes = "D2"
    sheet.auto_filter.ref = sheet.dimensions


def write_csv(path: Path, rows: list[dict[str, Any]], header_labels: dict[str, str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    headers = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([(header_labels or {}).get(header, header) for header in headers])
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
    output_stem: str = "equity_fund",
    excel_outdir: Path | None = None,
) -> dict[str, str]:
    outdir.mkdir(parents=True, exist_ok=True)
    excel_outdir = Path(excel_outdir) if excel_outdir else outdir
    excel_outdir.mkdir(parents=True, exist_ok=True)
    summary_rows, holding_rows, geography_rows, sector_rows = flatten(records)
    combined_json = outdir / f"{output_stem}_combined.json"
    combined_json.write_text(
        json.dumps(
            {
                "records": records,
                "funds": summary_rows,
                "top_holdings": holding_rows,
                "geographic_allocation": geography_rows,
                "sector_allocation": sector_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    write_sheet(workbook, "基金汇总", summary_rows, SUMMARY_HEADERS)
    write_sheet(workbook, "前十大持仓", holding_rows, HOLDING_HEADERS)
    _write_matrix_sheet(workbook, "地区矩阵", summary_rows, geography_rows, "region")
    _write_matrix_sheet(workbook, "行业矩阵", summary_rows, sector_rows, "sector")
    legacy_names = output_stem == "equity_fund"
    xlsx_name = "equity_fund_excel.xlsx" if legacy_names else f"{output_stem}.xlsx"
    saved_xlsx_path, warning = save_workbook_with_fallback(workbook, excel_outdir / xlsx_name)

    csv_prefix = "" if legacy_names else f"{output_stem}_"
    write_csv(outdir / f"{csv_prefix}fund_summary.csv", summary_rows, SUMMARY_HEADERS)
    write_csv(outdir / f"{csv_prefix}top_holdings.csv", holding_rows)
    write_csv(outdir / f"{csv_prefix}geographic_allocation.csv", geography_rows)
    write_csv(outdir / f"{csv_prefix}sector_allocation.csv", sector_rows)
    outputs = {
        "combined_json": str(combined_json),
        "excel": str(saved_xlsx_path),
        "fund_count": str(len(summary_rows)),
        "holding_count": str(len(holding_rows)),
        "geography_count": str(len(geography_rows)),
        "sector_count": str(len(sector_rows)),
    }
    if warning:
        outputs["warning"] = warning
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Combine equity fund extraction JSON files into JSON/CSV/XLSX outputs.")
    parser.add_argument("inputs", nargs="*", type=Path, default=[DEFAULT_INPUT])
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--excel-outdir", type=Path)
    args = parser.parse_args()

    outputs = write_outputs(
        load_records(iter_json_files(args.inputs)),
        args.outdir,
        excel_outdir=args.excel_outdir,
    )
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
