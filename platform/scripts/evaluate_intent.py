"""生成并评估 150 道 Agent 意图金标准题。"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

from app.services.intent import classify_intent_envelope, route_intent


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = PROJECT_ROOT / "docs" / "development" / "artifacts" / "20261109-week" / "intent-golden-v1.jsonl"
DEFAULT_REPORT = PROJECT_ROOT / "docs" / "development" / "artifacts" / "20261109-week" / "intent-golden-v1-report.json"


def _add(
    cases: list[dict[str, Any]],
    *,
    category: str,
    question: str,
    intent: str,
    codes: list[str] | None = None,
    report_type: str = "unknown",
    report_date: str | None = None,
    metric: str | None = None,
    top_n: int | None = None,
    needs_clarification: bool = False,
    operation: str = "read",
    route: str | None = None,
) -> None:
    """追加一条金标准题；参数为题目分类、问题、期望意图和槽位。"""

    cases.append(
        {
            "case_id": f"intent-{len(cases) + 1:03d}",
            "category": category,
            "question": question,
            "gold": {
                "intent": intent,
                "fund_codes": codes or [],
                "report_type": report_type,
                "report_date": report_date,
                "metric": metric,
                "top_n": top_n,
                "needs_clarification": needs_clarification,
                "operation": operation,
                "route": route or ("plan" if operation == "plan" else intent),
            },
        }
    )


def build_dataset() -> list[dict[str, Any]]:
    """构造固定 150 道金标准题；参数为空，返回可序列化题目。"""

    cases: list[dict[str, Any]] = []
    codes = ["BPJ877", "BQN919", "BTE502", "BTF715", "BNW892"]
    metric_templates = [
        ("到期收益率", "portfolio_ytm_percent"),
        ("加权平均到期日", "weighted_average_maturity_days"),
        ("日流动资产", "daily_liquid_assets_percent"),
        ("周流动资产", "weekly_liquid_assets_percent"),
        ("年化收益率", "annualized_return_percent"),
    ]
    patterns = [
        ("{code} 2026年9月的{metric}是多少", "unknown", "2026-09-01"),
        ("请查询{code} 2026年9月月报的{metric}", "monthly", "2026-09-01"),
        ("{code}最新月报{metric}", "monthly", None),
        ("截至2026-09-30，{code}的{metric}", "unknown", "2026-09-30"),
        ("{code}的{metric}", "unknown", None),
    ]
    for metric_text, metric in metric_templates:
        for code, (pattern, report_type, report_date) in zip(codes, patterns):
            _add(
                cases,
                category="query_metric",
                question=pattern.format(code=code, metric=metric_text),
                intent="query_metric",
                codes=[code],
                report_type=report_type,
                report_date=report_date,
                metric=metric,
                route="read",
            )

    holding_patterns = [
        ("{code} 2026年半年报前十大持仓", 10),
        ("请列出{code} 2026年9月月报前五大持仓", 5),
        ("{code}的主要持仓有哪些", 10),
    ]
    for index, (pattern, top_n) in enumerate(holding_patterns):
        for code in codes:
            _add(
                cases,
                category="query_holdings",
                question=pattern.format(code=code),
                intent="query_holdings",
                codes=[code],
                report_type="semiannual" if index == 0 else "monthly" if index == 1 else "unknown",
                report_date="2026-09-01" if index == 1 else None,
                top_n=top_n,
                route="read",
            )

    pairs = [(codes[i], codes[(i + 1) % len(codes)]) for i in range(5)]
    compare_patterns = [
        "比较{a}和{b} 2026年9月的到期收益率",
        "对比{a}与{b} 2026年9月月报的日流动资产",
        "给{a}、{b}按基金规模排名",
        "{a}和{b}的投资策略有什么差异",
    ]
    for pattern in compare_patterns:
        for a, b in pairs:
            metric = (
                "portfolio_ytm_percent" if "收益率" in pattern
                else "daily_liquid_assets_percent" if "日流动资产" in pattern
                else "fund_size_hkd_100m" if "基金规模" in pattern
                else "investment_strategy" if "投资策略" in pattern
                else None
            )
            _add(
                cases,
                category="compare_funds",
                question=pattern.format(a=a, b=b),
                intent="compare_funds",
                codes=[a, b],
                report_type="monthly" if "月报" in pattern else "unknown",
                report_date="2026-09-01" if "2026年9月" in pattern else None,
                metric=metric,
                route="read",
            )

    explain_patterns = [
        "{code} 2026年9月月报的投资目标是什么",
        "请解释{code} 2026年9月月报的投资策略",
        "{code}半年报中的投资目标说明",
        "请说明{code}报告里的投资策略",
    ]
    for pattern in explain_patterns:
        for code in codes:
            explain_metric = "investment_objective" if "投资目标" in pattern else "investment_strategy" if "投资策略" in pattern else None
            _add(
                cases,
                category="explain_report",
                question=pattern.format(code=code),
                intent="explain_report",
                codes=[code],
                report_type="monthly" if "月报" in pattern else "semiannual" if "半年报" in pattern else "unknown",
                report_date="2026-09-01" if "月报" in pattern else None,
                metric=explain_metric,
                route="read",
            )

    quality_patterns = [
        "为什么{code}月报没有持仓",
        "排查{code}结构化数据有误的原因",
        "{code}为什么进入待复核清单",
    ]
    for pattern in quality_patterns:
        for code in codes:
            _add(
                cases,
                category="trace_quality_issue",
                question=pattern.format(code=code),
                intent="trace_quality_issue",
                codes=[code],
                report_type="monthly" if "月报" in pattern else "unknown",
                route="read",
            )

    source_patterns = [
        "{code}半年报原文在哪一页",
        "请给出{code}月报数据来源和页码",
    ]
    for pattern in source_patterns:
        for code in codes:
            _add(
                cases,
                category="locate_source",
                question=pattern.format(code=code),
                intent="locate_source",
                codes=[code],
                report_type="semiannual" if "半年报" in pattern else "monthly",
                route="read",
            )

    missing_questions = [
        "哪些基金缺少半年报来源",
        "列出当前缺失月报来源的基金",
    ]
    for question in missing_questions:
        for _ in range(5):
            _add(
                cases,
                category="find_missing_data",
                question=question,
                intent="find_missing_data",
                report_type="semiannual" if "半年报" in question else "monthly",
                route="read",
            )

    for code in codes:
        _add(cases, category="plan_monthly_job", question=f"为{code}创建月报任务", intent="plan_monthly_job", codes=[code], report_type="monthly", operation="plan")
        _add(cases, category="plan_monthly_job", question=f"给{code}创建2026年9月月报下载任务", intent="plan_monthly_job", codes=[code], report_type="monthly", report_date="2026-09-01", operation="plan")
        _add(cases, category="plan_semiannual_job", question=f"为{code}创建半年报任务", intent="plan_semiannual_job", codes=[code], report_type="semiannual", operation="plan")
        _add(cases, category="plan_semiannual_job", question=f"给{code}补2026年半年报", intent="plan_semiannual_job", codes=[code], report_type="semiannual", operation="plan")

    supplement_urls = [
        "https://example.com/bpj877.pdf",
        "https://example.com/bqn919.pdf",
        "https://example.com/bte502.pdf",
        "https://example.com/btf715.pdf",
        "https://example.com/bnw892.pdf",
    ]
    for code, url in zip(codes, supplement_urls):
        _add(cases, category="plan_supplement_source", question=f"给{code}补充来源{url}", intent="plan_supplement_source", codes=[code], operation="plan")
        _add(cases, category="plan_supplement_source", question=f"把{code}的新地址{url}作为候选来源", intent="plan_supplement_source", codes=[code], operation="plan")

    for question in [
        "请帮我看看这个情况",
        "给我一个合适的答案",
        "这批数据怎么样",
        "我想做点分析",
        "帮我处理一下",
    ]:
        _add(cases, category="unknown", question=question, intent="unknown", needs_clarification=True, route="clarification")

    assert len(cases) == 150, len(cases)
    return cases


def _slot_checks(gold: dict[str, Any], envelope: Any) -> dict[str, bool]:
    """比较金标准槽位；参数 gold/envelope 为期望值和实际意图。"""

    return {
        "fund_codes": envelope.scope.fund_codes == gold["fund_codes"],
        "report_type": envelope.time.report_type == gold["report_type"],
        "report_date": (envelope.time.report_date.isoformat() if envelope.time.report_date else None) == gold["report_date"],
        "metric": envelope.constraints.metric == gold["metric"],
        "top_n": envelope.constraints.top_n == gold["top_n"],
        "needs_clarification": envelope.needs_clarification == gold["needs_clarification"],
        "operation": envelope.operation == gold["operation"],
    }


def evaluate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """运行意图金标准评测；参数 cases 为题目列表。"""

    rows: list[dict[str, Any]] = []
    intent_pass = 0
    slot_total = 0
    slot_pass = 0
    route_pass = 0
    clarification_pass = 0
    for case in cases:
        gold = case["gold"]
        envelope = classify_intent_envelope(case["question"], allow_local_model=False)
        route = route_intent(envelope)
        checks = _slot_checks(gold, envelope)
        intent_ok = envelope.intent == gold["intent"]
        route_ok = route.route == ("clarification" if gold["needs_clarification"] else gold["route"])
        rows.append({"case_id": case["case_id"], "intent_ok": intent_ok, "route_ok": route_ok, "slot_checks": checks, "actual": envelope.model_dump(mode="json")})
        intent_pass += int(intent_ok)
        route_pass += int(route_ok)
        clarification_pass += int(checks["needs_clarification"])
        slot_total += len(checks) - 1
        slot_pass += sum(int(value) for key, value in checks.items() if key != "needs_clarification")
    return {
        "schema_version": "intent-eval-v1",
        "count": len(cases),
        "intent_accuracy": round(intent_pass / len(cases), 4),
        "slot_accuracy": round(slot_pass / slot_total, 4),
        "clarification_accuracy": round(clarification_pass / len(cases), 4),
        "route_safety_accuracy": round(route_pass / len(cases), 4),
        "intent_pass": intent_pass,
        "route_pass": route_pass,
        "failed_cases": [row for row in rows if not row["intent_ok"] or not row["route_ok"] or not all(row["slot_checks"].values())],
        "category_counts": {category: sum(1 for case in cases if case["category"] == category) for category in sorted({case["category"] for case in cases})},
    }


def main() -> int:
    """生成数据集并输出评测报告；参数为空，返回进程退出码。"""

    parser = argparse.ArgumentParser(description="评估 150 道 Agent 意图金标准题")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    cases = build_dataset()
    args.dataset.parent.mkdir(parents=True, exist_ok=True)
    args.dataset.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")
    report = evaluate(cases)
    report["dataset"] = str(args.dataset)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["failed_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
