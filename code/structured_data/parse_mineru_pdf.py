#!/usr/bin/env python
"""Parse one local PDF with MinerU and persist inspectable artifacts.

The script supports both MinerU's token-authenticated precise API and its
token-free Agent API.  It is intentionally standalone so the existing fund
extraction pipeline can be compared before it is changed.
"""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import html
import json
import os
import random
import re
import shutil
import sys
import threading
import time
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pypdf import PdfReader

import requests

try:
    from app_config import value as config_value
except ImportError:  # pragma: no cover - standalone legacy invocation
    config_value = None


MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("FACTSHEET_PROJECT_ROOT") or MODULE_DIR.parents[1]).resolve()
DEFAULT_OUTDIR = Path(
    os.environ.get("FACTSHEET_MINERU_OUTDIR")
    or PROJECT_ROOT / "data" / "_mineru"
).resolve()
API_ROOT = (
    os.environ.get("MINERU_API_URL")
    or (config_value("api", "mineru_api_url", "") if config_value else "")
    or "https://mineru.net/api"
).rstrip("/")


class SlidingWindowRateLimiter:
    """线程安全的滑动窗口限流器。"""

    def __init__(
        self,
        *,
        limit: int,
        period_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """初始化限流器；参数 limit/period/clock/sleeper 为窗口和测试时钟。"""

        self.limit = max(1, int(limit))
        self.period_seconds = max(0.1, float(period_seconds))
        self.clock = clock
        self.sleeper = sleeper
        self.lock = threading.Lock()
        self.timestamps: deque[float] = deque()

    def acquire(self) -> None:
        """占用一个提交额度，超出窗口时阻塞；参数为空。"""

        while True:
            with self.lock:
                now = self.clock()
                threshold = now - self.period_seconds
                while self.timestamps and self.timestamps[0] <= threshold:
                    self.timestamps.popleft()
                if len(self.timestamps) < self.limit:
                    self.timestamps.append(now)
                    return
                wait_seconds = (
                    self.timestamps[0] + self.period_seconds - now + 0.05
                )
            self.sleeper(max(0.05, wait_seconds))


MINERU_UPLOAD_LIMITER = SlidingWindowRateLimiter(
    limit=max(1, int(os.environ.get("MINERU_UPLOAD_RATE_PER_MINUTE", "50"))),
    period_seconds=60,
)
MINERU_RESULT_LIMITER = SlidingWindowRateLimiter(
    limit=max(1, int(os.environ.get("MINERU_RESULT_RATE_PER_MINUTE", "450"))),
    period_seconds=60,
)
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_COOLDOWN_UNTIL = 0.0
_RATE_LIMIT_MAX_ATTEMPTS = max(
    1,
    int(os.environ.get("MINERU_RATE_LIMIT_RETRIES", "8")),
)

try:
    if __package__:
        from . import local_config
    else:
        sys.path.insert(0, str(MODULE_DIR))
        import local_config
except (ImportError, SyntaxError):  # pragma: no cover - optional local file
    local_config = None


class MinerUError(RuntimeError):
    """Raised when MinerU returns an unusable response or artifact."""


def _parse_native_pdf_text(
    pdf_path: Path,
    output_dir: Path,
    *,
    reason: str,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create a MinerU-compatible Markdown bundle from a PDF text layer.

    This is intentionally a last-resort parser: it does not claim OCR or table
    reconstruction.  It keeps the download→parse→structure contract alive when
    the remote MinerU endpoint is unavailable, while the structured extractor
    can still use native text and the original PDF for evidence.
    """

    markdown_path = output_dir / "full.md"
    pages = PdfReader(str(pdf_path)).pages
    lines = ["# Native PDF text fallback", ""]
    for number, page in enumerate(pages, start=1):
        text = (page.extract_text() or "").strip()
        lines.extend([f"## Page {number}", "", text or "（本页无文字层）", ""])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    _emit_log(f"MinerU 外部端点不可用，改用 PDF 原生文字层：{reason}", log_callback)
    return {
        "mode": "native",
        "model_version": "native-pdf-text",
        "markdown_path": str(markdown_path),
        "fallback_reason": reason,
    }


def _emit_log(
    message: str,
    callback: Callable[[str], None] | None = None,
) -> None:
    """输出 MinerU 状态；参数 message/callback 为文本和可选收集器。"""

    if callback is None:
        print(message, flush=True)
    else:
        callback(message)


def _configured_token() -> str:
    local_token = getattr(local_config, "MINERU_API_TOKEN", "") if local_config else ""
    unified_token = config_value("api", "mineru_api_token", "") if config_value else ""
    return (os.environ.get("MINERU_API_TOKEN") or unified_token or local_token or "").strip()


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" ._")
    return cleaned[:120] or "document"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _restore_external_cache(
    manifest_path: Path,
    *,
    output_dir: Path,
    pdf_path: Path,
    requested_mode: str,
    model: str,
) -> Path | None:
    """复制历史 MinerU 缓存；参数 manifest/output/pdf/mode/model 为缓存上下文。"""

    try:
        cached = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cached_mode = str(cached.get("mode") or "")
    if cached.get("status") != "done":
        return None
    if requested_mode in {"precise", "agent"} and cached_mode != requested_mode:
        return None
    if cached_mode == "precise" and cached.get("model_version") != model:
        return None
    markdown_value = cached.get("markdown_path")
    if not markdown_value:
        return None
    source_dir = manifest_path.parent
    markdown_path = Path(str(markdown_value))
    if not markdown_path.is_file():
        return None
    shutil.copytree(source_dir, output_dir, dirs_exist_ok=True)

    def remap(value: Any) -> str | None:
        """把缓存绝对路径映射到新目录；参数 value 为旧路径。"""

        if not value:
            return None
        old_path = Path(str(value))
        try:
            relative = old_path.relative_to(source_dir)
        except ValueError:
            relative = Path(old_path.name)
        return str((output_dir / relative).resolve())

    for key in ("markdown_path", "artifacts_dir", "zip_path"):
        cached[key] = remap(cached.get(key))
    cached["source_pdf"] = str(pdf_path)
    cached["cache_reused_from"] = str(manifest_path)
    target_manifest = output_dir / "manifest.json"
    target_manifest.write_text(
        json.dumps(cached, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not Path(str(cached.get("markdown_path") or "")).is_file():
        return None
    return target_manifest


def _request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: int,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """发送带瞬时错误退避的 JSON 请求；参数 session/method/url 等为请求配置。"""

    response = None
    result_endpoint = "/extract-results/" in url or (
        method.upper() == "GET" and "/agent/parse/" in url
    )
    for attempt in range(1, _RATE_LIMIT_MAX_ATTEMPTS + 1):
        _wait_rate_limit_cooldown()
        if result_endpoint:
            MINERU_RESULT_LIMITER.acquire()
        response = session.request(
            method,
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        if response.status_code == 429:
            _register_rate_limit(response, attempt)
            if attempt < _RATE_LIMIT_MAX_ATTEMPTS:
                continue
            break
        if response.status_code not in {500, 502, 503, 504}:
            break
        retry_after = response.headers.get("Retry-After", "")
        try:
            delay = float(retry_after)
        except ValueError:
            delay = float(attempt * 2)
        time.sleep(min(max(delay, 1.0), 30.0) + random.uniform(0, 0.5))
    assert response is not None
    if response.status_code >= 400:
        raise MinerUError(f"HTTP {response.status_code} from {url}: {response.text[:800]}")
    try:
        body = response.json()
    except ValueError as exc:
        raise MinerUError(f"Non-JSON response from {url}: {response.text[:800]}") from exc
    if body.get("code") != 0:
        raise MinerUError(
            f"MinerU error {body.get('code')} from {url}: {body.get('msg') or body}"
        )
    data = body.get("data")
    if not isinstance(data, dict):
        raise MinerUError(f"Unexpected MinerU response from {url}: {body}")
    return data


def _wait_rate_limit_cooldown() -> None:
    """等待全局限流冷却结束；参数为空。"""

    with _RATE_LIMIT_LOCK:
        wait_seconds = _RATE_LIMIT_COOLDOWN_UNTIL - time.monotonic()
    if wait_seconds > 0:
        time.sleep(wait_seconds + random.uniform(0, 0.5))


def _register_rate_limit(
    response: requests.Response,
    attempt: int,
) -> None:
    """登记全批次 429 冷却；参数 response/attempt 为响应和尝试次数。"""

    global _RATE_LIMIT_COOLDOWN_UNTIL

    retry_after = response.headers.get("Retry-After", "")
    try:
        delay = float(retry_after)
    except ValueError:
        delay = min(60.0, 8.0 * (2 ** (attempt - 1)))
    cooldown_until = time.monotonic() + min(max(delay, 3.0), 60.0)
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_COOLDOWN_UNTIL = max(
            _RATE_LIMIT_COOLDOWN_UNTIL,
            cooldown_until,
        )


def _put_file(session: requests.Session, upload_url: str, pdf_path: Path, timeout: int) -> None:
    """上传签名文件并退避瞬时错误；参数 session/upload_url/pdf_path/timeout 为上传配置。"""

    response = None
    for attempt in range(1, 4):
        with pdf_path.open("rb") as handle:
            response = session.put(upload_url, data=handle, timeout=timeout)
        if response.status_code not in {429, 500, 502, 503, 504}:
            break
        retry_after = response.headers.get("Retry-After", "")
        try:
            delay = float(retry_after)
        except ValueError:
            delay = float(attempt * 2)
        time.sleep(min(max(delay, 1.0), 30.0))
    assert response is not None
    if response.status_code >= 400:
        raise MinerUError(
            f"MinerU signed upload failed with HTTP "
            f"{response.status_code}: {response.text[:800]}"
        )


def _download(session: requests.Session, url: str, target: Path, timeout: int) -> None:
    with session.get(url, stream=True, timeout=timeout) as response:
        if response.status_code >= 400:
            raise MinerUError(f"Artifact download failed with HTTP {response.status_code}: {response.text[:800]}")
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def _extract_zip_safely(zip_path: Path, target_dir: Path) -> None:
    target_root = target_dir.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for item in archive.infolist():
            item_target = (target_dir / item.filename).resolve()
            if item_target != target_root and target_root not in item_target.parents:
                raise MinerUError(f"Unsafe path in MinerU ZIP: {item.filename}")
        archive.extractall(target_dir)


def _poll_precise(
    session: requests.Session,
    batch_id: str,
    headers: dict[str, str],
    *,
    request_timeout: int,
    poll_timeout: int,
    interval: float,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + poll_timeout
    url = f"{API_ROOT}/v4/extract-results/batch/{batch_id}"
    while time.monotonic() < deadline:
        data = _request_json(session, "GET", url, headers=headers, timeout=request_timeout)
        results = data.get("extract_result")
        if isinstance(results, list) and results:
            result = results[0]
            state = result.get("state")
            _emit_log(f"MinerU precise state: {state}", log_callback)
            if state == "done":
                return result
            if state == "failed":
                raise MinerUError(
                    f"MinerU precise parsing failed: {result.get('err_code')} {result.get('err_msg')}"
                )
        time.sleep(interval)
    raise MinerUError(f"MinerU precise parsing timed out after {poll_timeout}s (batch_id={batch_id})")


def _parse_precise(
    session: requests.Session,
    pdf_path: Path,
    output_dir: Path,
    *,
    token: str,
    model: str,
    language: str,
    is_ocr: bool,
    request_timeout: int,
    poll_timeout: int,
    interval: float,
    data_id: str,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    MINERU_UPLOAD_LIMITER.acquire()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    submitted = _request_json(
        session,
        "POST",
        f"{API_ROOT}/v4/file-urls/batch",
        headers=headers,
        timeout=request_timeout,
        payload={
            "files": [{"name": pdf_path.name, "data_id": data_id, "is_ocr": is_ocr}],
            "model_version": model,
            "language": language,
            "enable_table": True,
            "enable_formula": True,
        },
    )
    batch_id = str(submitted.get("batch_id") or "")
    upload_urls = submitted.get("file_urls")
    if not batch_id or not isinstance(upload_urls, list) or not upload_urls:
        raise MinerUError(f"Missing batch_id or upload URL: {submitted}")
    _put_file(session, str(upload_urls[0]), pdf_path, request_timeout)
    result = _poll_precise(
        session,
        batch_id,
        headers,
        request_timeout=request_timeout,
        poll_timeout=poll_timeout,
        interval=interval,
        log_callback=log_callback,
    )
    artifact_url = str(result.get("full_zip_url") or "")
    if not artifact_url:
        raise MinerUError(f"Completed precise task has no full_zip_url: {result}")
    zip_path = output_dir / "mineru_output.zip"
    _download(session, artifact_url, zip_path, request_timeout)
    if not zipfile.is_zipfile(zip_path):
        raise MinerUError("MinerU precise artifact is not a valid ZIP")
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    _extract_zip_safely(zip_path, artifacts_dir)
    markdown_files = sorted(artifacts_dir.rglob("full.md")) or sorted(artifacts_dir.rglob("*.md"))
    markdown_path = markdown_files[0] if markdown_files else None
    return {
        "mode": "precise",
        "model_version": model,
        "batch_id": batch_id,
        "artifact_url": artifact_url,
        "zip_path": str(zip_path),
        "artifacts_dir": str(artifacts_dir),
        "markdown_path": str(markdown_path) if markdown_path else None,
    }


def _poll_agent(
    session: requests.Session,
    task_id: str,
    *,
    request_timeout: int,
    poll_timeout: int,
    interval: float,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + poll_timeout
    url = f"{API_ROOT}/v1/agent/parse/{task_id}"
    while time.monotonic() < deadline:
        data = _request_json(session, "GET", url, timeout=request_timeout)
        state = data.get("state")
        _emit_log(f"MinerU agent state: {state}", log_callback)
        if state == "done":
            return data
        if state == "failed":
            raise MinerUError(f"MinerU Agent parsing failed: {data.get('err_code')} {data.get('err_msg')}")
        time.sleep(interval)
    raise MinerUError(f"MinerU Agent parsing timed out after {poll_timeout}s (task_id={task_id})")


def _parse_agent(
    session: requests.Session,
    pdf_path: Path,
    output_dir: Path,
    *,
    language: str,
    is_ocr: bool,
    request_timeout: int,
    poll_timeout: int,
    interval: float,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    MINERU_UPLOAD_LIMITER.acquire()
    submitted = _request_json(
        session,
        "POST",
        f"{API_ROOT}/v1/agent/parse/file",
        timeout=request_timeout,
        payload={
            "file_name": pdf_path.name,
            "language": language,
            "enable_table": True,
            "enable_formula": True,
            "is_ocr": is_ocr,
        },
    )
    task_id = str(submitted.get("task_id") or "")
    upload_url = str(submitted.get("file_url") or "")
    if not task_id or not upload_url:
        raise MinerUError(f"Missing task_id or upload URL: {submitted}")
    _put_file(session, upload_url, pdf_path, request_timeout)
    result = _poll_agent(
        session,
        task_id,
        request_timeout=request_timeout,
        poll_timeout=poll_timeout,
        interval=interval,
        log_callback=log_callback,
    )
    markdown_url = str(result.get("markdown_url") or "")
    if not markdown_url:
        raise MinerUError(f"Completed Agent task has no markdown_url: {result}")
    markdown_path = output_dir / "full.md"
    _download(session, markdown_url, markdown_path, request_timeout)
    return {
        "mode": "agent",
        "model_version": "pipeline-lightweight",
        "task_id": task_id,
        "artifact_url": markdown_url,
        "markdown_path": str(markdown_path),
    }


def parse_pdf(args: argparse.Namespace) -> Path:
    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file: {pdf_path}")
    with pdf_path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError(f"File does not start with a PDF header: {pdf_path}")

    file_hash = _sha256(pdf_path)
    output_dir = args.outdir.resolve() / f"{_safe_name(pdf_path.stem)}__{file_hash[:12]}"
    output_dir.mkdir(parents=True, exist_ok=True)
    token = args.token or _configured_token()
    log_callback = getattr(args, "log_callback", None)
    requested_mode = args.mode
    mode = args.mode
    if mode == "auto":
        mode = "precise" if token else "agent"
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists() and not args.force:
        try:
            cached = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = {}
        cached_markdown = cached.get("markdown_path")
        cached_mode = cached.get("mode")
        same_parser = cached_mode == mode or (
            requested_mode == "auto" and cached_mode in {"precise", "agent", "native"}
        )
        same_model = (
            cached_mode != "precise"
            or cached.get("model_version") == args.model
        )
        if (
            cached.get("status") == "done"
            and same_parser
            and same_model
            and cached_markdown
            and Path(cached_markdown).is_file()
        ):
            print(f"Using cached MinerU result: {manifest_path}")
            return manifest_path
    external_manifest = getattr(args, "cached_manifest", None)
    if external_manifest:
        restored = _restore_external_cache(
            Path(external_manifest),
            output_dir=output_dir,
            pdf_path=pdf_path,
            requested_mode=requested_mode,
            model=args.model,
        )
        if restored is not None:
            print(f"Using cross-batch MinerU cache: {external_manifest}")
            return restored
    if getattr(args, "cache_only", False):
        raise MinerUError(
            f"未找到可用的 MinerU 缓存：{pdf_path.name}。请先运行“一键 MinerU 转换”。"
        )
    if mode == "precise" and not token:
        raise MinerUError("缺少 MinerU Token，请双击“配置API.bat”填写 mineru_api_token")
    source_copy = output_dir / "source.pdf"
    if not source_copy.exists():
        shutil.copy2(pdf_path, source_copy)

    session = requests.Session()
    session.trust_env = os.environ.get("MINERU_TRUST_ENV", "").strip().lower() in {
        "1", "true", "yes"
    }
    started = datetime.now(timezone.utc)
    try:
        if mode == "precise":
            try:
                parsed = _parse_precise(
                    session,
                    pdf_path,
                    output_dir,
                    token=token,
                    model=args.model,
                    language=args.language,
                    is_ocr=args.ocr,
                    request_timeout=args.request_timeout,
                    poll_timeout=args.poll_timeout,
                    interval=args.interval,
                    data_id=file_hash,
                    log_callback=log_callback,
                )
            except MinerUError as exc:
                if requested_mode != "auto":
                    raise
                fallback_reason = f"{type(exc).__name__}: {exc}"
                _emit_log(
                    f"MinerU precise 不可用，自动降级到 Agent API：{fallback_reason}",
                    log_callback,
                )
                parsed = _parse_agent(
                    session,
                    pdf_path,
                    output_dir,
                    language=args.language,
                    is_ocr=args.ocr,
                    request_timeout=args.request_timeout,
                    poll_timeout=args.poll_timeout,
                    interval=args.interval,
                    log_callback=log_callback,
                )
                parsed["fallback_from"] = "precise"
                parsed["fallback_reason"] = fallback_reason
        else:
            parsed = _parse_agent(
                session,
                pdf_path,
                output_dir,
                language=args.language,
                is_ocr=args.ocr,
                request_timeout=args.request_timeout,
                poll_timeout=args.poll_timeout,
                interval=args.interval,
                log_callback=log_callback,
            )
    except (requests.RequestException, ConnectionError, OSError) as exc:
        if requested_mode not in {"auto", "agent"}:
            raise
        parsed = _parse_native_pdf_text(
            pdf_path,
            output_dir,
            reason=f"{type(exc).__name__}: {exc}",
            log_callback=log_callback,
        )

    markdown_path = Path(parsed["markdown_path"]) if parsed.get("markdown_path") else None
    manifest = {
        "status": "done",
        "source_pdf": str(pdf_path),
        "source_sha256": file_hash,
        "source_size_bytes": pdf_path.stat().st_size,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "language": args.language,
        "is_ocr": args.ocr,
        "markdown_size_bytes": markdown_path.stat().st_size if markdown_path and markdown_path.exists() else None,
        **parsed,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def parse_local_pdf(
    pdf_path: Path,
    *,
    outdir: Path = DEFAULT_OUTDIR,
    mode: str = "precise",
    model: str = "vlm",
    language: str = "ch",
    is_ocr: bool = False,
    force: bool = False,
    request_timeout: int = 180,
    poll_timeout: int = 900,
    interval: float = 3.0,
    cache_only: bool = False,
    log_callback: Callable[[str], None] | None = None,
    cached_manifest: Path | None = None,
) -> dict[str, Any]:
    """Parse a local PDF and return its persisted MinerU manifest."""
    args = argparse.Namespace(
        pdf=Path(pdf_path),
        mode=mode,
        token="",
        model=model,
        language=language,
        ocr=is_ocr,
        force=force,
        outdir=Path(outdir),
        request_timeout=request_timeout,
        poll_timeout=poll_timeout,
        interval=interval,
        cache_only=cache_only,
        log_callback=log_callback,
        cached_manifest=cached_manifest,
    )
    manifest_path = parse_pdf(args)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _extract_report_month_hint(
    content_items: list[dict[str, Any]],
    header_footer_texts: list[str],
) -> str | None:
    """Find an explicit report/as-of month without interpreting unrelated dates."""
    candidates: list[tuple[int, int, str]] = []
    negative_terms = ("成立", "推出", "发行", "發行", "launch", "award", "奖", "獎", "表现", "表現")

    def add_candidate(text: str, order: int, *, is_header: bool) -> None:
        compact = " ".join(unicodedata.normalize("NFKC", text).split())
        compact = re.sub(r"</?[^>]+>", "", compact)
        if not compact:
            return
        lower = compact.lower()
        negative = any(term in lower for term in negative_terms)
        patterns = [
            (r"月[報报]\s*(\d{1,2})/(\d{1,2})/(20\d{2})", 120, (3, 2)),
            (
                r"(?:截至|截止|資料截至|资料截至|as\s+of|data\s+as\s+at|as\s+at)"
                r".{0,30}?(\d{1,2})/(\d{1,2})/(20\d{2})",
                115,
                (3, 2),
            ),
            (
                r"(20\d{2})\s*年\s*(\d{1,2})\s*月"
                r"(?:\s*[（(].{0,20}?(?:截至|截止))?",
                100,
                (1, 2),
            ),
        ]
        for pattern, base_score, groups in patterns:
            for match in re.finditer(pattern, compact, flags=re.IGNORECASE):
                try:
                    year = int(match.group(groups[0]))
                    month = int(match.group(groups[1]))
                except (TypeError, ValueError):
                    continue
                if not (2000 <= year <= 2099 and 1 <= month <= 12):
                    continue
                score = base_score + (10 if is_header else 0)
                if re.search(r"截至|截止|as\s+of|as\s+at", compact, flags=re.IGNORECASE):
                    score += 10
                if re.fullmatch(r"\s*20\d{2}\s*年\s*\d{1,2}\s*月\s*", compact):
                    score += 15
                if negative:
                    score -= 80
                candidates.append((score, -order, f"{year:04d}-{month:02d}"))

    for order, text in enumerate(header_footer_texts):
        add_candidate(text, order, is_header=True)
    for order, item in enumerate(content_items[:80]):
        if item.get("type") not in {"text", "header", "footer"}:
            continue
        add_candidate(str(item.get("text") or ""), order, is_header=item.get("type") in {"header", "footer"})

    return max(candidates)[2] if candidates else None


def _context_mode() -> str:
    """读取上下文模式；参数为空，返回 compact/full。"""

    value = os.environ.get("FACTSHEET_CONTEXT_MODE", "compact").strip().casefold()
    return value if value in {"compact", "full"} else "compact"


def _plain_text(value: Any) -> str:
    """去掉 HTML 标签并压缩空白，仅用于去重判断，不替换原文。"""

    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact_key(value: Any) -> str:
    """返回不含空白的比较键，避免换行差异造成重复上下文。"""

    return re.sub(r"\s+", "", _plain_text(value)).casefold()


def _content_item_text(item: dict[str, Any]) -> str:
    """提取 content_list 块的可读正文；保留表格 HTML 以便模型读列。"""

    kind = str(item.get("type") or "text")
    if kind == "table":
        body = str(item.get("table_body") or item.get("text") or "").strip()
        captions = " ".join(
            str(value)
            for key in ("table_caption", "table_footnote")
            for value in (item.get(key) or [])
            if str(value).strip()
        )
        return f"{captions}\n{body}".strip()
    if kind == "chart":
        captions = " ".join(
            str(value)
            for key in ("chart_caption", "chart_footnote")
            for value in (item.get(key) or [])
            if str(value).strip()
        )
        content = str(item.get("content") or "").strip()
        return f"{captions}\n{content}".strip()
    return str(item.get("text") or "").strip()


def _is_covered_by_projection(plain: str, projection: str) -> bool:
    """判断 Markdown 块是否已被 content_list 投影覆盖。"""

    key = _compact_key(plain)
    if not key:
        return True
    if key in projection:
        return True
    if len(key) < 80:
        return False
    shingles = [key[index : index + 24] for index in range(0, len(key) - 24, 24)]
    if not shingles:
        return False
    hits = sum(1 for shingle in shingles if shingle in projection)
    return hits / len(shingles) >= 0.8


def _compact_mineru_context(
    *,
    markdown: str,
    content_items: list[dict[str, Any]],
    header_footer: str,
    report_month_hint: str | None,
) -> str:
    """用“页标记 + content_list 正文投影”替代重复的 content_list JSON。

    参数 markdown/content_items/header_footer/report_month_hint 为 MinerU 产物。
    content_list 保留页码和表格 HTML；Markdown 中未被投影覆盖的块再补到
    ``[MARKDOWN_SUPPLEMENT]``，因此不会为了省 token 丢掉正文。
    """

    lines = [
        "以下内容由 MinerU 从同一份 PDF 解析而来。只能依据这些内容抽取，不要猜测。",
        "",
        "[REPORT_MONTH_HINT]",
        report_month_hint or "",
        "",
        "[HEADER_FOOTER]",
        header_footer,
        "",
        "[DOCUMENT_PAGE_TEXT]",
    ]
    page_texts: list[str] = []
    current_page: int | None = None
    block_index = 1 if report_month_hint and str(report_month_hint).strip() else 0
    for item in content_items:
        if not isinstance(item, dict) or item.get("type") in {"header", "footer"}:
            continue
        text = _content_item_text(item)
        if not text:
            continue
        try:
            page = int(item.get("page_idx") or 0)
        except (TypeError, ValueError):
            page = 0
        if page != current_page:
            lines.append(f"[PAGE {page}]")
            current_page = page
        lines.append(f"[BLOCK {block_index}]")
        lines.append(text)
        page_texts.append(text)
        block_index += 1

    projection = _compact_key(" ".join(page_texts))
    supplement: list[str] = []
    for block in re.split(r"\n{2,}", markdown):
        block = block.strip()
        plain = _plain_text(block)
        if len(plain) < 8 or _is_covered_by_projection(plain, projection):
            continue
        page = None
        for item in content_items:
            if not isinstance(item, dict):
                continue
            if _compact_key(plain)[:24] and _compact_key(plain)[:24] in _compact_key(_content_item_text(item)):
                try:
                    page = int(item.get("page_idx") or 0)
                except (TypeError, ValueError):
                    page = None
                break
        supplement.append(f"[PAGE {page if page is not None else 'unknown'}]\n{block}")

    if supplement:
        lines.extend(["", "[MARKDOWN_SUPPLEMENT]", *supplement])
    return "\n".join(lines).strip()


def load_mineru_bundle(manifest: dict[str, Any], *, max_context_chars: int = 180_000) -> dict[str, Any]:
    """Load model-ready text plus structured artifact and image paths."""
    markdown_value = manifest.get("markdown_path")
    if not markdown_value:
        raise MinerUError("MinerU manifest has no markdown_path")
    markdown_path = Path(str(markdown_value))
    if not markdown_path.is_file():
        raise MinerUError(f"MinerU Markdown not found: {markdown_path}")

    artifacts_value = manifest.get("artifacts_dir")
    artifacts_dir = Path(str(artifacts_value)) if artifacts_value else markdown_path.parent
    content_candidates = sorted(artifacts_dir.glob("*_content_list.json"))
    if not content_candidates:
        content_candidates = sorted(artifacts_dir.glob("*_content_list_v2.json"))
    content_list_path = content_candidates[0] if content_candidates else None
    images_dir = artifacts_dir / "images"
    all_image_paths = (
        sorted(path for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
        if images_dir.is_dir()
        else []
    )

    markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
    image_by_name = {path.name: path for path in all_image_paths}
    referenced_names = re.findall(r"\(images/([^\)]+)\)", markdown)
    image_paths = [image_by_name[name] for name in referenced_names if name in image_by_name]
    image_paths.extend(path for path in all_image_paths if path not in image_paths)
    content_list = (
        content_list_path.read_text(encoding="utf-8", errors="replace")
        if content_list_path
        else "[]"
    )
    header_footer_texts: list[str] = []
    content_items: list[dict[str, Any]] = []
    try:
        parsed_items = json.loads(content_list)
        if isinstance(parsed_items, list):
            content_items = [item for item in parsed_items if isinstance(item, dict)]
            for item in content_items:
                if not isinstance(item, dict) or item.get("type") not in {"header", "footer"}:
                    continue
                text_value = str(item.get("text") or "").strip()
                if text_value and text_value not in header_footer_texts:
                    header_footer_texts.append(text_value)
    except json.JSONDecodeError:
        pass
    header_footer = "\n".join(header_footer_texts)
    report_month_hint = _extract_report_month_hint(content_items, header_footer_texts)
    full_context = (
        "以下内容由 MinerU 从同一份 PDF 解析而来。只能依据这些内容抽取，不要猜测。\n\n"
        "[REPORT_MONTH_HINT]\n"
        f"{report_month_hint or ''}\n\n"
        "[HEADER_FOOTER]\n"
        f"{header_footer}\n\n"
        "[MARKDOWN]\n"
        f"{markdown}\n\n"
        "[CONTENT_LIST_JSON]\n"
        f"{content_list}"
    )
    mode = _context_mode()
    context = (
        _compact_mineru_context(
            markdown=markdown,
            content_items=content_items,
            header_footer=header_footer,
            report_month_hint=report_month_hint,
        )
        if mode == "compact" and content_items
        else full_context
    )
    if len(context) > max_context_chars:
        context = context[:max_context_chars] + "\n[内容因长度限制截断]"
    return {
        "context": context,
        "context_full": full_context,
        "context_mode": mode if content_items else "full",
        "context_chars": len(context),
        "context_full_chars": len(full_context),
        "context_sha256": hashlib.sha256(context.encode("utf-8")).hexdigest(),
        "context_full_sha256": hashlib.sha256(full_context.encode("utf-8")).hexdigest(),
        "markdown": markdown,
        "content_list": content_list,
        "header_footer": header_footer,
        "report_month_hint": report_month_hint,
        "markdown_path": markdown_path,
        "content_list_path": content_list_path,
        "image_paths": image_paths,
        "artifacts_dir": artifacts_dir,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse one local PDF with MinerU")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--mode", choices=("auto", "precise", "agent"), default="auto")
    parser.add_argument("--token", default="", help="MinerU token; prefer MINERU_API_TOKEN")
    parser.add_argument("--model", choices=("vlm", "pipeline"), default="vlm")
    parser.add_argument("--language", default="ch")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--force", action="store_true", help="Ignore a completed cached result")
    parser.add_argument("--cache-only", action="store_true", help="Only load a completed cache; never call MinerU")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--request-timeout", type=int, default=180)
    parser.add_argument("--poll-timeout", type=int, default=600)
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()
    try:
        manifest_path = parse_pdf(args)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
