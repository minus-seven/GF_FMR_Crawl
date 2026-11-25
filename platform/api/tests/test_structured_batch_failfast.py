"""Batch structured extraction should stop fast on account-level provider errors."""

from pathlib import Path
import json
import sys

import pytest

CODE_DIR = Path(__file__).resolve().parents[3] / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from structured_data import qwen_client, run_batch


def test_provider_unavailable_reason_matches_account_errors() -> None:
    """余额、欠费和认证错误应被识别为供应商不可用。"""

    assert qwen_client.provider_unavailable_reason(
        "DeepSeek Responses API HTTP 402: {\"error\":\"Insufficient Balance\"}"
    ) == "insufficient balance"
    assert qwen_client.provider_unavailable_reason("account is in arrearage") == "arrearage"
    assert qwen_client.provider_unavailable_reason("HTTP 401 invalid key") == "http 401"
    assert qwen_client.provider_unavailable_reason("network timeout") is None


def test_retry_call_does_not_retry_fatal_account_errors() -> None:
    """账户级错误不应触发重试退避；参数为空。"""

    calls = {"count": 0}

    def failing() -> None:
        """模拟余额不足；参数为空。"""

        calls["count"] += 1
        raise qwen_client.QwenClientError("HTTP 402: Insufficient Balance")

    with pytest.raises(qwen_client.QwenClientError):
        qwen_client.retry_call(failing, attempts=3, base_delay=0)
    assert calls["count"] == 1


def test_run_batch_stops_remaining_pdfs_after_provider_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """首个账户级失败后应跳过剩余 PDF 并返回退出码 2。"""

    pdfs = [tmp_path / f"{name}.pdf" for name in ("a", "b", "c")]
    calls: list[str] = []

    def fake_discover(_paths: list[Path]) -> list[Path]:
        """返回固定的三份 PDF；参数 _paths 被忽略。"""

        return pdfs

    def fake_extract_pdf(**kwargs: object) -> dict:
        """第一份返回余额不足失败，其余不应被调用。"""

        pdf_path = Path(str(kwargs["pdf_path"]))
        calls.append(pdf_path.name)
        return {
            "status": "failed",
            "data": {},
            "_meta": {
                "source_file": str(pdf_path),
                "failures": [
                    "deepseek-flash/mineru: QwenClientError: DeepSeek Responses "
                    "API HTTP 402: {\"error\":\"Insufficient Balance\"}"
                ],
            },
        }

    monkeypatch.setattr(run_batch, "discover_pdfs", fake_discover)
    monkeypatch.setattr(run_batch, "extract_pdf", fake_extract_pdf)
    monkeypatch.setattr(run_batch, "write_outputs", lambda *a, **k: {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_batch.py",
            "--workers",
            "1",
            "--per-pdf-outdir",
            str(tmp_path / "out"),
            "--combined-outdir",
            str(tmp_path / "combined"),
        ],
    )

    exit_code = run_batch.main()
    output = capsys.readouterr().out
    summary = json.loads(output[output.index('{\n  "pdf_count"'):])

    assert calls[0] == "a.pdf"
    assert len(calls) <= 2
    assert exit_code == 2
    assert summary["skipped_count"] >= 1
    assert summary["failure_count"] + summary["skipped_count"] == 3
    assert summary["provider_unavailable"] == "insufficient balance"


def test_run_batch_invalidates_same_name_cache_when_pdf_sha_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同名 PDF 内容变化时，--skip-existing 不能继续返回旧结构化结果。"""

    pdf = tmp_path / "same-name.pdf"
    pdf.write_bytes(b"%PDF-old")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    output = output_dir / "same-name.fixed_income.json"
    old_meta = {
        "status": "ok",
        "data": {"fund_name": "旧报告"},
        "_meta": {"source_file": str(pdf), "source_sha256": "old-sha"},
    }
    output.write_text(json.dumps(old_meta["data"]), encoding="utf-8")
    output.with_suffix(".meta.json").write_text(
        json.dumps(old_meta), encoding="utf-8"
    )
    calls: list[str] = []

    def fake_extract_pdf(**kwargs: object) -> dict:
        calls.append(str(kwargs["pdf_path"]))
        return {
            "status": "ok",
            "data": {"fund_name": "新报告"},
            "_meta": {"source_file": str(pdf)},
        }

    monkeypatch.setattr(run_batch, "extract_pdf", fake_extract_pdf)
    monkeypatch.setattr(run_batch, "write_outputs", lambda *a, **k: {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_batch.py",
            str(pdf),
            "--skip-existing",
            "--workers",
            "1",
            "--per-pdf-outdir",
            str(output_dir),
            "--combined-outdir",
            str(tmp_path / "combined"),
        ],
    )

    assert run_batch.main() == 0
    assert calls == [str(pdf)]
    refreshed = json.loads(output.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert refreshed["_meta"]["source_sha256"] != "old-sha"
