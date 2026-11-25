"""字段流水线契约的离线回归测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from structured_data.extract_fixed_income import _fund_size_conversion  # noqa: E402
from structured_data.evidence_contract import build_subschema, resolve_evidence  # noqa: E402
from structured_data.field_pipeline import execute_pipeline  # noqa: E402
from structured_data.local_first import EvidenceBlock, build_evidence_index  # noqa: E402
from structured_data.parse_mineru_pdf import _compact_mineru_context  # noqa: E402


class FieldPipelineTests(unittest.TestCase):
    """覆盖契约选择、漂移回退及统一规模换算。"""

    def test_jpm_layout_and_deterministic_scalars(self) -> None:
        """JPM 锚点齐全时应命中特定契约；参数为空。"""

        markdown = """
        摩根基金 SICAV系列
        基金資料
        總資產值（百萬） 1,768.3 美元
        截至 2026年10月
        <table><tr><td>平均存續期</td><td>6.8</td></tr>
        <tr><td>期滿收益率(%)</td><td>6.50</td></tr>
        <tr><td>債券質素</td><td>AAA: 15.0 AA: 17.7 A: 39.7 BBB: 19.4</td></tr></table>
        """
        result, meta = execute_pipeline(
            markdown,
            category="fixed",
            fx_rates={"USD": 7.8},
            preferred_layout_id="jpm-hk-factsheet-2026",
        )
        self.assertEqual(meta["layout_id"], "jpm-hk-factsheet-2026")
        self.assertFalse(meta["layout_drift"])
        self.assertEqual(result.fields["fund_size"].value["hkd_100m"], 137.9274)
        self.assertEqual(result.fields["report_date"].value, "2026-10")
        self.assertEqual(result.fields["portfolio_duration_years"].value, 6.8)
        self.assertEqual(result.fields["portfolio_ytm_percent"].value, 6.5)

    def test_preferred_layout_drift_falls_back_to_generic(self) -> None:
        """画像锚点缺失时必须回退通用契约；参数为空。"""

        result, meta = execute_pipeline(
            "基金資料 總資產值（百萬） 10 美元",
            category="fixed",
            preferred_layout_id="jpm-hk-factsheet-2026",
        )
        self.assertEqual(meta["layout_id"], "generic")
        self.assertTrue(meta["layout_drift"])
        self.assertEqual(meta["fallback_reason"], "preferred_layout_drift")
        self.assertEqual(result.fields["fund_size"].status, "ok")

    def test_fund_size_conversion_records_rate_source(self) -> None:
        """模型路径换算应记录汇率值与来源；参数为空。"""

        conversion = _fund_size_conversion({"amount": 10, "currency": "HKD", "unit": "million"})
        self.assertEqual(conversion["status"], "ok")
        self.assertEqual(conversion["fx_rate"], 1.0)
        self.assertIn(conversion["fx_source"], {"api", "fallback"})
        self.assertEqual(conversion["hkd_100m"], 0.1)

    def test_block_reference_contract_round_trips_to_evidence(self) -> None:
        """紧凑上下文的块号必须与证据索引一致；参数为空。"""

        content_list = [{"type": "text", "page_idx": 2, "text": "总资产值 100 美元"}]
        context = _compact_mineru_context(
            markdown="总资产值 100 美元",
            content_items=content_list,
            header_footer="",
            report_month_hint="2026-10",
        )
        blocks = build_evidence_index(
            "总资产值 100 美元",
            content_list=content_list,
            report_month_hint="2026-10",
        )
        self.assertIn("[PAGE 2]\n[BLOCK 1]", context)
        resolved, audit = resolve_evidence(
            {"fund_size_original": {"page_idx": 2, "block_index": 1}},
            blocks,
            fields=["fund_size_original"],
        )
        self.assertEqual(resolved["fund_size_original"][0]["quote"], "总资产值 100 美元")
        self.assertEqual(audit["valid_rate"], 1.0)

    def test_subschema_caps_arrays_and_disallows_free_text_evidence(self) -> None:
        """子 Schema 必须限制数组条数并关闭自由证据文本；参数为空。"""

        base = {
            "type": "object",
            "properties": {
                "top_holdings": {"type": "array", "items": {"type": "object"}},
                "field_evidence": {"type": "object"},
            },
        }
        schema = build_subschema(base, fields=["top_holdings"])
        self.assertEqual(schema["properties"]["top_holdings"]["maxItems"], 20)
        self.assertFalse(schema["properties"]["field_evidence"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
