"""Tests for MinerU precise-to-agent fallback behavior."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CODE_ROOT = PROJECT_ROOT / "code"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from structured_data import parse_mineru_pdf  # noqa: E402
import run_all_mineru  # noqa: E402


def _args(pdf: Path, outdir: Path, mode: str) -> argparse.Namespace:
    """构造单 PDF 解析参数；参数 pdf/outdir/mode 为测试输入。"""

    return argparse.Namespace(
        pdf=pdf,
        mode=mode,
        token="",
        model="vlm",
        language="ch",
        ocr=False,
        force=False,
        outdir=outdir,
        request_timeout=1,
        poll_timeout=1,
        interval=0,
        cache_only=False,
    )


def test_auto_falls_back_to_agent_when_precise_token_expires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto 模式应在 Precise Token 失效时使用 Agent 完成解析。"""

    pdf = tmp_path / "fund.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    outdir = tmp_path / "markdown"

    def fail_precise(*args: object, **kwargs: object) -> dict[str, object]:
        raise parse_mineru_pdf.MinerUError("HTTP 401 user token expired")

    def succeed_agent(
        session: object,
        pdf_path: Path,
        output_dir: Path,
        **kwargs: object,
    ) -> dict[str, object]:
        markdown = output_dir / "full.md"
        markdown.write_text("# Parsed fund\n", encoding="utf-8")
        return {
            "mode": "agent",
            "model_version": "pipeline-lightweight",
            "markdown_path": str(markdown),
        }

    monkeypatch.setattr(parse_mineru_pdf, "_configured_token", lambda: "expired")
    monkeypatch.setattr(parse_mineru_pdf, "_parse_precise", fail_precise)
    monkeypatch.setattr(parse_mineru_pdf, "_parse_agent", succeed_agent)

    manifest_path = parse_mineru_pdf.parse_pdf(_args(pdf, outdir, "auto"))
    manifest = parse_mineru_pdf.json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["status"] == "done"
    assert manifest["mode"] == "agent"
    assert manifest["fallback_from"] == "precise"
    assert "HTTP 401" in manifest["fallback_reason"]
    assert Path(manifest["markdown_path"]).is_file()

    def unexpected_network_call(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("completed Agent cache must be reused")

    monkeypatch.setattr(
        parse_mineru_pdf,
        "_parse_precise",
        unexpected_network_call,
    )
    monkeypatch.setattr(
        parse_mineru_pdf,
        "_parse_agent",
        unexpected_network_call,
    )
    cache_args = _args(pdf, outdir, "auto")
    cache_args.cache_only = True
    cached_path = parse_mineru_pdf.parse_pdf(cache_args)
    assert cached_path == manifest_path


def test_explicit_precise_mode_does_not_fall_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式 precise 模式应保留失败，避免调用方误判解析质量。"""

    pdf = tmp_path / "fund.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    def fail_precise(*args: object, **kwargs: object) -> dict[str, object]:
        raise parse_mineru_pdf.MinerUError("HTTP 401 user token expired")

    monkeypatch.setattr(parse_mineru_pdf, "_configured_token", lambda: "expired")
    monkeypatch.setattr(parse_mineru_pdf, "_parse_precise", fail_precise)

    with pytest.raises(parse_mineru_pdf.MinerUError, match="HTTP 401"):
        parse_mineru_pdf.parse_pdf(_args(pdf, tmp_path / "markdown", "precise"))


def test_mineru_batch_parses_independent_pdfs_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """workers=2 应并行解析两份独立 PDF。"""

    category = tmp_path / "factsheet" / "固收"
    category.mkdir(parents=True)
    for name in ("a.pdf", "b.pdf"):
        (category / name).write_bytes(b"%PDF-1.4\n%%EOF\n")

    intervals: list[float] = []

    def fake_parse(pdf_path: Path, **kwargs: object) -> dict[str, str]:
        intervals.append(float(kwargs["interval"]))
        time.sleep(0.35)
        return {"manifest_path": str(pdf_path) + ".manifest.json"}

    monkeypatch.setattr(run_all_mineru, "parse_local_pdf", fake_parse)
    started = time.monotonic()
    result = run_all_mineru.main(
        [str(tmp_path), "--workers", "2", "--mode", "agent"]
    )
    elapsed = time.monotonic() - started

    assert result == 0
    assert elapsed < 0.80
    assert sorted(intervals) == [2.5, 2.5]


def test_mineru_sliding_window_limiter_releases_after_window() -> None:
    """提交限流器在达到每分钟额度后应等待窗口释放。"""

    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        """返回模拟时间；参数为空。"""

        return now[0]

    def sleeper(seconds: float) -> None:
        """推进模拟时间；参数 seconds 为等待秒数。"""

        sleeps.append(seconds)
        now[0] += seconds

    limiter = parse_mineru_pdf.SlidingWindowRateLimiter(
        limit=2,
        period_seconds=60,
        clock=clock,
        sleeper=sleeper,
    )
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()

    assert len(limiter.timestamps) == 1
    assert sleeps == [60.05]


def test_request_json_waits_and_retries_after_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """结果查询遇到 429 后应共享冷却并重试原任务。"""

    class FakeResponse:
        def __init__(self, status_code: int, body: dict, retry_after: str = ""):
            """构造响应；参数 status_code/body/retry_after 为响应配置。"""

            self.status_code = status_code
            self._body = body
            self.headers = {"Retry-After": retry_after}
            self.text = str(body)

        def json(self) -> dict:
            """返回 JSON；参数为空。"""

            return self._body

    class FakeSession:
        def __init__(self) -> None:
            """初始化调用计数；参数为空。"""

            self.calls = 0

        def request(self, *args: object, **kwargs: object) -> FakeResponse:
            """先返回 429，再返回完成结果；参数为请求配置。"""

            self.calls += 1
            if self.calls == 1:
                return FakeResponse(
                    429,
                    {"error": "rate limit"},
                    retry_after="3",
                )
            return FakeResponse(
                200,
                {"code": 0, "data": {"state": "done"}},
            )

    sleeps: list[float] = []
    monkeypatch.setattr(
        parse_mineru_pdf,
        "_RATE_LIMIT_COOLDOWN_UNTIL",
        0.0,
    )
    monkeypatch.setattr(
        parse_mineru_pdf.MINERU_RESULT_LIMITER,
        "acquire",
        lambda: None,
    )
    monkeypatch.setattr(
        parse_mineru_pdf.time,
        "sleep",
        lambda seconds: sleeps.append(seconds),
    )
    session = FakeSession()
    result = parse_mineru_pdf._request_json(
        session,
        "GET",
        "https://mineru.net/api/v1/agent/parse/task-1",
        timeout=10,
    )

    assert result["state"] == "done"
    assert session.calls == 2
    assert sleeps and sleeps[0] >= 3


def test_mineru_batch_reuses_agent_after_precise_auth_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Precise 认证失败后，同批后续 PDF 应直接走 Agent。"""

    category = tmp_path / "factsheet" / "固收"
    category.mkdir(parents=True)
    for name in ("a.pdf", "b.pdf"):
        (category / name).write_bytes(b"%PDF-1.4\n%%EOF\n")

    modes: list[str] = []

    def fake_parse(pdf_path: Path, **kwargs: object) -> dict[str, str]:
        mode = str(kwargs["mode"])
        modes.append(mode)
        result = {
            "manifest_path": str(pdf_path) + ".manifest.json",
            "mode": "agent",
        }
        if mode == "auto":
            result.update(
                {
                    "fallback_from": "precise",
                    "fallback_reason": "MinerUError: HTTP 401 user token expired",
                }
            )
        return result

    monkeypatch.setattr(run_all_mineru, "parse_local_pdf", fake_parse)
    result = run_all_mineru.main(
        [str(tmp_path), "--workers", "1", "--mode", "auto"]
    )

    assert result == 0
    assert modes == ["auto", "agent"]


def test_cross_batch_cache_reuses_same_pdf_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨任务缓存应按 PDF SHA-256 复制并重写 manifest 路径。"""

    pdf = tmp_path / "fund.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    digest = parse_mineru_pdf._sha256(pdf)
    old_dir = tmp_path / "old-cache"
    old_dir.mkdir()
    old_markdown = old_dir / "full.md"
    old_markdown.write_text("# Cached\n", encoding="utf-8")
    old_manifest = old_dir / "manifest.json"
    old_manifest.write_text(
        parse_mineru_pdf.json.dumps(
            {
                "status": "done",
                "mode": "agent",
                "model_version": "pipeline-lightweight",
                "source_sha256": digest,
                "markdown_path": str(old_markdown),
            }
        ),
        encoding="utf-8",
    )

    def unexpected_network_call(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("cross-batch cache should avoid MinerU call")

    monkeypatch.setattr(parse_mineru_pdf, "_parse_agent", unexpected_network_call)
    manifest = parse_mineru_pdf.parse_local_pdf(
        pdf,
        outdir=tmp_path / "new-cache",
        mode="agent",
        cached_manifest=old_manifest,
    )

    assert manifest["cache_reused_from"] == str(old_manifest)
    assert Path(manifest["markdown_path"]).read_text(encoding="utf-8") == "# Cached\n"


def test_cache_index_prefers_precise_over_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 PDF 同时有 Precise/Agent 缓存时应优先 Precise。"""

    data_dir = tmp_path / "data"
    digest = "a" * 64
    manifests = {}
    for mode in ("agent", "precise"):
        cache_dir = data_dir / "batch" / "markdown" / "固收" / mode
        cache_dir.mkdir(parents=True)
        markdown = cache_dir / "full.md"
        markdown.write_text(mode, encoding="utf-8")
        manifest = cache_dir / "manifest.json"
        manifest.write_text(
            parse_mineru_pdf.json.dumps(
                {
                    "status": "done",
                    "mode": mode,
                    "model_version": "vlm" if mode == "precise" else "pipeline-lightweight",
                    "source_sha256": digest,
                    "markdown_path": str(markdown),
                }
            ),
            encoding="utf-8",
        )
        manifests[mode] = manifest
    monkeypatch.setattr(run_all_mineru, "DATA_DIR", data_dir)

    index = run_all_mineru.build_cache_index(
        tmp_path / "current",
        requested_mode="auto",
        model="vlm",
    )

    assert index[digest] == manifests["precise"]
