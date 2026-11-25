#!/usr/bin/env python3
"""从月报覆盖审计 JSON 生成管理人缺口表。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import re
import unicodedata

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/development/artifacts/monthly-coverage-baseline.json"
OUTPUT = ROOT / "docs/development/artifacts/monthly-gap-by-manager-20261125.md"
CRAWLER = ROOT / "爬虫list.xlsx"


def _norm(value: object) -> str:
    """规范化基金名；参数 value 为工作簿单元格文本。"""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\u3000\-_/·・,.，。()（）【】\[\]]+", "", text)


def _read_dedup() -> tuple[dict[tuple[str, str], dict[str, object]], int]:
    """读取正式工作簿并按管理人/基金名去重；无参数。"""

    book = openpyxl.load_workbook(CRAWLER, read_only=True, data_only=True)
    dedup: dict[tuple[str, str], dict[str, object]] = {}
    raw_links = 0
    for sheet in book.worksheets:
        rows = sheet.iter_rows(values_only=True)
        try:
            header = [str(x or "").strip() for x in next(rows)]
        except StopIteration:
            continue
        index = {name: pos for pos, name in enumerate(header) if name}
        if not {"基金", "管理人", "月报链接"}.issubset(index):
            continue
        for row in rows:
            fund = str(row[index["基金"]] or "").strip() if index["基金"] < len(row) else ""
            manager = str(row[index["管理人"]] or "").strip() if index["管理人"] < len(row) else "未填写"
            if not fund:
                continue
            link = str(row[index["月报链接"]] or "").strip() if index["月报链接"] < len(row) else ""
            raw_links += bool(link)
            key = (manager or "未填写", _norm(fund))
            current = dedup.setdefault(key, {"manager": manager or "未填写", "fund_count": 0, "covered": False})
            current["fund_count"] = 1
            current["covered"] = bool(current["covered"] or bool(link))
    book.close()
    return dedup, raw_links


def main() -> None:
    """写出按缺口降序排列的管理人缺口报告。"""

    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    dedup, raw_links = _read_dedup()
    # 与覆盖审计保持同一份“已接入适配器”口径，避免适配器已存在但
    # 报表误标为“否”（尤其是本轮新增的广发国际、华泰及此前接入的
    # 博时/华夏/东方汇理等）。缺口数仍完全按工作簿内容计算。
    adapter_labels = {
        "汇添富", "东方汇理", "贝莱德", "中银香港", "博时", "东亚联丰", "建银国际", "华夏", "中金",
        "招银国际", "招商", "南方东英", "大成", "易方达", "富达", "首域盈信", "睿远", "富国", "高腾",
        "广发国际", "海通", "国泰君安", "嘉实", "汇丰", "华泰", "摩根", "摩根大通", "东方资管", "柏瑞",
        "平安资管", "泰康", "太平", "瑞银", "惠理", "兴证", "兴证国际", "荷宝", "安联环球", "农银国际", "农银汇理", "安本", "广发基金", "建银", "德意志资管", "民银资本", "永明", "申万宏源", "罗安贝", "英仕曼", "融通基金", "誉信", "华富建业", "国信", "国投证券", "国泰", "国泰全球", "国海富兰克林", "小飞", "工银", "巴克莱", "弘收投资", "日兴资产", "日兴资管", "时富金融", "民银", "永丰金", "法盛", "简坤", "融通国际", "资威", "辉立资本", "银河", "长盛基金", "鹏格斯资本", "富兰克林邓普顿", "施罗德",
        "景顺", "联博资产", "中银保诚", "法巴", "宏利", "安本标准", "恒生", "普信", "晋达", "资本集团",
        "路博迈", "霸菱", "保诚瀚亚", "瑞士百达", "首源投资", "骏利亨德森", "纽约梅隆", "美富信", "高盛",
        "信安", "安盛", "品浩", "木星资管", "法盛资本", "威灵顿", "安联", "思博瑞资管", "摩根斯坦利", "工银资管", "未来", "野村", "Muzinich", "中信", "友邦", "英卓", "中国人寿富兰克林", "动量环球", "圣詹姆斯广场", "浦银国际", "华安", "工银瑞信", "三井住友", "中邮国际", "交银国际", "尚渤投资", "招商永隆", "凯基", "建信", "招证资管", "瑞士盈丰", "资产管理集团", "铭基环球", "中欧", "信达", "信银资本", "光大",
    }
    grouped: dict[str, dict[str, object]] = {}
    for item in dedup.values():
        manager = str(item["manager"])
        group = grouped.setdefault(manager, {"fund_count": 0, "covered": 0})
        group["fund_count"] = int(group["fund_count"]) + 1
        group["covered"] = int(group["covered"]) + int(bool(item["covered"]))
    rows = []
    for manager, item in grouped.items():
        total_count = int(item["fund_count"])
        covered_count = int(item["covered"])
        rows.append({"manager": manager, "fund_count": total_count, "covered": covered_count, "gap": total_count-covered_count, "has_adapter": manager in adapter_labels})
    rows.sort(key=lambda x: (-int(x["gap"]), str(x["manager"])))
    total = sum(int(x["fund_count"]) for x in rows)
    missing = sum(int(x["gap"]) for x in rows)
    covered = total - missing
    raw = raw_links
    generated = datetime.now().strftime("%Y-%m-%d")
    lines = [
        "# 月报链接缺口按管理人统计",
        "",
        f"生成时间：{generated}（第六批 31 家管理人适配器接入并回填；仅将身份校验通过的链接落盘）",
        "",
        "口径：对 `爬虫list.xlsx` 按（管理人、规范化基金名称）去重；该组合在所有页表均无“月报链接”时计为缺口。",
        "",
        f"- 缺口组合：{missing}",
        f"- 去重组合总数：{total}",
        f"- 有月报入口组合：{covered}",
        f"- 原始页表链接记录：{raw}",
        "",
        "|排名|管理人|缺口组合数|组合总数|已覆盖|是否已接入适配器|",
        "|---:|---|---:|---:|---:|:---:|",
    ]
    for rank, row in enumerate(rows, start=1):
        total_count = int(row.get("fund_count", 0))
        gap_count = int(row.get("gap", 0))
        covered_count = int(row.get("covered", 0))
        lines.append(
            f"|{rank}|{row.get('manager', '')}|{gap_count}|{total_count}|{covered_count}|{'是' if row.get('has_adapter') else '否'}|"
        )
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written={OUTPUT} rows={len(rows)} missing={missing}")


if __name__ == "__main__":
    main()
