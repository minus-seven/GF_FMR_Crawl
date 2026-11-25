#!/usr/bin/env python3
"""Build a conservative comparison table from the local-Qwen run and fixed evidence.

The local Qwen extractor is deliberately reported as ``review`` until each value has
an evidence quote that is more specific than a page heading.  The three formerly
missing funds are represented from downloaded reports (or, for BTE502, the official
HKEX file index when the file endpoint is unavailable).
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOB = ROOT / "data/jobs/75a732b1-383d-4a92-9741-a21f9a353749/staging/75a732b1-383d-4a92-9741-a21f9a353749"
PER_PDF = JOB / "structure_data/_work/money_market/per_pdf"
TOP15 = ROOT / "case/01-money-market-top15.csv"
OUT_CSV = ROOT / "case/01-money-market-comparison.csv"
OUT_MD = ROOT / "case/01-money-market-comparison.md"


def clean(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())


def load_qwen() -> dict[str, tuple[dict, dict]]:
    result: dict[str, tuple[dict, dict]] = {}
    for path in PER_PDF.glob("*.money_market.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        meta_path = path.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        # Failed attempts may leave a placeholder JSON beside the meta record;
        # do not let those placeholders masquerade as extracted evidence.
        if meta.get("status") == "failed":
            continue
        result[clean(str(data.get("fund_name") or path.stem))] = (data, meta)
    return result


MANUAL = {
    "BQN919": {
        "report_date": "2022-12-31",
        "wam_days": "34.57",
        "wal_days": "56.69",
        "daily_liquid_pct": "14.60",
        "weekly_liquid_pct": "22.87",
        "top10": "CHINA PING AN INSURANCE OVERSEAS HLDGS LTD（8笔）",
        "asset_type": "未上市债务证券/短期债务工具",
        "bank_exposure": "未识别为银行发行人",
        "stock_bond": "股票 0%；债券/债务证券 10.27%（其余为其他净资产）",
        "source": "case/sources/money-market/BQN919_PingAn_MoneyMarket_Interim-Report-EN.pdf",
        "status": "已下载；报告期 2022H2（非 2026H1）",
    },
    "BTE097": {
        "report_date": "2024-06-30",
        "wam_days": "52.18",
        "wal_days": "52.18",
        "daily_liquid_pct": "15.43",
        "weekly_liquid_pct": "21.75",
        "top10": "HKD 子基金未列出投资组合持仓（仅披露流动资产）",
        "asset_type": "未披露（报告中的 HKD 子基金组合为空）",
        "bank_exposure": "未披露",
        "stock_bond": "股票 0%；债券未披露",
        "source": "case/sources/money-market/BTE097_Bosera_HKD_MoneyMarketETF_Interim-2024-06-30.pdf",
        "status": "已下载；报告期 2024H1（非 2026H1）",
    },
    "BTE502": {
        "report_date": "2026-06-30",
        "source": "case/sources/money-market/BTE502_Guangfa_USD_MoneyMarket_HKEX_financial-report-index.json",
        "status": "HKEX 已确认中期报告存在；文件流 HTTP 500，未把索引冒充 PDF",
    },
}


def main() -> None:
    qwen = load_qwen()
    # Repeated identical metric bundles across unrelated funds are a strong
    # signal that a small local model copied a template rather than grounded
    # the value in the current report. Keep the candidate values for audit,
    # but mark them as unusable without page-level review.
    fingerprints: dict[tuple[str, str, str, str], int] = {}
    for data, _meta in qwen.values():
        mm = data.get("money_market_metrics") or {}
        fp = tuple(str(mm.get(key) or "") for key in (
            "weighted_average_maturity_days",
            "weighted_average_life_days",
            "daily_liquid_assets_percent",
            "weekly_liquid_assets_percent",
        ))
        if any(fp):
            fingerprints[fp] = fingerprints.get(fp, 0) + 1
    rows: list[dict[str, str]] = []
    with TOP15.open(encoding="utf-8-sig", newline="") as handle:
        for base in csv.DictReader(handle):
            code = base["code"]
            row = {"rank": base["rank"], "code": code, "name": base["name"], "manager": base["manager"], "currency": base["currency"], "scale_hkd_100m": base["scale_hkd_100m"]}
            if code in MANUAL:
                row.update({"report_date": "", "wam_days": "", "wal_days": "", "daily_liquid_pct": "", "weekly_liquid_pct": "", "top10": "", "asset_type": "", "bank_exposure": "", "stock_bond": "", **MANUAL[code]})
            else:
                data, meta = qwen.get(clean(base["name"]), ({}, {}))
                mm = data.get("money_market_metrics") or {}
                evidence = data.get("field_evidence") or {}
                specific = any(any(int(item.get("block_index", 0)) > 0 or item.get("kind") not in {"heading", "title"} for item in items) for items in evidence.values() if isinstance(items, list))
                fp = tuple(str(mm.get(key) or "") for key in (
                    "weighted_average_maturity_days",
                    "weighted_average_life_days",
                    "daily_liquid_assets_percent",
                    "weekly_liquid_assets_percent",
                ))
                repeated = fingerprints.get(fp, 0) >= 3 and any(fp)
                evidence_status = "疑似模板复用，禁止直接引用" if repeated else ("证据需复核" if not specific else "有非标题证据")
                row.update({
                    "report_date": str(data.get("report_date") or ""),
                    "wam_days": str(mm.get("weighted_average_maturity_days") or ""),
                    "wal_days": str(mm.get("weighted_average_life_days") or ""),
                    "daily_liquid_pct": str(mm.get("daily_liquid_assets_percent") or ""),
                    "weekly_liquid_pct": str(mm.get("weekly_liquid_assets_percent") or ""),
                    "top10": "; ".join(str(x.get("name") or "") for x in (data.get("top_holdings") or [])[:10]),
                    "asset_type": "; ".join(str(x.get("type") or "") for x in (mm.get("asset_type_allocation") or [])),
                    "bank_exposure": "; ".join(str(x.get("name") or "") for x in (mm.get("bank_exposure") or [])),
                    "stock_bond": "未披露（货币市场工具不可强行归类）",
                    "source": "本地 Qwen 4B + MinerU 月报缓存",
                    "status": f"{meta.get('status', '未完成')}；{evidence_status}",
                })
            rows.append(row)

    fields = ["rank", "code", "name", "manager", "currency", "scale_hkd_100m", "report_date", "wam_days", "wal_days", "daily_liquid_pct", "weekly_liquid_pct", "top10", "asset_type", "bank_exposure", "stock_bond", "source", "status"]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# 前 15 名货币基金：输出与证据比较表",
        "",
        "本表的市场规模来自 HKEX 2026-08-31 快照；49 份覆盖月报最终均由本地 `qwen3:4b` 生成结构化结果（首轮 3 份 JSON 修复失败，提升修复预算后重跑成功）。Qwen 结果保留原值但统一标为复核状态，只有下载报告且有明确页内披露的字段才作为人工证据。",
        "",
        "| 排名 | 代码 | 基金 | 报告期 | WAM 天 | WAL 天 | 日流动资产 | 周流动资产 | 前十大/持仓口径 | 银行敞口 | 资产类型 | 股债比 | 证据状态 |",
        "|---:|---|---|---|---:|---:|---:|---:|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append("| {rank} | {code} | {name} | {report_date} | {wam_days} | {wal_days} | {daily_liquid_pct} | {weekly_liquid_pct} | {top10} | {bank_exposure} | {asset_type} | {stock_bond} | {status} |".format(**{k: (v or "—").replace("|", "\\|") for k, v in row.items()}))
    lines += [
        "",
        "## 解释与边界",
        "",
        "- BQN919 与 BTE097 的中期报告已下载，但可获得版本分别为 2022H2 与 2024H1；不能伪装成 2026H1。",
        "- BTE502 的 HKEX 财务报告索引确认 2026-06-30 中期报告和文件名，但文件服务返回 HTTP 500，因此该基金的半年报持仓、银行敞口和股债比仍是缺口。",
        "- 本地 Qwen 结果若证据仅落在 MinerU 标题块，表中数字只能视为候选抽取结果，不能用于客户投资结论。",
        "- 本次运行中多个不同基金出现完全相同的 WAM/WAL/流动资产/持仓组合，已标为“疑似模板复用”；这些候选值必须逐页复核，不能作为横向数值比较。",
        "- CSV 保存完整字段，便于后续人工复核或再次运行本地模型后差异比较。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_CSV}")
    print(f"wrote {OUT_MD}")


if __name__ == "__main__":
    main()
