"""审计 HKEX 主数据与月报爬虫工作簿的覆盖缺口。

该脚本只读主数据、爬虫工作簿和本地适配器目录，不修改 Excel、数据库或来源记录。
由于历史爬虫工作簿没有统一基金代码，本版本先输出名称级可复核基线，后续再接入
fund_report_sources 做基金代码级对账。
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import unicodedata
from typing import Any

import openpyxl


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = Path(
    os.environ.get("FACTSHEET_STATE_ROOT") or PROJECT_ROOT.parent.parent / "fund-research-state"
).expanduser().resolve()
DEFAULT_MASTER = STATE_ROOT / "inputs" / "HKEX基金综合资料库_完整_20261008.xlsx"
DEFAULT_CRAWLER = STATE_ROOT / "inputs" / "爬虫list.xlsx"
DEFAULT_REPORT = PROJECT_ROOT / "docs" / "development" / "artifacts" / "monthly-coverage-baseline.json"
REQUIRED_CRAWLER_COLUMNS = {"基金", "月报链接", "运行方式"}
ADAPTER_MANAGER_LABELS = {
    "汇添富", "东方汇理", "贝莱德", "中银香港", "博时", "东亚联丰", "建银国际", "华夏", "中金",
    "招银国际", "招商", "南方东英", "大成", "易方达", "富达", "首域盈信", "睿远", "富国", "高腾",
    "广发国际", "海通", "国泰君安", "嘉实", "汇丰", "华泰", "摩根", "摩根大通", "东方资管", "柏瑞", "荷宝",
    "平安资管", "泰康", "太平", "瑞银", "惠理", "兴证", "安联", "安联环球",
    "农银国际", "农银汇理", "安本", "广发基金", "建银", "德意志资管",
    "民银资本", "永明", "申万宏源", "罗安贝", "英仕曼", "融通基金", "誉信", "兴证国际", "华富建业", "国信", "国投证券", "国泰", "国泰全球", "国海富兰克林", "小飞", "工银", "巴克莱", "弘收投资", "日兴资产", "日兴资管", "时富金融", "民银", "永丰金", "法盛", "简坤", "融通国际", "资威", "辉立资本", "银河", "长盛基金", "鹏格斯资本",
    "富兰克林邓普顿", "施罗德", "景顺", "联博资产", "中银保诚", "法巴", "宏利", "安本标准", "恒生", "普信", "晋达", "资本集团", "路博迈", "霸菱", "保诚瀚亚", "瑞士百达", "首源投资", "骏利亨德森", "纽约梅隆", "美富信", "高盛", "信安", "安盛", "品浩", "木星资管", "法盛资本", "威灵顿", "思博瑞资管", "摩根斯坦利", "工银资管", "未来", "野村", "Muzinich", "中信", "友邦", "英卓", "中国人寿富兰克林", "动量环球", "圣詹姆斯广场", "浦银国际", "华安", "工银瑞信", "三井住友", "中邮国际", "交银国际", "尚渤投资", "招商永隆", "凯基", "建信", "招证资管", "瑞士盈丰", "资产管理集团", "铭基环球", "中欧", "信达", "信银资本", "光大",
}


def _norm(value: Any) -> str:
    """规范化名称用于可复核的精确匹配；参数 value 为原始单元格值。"""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[\s\u3000\-_/·・,.，。()（）【】\[\]]+", "", text)
    return text


def _headers(row: tuple[Any, ...]) -> dict[str, int]:
    """构造非空表头索引；参数 row 为工作表首行。"""

    return {str(value).strip(): index for index, value in enumerate(row) if value is not None and str(value).strip()}


def _cell(row: tuple[Any, ...], index: int | None) -> str:
    """安全读取单元格文本；参数 row/index 为数据行和列索引。"""

    return str(row[index] or "").strip() if index is not None and index < len(row) else ""


def read_master(path: Path) -> list[dict[str, str]]:
    """读取 HKEX 主数据基金身份；参数 path 为主数据 xlsx。"""

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook["主数据"]
    iterator = sheet.iter_rows(values_only=True)
    header = _headers(next(iterator))
    rows: list[dict[str, str]] = []
    for row in iterator:
        code = _cell(row, header.get("基金代码"))
        if not code:
            continue
        rows.append(
            {
                "fund_code": code,
                "name_cn": _cell(row, header.get("基金名称")),
                "name_en": _cell(row, header.get("Fund name")),
                "manager": _cell(row, header.get("基金管理人")),
                "manager_short": _cell(row, header.get("管理人简称")),
                "category": _cell(row, header.get("资产类别一级分类")),
            }
        )
    workbook.close()
    return rows


def read_crawler_rows(path: Path) -> list[dict[str, Any]]:
    """读取所有可识别爬虫工作表；参数 path 为爬虫工作簿。"""

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows: list[dict[str, Any]] = []
    skipped_sheets: list[dict[str, Any]] = []
    for sheet in workbook.worksheets:
        iterator = sheet.iter_rows(values_only=True)
        try:
            header = _headers(next(iterator))
        except StopIteration:
            skipped_sheets.append({"sheet": sheet.title, "reason": "empty"})
            continue
        missing = sorted(REQUIRED_CRAWLER_COLUMNS - set(header))
        if missing:
            skipped_sheets.append({"sheet": sheet.title, "reason": "missing_columns", "columns": missing})
            continue
        for row_number, row in enumerate(iterator, start=2):
            fund_name = _cell(row, header.get("基金"))
            if not fund_name:
                continue
            rows.append(
                {
                    "sheet": sheet.title,
                    "row": row_number,
                    "fund_name": fund_name,
                    "fund_key": _norm(fund_name),
                    "manager": _cell(row, header.get("管理人")),
                    "monthly_url": _cell(row, header.get("月报链接")),
                    "run_command": _cell(row, header.get("运行方式")),
                }
            )
    workbook.close()
    return rows, skipped_sheets


def build_report(master_path: Path, crawler_path: Path) -> dict[str, Any]:
    """生成月报覆盖审计报告；参数为主数据和爬虫工作簿路径。"""

    master = read_master(master_path)
    crawler, skipped_sheets = read_crawler_rows(crawler_path)
    master_by_name: dict[str, list[dict[str, str]]] = {}
    for item in master:
        for value in (item["name_cn"], item["name_en"]):
            key = _norm(value)
            if key:
                master_by_name.setdefault(key, []).append(item)

    matched = 0
    ambiguous = 0
    unmatched: list[dict[str, Any]] = []
    for item in crawler:
        candidates = master_by_name.get(item["fund_key"], [])
        if len(candidates) == 1:
            matched += 1
        elif len(candidates) > 1:
            ambiguous += 1
        else:
            unmatched.append(item)

    adapter_paths = sorted((PROJECT_ROOT / "code" / "companies").glob("*/*_fund_link.py"))
    manager_counts = Counter(item["manager"] or "未填写" for item in crawler)
    manager_links = Counter(item["manager"] or "未填写" for item in crawler if item["monthly_url"])
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in crawler:
        key = (item["manager"] or "未填写", item["fund_key"])
        current = deduped.setdefault(
            key,
            {
                "manager": item["manager"] or "未填写",
                "fund_name": item["fund_name"],
                "fund_key": item["fund_key"],
                "monthly_url": False,
                "run_command": False,
                "sheets": set(),
            },
        )
        current["monthly_url"] = current["monthly_url"] or bool(item["monthly_url"])
        current["run_command"] = current["run_command"] or bool(item["run_command"])
        current["sheets"].add(item["sheet"])
    gap_by_manager: dict[str, dict[str, Any]] = {}
    for item in deduped.values():
        manager = item["manager"]
        gap = gap_by_manager.setdefault(
            manager,
            {
                "manager": manager,
                "has_adapter": manager in ADAPTER_MANAGER_LABELS,
                "fund_count": 0,
                "monthly_url_fund_count": 0,
                "missing_monthly_link_fund_count": 0,
                "missing_run_command_fund_count": 0,
                "sheets": set(),
            },
        )
        gap["fund_count"] += 1
        gap["monthly_url_fund_count"] += int(item["monthly_url"])
        gap["missing_monthly_link_fund_count"] += int(not item["monthly_url"])
        gap["missing_run_command_fund_count"] += int(not item["run_command"])
        gap["sheets"].update(item["sheets"])
    adapter_gaps = [
        {
            **item,
            "sheets": sorted(item["sheets"]),
        }
        for item in gap_by_manager.values()
        if not item["has_adapter"]
    ]
    adapter_gaps.sort(key=lambda item: (-item["missing_monthly_link_fund_count"], item["manager"]))
    report = {
        "schema_version": "monthly-coverage-baseline-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {"master_workbook": str(master_path), "crawler_workbook": str(crawler_path)},
        "master": {
            "fund_count": len(master),
            "manager_entity_count": len({item["manager"] for item in master if item["manager"]}),
            "manager_short_count": len({item["manager_short"] for item in master if item["manager_short"]}),
            "category_counts": dict(Counter(item["category"] or "未分类" for item in master)),
        },
        "crawler": {
            "row_count": len(crawler),
            "unique_fund_name_count": len({item["fund_key"] for item in crawler}),
            "manager_count": len({item["manager"] for item in crawler if item["manager"]}),
            "monthly_url_count": sum(bool(item["monthly_url"]) for item in crawler),
            "run_command_count": sum(bool(item["run_command"]) for item in crawler),
            "sheet_count": len({item["sheet"] for item in crawler}),
            "manager_counts": dict(manager_counts.most_common()),
            "manager_monthly_url_counts": dict(manager_links.most_common()),
            "deduplicated_fund_manager_rows": len(deduped),
        },
        "adapter_gaps": adapter_gaps,
        "name_match": {
            "unique_exact_match_rows": matched,
            "ambiguous_rows": ambiguous,
            "unmatched_rows": len(unmatched),
            "unmatched_sample": unmatched[:200],
            "warning": "名称级匹配仅用于审计基线，正式落库必须通过基金代码或人工确认。",
        },
        "adapters": {
            "fund_link_adapter_count": len(adapter_paths),
            "paths": [path.relative_to(PROJECT_ROOT).as_posix() for path in adapter_paths],
        },
        "skipped_sheets": skipped_sheets,
        "next_action": "建立基金代码级 source_coverage_audit，对齐 funds 与 fund_report_sources 后再执行批量补链。",
    }
    return report


def main() -> int:
    """执行审计并写出 JSON；参数通过命令行提供。"""

    parser = argparse.ArgumentParser(description="审计 HKEX 主数据和月报爬虫工作簿覆盖率")
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    parser.add_argument("--crawler", type=Path, default=DEFAULT_CRAWLER)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    for path in (args.master, args.crawler):
        if not path.is_file():
            raise SystemExit(f"输入文件不存在：{path}")
    report = build_report(args.master.resolve(), args.crawler.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "master_funds": report["master"]["fund_count"],
        "master_managers": report["master"]["manager_entity_count"],
        "crawler_rows": report["crawler"]["row_count"],
        "crawler_unique_funds": report["crawler"]["unique_fund_name_count"],
        "monthly_urls": report["crawler"]["monthly_url_count"],
        "name_exact_matches": report["name_match"]["unique_exact_match_rows"],
        "name_unmatched": report["name_match"]["unmatched_rows"],
        "adapters": report["adapters"]["fund_link_adapter_count"],
        "report": str(args.report),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
