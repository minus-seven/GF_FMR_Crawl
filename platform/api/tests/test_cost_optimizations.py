"""Tests for prompt caching, ticker reuse, and targeted visual review."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

CODE_DIR = Path(__file__).resolve().parents[3] / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from structured_data import extract_fixed_income
from structured_data import extract_equity_fund
from structured_data import holding_tickers
from structured_data.manager_profiles import ManagerProfile
from structured_data import qwen_client


@pytest.fixture(autouse=True)
def clear_ticker_inflight() -> None:
    """Keep module-level ticker coordination isolated between tests."""

    with holding_tickers._CACHE_CONDITION:
        holding_tickers._INFLIGHT_KEYS.clear()
    yield
    with holding_tickers._CACHE_CONDITION:
        holding_tickers._INFLIGHT_KEYS.clear()


def _write_cache(path: Path, securities: dict) -> None:
    """Write a minimal security cache fixture."""

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": None,
                "securities": securities,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_structured_system_prompt_is_stable_and_cacheable() -> None:
    """All structured requests should share a non-trivial fixed system prefix."""

    prompt = qwen_client.STRUCTURED_SYSTEM_PROMPT
    assert len(prompt) > 100
    assert "基金" in prompt
    assert "JSON" in prompt
    assert "{pdf_path" not in prompt


def test_context_messages_put_context_before_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认应把完整上下文放在前面，使同 PDF 的多次调用共享缓存前缀。"""

    monkeypatch.setenv("FACTSHEET_CACHE_FRIENDLY_CONTEXT", "1")
    messages = extract_fixed_income._context_messages("REPORT_CONTEXT", "INSTRUCTION")

    assert messages[0]["role"] == "system"
    assert messages[1]["content"].startswith("REPORT_CONTEXT")
    assert messages[1]["content"].endswith("INSTRUCTION")


def test_context_messages_support_legacy_prompt_first_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A/B 基线可通过环境变量回退到 prompt 在前的旧顺序。"""

    monkeypatch.setenv("FACTSHEET_CACHE_FRIENDLY_CONTEXT", "0")
    messages = extract_fixed_income._context_messages("REPORT_CONTEXT", "INSTRUCTION")

    assert messages[1]["content"].startswith("INSTRUCTION")
    assert messages[1]["content"].endswith("REPORT_CONTEXT")


def test_chat_context_json_sends_context_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实调用路径应使用缓存友好的消息顺序。"""

    class FakeClient:
        """Capture chat payloads without calling a provider."""

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def chat(self, **kwargs: object) -> str:
            """Return a minimal valid JSON payload."""

            self.calls.append(dict(kwargs))
            return '{"status":"ok"}'

    monkeypatch.setenv("FACTSHEET_CACHE_FRIENDLY_CONTEXT", "1")
    client = FakeClient()
    result = extract_fixed_income._chat_context_json(
        client,
        model="deepseek-flash",
        context="REPORT_CONTEXT",
        prompt="INSTRUCTION",
        attempts=1,
        enable_thinking=False,
        thinking_budget=None,
        call_name="ab_test",
    )

    assert result == {"status": "ok"}
    content = client.calls[0]["messages"][1]["content"]
    assert content.startswith("REPORT_CONTEXT")


def test_compose_pdf_text_auto_skips_redundant_native_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MinerU 内容完整时，auto 模式不应重复追加原生 PDF 文本。"""

    monkeypatch.setenv("FACTSHEET_PDF_TEXT_LAYER", "auto")
    text, meta = extract_fixed_income._compose_pdf_text(
        "M" * 4000,
        "N" * 3000,
    )

    assert text == "M" * 4000
    assert meta["native_included"] is False
    assert meta["mode"] == "auto"


def test_compose_pdf_text_auto_keeps_native_layer_for_thin_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MinerU 文本明显偏少时仍应补充原生 PDF 文本。"""

    monkeypatch.setenv("FACTSHEET_PDF_TEXT_LAYER", "auto")
    text, meta = extract_fixed_income._compose_pdf_text(
        "M" * 400,
        "N" * 3000,
    )

    assert "[PDF_TEXT_LAYER]" in text
    assert text.endswith("N" * 3000)
    assert meta["native_included"] is True


def test_compose_pdf_text_uses_full_context_as_completeness_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """紧凑上下文变短时，不应误判 MinerU 缺失而追加原生层。"""

    monkeypatch.setenv("FACTSHEET_PDF_TEXT_LAYER", "auto")
    text, meta = extract_fixed_income._compose_pdf_text(
        "M" * 900,
        "N" * 1200,
        reference_context="R" * 5000,
    )

    assert text == "M" * 900
    assert meta["native_included"] is False
    assert meta["context_chars"] == 900


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("always", True), ("never", False)],
)
def test_compose_pdf_text_honours_explicit_mode(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected: bool,
) -> None:
    """always/never 应覆盖 auto 判断，便于 A/B 和回退。"""

    monkeypatch.setenv("FACTSHEET_PDF_TEXT_LAYER", mode)
    text, meta = extract_fixed_income._compose_pdf_text("M" * 4000, "N" * 3000)

    assert ("[PDF_TEXT_LAYER]" in text) is expected
    assert meta["native_included"] is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("700.HK", "0700.HK"),
        ("aapl.us", "AAPL.US"),
        ("600519.sh", "600519.SH"),
        ("300750.sz", "300750.SZ"),
        ("BRK.B.US", "BRK.B.US"),
        ("730 HK", None),
        ("US0378331005", None),
        ("XS1234567890", None),
        ("037833100", None),
        ("B0YQ5W0", None),
        ("HK0001005625", None),
    ],
)
def test_ticker_validation_rejects_non_equity_identifiers(
    raw: str,
    expected: str | None,
) -> None:
    """Only exchange-qualified equity tickers should enter the reusable cache."""

    assert holding_tickers._normalize_ticker(raw) == expected


def test_report_ticker_alias_hits_cache_without_model_call(tmp_path: Path) -> None:
    """A report ticker should reuse a verified alias even when the name is new."""

    cache_path = tmp_path / "security-cache.json"
    _write_cache(
        cache_path,
        {
            "腾讯控股有限公司": {
                "ticker": "0700.HK",
                "name_zh": "腾讯控股有限公司",
                "confidence": "high",
            }
        },
    )

    class NoCallClient:
        """Fail if the cache alias is not used."""

        def respond(self, **kwargs: object) -> str:
            """Unexpected model request."""

            raise AssertionError(f"model should not be called: {kwargs}")

    holdings, metadata = holding_tickers.resolve_holding_tickers(
        NoCallClient(),
        [{"name": "Tencent Holdings", "ticker": "700.HK"}],
        model="deepseek-flash",
        attempts=1,
        asset_class="权益类",
        cache_path=cache_path,
    )

    assert holdings == [
        {
            "name": "腾讯控股有限公司",
            "ticker": "0700.HK",
        }
    ]
    assert metadata is not None
    assert metadata["_cache"]["requested"] == 0
    assert metadata["_cache"]["ticker_hits"] == 1


def test_verified_ticker_success_writes_alias_and_rejects_invalid_ticker(
    tmp_path: Path,
) -> None:
    """Verified successes should write a ticker alias; invalid identifiers stay unusable."""

    cache_path = tmp_path / "security-cache.json"

    class FakeClient:
        """Return one valid and one invalid security identifier."""

        def respond(self, **kwargs: object) -> str:
            """Return structured model output."""

            return json.dumps(
                {
                    "items": [
                        {
                            "index": 1,
                            "name_zh": "苹果公司",
                            "ticker": "AAPL.US",
                            "confidence": "high",
                        },
                        {
                            "index": 2,
                            "name_zh": "某债券",
                            "ticker": "US0378331005",
                            "confidence": "high",
                        },
                    ]
                },
                ensure_ascii=False,
            )

    holdings, _ = holding_tickers.resolve_holding_tickers(
        FakeClient(),
        [
            {"name": "Apple", "ticker": "AAPL.US"},
            {"name": "Unknown Bond", "ticker": "US0378331005"},
        ],
        model="deepseek-flash",
        attempts=1,
        asset_class="权益类",
        cache_path=cache_path,
    )
    stored = json.loads(cache_path.read_text(encoding="utf-8"))["securities"]

    assert holdings[0]["ticker"] == "AAPL.US"
    assert holdings[1]["ticker"] is None
    assert stored["ticker:AAPL.US"]["ticker"] == "AAPL.US"
    assert not any(
        entry.get("ticker") == "US0378331005"
        for entry in stored.values()
    )


def test_complex_visual_review_prefers_capped_mineru_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MinerU chart crops should be selected before rendering any PDF pages."""

    images = []
    for index in range(14):
        path = tmp_path / f"mineru-{index}.png"
        path.write_bytes(b"image")
        images.append(path)
    captured: dict[str, list[str]] = {}

    monkeypatch.setattr(
        extract_fixed_income,
        "image_data_url",
        lambda path: f"data:mineru/{path.name}",
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "pdf_summary_candidate_image_urls",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("summary crops should not be rendered")
        ),
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "pdf_page_image_urls",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("full pages should not be rendered")
        ),
    )

    def fake_chat(*args: object, **kwargs: object) -> dict:
        """Capture selected images and return a valid review object."""

        captured["images"] = kwargs["image_urls"]  # type: ignore[assignment]
        return {
            field: {"disclosed": False, "items": []}
            for field in (
                "top_holdings",
                "duration_distribution",
                "geographic_allocation",
            )
        }

    monkeypatch.setattr(extract_fixed_income, "_chat_image_json", fake_chat)
    extract_fixed_income._review_complex_fields_from_pdf_images(
        SimpleNamespace(),
        pdf_path=tmp_path / "fund.pdf",
        vision_model="deepseek-flash",
        attempts=1,
        max_pages=6,
        mineru_image_paths=images,
    )

    assert captured["images"] == [
        f"data:mineru/mineru-{index}.png"
        for index in range(extract_fixed_income.MAX_VISUAL_REVIEW_IMAGES)
    ]


def test_complex_visual_review_uses_summary_crops_before_full_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without MinerU artifacts, targeted summary crops should replace full pages."""

    monkeypatch.setattr(
        extract_fixed_income,
        "pdf_summary_candidate_image_urls",
        lambda *args, **kwargs: ["data:summary/right-mid"],
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "pdf_page_image_urls",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("full pages should not be rendered")
        ),
    )
    captured: dict[str, list[str]] = {}

    def fake_chat(*args: object, **kwargs: object) -> dict:
        """Capture summary images and return empty disclosures."""

        captured["images"] = kwargs["image_urls"]  # type: ignore[assignment]
        return {}

    monkeypatch.setattr(extract_fixed_income, "_chat_image_json", fake_chat)
    extract_fixed_income._review_complex_fields_from_pdf_images(
        SimpleNamespace(),
        pdf_path=tmp_path / "fund.pdf",
        vision_model="deepseek-flash",
        attempts=1,
        max_pages=6,
    )

    assert captured["images"] == ["data:summary/right-mid"]


def test_complex_visual_review_empty_profile_fields_skips_image_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理人 profile 过滤掉全部字段后不应再发起视觉请求。"""

    monkeypatch.setattr(
        extract_fixed_income,
        "_chat_image_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("visual review should be skipped")
        ),
    )

    result = extract_fixed_income._review_complex_fields_from_pdf_images(
        SimpleNamespace(),
        pdf_path=tmp_path / "fund.pdf",
        vision_model="deepseek-flash",
        attempts=1,
        max_pages=6,
        fields=[],
    )

    assert result == {}


def test_mineru_review_ranking_keeps_late_relevant_chart(tmp_path: Path) -> None:
    """A later duration/geography chart should outrank earlier irrelevant crops."""

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    image_paths = []
    content_items = []
    for index in range(14):
        path = images_dir / f"crop-{index}.png"
        path.write_bytes(b"image")
        image_paths.append(path)
        content_items.append(
            {
                "type": "chart",
                "img_path": f"images/{path.name}",
                "chart_caption": (
                    ["地区分布"] if index == 13 else ["业绩走势"]
                ),
                "page_idx": 0 if index < 7 else 1,
            }
        )
    (tmp_path / "fixture_content_list.json").write_text(
        json.dumps(content_items, ensure_ascii=False),
        encoding="utf-8",
    )

    ranked = extract_fixed_income._rank_mineru_review_images(image_paths)

    assert ranked[0] == image_paths[13]
    assert image_paths[0] in ranked[1:]


@pytest.mark.parametrize(
    ("flash_fills", "skip_refinements", "expected_models"),
    [
        (True, None, ["deepseek-flash"]),
        (False, None, ["deepseek-flash", "deepseek-v4-pro"]),
        (False, {"matrix_flash"}, ["deepseek-v4-pro"]),
    ],
)
def test_fixed_income_matrix_refinement_uses_pro_only_for_remaining_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flash_fills: bool,
    skip_refinements: set[str] | None,
    expected_models: list[str],
) -> None:
    """Flash should handle matrix fallback first; Pro is only the residual retry."""

    refinement_models: list[str] = []

    monkeypatch.setattr(
        extract_fixed_income,
        "parse_local_pdf",
        lambda *args, **kwargs: {"markdown_path": "unused"},
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "load_mineru_bundle",
        lambda manifest: {
            "context": "MinerU context",
            "image_paths": [],
        },
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "QwenClient",
        lambda **kwargs: SimpleNamespace(token_usage=lambda: {}),
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "_extract_pdf_text",
        lambda path: "PDF text",
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "profile_for_pdf",
        lambda path: ManagerProfile(
            skip_refinements=frozenset(skip_refinements or ()),
        ),
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "_needs_refinement",
        lambda record, *args, **kwargs: False,
    )

    def fake_chat_context(*args: object, **kwargs: object) -> dict:
        """Return matrix values according to the active model."""

        call_name = str(kwargs["call_name"])
        model = str(kwargs["model"])
        if call_name == "mineru_primary_extraction":
            return {
                "duration_distribution": [{"bucket": "1-3年", "weight_percent": 100}],
                "geographic_allocation": [],
            }
        refinement_models.append(model)
        if model == "deepseek-flash" and flash_fills:
            return {
                "geographic_allocation": [
                    {"region": "美国", "weight_percent": 100}
                ]
            }
        if model == "deepseek-v4-pro":
            return {
                "geographic_allocation": [
                    {"region": "美国", "weight_percent": 100}
                ]
            }
        return {}

    monkeypatch.setattr(
        extract_fixed_income,
        "_chat_context_json",
        fake_chat_context,
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "_merge_raw",
        lambda left, right, *args: {**left, **right},
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "_extract_fixed_income_metrics_from_images",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "_review_complex_fields_from_pdf_images",
        lambda *args, **kwargs: {
            field: {"disclosed": False, "items": []}
            for field in (
                "top_holdings",
                "duration_distribution",
                "geographic_allocation",
            )
        },
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "_validation_issues",
        lambda record: [],
    )

    result = extract_fixed_income.extract_pdf(
        pdf_path=tmp_path / "fund.pdf",
        primary_model="deepseek-flash",
        fallback_model="deepseek-v4-pro",
        attempts=1,
        prompt_path=tmp_path / "missing-prompt.md",
        timeout=10,
        max_pages=6,
        zoom=2.0,
        split_pages=True,
        source="mineru",
    )

    assert result["status"] == "ok"
    assert refinement_models == expected_models


def test_ticker_resolution_uses_valid_chinese_report_ticker_without_model(
    tmp_path: Path,
) -> None:
    """中文名称加有效报告 Ticker 应直接采用，不调用模型。"""

    class NoCallClient:
        """验证直接采用路径不会发起模型请求。"""

        def respond(self, **kwargs: object) -> str:
            raise AssertionError(f"model should not be called: {kwargs}")

    holdings, metadata = holding_tickers.resolve_holding_tickers(
        NoCallClient(),
        [{"name": "腾讯控股", "ticker": "700.HK"}],
        model="deepseek-flash",
        attempts=1,
        asset_class="权益类",
        cache_path=tmp_path / "security-cache.json",
    )

    assert holdings == [{"name": "腾讯控股", "ticker": "0700.HK"}]
    assert metadata is not None
    assert metadata["_cache"]["direct"] == 1
    assert metadata["_cache"]["requested"] == 0


def test_ticker_resolution_light_pass_disables_thinking_and_tools(
    tmp_path: Path,
) -> None:
    """英文名称先走无 thinking、无联网的轻量核对。"""

    calls: list[dict] = []

    class LightClient:
        """返回高置信结果，避免进入 reasoning 阶段。"""

        def respond(self, **kwargs: object) -> str:
            calls.append(dict(kwargs))
            return json.dumps(
                {
                    "items": [
                        {
                            "index": 1,
                            "name_zh": "苹果公司",
                            "ticker": "AAPL.US",
                            "confidence": "high",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    holdings, metadata = holding_tickers.resolve_holding_tickers(
        LightClient(),
        [{"name": "Apple", "ticker": "AAPL.US"}],
        model="deepseek-flash",
        attempts=1,
        asset_class="权益类",
        cache_path=tmp_path / "security-cache.json",
    )

    assert holdings[0]["name"] == "苹果公司"
    assert len(calls) == 1
    assert calls[0]["enable_thinking"] is False
    assert calls[0]["tools"] is None
    assert metadata is not None
    assert metadata["_cache"]["light_requests"] == 1
    assert metadata["_cache"]["reasoned_requests"] == 0


def test_ticker_resolution_escalates_only_unresolved_items(
    tmp_path: Path,
) -> None:
    """轻量核对不确定时才启用 thinking 与联网工具。"""

    calls: list[dict] = []

    class EscalatingClient:
        """第一轮返回 medium，第二轮返回 high。"""

        def respond(self, **kwargs: object) -> str:
            calls.append(dict(kwargs))
            confidence = (
                "medium"
                if kwargs["call_name"] == "holding_ticker_resolution_light"
                else "high"
            )
            return json.dumps(
                {
                    "items": [
                        {
                            "index": 1,
                            "name_zh": "某公司",
                            "ticker": "0001.HK",
                            "confidence": confidence,
                        }
                    ]
                },
                ensure_ascii=False,
            )

    holdings, metadata = holding_tickers.resolve_holding_tickers(
        EscalatingClient(),
        [{"name": "某公司", "ticker": None}],
        model="deepseek-flash",
        attempts=1,
        asset_class="权益类",
        cache_path=tmp_path / "security-cache.json",
    )

    assert holdings[0]["ticker"] == "0001.HK"
    assert len(calls) == 2
    assert calls[1]["enable_thinking"] is True
    assert calls[1]["tools"]
    assert metadata is not None
    assert metadata["_cache"]["reasoned_requests"] == 1


def test_manager_profile_filters_absent_matrix_fields() -> None:
    """管理人稳定不披露的矩阵字段不应继续触发复核。"""

    profile = ManagerProfile(
        manager="示例管理人",
        absent_fields=frozenset({"duration_distribution"}),
    )
    fixed_record = {
        "duration_distribution": [],
        "geographic_allocation": [],
    }
    equity_record = {
        "geographic_allocation": [],
        "sector_allocation": [],
    }

    assert extract_fixed_income._missing_matrix_fields_for_profile(
        fixed_record,
        profile,
        "没有久期分布",
    ) == ["geographic_allocation"]
    assert extract_equity_fund._missing_matrix_fields_for_profile(
        equity_record,
        ManagerProfile(absent_fields=frozenset({"sector_allocation"})),
        "没有行业分布",
    ) == ["geographic_allocation"]


def test_manager_profile_absent_field_suppresses_text_refinement() -> None:
    """仅有 profile 标记缺失的矩阵时，不应触发固收文本二次核对。"""

    record = {
        "fund_size_hkd_100m": 10.0,
        "credit_rating": "AA",
        "portfolio_duration_years": 3.0,
        "portfolio_ytm_percent": 4.0,
        "top_holdings": [{"name": "债券", "weight_percent": 5.0}],
        "duration_distribution": [],
        "geographic_allocation": [{"region": "美国", "weight_percent": 100.0}],
        "dividend": None,
    }

    assert extract_fixed_income._needs_refinement(
        record,
        ManagerProfile(absent_fields=frozenset({"duration_distribution"})),
        "没有久期分布",
    ) is False
