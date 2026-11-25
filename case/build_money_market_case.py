#!/usr/bin/env python3
"""Build reproducible money-market-fund case reports from the local HKEX snapshot."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "HKEX基金综合资料库_完整_20261008.xlsx"
ARTIFACT_ROOT = ROOT / "data/jobs/b0995014-5a6c-40d1-a10c-d1fcf5238805/staging/b0995014-5a6c-40d1-a10c-d1fcf5238805"
OUT = ROOT / "case"
SCALE_COLUMN = "2026-08-31规模（亿港币）"


def norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("（", "(").replace("）", ")")


def load_rows() -> list[dict[str, Any]]:
    workbook = load_workbook(WORKBOOK, read_only=True, data_only=True)
    sheet = workbook["主数据"]
    rows = sheet.iter_rows(values_only=True)
    headers = [str(value or "") for value in next(rows)]
    return [dict(zip(headers, row)) for row in rows]


def local_artifacts(fund_name: str) -> tuple[Path | None, Path | None, str]:
    """Locate the first local PDF and MinerU full.md whose path contains the fund name."""
    needle = norm(fund_name).lower()
    pdf = None
    markdown = None
    for path in ROOT.glob("data/**/*.pdf"):
        if needle and needle in norm(path.stem).lower():
            pdf = path
            break
    for path in ROOT.glob("data/**/full.md"):
        if needle and needle in norm(path.parent.name).lower():
            markdown = path
            break
    if markdown is None:
        # Some crawler filenames omit punctuation or use an English title; match by PDF stem.
        for path in ROOT.glob("data/**/full.md"):
            if pdf and norm(pdf.stem).lower() in norm(path.parent.name).lower():
                markdown = path
                break
    text = markdown.read_text(encoding="utf-8", errors="ignore") if markdown else ""
    flags = []
    for label, pattern in (
        ("收益率", r"yield|收益率|年化"),
        ("流动性", r"liquid|流动|赎回"),
        ("持仓", r"top.?holdings|投资组合|持仓|银行存款|商业票据"),
        ("半年报", r"六月三十日|中期报告|interim|半年"),
    ):
        if re.search(pattern, text, flags=re.I):
            flags.append(label)
    return pdf, markdown, "、".join(flags) or "未检出关键字"


def money_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("资产类别一级分类") or "").strip() == "货币"]


def fmt(value: Any, digits: int = 2) -> str:
    if value in (None, ""):
        return "—"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out.extend("| " + " | ".join(str(cell).replace("|", "\\|") for cell in row) + " |" for row in rows)
    return "\n".join(out)


def main() -> None:
    rows = money_rows(load_rows())
    chinese = [row for row in rows if str(row.get("管理人中外资") or "").strip() == "中资"]
    currencies = ("美元", "港元", "人民币")
    latest = lambda row: row.get(SCALE_COLUMN)
    top = sorted(chinese, key=lambda row: float(latest(row) or 0), reverse=True)
    gf_rank = next((index for index, row in enumerate(top, 1) if "广发" in str(row.get("基金管理人") or "") or "广发" in str(row.get("基金名称") or "")), None)
    top_n = ((gf_rank + 4) // 5) * 5 if gf_rank else 15
    top15 = top[:top_n]

    universe_rows = []
    for currency in currencies:
        subset = [row for row in chinese if str(row.get("基础货币") or "").strip() == currency]
        universe_rows.append([currency, len(subset), fmt(sum(float(latest(row) or 0) for row in subset), 2)])
    all_by_currency = []
    for currency in currencies:
        subset = [row for row in rows if str(row.get("基础货币") or "").strip() == currency]
        all_by_currency.append([currency, len(subset), fmt(sum(float(latest(row) or 0) for row in subset), 2)])

    top_rows = []
    coverage_rows = []
    csv_rows = []
    for rank, row in enumerate(top15, 1):
        name = str(row.get("基金名称") or "")
        pdf, markdown, flags = local_artifacts(name)
        pdf_rel = pdf.relative_to(ROOT).as_posix() if pdf else "本地未找到"
        md_rel = markdown.relative_to(ROOT).as_posix() if markdown else "本地未找到"
        top_rows.append([
            rank,
            row.get("基金代码"),
            name,
            row.get("管理人简称") or row.get("基金管理人"),
            row.get("基础货币"),
            fmt(latest(row)),
            row.get("证监会认可日期") or "—",
            "有" if pdf else "缺",
            "有" if markdown else "缺",
        ])
        coverage_rows.append([row.get("基金代码"), name, flags, pdf_rel, md_rel])
        csv_rows.append({"rank": rank, "code": row.get("基金代码"), "name": name, "manager": row.get("管理人简称") or row.get("基金管理人"), "currency": row.get("基础货币"), "scale_hkd_100m": latest(row), "recognized_date": row.get("证监会认可日期"), "pdf": pdf_rel, "markdown": md_rel, "keyword_evidence": flags})

    with (OUT / "01-money-market-top15.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)

    client = f"""# 香港中资货币市场基金研究（Case 01）

## 一页结论

本报告以项目内 HKEX 主数据快照 `HKEX基金综合资料库_完整_20261008.xlsx` 为主数据源，规模口径为 **2026-08-31、亿港币**。筛选 `资产类别一级分类=货币` 后，共 **{len(rows)} 只**；其中 `管理人中外资=中资` 共 **{len(chinese)} 只**，合计 **{fmt(sum(float(latest(row) or 0) for row in chinese))} 亿港币**。这是一张可复核的“市场容量底表”，不是把不同币种直接相加后的投资建议。

中资口径按币种如下：

{md_table(["币种", "基金数", "规模（亿港币）"], universe_rows)}

全市场货币基金作参照：美元 {all_by_currency[0][1]} 只/{all_by_currency[0][2]} 亿港币、港元 {all_by_currency[1][1]} 只/{all_by_currency[1][2]} 亿港币、人民币 {all_by_currency[2][1]} 只/{all_by_currency[2][2]} 亿港币；另有少量英镑/欧元基金，未纳入客户要求的三币种比较。

广发美元货币市场基金（BTE502）在中资规模排序中为 **第 {gf_rank or '未识别'} 名**，按“向上取 5 的倍数”形成 **前 {top_n} 名**比较组。该组用于横向研究，不等同于全部中资市场。

## 前 {top_n} 名比较组

{md_table(["排名", "代码", "基金", "管理人", "币种", "规模（亿港币）", "认可日期", "PDF", "Markdown"], top_rows)}

### 收益率、流动性与半年报持仓

项目已有货币月报 Markdown 的关键字覆盖情况如下。当前服务按用户要求保持停止，因此本次没有调用模型重新跑结构化；“收益率/流动性/持仓”列代表本地文本存在可检索证据，不把关键字当成数值结论。

{md_table(["代码", "基金", "本地文本关键字证据", "PDF路径", "Markdown路径"], coverage_rows)}

本组本地 PDF/Markdown 覆盖以脚本生成的 CSV 为准：`[01-money-market-top15.csv](./01-money-market-top15.csv)`。本地已覆盖的文件可进入新增的货币结构化通道，抽取七日年化收益率、年化收益率、WAM/WAL、每日/每周流动资产、资产类型、银行敞口和前十大持仓；缺失文件不得用规模或相邻基金推断。

成立时间采用 HKEX `证监会认可日期`，是可比的产品认可日期，不是管理人品牌成立时间。股债比对货币基金应按“报告明确披露的股票/债券资产类型”计算；没有披露时报告为“未披露”，不能把货币市场工具强行归为股票或普通债券。

## 监管参考（客户可读摘要）

- 2024 版 SFC UT Code/Handbook 对货币市场基金规定了短期、高质量货币市场工具框架，并给出 WAM 不超过 60 天、WAL 不超过 120 天、单项工具剩余期限通常不超过 397 天等数值要求；发行人集中度、政府/公共机构例外和基金获认可条件需结合原文条款核验。参见 [SFC Handbook for Unit Trusts and Mutual Funds](https://en-rules.sfc.hk/-/media/EN/assets/components/codes/files-current/web/codes/sfc-handbook-for-unit-trusts-and-mutual-funds/20241001-sfc-handbook-for-unit-trusts-and-mutual-funds-Eng-Final.pdf)。
- SFC 于 2024-12-17 发布货币市场基金相关通函，强调流动性风险政策要结合策略、投资者基础、流动性状况和赎回义务，并提示不能机械依赖外部评级。参见 [SFC circular 24EC64](https://apps.sfc.hk/edistributionWeb/api/circular/list-content/circular/doc?lang=EN&refNo=24EC64)。
- SFC 关于年度化回报的通函要求披露时保持计算基础和涵盖期间一致；七日年化收益率不能直接解读为未来一年实际回报。参见 [SFC circular 23EC49](https://apps.sfc.hk/edistributionWeb/api/circular/list-content/circular/doc?lang=EN&refNo=23EC49)。
- 当前日期为 2026-11-02；SFC 已发布 2026 修订 UT Code 及流动性风险管理/货币市场基金过渡安排，正式客户版本应在发布前再次核对 [SFC current Codes](https://www.sfc.hk/en/Rules-and-standards/Codes-and-guidelines/Codes) 和 [2026 liquidity circular 26EC67](https://apps.sfc.hk/edistributionWeb/api/circular/list-content/circular/doc?lang=TC&refNo=26EC67)。

## 数据边界与联网补充

- HKEX 主数据是本报告市场容量和排名的唯一主口径；规模快照日期与文件快照日期不同，分别为 2026-08-31 与 2026-10-08。
- 本地缺失的 BQN919、BTE502、BTE097 未用猜测值补齐。联网核验已找到平安官方产品页（可见七日/三十日年化收益率说明）、HKEX BTE502 基金仓库页、博时 BTE097 官方产品资料 PDF，但本次没有把网页动态值混入 2026-08-31 市场容量或半年报持仓表；正式交付应下载并固化其原始文件和抓取日期。
- SFC 监管数值来自官方规则/通函；它们是合规参考，不是对单只基金实际合规状态的审计结论。

## 交付状态

已完成：货币基金 HKEX 筛选、三币种容量、中资排名、广发倍数规则、检索/创建任务入口、专用结构化提示词与导入字段。

待下一次有模型/任务服务时完成：对覆盖月报执行结构化、下载缺失三只基金的中期报告、从半年报抽取前十大持仓并生成银行/资产类型/股债比数值比较表。
"""
    (OUT / "01-money-market-client-report.md").write_text(client, encoding="utf-8")

    dev = f"""# Case 01 开发实现说明：货币基金流水线

## 1. 范围与口径

- 主数据：`HKEX基金综合资料库_完整_20261008.xlsx`，权威过滤条件为 `资产类别一级分类 == 货币`；中资过滤条件为 `管理人中外资 == 中资`。
- 规模：`{SCALE_COLUMN}`，单位为亿港币；不要将基金基础货币误作规模换算币种。
- 研究排名：中资集合按快照规模降序；识别广发 BTE502 后向上取 5 的倍数，生成前 {top_n} 名。
- 报告/证据：本地 PDF、MinerU `full.md`、结构化 JSON/Excel 各自记录来源；找不到时写“缺失”，不回填邻近报告。

## 2. 已吸收到项目的代码

1. `code/batch_layout.py`：把 `货币` 加入标准批次类别，保证 factsheet/markdown/structure_data 目录发现一致。
2. `platform/api/app/worker/adapters.py`：将 `货币` 加入结构化白名单，任务不再因为类别未支持而跳过。
3. `code/run_all_structured_data.py`：货币类别单独建立 pipeline，使用 `money_market_prompt.md`、`money_market` 工作目录和 `货币_基金提取结果` 输出；与固收 pipeline 并行，避免原先“多个 fixed 类别只取第一个”的遗漏。
4. `code/run_all_fixed_income.py` / `code/structured_data/run_batch.py` / `extract_fixed_income.py`：增加 `--category`、`--prompt`、`--output-suffix`，复用稳定的 MinerU/本地模型/证据契约，而不复制一套抽取器。
5. `code/structured_data/extract_fixed_income.py`：新增可选 `money_market_metrics`，包括七日/年化收益率、WAM、WAL、每日/每周流动资产、资产类型和银行敞口；旧固收记录仍可为 null。
6. `combine_fixed_income.py`：汇总货币指标和“货币资产类型”sheet/CSV；`platform/api/app/importers/service.py` 将标量指标和资产类型导入通用 FundMetric/FundAllocation，知识库 Agent 可按报告期追溯。
7. `platform/web/src/pages/FundsPage.tsx`：增加“货币市场基金”快捷筛选，仍以 HKEX 一级分类为事实源。`TasksPage.tsx`：货币类别出现在任务下拉；数据库快照尚未导入时保留待导入提示。

## 3. 数据流

```text
HKEX 主数据
  -> category=货币 / manager_origin=中资
  -> FundsPage 快捷筛选 或 TasksPage 按资产类别
  -> download -> parse(MinerU) -> structure
  -> money_market_prompt.md + local-first(category=money)
  -> per_pdf/*.money_market.json
  -> 货币_基金提取结果_combined.json / xlsx / CSV
  -> D1ImportService FundMetric/FundHolding/FundAllocation
  -> 基金详情与知识库 Agent（报告期、来源文档、证据链）
```

`money_market_metrics` 是可选字段，避免破坏既有固收 JSON；提示词明确 WAM/WAL、收益率、流动性和持仓关键词只在原文披露时填写。前十大持仓仍进入统一 `FundHolding`，资产类型和银行敞口进入 allocation dimension=`asset_type`/`bank_exposure`。

## 4. 货币基金专用口径

- 收益率：七日年化、三十日/年度化等字段分开，不把累计回报或派息率替代七日年化。
- 流动性：WAM/WAL 与每日/每周流动资产是不同概念；WAM/WAL 以天为单位，流动资产以百分比为单位。
- 银行与资产类型：`bank_exposure` 只接受报告明确的银行发行人/存款敞口；前十大持仓保留完整证券名称，后续可按银行词典和发行人类型分类。
- 股债比：货币市场工具、存款、回购、现金不能被强制归为普通股票/债券；没有清晰披露时返回“未披露”。
- 半年报：月报通道可以先提供收益率/流动性/持仓线索，但半年报必须保存独立 source document，并在报告中标明报告期。

## 5. 本地数据和联网缺口

- 本地发现：`data/` 下多个批次已有货币 PDF/Markdown；前 {top_n} 组的覆盖和路径由 `01-money-market-top15.csv` 固化。
- 缺失：BQN919、BTE502、BTE097 在本地批次未找到可用 PDF/Markdown。联网找到了平安官方产品页、HKEX BTE502 仓库页和博时 BTE097 官方资料页，但网页动态收益率/文件版本不能未经下载固化后直接进入本地数据库。
- 监管：2024 UT Handbook 的 WAM/WAL/397 天口径、24EC64 流动性通函、23EC49 年化回报通函及 2026 修订过渡安排均已在客户报告给出官方链接；正式合规判断需以当期规则文本为准。

## 6. 验证方式

- 已执行 `.venv/bin/python -m py_compile` 检查所有修改的 Python 模块。
- 服务保持停止，未调用外部模型、未写数据库；因此本次没有伪造结构化成功数。
- 下一步运行：先完成 MinerU 缓存，再执行 `code/run_all_structured_data.py <run_root> --source mineru --skip-existing`；检查 `structured_run_summary_*.json` 的货币 stage、`money_market_metrics` 非空率、top_holdings 证据和 importer warnings。

## 7. 可供项目继续吸收的经验

- “分类”应由 HKEX 主数据枚举驱动，UI 快捷入口只是别名；不要通过基金名称关键字判定货币基金。
- 每个资产类别应拥有明确的 prompt/schema/output stem，但尽量复用同一证据契约、模型账户熔断、MinerU 缓存和导入器。
- 结构化字段必须带报告期、原文证据和来源文件；知识库 Agent 的答案只能引用这些节点，并把“未披露”和“本地缺失”分开。
- 市场容量、收益率和监管口径是三类不同事实：分别来自 HKEX 快照、基金报告/事实资料、SFC 规则；报告必须显示来源和快照日期。
"""
    (OUT / "01-money-market-developer-notes.md").write_text(dev, encoding="utf-8")
    print(f"Generated {OUT / '01-money-market-client-report.md'}")
    print(f"Generated {OUT / '01-money-market-developer-notes.md'}")
    print(f"Generated {OUT / '01-money-market-top15.csv'}")


if __name__ == "__main__":
    main()
