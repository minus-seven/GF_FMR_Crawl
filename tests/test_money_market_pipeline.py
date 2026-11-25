from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))


def load_run_all():
    spec = importlib.util.spec_from_file_location(
        "run_all_structured_data_money_test",
        ROOT / "code" / "run_all_structured_data.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_money_category_is_a_supported_structured_stage() -> None:
    module = load_run_all()
    assert module.CATEGORY_TO_EXTRACTOR["货币"] == "money"
    assert module.CURRENCY_PROMPT.is_file()


def test_money_output_keeps_metrics_and_asset_allocations(tmp_path: Path) -> None:
    from structured_data.combine_fixed_income import write_outputs

    record = {
        "fund_name": "测试货币基金",
        "report_date": "2026-08",
        "top_holdings": [],
        "money_market_metrics": {
            "seven_day_yield_percent": 2.1,
            "weighted_average_maturity_days": 35,
            "asset_type_allocation": [{"type": "银行存款", "weight_percent": 80}],
            "bank_exposure": [{"name": "测试银行", "weight_percent": 20}],
        },
    }
    outputs = write_outputs([record], tmp_path, output_stem="货币_基金提取结果")
    payload = json.loads(Path(outputs["combined_json"]).read_text(encoding="utf-8"))
    assert payload["money_asset_allocation"][0]["asset_type"] == "银行存款"
    assert payload["funds"][0]["seven_day_yield_percent"] == 2.1


def test_pingan_monthly_chart_and_strategy_fields_are_recovered() -> None:
    from structured_data.extract_fixed_income import (
        _extract_money_market_long_text,
        _parse_money_market_text_facts,
    )

    text = """
    [BLOCK 14]
    投資策略
    [BLOCK 15]
    本基金尋求透過主要投資於美元計價及結算短期存款，以實現其投資目標。
    [BLOCK 16]
    基金詳情
    [BLOCK 18]
    組合統計數據
    <table><tr><td>加權平均到期日</td><td>49.42日</td></tr>
    <tr><td>加權平均收益率</td><td>4.1695%</td></tr></table>
    資產分佈
    五大持倉
    <table><tr><td>持倉</td><td>票息率(%)</td><td>到期日</td><td>百分比(%)</td></tr>
    <tr><td>存款A</td><td>4.27</td><td>27/10/2026</td><td>1.49</td></tr></table>
    0-7日42.77% 8-31日19.71% 32-92日19.75% 93-183日11.97% 184-366日3.70% 367-397日2.09%
    存款 46.23% 其他貨幣市場工具 41.67% 現金及其他12.10%
    """

    long_text = _extract_money_market_long_text(text)
    facts = _parse_money_market_text_facts(text)

    assert long_text["investment_strategy"] == (
        "本基金尋求透過主要投資於美元計價及結算短期存款，以實現其投資目標。"
    )
    assert facts["weighted_average_maturity_days"] == 49.42
    assert facts["portfolio_ytm_percent"] == 4.1695
    assert len(facts["duration_distribution"]) == 6
    assert facts["asset_type_allocation"][0]["weight_percent"] == 46.23
    assert facts["top_holdings"][0]["name"] == "存款A"
