"""Tests for ordered download source candidates and runtime fallback."""

from pathlib import Path
from types import SimpleNamespace
import threading
import uuid

from app.services.download_sources import (
    DownloadSource,
    infer_download_sources,
    sources_from_metadata,
    serialize_download_sources,
)
from app.worker.adapters import (
    DownloadSpec,
    _download_supplementary_pdf,
    _run_download,
)


def test_pinebridge_candidates_are_ordered_and_round_trip_in_metadata():
    """柏瑞应保存 PDF 直链主路径和官方基金页兜底路径。"""

    direct_url = (
        "https://assets.pinebridge.com/asset/test/"
        "PineBridge-Asian-High-Yield-Total-Return-Bond-Fund-Class-A-Units_HKC.PDF"
    )
    page_url = (
        "https://www.metlife.com/investments/zh-hk/funds/fund/"
        "?fund=pinebridge-asian-high-yield-total-return-bond-fund&unit=A"
    )
    sources = infer_download_sources(
        manager_name="柏瑞",
        monthly_report_url=page_url,
        fallback_report_url="",
        run_command=f'python download_pinebridge_factsheet.py "{direct_url}"',
    )

    assert [item.rank for item in sources] == [1, 2]
    assert [item.kind for item in sources] == ["direct_pdf", "fund_page"]
    restored = sources_from_metadata(
        {"download_candidates": serialize_download_sources(sources)}
    )
    assert restored == sources


def test_download_uses_page_fallback_when_direct_pdf_fails(
    monkeypatch,
    tmp_path: Path,
):
    """主 PDF 失败时应自动尝试第二来源，并记录一次成功兜底。"""

    direct_url = "https://assets.pinebridge.com/asset/old/fund.PDF"
    page_url = (
        "https://www.metlife.com/investments/zh-hk/funds/fund/"
        "?fund=pinebridge-global-bond-fund&unit=A"
    )
    sources = (
        DownloadSource(
            1,
            "direct_pdf",
            "high",
            direct_url,
            f'python download_pinebridge_factsheet.py "{direct_url}"',
            "primary",
        ),
        DownloadSource(
            2,
            "fund_page",
            "fallback",
            page_url,
            f'python download_pinebridge_factsheet.py "{page_url}"',
            "fallback",
        ),
    )
    spec = DownloadSpec(
        index=0,
        fund_code="BRZ231",
        fund_name="柏瑞亚洲高收益总回报债券基金",
        manager_key="manager",
        manager_name="柏瑞",
        category="固收",
        subcategory="",
        source_row=7,
        source_url=page_url,
        run_command=sources[0].run_command,
        sources=sources,
    )
    calls: list[list[str]] = []

    def fake_runner(target, *args, **kwargs):
        """模拟直链失效、基金页恢复；参数 target 为单来源下载参数。"""

        calls.append(target["args"])
        if target["args"] == [direct_url]:
            return {
                "status": "failed",
                "exception_summary": "HTTP 404",
            }
        return {
            "status": "downloaded",
            "output_file": str(tmp_path / "fund.pdf"),
        }

    monkeypatch.setattr(
        "app.worker.adapters.run_target_with_retries",
        fake_runner,
    )
    events: list[tuple[str, dict]] = []
    status, reason, result = _run_download(
        SimpleNamespace(id=uuid.uuid4()),
        spec,
        lookup={
            "download_pinebridge_factsheet.py": (
                "companies/pinebridge/download_pinebridge_factsheet.py"
            )
        },
        staging_root=tmp_path,
        log_path=tmp_path / "download.log",
        cancel_event=threading.Event(),
        event_callback=lambda event_type, payload: events.append(
            (event_type, payload)
        ),
    )

    assert status == "success"
    assert reason is None
    assert result["fallback_used"] is True
    assert [item["status"] for item in result["source_attempts"]] == [
        "failed",
        "downloaded",
    ]
    assert calls == [[direct_url], [page_url]]
    assert any(
        payload.get("phase") == "source_fallback" for _, payload in events
    )


def test_download_rejects_html_saved_as_pdf_and_uses_fallback(
    monkeypatch,
    tmp_path: Path,
):
    """官网限流返回 HTML 时不能被标成 downloaded。"""

    sources = (
        DownloadSource(1, "direct_pdf", "high", "https://bad.test/a", "python download_pinebridge_factsheet.py https://bad.test/a", "primary"),
        DownloadSource(2, "fund_page", "fallback", "https://ok.test/b", "python download_pinebridge_factsheet.py https://ok.test/b", "fallback"),
    )
    spec = DownloadSpec(
        index=0,
        fund_code="BRZ231",
        fund_name="测试基金",
        manager_key="manager",
        manager_name="柏瑞",
        category="固收",
        subcategory="",
        source_row=7,
        source_url="https://ok.test/b",
        run_command=sources[0].run_command,
        sources=sources,
    )
    bad = tmp_path / "bad.pdf"
    good = tmp_path / "good.pdf"

    def fake_runner(target, *args, **kwargs):
        if target["args"][-1].endswith("/a"):
            bad.write_bytes(b"<html>rate limited</html>")
            return {"status": "downloaded", "output_file": str(bad)}
        good.write_bytes(b"%PDF-1.7\n")
        return {"status": "downloaded", "output_file": str(good)}

    monkeypatch.setattr("app.worker.adapters.run_target_with_retries", fake_runner)
    status, reason, result = _run_download(
        SimpleNamespace(id=uuid.uuid4()),
        spec,
        lookup={"download_pinebridge_factsheet.py": "companies/pinebridge/download_pinebridge_factsheet.py"},
        staging_root=tmp_path,
        log_path=tmp_path / "download-invalid.log",
        cancel_event=threading.Event(),
        event_callback=lambda *_args: None,
    )

    assert status == "success"
    assert reason is None
    assert result["fallback_used"] is True
    assert result["source_attempts"][0]["status"] == "failed"


def test_supplementary_download_unwraps_official_iframe_pdf(monkeypatch):
    """平安等站点的同名 .pdf 页面应能解析 iframe 中的真实 PDF。"""

    page_url = "https://asset.example.test/report/Interim-Report-EN.pdf"
    pdf_url = "https://asset.example.test/userfile/report-2026.pdf"

    class FakeResponse:
        def __init__(self, url, content, content_type):
            self.url = url
            self.content = content
            self.headers = {"content-type": content_type}

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        assert kwargs["follow_redirects"] is True
        if url == page_url:
            return FakeResponse(
                url,
                f'<iframe src="{pdf_url}"></iframe>'.encode(),
                "text/html; charset=UTF-8",
            )
        assert url == pdf_url
        return FakeResponse(url, b"%PDF-1.7\n...", "application/pdf")

    monkeypatch.setattr("app.worker.adapters.httpx.get", fake_get)
    content, resolved = _download_supplementary_pdf(page_url)

    assert content.startswith(b"%PDF")
    assert resolved == pdf_url
