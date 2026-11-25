"""150 道正式意图评测集的发布契约测试。"""

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ARTIFACT_DIR = PROJECT_ROOT / "docs" / "development" / "artifacts" / "20261109-week"
DATASET_PATH = ARTIFACT_DIR / "intent-golden-v1.jsonl"
REPORT_PATH = ARTIFACT_DIR / "intent-golden-v1-report.json"


def test_intent_golden_dataset_is_formal_150_cases() -> None:
    """正式题集必须固定为 150 道、题号唯一且覆盖全部注册类别。"""

    cases = [json.loads(line) for line in DATASET_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(cases) == 150
    assert len({case["case_id"] for case in cases}) == 150
    assert {case["category"] for case in cases} == {
        "query_metric",
        "query_holdings",
        "compare_funds",
        "explain_report",
        "trace_quality_issue",
        "locate_source",
        "find_missing_data",
        "plan_monthly_job",
        "plan_semiannual_job",
        "plan_supplement_source",
        "unknown",
    }


def test_intent_golden_report_meets_release_gate() -> None:
    """发布报告必须达到意图、槽位、澄清和路由四项全通过。"""

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["count"] == 150
    assert report["intent_accuracy"] == 1.0
    assert report["slot_accuracy"] == 1.0
    assert report["clarification_accuracy"] == 1.0
    assert report["route_safety_accuracy"] == 1.0
    assert report["failed_cases"] == []
