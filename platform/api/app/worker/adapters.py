"""Validated adapters around the existing download and model scripts."""

from __future__ import annotations

from collections import defaultdict
import concurrent.futures
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable
from urllib.parse import unquote, urljoin, urlsplit
from uuid import UUID

import httpx

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    DocumentSubject,
    DocumentVersion,
    Fund,
    FundClassification,
    FundMetric,
    FundReportSource,
    Job,
    JobFundScope,
    Manager,
)
from app.services.download_sources import (
    DOWNLOAD_CANDIDATES_KEY,
    DownloadSource,
    infer_download_sources,
    serialize_download_sources,
    sources_for_source,
)
from app.services.supplementary_pdf import prepare_target_pdf
from app.services.master_database import run_master_database


PROJECT_ROOT = get_settings().project_root
LEGACY_CODE_DIR = PROJECT_ROOT / "code"
if str(LEGACY_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(LEGACY_CODE_DIR))

from run_all_downloaders import (  # noqa: E402
    BACKFILL_SUBPROCESS_TIMEOUT,
    discover_fund_link_scripts,
    fund_link_scripts_for_sheet,
    parse_run_command,
    run_target_with_retries,
    script_lookup,
)
from app_config import (  # noqa: E402
    integer as config_integer,
    value as config_value,
)


DOWNLOAD_TIMEOUT_SECONDS = 300
DOWNLOAD_RETRIES = 2
DOWNLOAD_RETRY_DELAY_SECONDS = 5
DOWNLOAD_WORKERS = 5
PROGRESS_COMMIT_EVERY = 10
PARSE_TIMEOUT_SECONDS = int(
    os.environ.get("PLATFORM_PARSE_TIMEOUT_SECONDS", "86400")
)
STRUCTURE_TIMEOUT_SECONDS = int(
    os.environ.get("PLATFORM_STRUCTURE_TIMEOUT_SECONDS", "86400")
)
MINERU_WORKERS = max(
    1,
    min(50, config_integer("processing", "mineru_workers", 36)),
)
MODEL_WORKERS = max(
    1,
    config_integer("processing", "model_workers", 32),
)
DOWNLOAD_LOG_LOCK = threading.Lock()
STRUCTURED_CATEGORIES = {"固收", "权益", "中国精选", "混合", "货币"}


class AdapterError(RuntimeError):
    """Raised when a whitelisted adapter cannot execute safely."""


def _embedded_pdf_urls(source_url: str, content: bytes) -> list[str]:
    """从官方 HTML 包装页提取嵌入的 PDF 地址。

    部分基金管理人把 PDF 直链配置成一个同名 HTML 页面，再通过 iframe
    加载真正文件。补充来源仍应保留用户输入的页面地址作为来源，下载阶段
    只把页面中的官方 PDF 地址作为实际内容地址。
    """

    text = content.decode("utf-8", errors="replace")
    # 优先抓 iframe/embed/object，其次兼容普通链接和常见 JS 配置。
    patterns = (
        r"<(?:iframe|embed|object)\b[^>]+(?:src|data)\s*=\s*['\"]([^'\"]+)['\"]",
        r"(?:href|src|url|file|pdf)\s*[:=]\s*['\"]([^'\"]+\.pdf(?:[?#][^'\"]*)?)['\"]",
        r"https?://[^\s'\"<>]+\.pdf(?:[?#][^\s'\"<>]*)?",
    )
    candidates: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for raw in re.findall(pattern, text, flags=re.IGNORECASE):
            raw = unquote(str(raw)).strip()
            if not raw or raw.startswith(("data:", "javascript:", "#")):
                continue
            candidate = urljoin(source_url, raw)
            parsed = urlsplit(candidate)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            # iframe 的 src 可能没有 .pdf 后缀；其它来源只接受明显的 PDF URL，
            # 避免把页面导航链接当作报告递归下载。
            is_pdf_path = ".pdf" in parsed.path.lower() or ".pdf" in parsed.query.lower()
            if not is_pdf_path and not pattern.startswith(r"<"):
                continue
            if candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
    return candidates[:8]


def _download_supplementary_pdf(source_url: str) -> tuple[bytes, str]:
    """下载补充来源，兼容直接 PDF 和官方 HTML iframe 包装页。

    返回 PDF 字节及实际 PDF URL。调用方仍使用 ``source_url`` 作为用户提供的
    来源地址，实际地址写入 ``resolved_url`` 供审计和复核。
    """

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; FundResearch/1.0)",
        "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
    }
    response = httpx.get(
        source_url,
        follow_redirects=True,
        timeout=120,
        headers=headers,
    )
    response.raise_for_status()
    if response.content.startswith(b"%PDF"):
        return response.content, str(response.url)

    candidates = _embedded_pdf_urls(str(response.url), response.content)
    for candidate in candidates:
        embedded = httpx.get(
            candidate,
            follow_redirects=True,
            timeout=120,
            headers=headers,
        )
        embedded.raise_for_status()
        if embedded.content.startswith(b"%PDF"):
            return embedded.content, str(embedded.url)

    content_type = (response.headers.get("content-type") or "unknown").split(";", 1)[0]
    if candidates:
        raise ValueError(
            f"远程补充来源未返回 PDF（首响应 {content_type}，已尝试 {len(candidates)} 个嵌入地址）"
        )
    raise ValueError(f"远程补充来源不是 PDF（首响应 {content_type}，且未找到嵌入 PDF）")


def _sha256_file(path: Path) -> str:
    """返回文件内容 SHA-256；参数 path 为已下载的本地文件。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _historical_source_hashes(
    session: Session,
    *,
    fund_id: UUID,
    source_url: str,
) -> list[str]:
    """查找同基金同 URL 的历史版本哈希，避免同 URL 内容变化被静默复用。"""

    if not source_url:
        return []
    versions: dict[UUID, DocumentVersion] = {}
    for statement in (
        select(DocumentVersion)
        .join(DocumentSubject, DocumentSubject.document_version_id == DocumentVersion.id)
        .where(
            DocumentSubject.fund_id == fund_id,
            DocumentVersion.source_url == source_url,
        ),
        select(DocumentVersion)
        .join(FundMetric, FundMetric.document_version_id == DocumentVersion.id)
        .where(
            FundMetric.fund_id == fund_id,
            DocumentVersion.source_url == source_url,
        ),
    ):
        for version in session.scalars(statement):
            versions[version.id] = version
    return sorted(
        {version.sha256 for version in versions.values() if version.sha256},
    )


@dataclass(frozen=True)
class AdapterResult:
    """Result of one adapter execution.

    参数 status：success/failed/cancelled；message：审计说明；
    details：结构化结果；artifact_count：本次识别的产物数。
    """

    status: str
    message: str
    details: dict
    artifact_count: int = 0


@dataclass
class DownloadCandidate:
    """一个任务基金及其当前来源；参数对应冻结范围和数据库记录。"""

    scope: JobFundScope
    fund: Fund | None
    classification: FundClassification | None
    source: FundReportSource | None
    source_url_override: str | None = None
    run_command_override: str | None = None


@dataclass(frozen=True)
class BackfillOutcome:
    """爬虫补全结果；参数描述状态、I/J 值和原因。"""

    status: str
    monthly_report_url: str | None = None
    run_command: str | None = None
    reason: str | None = None
    script: str | None = None
    source: str = "company_script"


@dataclass(frozen=True)
class DownloadSpec:
    """线程安全下载参数；参数均为任务创建时已读取的纯值。"""

    index: int
    fund_code: str
    fund_name: str
    manager_key: str
    manager_name: str
    category: str
    subcategory: str
    source_row: int
    source_url: str
    run_command: str
    sources: tuple[DownloadSource, ...]


def write_log(path: Path, message: str) -> None:
    """向任务日志追加文本；参数 path/message 为日志路径和文本。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with DOWNLOAD_LOG_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"[{datetime.now(timezone.utc).isoformat()}] {message}\n"
            )


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """终止受控子进程；参数 process：子进程。"""

    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
            time.sleep(0.5)
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _run_fixed_process(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    cancel_event: threading.Event,
    timeout: int,
    extra_env: dict[str, str] | None = None,
) -> tuple[int | None, bool, bool]:
    """执行固定白名单命令；参数 command/cwd/log/cancel/timeout 为运行配置。"""

    env = os.environ.copy()
    env["FACTSHEET_PROJECT_ROOT"] = str(PROJECT_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["FACTSHEET_PROGRESS_PIPE"] = "1"
    env["PYTHONPATH"] = str(LEGACY_CODE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    popen_kwargs = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_handle:
        log_handle.write("\n$ " + " ".join(command) + "\n")
        log_handle.flush()
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **popen_kwargs,
        )
        started = time.monotonic()
        while process.poll() is None:
            if cancel_event.is_set():
                _terminate_process_tree(process)
                process.wait()
                return process.returncode, True, False
            if time.monotonic() - started > timeout:
                _terminate_process_tree(process)
                process.wait()
                return process.returncode, False, True
            time.sleep(0.25)
        return process.returncode, False, False


def _run_captured_process(
    command: list[str],
    *,
    log_path: Path,
    cancel_event: threading.Event,
    timeout: int,
) -> tuple[int | None, bool, bool, str]:
    """运行白名单命令并返回本次追加的日志；参数 command/log/cancel/timeout。"""

    offset = log_path.stat().st_size if log_path.exists() else 0
    code, cancelled, timed_out = _run_fixed_process(
        command,
        cwd=PROJECT_ROOT,
        log_path=log_path,
        cancel_event=cancel_event,
        timeout=timeout,
    )
    with log_path.open("rb") as handle:
        handle.seek(offset)
        output = handle.read().decode("utf-8", errors="replace")
    return code, cancelled, timed_out, output


def _backfill_command(
    script_rel: str,
    *,
    excel_path: Path,
    sheet_name: str,
    row_numbers: set[int],
    probe: bool,
) -> list[str]:
    """构造白名单补全命令；参数 script/sheet/rows/probe 为调用范围。"""

    script_path = (LEGACY_CODE_DIR / script_rel).resolve()
    try:
        script_path.relative_to(LEGACY_CODE_DIR / "companies")
    except ValueError as exc:
        raise AdapterError("补全脚本不在 companies 白名单内") from exc
    if not script_path.is_file() or not script_path.name.endswith("_fund_link.py"):
        raise AdapterError(f"补全脚本不存在或文件名非法：{script_rel}")
    return [
        sys.executable,
        str(script_path),
        "--probe" if probe else "--backfill",
        "--excel",
        str(excel_path),
        "--sheet",
        sheet_name,
        "--rows",
        ",".join(str(value) for value in sorted(row_numbers)),
    ]


def _parse_backfill_contract(
    output: str,
    *,
    expected_rows: set[int],
    script_rel: str,
) -> tuple[dict[int, BackfillOutcome], dict[int, BackfillOutcome], set[int]]:
    """解析 OK/SKIP/probe 输出；参数 output/rows/script 为脚本契约。"""

    successes: dict[int, BackfillOutcome] = {}
    skips: dict[int, BackfillOutcome] = {}
    probed: set[int] = set()
    for raw_line in output.splitlines():
        parts = raw_line.split("\t")
        if not parts:
            continue
        if parts[0] == "OK" and len(parts) >= 6:
            try:
                row = int(parts[1].strip())
            except ValueError:
                continue
            url = parts[4].strip()
            command = parts[5].strip()
            if row not in expected_rows:
                continue
            if not url.startswith(("http://", "https://")) or not command:
                skips[row] = BackfillOutcome(
                    "failed",
                    reason="补全脚本返回了不完整的月报链接或运行方式",
                    script=script_rel,
                )
                continue
            successes[row] = BackfillOutcome(
                "succeeded",
                monthly_report_url=url,
                run_command=command,
                script=script_rel,
            )
        elif parts[0] == "SKIP" and len(parts) >= 6:
            try:
                row = int(parts[1].strip())
            except ValueError:
                continue
            if row in expected_rows:
                skips.setdefault(
                    row,
                    BackfillOutcome(
                        "skipped",
                        reason=parts[5].strip() or "官网未收录，跳过",
                        script=script_rel,
                    ),
                )
        elif len(parts) >= 2 and parts[0].strip().isdigit():
            try:
                row = int(parts[0].strip())
            except ValueError:
                continue
            if row in expected_rows:
                probed.add(row)
    return successes, skips, probed


def _source_needs_backfill(source: FundReportSource) -> bool:
    """判断来源是否缺少月报链接或运行方式；参数 source 为 crawler 来源。"""

    return not str(source.monthly_report_url or "").strip() or not str(
        source.run_command or ""
    ).strip()


def _prepare_backfill_workbook(
    candidates: list[DownloadCandidate],
    *,
    job_root: Path,
    log_path: Path,
) -> Path | None:
    """复制任务级 crawler 表并清空待补全行 I/J；参数 candidates/job_root。"""

    all_sources = [
        candidate.source
        for candidate in candidates
        if candidate.source is not None
    ]
    target_sources = [
        source for source in all_sources if _source_needs_backfill(source)
    ]
    if not all_sources:
        return None
    source_path = get_settings().crawler_workbook or PROJECT_ROOT / "爬虫list.xlsx"
    if not source_path.is_file():
        write_log(log_path, "无法准备补全工作簿：爬虫list.xlsx 不存在")
        return None
    target_path = job_root / "backfill" / "爬虫list.xlsx"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source_path, target_path)
        from openpyxl import load_workbook

        workbook = load_workbook(target_path)
        try:
            for source in target_sources:
                sheet_name = str(source.source_sheet or "").strip()
                if sheet_name not in workbook.sheetnames:
                    continue
                sheet = workbook[sheet_name]
                headers = {
                    str(cell.value).strip(): index
                    for index, cell in enumerate(sheet[1])
                    if cell.value is not None
                }
                link_col = headers.get("月报链接")
                command_col = headers.get("运行方式")
                row = int(source.source_row)
                if link_col is not None:
                    sheet.cell(row, link_col + 1).value = None
                if command_col is not None:
                    sheet.cell(row, command_col + 1).value = None
            workbook.save(target_path)
        finally:
            workbook.close()
    except Exception as exc:
        write_log(
            log_path,
            f"准备任务级补全工作簿失败：{type(exc).__name__}: {exc}",
        )
        return None
    return target_path


def _record_backfill_metadata(
    source: FundReportSource,
    outcome: BackfillOutcome,
) -> None:
    """把补全状态写回来源 metadata；参数 source/outcome 为结果。"""

    metadata = dict(getattr(source, "metadata_json", None) or {})
    metadata["backfill"] = {
        "status": outcome.status,
        "script": outcome.script,
        "source": outcome.source,
        "reason": outcome.reason,
        "attempted_at": datetime.now(timezone.utc).isoformat(),
    }
    if outcome.status == "succeeded":
        metadata[DOWNLOAD_CANDIDATES_KEY] = serialize_download_sources(
            infer_download_sources(
                manager_name=str(source.manager_name_source or ""),
                monthly_report_url=outcome.monthly_report_url,
                fallback_report_url=source.fallback_report_url,
                run_command=outcome.run_command,
            )
        )
    source.metadata_json = metadata


def _write_backfill_successes(
    excel_path: Path,
    *,
    sheet_name: str,
    successes: dict[int, BackfillOutcome],
    log_path: Path,
) -> None:
    """把脚本本批 OK 结果写回任务工作簿；参数为路径、工作表和结果。"""

    if not successes:
        return
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(excel_path)
        try:
            if sheet_name not in workbook.sheetnames:
                return
            sheet = workbook[sheet_name]
            headers = {
                str(cell.value).strip(): index
                for index, cell in enumerate(sheet[1])
                if cell.value is not None
            }
            link_col = headers.get("月报链接")
            command_col = headers.get("运行方式")
            if link_col is None or command_col is None:
                raise ValueError("补全工作簿缺少“月报链接”或“运行方式”列")
            for row, outcome in successes.items():
                if outcome.monthly_report_url:
                    sheet.cell(row, link_col + 1).value = outcome.monthly_report_url
                if outcome.run_command:
                    sheet.cell(row, command_col + 1).value = outcome.run_command
            workbook.save(excel_path)
        finally:
            workbook.close()
    except Exception as exc:  # noqa: BLE001
        write_log(
            log_path,
            f"补全成功后写回任务工作簿失败：{type(exc).__name__}: {exc}",
        )


def _backfill_failure_reason(output: str, returncode: int | None) -> str:
    """从补全脚本输出提取简明失败原因；参数 output/returncode 为输出和码。"""

    for line in reversed(output.splitlines()):
        text = line.strip()
        if text.startswith("[!] 查询失败："):
            return f"补全脚本退出码 {returncode}：{text.split('：', 1)[-1]}"
    return f"补全脚本退出码 {returncode}"


def _run_probe_for_sheet(
    *,
    excel_path: Path,
    sheet_name: str,
    row_numbers: set[int],
    script_rel: str,
    log_path: Path,
    cancel_event: threading.Event,
) -> set[int]:
    """执行公司 probe，返回仍需补全的 Excel 行；参数为工作表与脚本。"""

    command = _backfill_command(
        script_rel,
        excel_path=excel_path,
        sheet_name=sheet_name,
        row_numbers=row_numbers,
        probe=True,
    )
    code, cancelled, timed_out, output = _run_captured_process(
        command,
        log_path=log_path,
        cancel_event=cancel_event,
        timeout=300,
    )
    if cancelled or timed_out or code != 0:
        return set()
    _, _, probed = _parse_backfill_contract(
        output,
        expected_rows=row_numbers,
        script_rel=script_rel,
    )
    return probed


def _run_backfill_for_sheet(
    sources: list[FundReportSource],
    *,
    excel_path: Path,
    script_rels: list[str],
    log_path: Path,
    cancel_event: threading.Event,
    event_callback: Callable[[str, dict], None],
) -> dict[int, BackfillOutcome]:
    """按原 GUI 补全脚本契约处理一个工作表；参数 sources/scripts/log。"""

    sheet_name = str(sources[0].source_sheet or "").strip()
    expected_rows = {int(source.source_row) for source in sources}
    successes: dict[int, BackfillOutcome] = {}
    skips: dict[int, BackfillOutcome] = {}
    event_callback(
        "job.backfill",
        {
            "status": "running",
            "sheet": sheet_name,
            "rows": sorted(expected_rows),
            "scripts": script_rels,
        },
    )
    for script_rel in script_rels:
        if cancel_event.is_set():
            break
        remaining_rows = sorted(expected_rows - set(successes))
        if not remaining_rows:
            break
        command = _backfill_command(
            script_rel,
            excel_path=excel_path,
            sheet_name=sheet_name,
            row_numbers=set(remaining_rows),
            probe=False,
        )
        code, cancelled, timed_out, output = _run_captured_process(
            command,
            log_path=log_path,
            cancel_event=cancel_event,
            timeout=BACKFILL_SUBPROCESS_TIMEOUT,
        )
        script_successes, script_skips, _ = _parse_backfill_contract(
            output,
            expected_rows=set(remaining_rows),
            script_rel=script_rel,
        )
        _write_backfill_successes(
            excel_path,
            sheet_name=sheet_name,
            successes=script_successes,
            log_path=log_path,
        )
        successes.update(script_successes)
        for row, outcome in script_skips.items():
            skips.setdefault(row, outcome)
        if cancelled:
            break
        if timed_out or code != 0:
            reason = (
                "补全脚本超时"
                if timed_out
                else _backfill_failure_reason(output, code)
            )
            for row in remaining_rows:
                if row not in successes:
                    skips.setdefault(
                        row,
                        BackfillOutcome("failed", reason=reason, script=script_rel),
                    )
    outcomes = dict(skips)
    outcomes.update(successes)
    for row in expected_rows:
        outcomes.setdefault(
            row,
            BackfillOutcome(
                "skipped",
                reason=(
                    "用户取消，未完成补全"
                    if cancel_event.is_set()
                    else (
                        "港台两套补全脚本均未匹配到月报"
                        if len(script_rels) > 1
                        else "补全脚本未返回该行结果"
                    )
                ),
            ),
        )
    event_callback(
        "job.backfill",
        {
            "status": "cancelled" if cancel_event.is_set() else "completed",
            "sheet": sheet_name,
            "rows": sorted(expected_rows),
            "succeeded": sorted(successes),
            "skipped": sorted(
                row for row, item in outcomes.items() if item.status == "skipped"
            ),
            "failed": sorted(
                row for row, item in outcomes.items() if item.status == "failed"
            ),
        },
    )
    return outcomes


def _run_download(
    job: Job,
    spec: DownloadSpec,
    *,
    lookup: dict[str, str],
    staging_root: Path,
    log_path: Path,
    cancel_event: threading.Event,
    event_callback: Callable[[str, dict], None],
) -> tuple[str, str | None, dict]:
    """按稳定性顺序尝试下载来源；参数 spec/job/staging 为执行上下文。"""

    attempts: list[dict] = []
    last_result: dict = {}
    last_reason = "下载失败"
    sources = list(spec.sources)
    for source_index, source in enumerate(sources):
        try:
            def retry_progress(payload: dict) -> None:
                """转发实际重试事件并附来源序号；参数 payload 为运行进度。"""

                phase = payload.get("phase")
                attempt = int(payload.get("attempt") or 1)
                if phase == "retry_wait" or (
                    phase == "attempt_started" and attempt > 1
                ):
                    event_callback(
                        "job.retry",
                        {
                            **payload,
                            "candidate_rank": source.rank,
                            "candidate_kind": source.kind,
                        },
                    )

            script_rel, args = parse_run_command(
                source.run_command,
                lookup,
                spec.source_row,
            )
            if not script_rel.startswith("companies/") or not os.path.basename(
                script_rel
            ).startswith("download_"):
                raise AdapterError("下载命令未指向受控下载器")
            target = {
                "row_number": spec.source_row,
                "fund_name": spec.fund_name,
                "manager": spec.manager_name,
                "category": spec.category,
                "subcategory": spec.subcategory,
                "source_url": source.source_url or spec.source_url,
                "run_command": source.run_command,
                "script": script_rel,
                "args": args,
            }
            result = run_target_with_retries(
                target,
                DOWNLOAD_TIMEOUT_SECONDS,
                str(job.id),
                retries=DOWNLOAD_RETRIES,
                retry_delay=DOWNLOAD_RETRY_DELAY_SECONDS,
                cancel_event=cancel_event,
                progress_callback=retry_progress,
                extra_env={
                    "FACTSHEET_OUTPUT_ROOT": str(staging_root),
                    "FACTSHEET_PROJECT_ROOT": str(PROJECT_ROOT),
                    "FACTSHEET_NETWORK_AUTO": "1",
                },
            )
            if not result.get("output_file"):
                expected = (
                    staging_root
                    / str(job.id)
                    / "factsheet"
                    / target["category"]
                    / f"{target['fund_name']}.pdf"
                )
                if expected.is_file():
                    result["output_file"] = str(expected)
            # 下载器返回码为 0 且文件存在，并不代表拿到的是 PDF；不少官网
            # 在限流/鉴权时会把 HTML 错误页保存成 .pdf。统一在 worker 层做
            # 最终魔数校验，失败后继续尝试候选来源并留下可诊断原因。
            output_value = str(result.get("output_file") or "")
            output_path = Path(output_value) if output_value else None
            if (
                result.get("status") in {"downloaded", "existing"}
                and output_path is not None
                and output_path.is_file()
                and output_path.suffix.casefold() == ".pdf"
            ):
                try:
                    with output_path.open("rb") as handle:
                        valid_pdf = handle.read(5) == b"%PDF-"
                except OSError:
                    valid_pdf = False
                if not valid_pdf:
                    result["status"] = "failed"
                    result["exception_summary"] = (
                        "下载器产物不是有效 PDF（缺少 %PDF- 文件头）："
                        f"{output_path}"
                    )
                    result["failure_category"] = "invalid_download"
        except Exception as exc:
            last_reason = f"{type(exc).__name__}: {exc}"
            last_result = {
                "status": "failed",
                "exception_summary": last_reason,
            }
            result = last_result

        status = result.get("status")
        attempts.append(
            {
                "rank": source.rank,
                "kind": source.kind,
                "stability": source.stability,
                "source_url": source.source_url,
                "run_command": source.run_command,
                "status": status,
                "reason": result.get("exception_summary") or None,
            }
        )
        result["source_attempts"] = list(attempts)
        result["fallback_used"] = source_index > 0
        write_log(log_path, json.dumps(result, ensure_ascii=False))
        if status in {"downloaded", "existing"}:
            return "success", None, result
        if status == "cancelled":
            return "cancelled", "用户取消", result

        last_result = result
        last_reason = (
            result.get("exception_summary")
            or ("下载器跳过" if status == "skipped" else "下载失败")
        )
        if source_index + 1 < len(sources):
            event_callback(
                "job.retry",
                {
                    "phase": "source_fallback",
                    "reason": last_reason,
                    "candidate_rank": source.rank,
                    "candidate_kind": source.kind,
                    "next_candidate_rank": sources[source_index + 1].rank,
                    "next_candidate_kind": sources[source_index + 1].kind,
                },
            )

    if not last_result:
        last_reason = "下载候选没有可执行来源"
        last_result = {
            "status": "failed",
            "exception_summary": last_reason,
        }
    last_result["source_attempts"] = attempts
    if last_result.get("status") == "skipped":
        return "skipped", last_reason, last_result
    return "failed", last_reason, last_result


def _download_spec(
    index: int,
    candidate: DownloadCandidate,
) -> DownloadSpec:
    """把 ORM 候选转为线程安全参数；参数 index/candidate 为任务范围。"""

    fund = candidate.fund
    source = candidate.source
    classification = candidate.classification
    run_command = candidate.run_command_override or (source.run_command if source else None)
    source_url = candidate.source_url_override or (source.monthly_report_url if source else None)
    if fund is None or source is None or not run_command:
        raise AdapterError("下载候选缺少基金、来源或运行方式")
    sources = sources_for_source(source)
    if candidate.source_url_override or candidate.run_command_override:
        sources = [
            DownloadSource(
                rank=1,
                kind="semiannual" if candidate.source_url_override else "registered",
                stability="high",
                source_url=source_url or "",
                run_command=run_command,
                reason="任务目标指定的半年报来源",
            )
        ]
    if not sources:
        raise AdapterError("下载候选没有可执行来源")
    metadata = dict(getattr(source, "metadata_json", None) or {})
    if DOWNLOAD_CANDIDATES_KEY not in metadata:
        metadata[DOWNLOAD_CANDIDATES_KEY] = serialize_download_sources(sources)
        source.metadata_json = metadata
    return DownloadSpec(
        index=index,
        fund_code=candidate.scope.fund_code_snapshot,
        fund_name=fund.name_cn,
        manager_key=str(fund.manager_id),
        manager_name=source.manager_name_source or "",
        category=classification.level_1 if classification else "未分类",
        subcategory=classification.level_2 if classification else "",
        source_row=int(source.source_row),
        source_url=source_url or "",
        run_command=run_command,
        sources=tuple(sources),
    )


def _run_download_batch(
    job: Job,
    candidates: list[DownloadCandidate],
    *,
    lookup: dict[str, str],
    staging_root: Path,
    log_path: Path,
    cancel_event: threading.Event,
    event_callback: Callable[[str, dict], None],
    result_callback: Callable[[int, str, str | None, dict], None] | None = None,
) -> list[tuple[int, str, str | None, dict]]:
    """按管理人隔离并发下载；参数 candidates/job/staging 为批任务。"""

    specs = [_download_spec(index, item) for index, item in enumerate(candidates)]
    if not specs:
        return []
    pending = list(specs)
    active: dict[concurrent.futures.Future, DownloadSpec] = {}
    active_managers: set[str] = set()
    results: list[tuple[int, str, str | None, dict]] = []
    workers = min(DOWNLOAD_WORKERS, len(specs))
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="platform-download",
    ) as executor:
        while pending or active:
            while (
                pending
                and len(active) < workers
                and not cancel_event.is_set()
            ):
                selected = next(
                    (
                        index
                        for index, spec in enumerate(pending)
                        if spec.manager_key not in active_managers
                    ),
                    None,
                )
                if selected is None:
                    break
                spec = pending.pop(selected)
                active_managers.add(spec.manager_key)
                event_callback(
                    "job.fund",
                    {
                        "stage": "download",
                        "fund_code": spec.fund_code,
                        "fund_name": spec.fund_name,
                        "status": "running",
                        "manager": spec.manager_name,
                    },
                )
                future = executor.submit(
                    _run_download,
                    job,
                    spec,
                    lookup=lookup,
                    staging_root=staging_root,
                    log_path=log_path,
                    cancel_event=cancel_event,
                    event_callback=event_callback,
                )
                active[future] = spec
            if not active:
                break
            done, _ = concurrent.futures.wait(
                active,
                timeout=0.25,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                spec = active.pop(future)
                active_managers.discard(spec.manager_key)
                try:
                    state, reason, result = future.result()
                except Exception as exc:  # noqa: BLE001
                    state = "failed"
                    reason = f"{type(exc).__name__}: {exc}"
                    result = {}
                outcome = (spec.index, state, reason, result)
                results.append(outcome)
                if result_callback is not None:
                    result_callback(*outcome)
    for spec in pending:
        outcome = (spec.index, "cancelled", "用户取消", {})
        results.append(outcome)
        if result_callback is not None:
            result_callback(*outcome)
    return sorted(results, key=lambda item: item[0])


def run_download_stage(
    session: Session,
    job: Job,
    *,
    job_root: Path,
    cancel_event: threading.Event,
    event_callback: Callable[[str, dict], None],
) -> AdapterResult:
    """按原 GUI 三段顺序补全并下载；参数描述任务、输出和取消信号。"""

    task_target = str(
        getattr(job, "task_target", None)
        or (getattr(job, "scope_definition", None) or {}).get("task_target")
        or "fund_monthly"
    )
    if task_target == "master_database":
        status, message, details = run_master_database(
            job,
            job_root=job_root,
            cancel_event=cancel_event,
            event_callback=event_callback,
        )
        return AdapterResult(status, message, details, artifact_count=1 if status == "success" else 0)

    supplementary = (
        (getattr(job, "scope_definition", None) or {})
        .get("document_policy", {})
        .get("supplementary_source")
    )
    if isinstance(supplementary, dict):
        return _run_supplementary_download(
            session,
            job,
            job_root=job_root,
            supplementary=supplementary,
            cancel_event=cancel_event,
            event_callback=event_callback,
        )

    scopes = list(
        session.scalars(
            select(JobFundScope)
            .where(JobFundScope.job_id == job.id)
            .order_by(JobFundScope.fund_code_snapshot)
        )
    )
    lookup = script_lookup()
    staging_root = job_root / "staging"
    log_path = job_root / "worker.log"
    successful = 0
    skipped = 0
    failed = 0
    backfilled = 0
    results = []
    candidates: list[DownloadCandidate] = []

    for scope in scopes:
        if cancel_event.is_set():
            return AdapterResult("cancelled", "任务已取消", {"results": results})
        fund = session.get(Fund, scope.fund_id)
        classification = session.scalar(
            select(FundClassification).where(
                FundClassification.fund_id == scope.fund_id
            )
        )
        source = session.scalars(
            select(FundReportSource)
            .where(FundReportSource.fund_id == scope.fund_id)
            .order_by(
                (FundReportSource.semiannual_report_url.is_(None) if task_target == "fund_semiannual" else FundReportSource.monthly_report_url.is_(None)),
                FundReportSource.is_active.desc(),
                FundReportSource.priority,
                FundReportSource.source_row,
            )
            .limit(1)
        ).first()
        if task_target == "fund_semiannual" and (source is None or not str(source.semiannual_report_url or "").strip()):
            # 半年报任务不降级使用月报来源，避免任务目标和实际文档错配。
            source = None
        candidates.append(
            DownloadCandidate(
                scope=scope,
                fund=fund,
                classification=classification,
                source=source,
                source_url_override=(source.semiannual_report_url if task_target == "fund_semiannual" and source else None),
                run_command_override=(source.semiannual_run_command if task_target == "fund_semiannual" and source and source.semiannual_run_command else None),
            )
        )

    scripts = discover_fund_link_scripts()
    sheet_candidates: dict[str, list[DownloadCandidate]] = defaultdict(list)
    sheet_scripts: dict[str, list[str]] = {}
    for candidate in candidates:
        source = candidate.source
        if source is None:
            continue
        sheet = str(source.source_sheet or "").strip()
        if not sheet:
            continue
        sheet_candidates[sheet].append(candidate)
    for sheet, items in sheet_candidates.items():
        manager = str(items[0].source.manager_name_source or "").strip()
        sheet_scripts[sheet] = fund_link_scripts_for_sheet(
            manager,
            sheet,
            scripts,
        )

    backfill_keys: set[tuple[str, int]] = set()
    backfill_workbook = None
    if task_target != "fund_semiannual":
        backfill_workbook = _prepare_backfill_workbook(
            candidates,
            job_root=job_root,
            log_path=log_path,
        )
        for sheet, items in sheet_candidates.items():
            scripts_for_sheet = sheet_scripts.get(sheet) or []
            rows = {int(item.source.source_row) for item in items}
            probed: set[int] = set()
            if len(scripts_for_sheet) == 1 and backfill_workbook is not None:
                probed = _run_probe_for_sheet(
                    excel_path=backfill_workbook,
                    sheet_name=sheet,
                    row_numbers=rows,
                    script_rel=scripts_for_sheet[0],
                    log_path=log_path,
                    cancel_event=cancel_event,
                )
            for item in items:
                if _source_needs_backfill(item.source) or item.source.source_row in probed:
                    backfill_keys.add((sheet, int(item.source.source_row)))

    def execute_many(batch: list[DownloadCandidate]) -> bool:
        """并发执行一批下载并按完成顺序入账；参数 batch 为基金列表。"""

        nonlocal successful, skipped, failed
        if not batch:
            return False
        cancelled = False
        processed_in_batch = 0

        def record_result(
            index: int,
            state: str,
            reason: str | None,
            result: dict,
        ) -> None:
            """记录单只完成结果；参数 index/state/reason/result。"""

            nonlocal successful, skipped, failed, cancelled, processed_in_batch
            candidate = batch[index]
            if result:
                results.append(result)
            candidate.scope.result_status = state
            candidate.scope.skip_reason = reason
            if state == "success":
                successful += 1
            elif state == "skipped":
                skipped += 1
            elif state == "failed":
                failed += 1
            elif state == "cancelled":
                cancelled = True
            processed_in_batch += 1
            if processed_in_batch % PROGRESS_COMMIT_EVERY == 0:
                session.commit()
            event_callback(
                "job.fund",
                {
                    "stage": "download",
                    "fund_code": candidate.scope.fund_code_snapshot,
                    "fund_name": candidate.scope.fund_name_snapshot,
                    "status": state,
                    "reason": reason,
                },
            )

        _run_download_batch(
            job,
            batch,
            lookup=lookup,
            staging_root=staging_root,
            log_path=log_path,
            cancel_event=cancel_event,
            event_callback=event_callback,
            result_callback=record_result,
        )
        session.commit()
        return cancelled

    phase1 = []
    for candidate in candidates:
        source = candidate.source
        sheet = str(source.source_sheet or "").strip() if source else ""
        if (
            source is not None
            and candidate.fund is not None
            and (candidate.run_command_override or source.run_command)
            and (sheet, int(source.source_row)) not in backfill_keys
        ):
            phase1.append(candidate)
    if execute_many(phase1):
        return AdapterResult(
            "cancelled",
            "下载被用户取消",
            {"results": results},
            artifact_count=successful,
        )

    phase2_downloads: list[DownloadCandidate] = []
    for sheet, items in sheet_candidates.items():
        needed = [
            item
            for item in items
            if (sheet, int(item.source.source_row)) in backfill_keys
        ]
        if not needed:
            continue
        script_rels = sheet_scripts.get(sheet) or []
        if not script_rels:
            for candidate in needed:
                reason = "该管理人暂无爬虫补全适配器"
                _record_backfill_metadata(
                    candidate.source,
                    BackfillOutcome("unsupported", reason=reason),
                )
                candidate.scope.result_status = "skipped"
                candidate.scope.skip_reason = reason
                skipped += 1
                event_callback(
                    "job.fund",
                    {
                        "stage": "download",
                        "fund_code": candidate.scope.fund_code_snapshot,
                        "fund_name": candidate.scope.fund_name_snapshot,
                        "status": "skipped",
                        "reason": reason,
                    },
                )
            continue
        if backfill_workbook is None:
            for candidate in needed:
                reason = "无法准备任务级 crawler 补全工作簿"
                _record_backfill_metadata(
                    candidate.source,
                    BackfillOutcome("failed", reason=reason),
                )
                candidate.scope.result_status = "failed"
                candidate.scope.skip_reason = reason
                failed += 1
                event_callback(
                    "job.fund",
                    {
                        "stage": "download",
                        "fund_code": candidate.scope.fund_code_snapshot,
                        "fund_name": candidate.scope.fund_name_snapshot,
                        "status": "failed",
                        "reason": reason,
                    },
                )
            continue
        outcomes = _run_backfill_for_sheet(
            [item.source for item in needed],
            excel_path=backfill_workbook,
            script_rels=script_rels,
            log_path=log_path,
            cancel_event=cancel_event,
            event_callback=event_callback,
        )
        for candidate in needed:
            source = candidate.source
            outcome = outcomes.get(
                int(source.source_row),
                BackfillOutcome(
                    "skipped",
                    reason="补全脚本未返回该行结果",
                ),
            )
            _record_backfill_metadata(source, outcome)
            if outcome.status == "succeeded":
                backfilled += 1
                source.monthly_report_url = outcome.monthly_report_url
                source.run_command = outcome.run_command
                source.is_active = True
                phase2_downloads.append(candidate)
            elif outcome.status == "failed":
                candidate.scope.result_status = "failed"
                candidate.scope.skip_reason = outcome.reason or "爬虫补全失败"
                failed += 1
                event_callback(
                    "job.fund",
                    {
                        "stage": "download",
                        "fund_code": candidate.scope.fund_code_snapshot,
                        "fund_name": candidate.scope.fund_name_snapshot,
                        "status": "failed",
                        "reason": candidate.scope.skip_reason,
                    },
                )
            else:
                reason = outcome.reason or "爬虫补全未匹配到月报"
                candidate.scope.result_status = "skipped"
                candidate.scope.skip_reason = reason
                skipped += 1
                event_callback(
                    "job.fund",
                    {
                        "stage": "download",
                        "fund_code": candidate.scope.fund_code_snapshot,
                        "fund_name": candidate.scope.fund_name_snapshot,
                        "status": "skipped",
                        "reason": reason,
                    },
                )
        # 与原 GUI 的增量回填一致：先持久化 I/J，再尝试下载。
        # 即使下载失败，补全结果仍可供重试复用，不必再次加载完整目录。
        session.commit()
        if cancel_event.is_set():
            for candidate in phase2_downloads:
                candidate.scope.result_status = "failed"
                candidate.scope.skip_reason = "用户取消"
            return AdapterResult(
                "cancelled",
                "爬虫补全被用户取消",
                {"results": results, "backfilled": backfilled},
                artifact_count=successful,
            )
    if execute_many(phase2_downloads):
        return AdapterResult(
            "cancelled",
            "下载被用户取消",
            {"results": results, "backfilled": backfilled},
            artifact_count=successful,
        )

    for candidate in candidates:
        if candidate.scope.result_status:
            continue
        if candidate.source is None:
            candidate.scope.result_status = "skipped"
            candidate.scope.skip_reason = "没有 crawler 来源记录，无法执行爬虫补全"
        elif not (candidate.run_command_override or candidate.source.run_command):
            candidate.scope.result_status = "skipped"
            candidate.scope.skip_reason = "爬虫补全未返回可执行运行方式"
        else:
            candidate.scope.result_status = "failed"
            candidate.scope.skip_reason = "下载 Adapter 未处理该基金"
        if candidate.scope.result_status == "skipped":
            skipped += 1
        else:
            failed += 1
        event_callback(
            "job.fund",
            {
                "stage": "download",
                "fund_code": candidate.scope.fund_code_snapshot,
                "fund_name": candidate.scope.fund_name_snapshot,
                "status": candidate.scope.result_status,
                "reason": candidate.scope.skip_reason,
            },
        )

    if cancel_event.is_set():
        return AdapterResult(
            "cancelled",
            "任务已取消",
            {"results": results},
            artifact_count=successful,
        )
    status = "failed" if successful == 0 and failed else "success"
    return AdapterResult(
        status,
        f"成功 {successful}，跳过 {skipped}，失败 {failed}",
        {
            "success": successful,
            "skipped": skipped,
            "failed": failed,
            "backfilled": backfilled,
            "results": results,
        },
        artifact_count=successful,
    )


def _run_supplementary_download(
    session: Session,
    job: Job,
    *,
    job_root: Path,
    supplementary: dict,
    cancel_event: threading.Event,
    event_callback: Callable[[str, dict], None],
) -> AdapterResult:
    """把手动 URL/PDF 纳入标准 factsheet 目录，供 MinerU 后续阶段消费。"""

    scopes = list(
        session.scalars(
            select(JobFundScope).where(JobFundScope.job_id == job.id)
        )
    )
    if len(scopes) != 1:
        return AdapterResult("failed", "补充来源任务必须只包含一只基金", {"complete_fund_results": True})
    scope = scopes[0]
    fund = session.get(Fund, scope.fund_id)
    if fund is None:
        return AdapterResult("failed", "补充来源目标基金不存在", {"complete_fund_results": True})
    classification = session.scalar(
        select(FundClassification).where(FundClassification.fund_id == fund.id)
    )
    category = classification.level_1 if classification else "未分类"
    if category not in STRUCTURED_CATEGORIES:
        category = "货币" if "货币" in (fund.name_cn or "") else "固收"
    if cancel_event.is_set():
        return AdapterResult("cancelled", "下载已取消", {"complete_fund_results": True})
    input_path = supplementary.get("input_path")
    source_url = str(supplementary.get("source_url") or "").strip()
    destination = job_root / "staging" / str(job.id) / "factsheet" / category / f"{fund.name_cn}.pdf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw_source = job_root / "supplementary_input" / "download.pdf"
    raw_source.parent.mkdir(parents=True, exist_ok=True)
    resolved_url = source_url
    try:
        if input_path:
            source = (PROJECT_ROOT / str(input_path)).resolve()
            source.relative_to(PROJECT_ROOT.resolve())
            if not source.is_file():
                raise FileNotFoundError(f"补充来源文件不存在：{input_path}")
            shutil.copy2(source, raw_source)
            effective_url = source_url or f"file://{input_path}"
        elif source_url.startswith(("http://", "https://")):
            content, resolved_url = _download_supplementary_pdf(source_url)
            raw_source.write_bytes(content)
            effective_url = source_url
        else:
            raise ValueError("补充来源 URL 必须为 http/https")
        pdf_result = prepare_target_pdf(
            raw_source,
            destination,
            fund_code=scope.fund_code_snapshot,
            fund_name_cn=fund.name_cn,
            fund_name_en=fund.name_en,
        )
    except Exception as exc:  # noqa: BLE001
        scope.result_status = "failed"
        scope.skip_reason = str(exc)
        event_callback("job.fund", {"stage": "download", "fund_code": scope.fund_code_snapshot, "fund_name": scope.fund_name_snapshot, "status": "failed", "reason": str(exc)})
        return AdapterResult("failed", f"补充来源下载失败：{exc}", {"complete_fund_results": True, "fund_results": [{"fund_code": scope.fund_code_snapshot, "status": "failed", "reason": str(exc)}]})
    download_sha256 = _sha256_file(destination)
    source_download_sha256 = _sha256_file(raw_source)
    historical_hashes = _historical_source_hashes(
        session,
        fund_id=fund.id,
        source_url=effective_url if effective_url.startswith(("http://", "https://")) else "",
    )
    content_changed = bool(historical_hashes and download_sha256 not in historical_hashes)
    previous_sha256 = historical_hashes[-1] if historical_hashes else None
    # 将“本次下载看到的内容”写回任务策略。后续结构化和发布阶段只认这一份
    # 内容指纹，从而不会在同 URL 更新后继续选择旧的 DocumentVersion。
    scope_definition = dict(job.scope_definition or {})
    document_policy = dict(scope_definition.get("document_policy") or {})
    supplementary_state = dict(document_policy.get("supplementary_source") or supplementary)
    supplementary_state.update(
        {
            "download_sha256": download_sha256,
            "source_download_sha256": source_download_sha256,
            "source_pages": pdf_result.source_pages,
            "matched_pages": list(pdf_result.matched_pages),
            "selected_pages": list(pdf_result.selected_pages),
            "pdf_sliced": pdf_result.sliced,
            "report_kind": pdf_result.report_kind,
            "match_method": pdf_result.match_method,
            "matched_names": list(pdf_result.matched_names),
            "previous_sha256": previous_sha256,
            "historical_sha256s": historical_hashes,
            "content_changed": content_changed,
            "resolved_url": resolved_url,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    document_policy["supplementary_source"] = supplementary_state
    scope_definition["document_policy"] = document_policy
    job.scope_definition = scope_definition
    report_dir = job_root / "staging" / str(job.id) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "download_run_report_supplementary.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "fund_code": scope.fund_code_snapshot,
                        "fund_name": fund.name_cn,
                        "category": category,
                        "source_url": effective_url,
                        "resolved_url": resolved_url,
                        "status": "success",
                        "download_sha256": download_sha256,
                        "source_download_sha256": source_download_sha256,
                        "source_pages": pdf_result.source_pages,
                        "matched_pages": list(pdf_result.matched_pages),
                        "selected_pages": list(pdf_result.selected_pages),
                        "pdf_sliced": pdf_result.sliced,
                        "report_kind": pdf_result.report_kind,
                        "match_method": pdf_result.match_method,
                        "matched_names": list(pdf_result.matched_names),
                        "previous_sha256": previous_sha256,
                        "historical_sha256s": historical_hashes,
                        "content_changed": content_changed,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    scope.result_status = "success"
    scope.skip_reason = None
    event_callback("job.fund", {"stage": "download", "fund_code": scope.fund_code_snapshot, "fund_name": scope.fund_name_snapshot, "status": "success", "source_url": effective_url, "download_sha256": download_sha256, "content_changed": content_changed})
    return AdapterResult(
        "success",
        "补充来源已下载" + ("（检测到内容变化）" if content_changed else ""),
        {
            "success": 1,
            "fund_results": [{"fund_code": scope.fund_code_snapshot, "status": "success"}],
            "complete_fund_results": True,
            "download_sha256": download_sha256,
            "source_download_sha256": source_download_sha256,
            "resolved_url": resolved_url,
            "source_pages": pdf_result.source_pages,
            "matched_pages": list(pdf_result.matched_pages),
            "selected_pages": list(pdf_result.selected_pages),
            "pdf_sliced": pdf_result.sliced,
            "report_kind": pdf_result.report_kind,
            "match_method": pdf_result.match_method,
            "matched_names": list(pdf_result.matched_names),
            "previous_sha256": previous_sha256,
            "content_changed": content_changed,
        },
        artifact_count=1,
    )


def _job_input_root(job: Job, job_root: Path) -> Path:
    """返回下载器写入的批次根目录；参数 job/job_root 为任务与任务目录。"""

    return job_root / "staging" / str(job.id)


def _monitor_file_progress(
    *,
    root: Path,
    pattern: str,
    stage: str,
    total: int,
    cancel_event: threading.Event,
    event_callback: Callable[[str, dict], None],
    exclude_suffix: str | None = None,
) -> tuple[threading.Event, threading.Thread]:
    """监控阶段产物数量并发送进度；参数 root/pattern/stage/total。"""

    stop = threading.Event()

    def monitor() -> None:
        """轮询产物数量；参数为空。"""

        last = -1
        while not stop.wait(1):
            if cancel_event.is_set():
                return
            files = list(root.glob(pattern)) if root.exists() else []
            if exclude_suffix:
                files = [path for path in files if not path.name.endswith(exclude_suffix)]
            current = len(files)
            if current == last:
                continue
            last = current
            event_callback(
                "job.stage",
                {
                    "stage": stage,
                    "status": "running",
                    "processed": min(current, total),
                    "total": total,
                },
            )

    thread = threading.Thread(
        target=monitor,
        name=f"{stage}-progress-monitor",
        daemon=True,
    )
    thread.start()
    return stop, thread


def _fund_file_key(value: str) -> str:
    """生成 PDF 文件名与基金名之间的匹配键；参数 value 为文件名或基金名。"""

    return "".join(
        character
        for character in Path(str(value or "")).stem.casefold()
        if character.isalnum()
    )


def _scope_file_map(
    session: Session,
    job: Job,
    run_root: Path,
) -> tuple[dict[str, list[JobFundScope]], set[str]]:
    """读取任务基金与 PDF 匹配关系；参数 session/job/run_root 为任务上下文。"""

    scopes = list(
        session.scalars(
            select(JobFundScope)
            .where(JobFundScope.job_id == job.id)
            .order_by(JobFundScope.fund_code_snapshot)
        )
    )
    by_file: dict[str, list[JobFundScope]] = defaultdict(list)
    for scope in scopes:
        by_file[_fund_file_key(scope.fund_name_snapshot)].append(scope)
    pdf_keys = {
        _fund_file_key(path.name)
        for path in (run_root / "factsheet").rglob("*.pdf")
    }
    return by_file, pdf_keys


def _manager_map_for_scopes(
    session: Session,
    scopes: list[JobFundScope],
) -> dict[str, str]:
    """生成基金名匹配键到管理人名称的映射；参数 session/scopes 为任务范围。"""

    fund_ids = {scope.fund_id for scope in scopes if scope.fund_id}
    if not fund_ids:
        return {}
    rows = session.execute(
        select(Fund.id, Manager.name_cn)
        .join(Manager, Manager.id == Fund.manager_id)
        .where(Fund.id.in_(fund_ids))
    ).all()
    manager_by_fund: dict[UUID, str] = {}
    for row in rows:
        fund_id = row[0]
        manager_name = row[1]
        if fund_id and manager_name:
            manager_by_fund[fund_id] = str(manager_name)
    mapping: dict[str, str] = {}
    for scope in scopes:
        manager_name = manager_by_fund.get(scope.fund_id)
        if manager_name:
            mapping[_fund_file_key(scope.fund_name_snapshot)] = manager_name
    return mapping


def _write_manager_map(run_root: Path, mapping: dict[str, str]) -> Path | None:
    """把 PDF 到管理人的映射写入批次工作目录；参数 run_root/mapping 为上下文。"""

    if not mapping:
        return None
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pdfs": dict(mapping),
    }
    for pdf_path in (run_root / "factsheet").rglob("*.pdf"):
        manager_name = mapping.get(_fund_file_key(pdf_path.name))
        if manager_name:
            payload["pdfs"][str(pdf_path.resolve())] = manager_name
    target = run_root / "structure_data" / "_work" / "manager_map.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def _append_scope_result(
    results: list[dict],
    existing: set[str],
    scopes: list[JobFundScope],
    *,
    status: str,
    reason: str,
) -> None:
    """追加逐基金阶段结果；参数 results/existing/scopes/status/reason 为上下文。"""

    for scope in scopes:
        if scope.fund_code_snapshot in existing:
            continue
        existing.add(scope.fund_code_snapshot)
        results.append(
            {
                "fund_code": scope.fund_code_snapshot,
                "fund_name": scope.fund_name_snapshot,
                "status": status,
                "reason": reason,
            }
        )


def _latest_stage_report(root: Path, pattern: str) -> Path | None:
    """查找阶段最新 JSON 报告；参数 root/pattern 为报告目录与文件模式。"""

    reports = list(root.glob(pattern)) if root.is_dir() else []
    return max(reports, key=lambda item: item.stat().st_mtime) if reports else None


def _missing_pdf_results(
    by_file: dict[str, list[JobFundScope]],
    pdf_keys: set[str],
) -> tuple[list[dict], set[str]]:
    """生成无 PDF 基金的跳过结果；参数 by_file/pdf_keys 为匹配上下文。"""

    results: list[dict] = []
    existing: set[str] = set()
    for file_key, scopes in by_file.items():
        if file_key in pdf_keys:
            continue
        _append_scope_result(
            results,
            existing,
            scopes,
            status="skipped",
            reason="没有下载 PDF，跳过当前阶段",
        )
    return results, existing


def _configured_mineru_mode() -> str:
    """返回 MinerU 子进程解析模式；参数为空。"""

    configured = config_value(
        "processing",
        "mineru_mode",
        "lightweight",
    ).strip()
    return "auto" if configured == "token" else "agent"


def _concise_failure_reason(reason: str) -> str:
    """把底层错误压缩成可读原因；参数 reason 为原始错误。"""

    text = " ".join(str(reason or "").split())
    normalized = text.casefold()
    if "429" in normalized or "requests/min limit" in normalized:
        return "接口触发限流"
    if "insufficient balance" in normalized:
        return "模型账户余额不足"
    if "arrearage" in normalized or "overdue payment" in normalized:
        return "模型账户欠费"
    if "timeout" in normalized or "timed out" in normalized:
        return "接口请求超时"
    if "401" in normalized or "unauthorized" in normalized:
        return "API 认证失败"
    if "403" in normalized:
        return "API 无访问权限"
    if "no such file" in normalized or "factsheet 下没有" in normalized:
        return "缺少输入文件"
    return text[:120] or "未提供具体原因"


def _stage_failure_message(
    stage_label: str,
    exit_code: int,
    fund_results: list[dict],
) -> str:
    """构造带简明原因的阶段错误；参数 stage_label/code/results 为阶段结果。"""

    failures = [
        item
        for item in fund_results
        if item.get("status") == "failed"
    ]
    if not failures:
        return f"{stage_label}退出码 {exit_code}"
    reason_counts: dict[str, int] = {}
    for item in failures:
        reason = _concise_failure_reason(str(item.get("reason") or ""))
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    if len(reason_counts) == 1:
        reason = next(iter(reason_counts))
        return (
            f"{stage_label}退出码 {exit_code}；"
            f"{len(failures)} 份基金失败：{reason}"
        )
    ordered = sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
    summary = "；".join(f"{reason} {count}" for reason, count in ordered[:3])
    remaining = sum(count for _, count in ordered[3:])
    if remaining:
        summary += f"；其他 {remaining}"
    return (
        f"{stage_label}退出码 {exit_code}；"
        f"{len(failures)} 份基金失败（{summary}）"
    )


def run_parse_stage(
    session: Session,
    job: Job,
    *,
    job_root: Path,
    cancel_event: threading.Event,
    event_callback: Callable[[str, dict], None],
) -> AdapterResult:
    """调用现有 MinerU 批量入口；参数 session/job/job_root/cancel/event 为任务上下文。"""

    run_root = _job_input_root(job, job_root)
    by_file, pdf_keys = _scope_file_map(session, job, run_root)
    fund_results, existing = _missing_pdf_results(by_file, pdf_keys)
    command = [
        sys.executable,
        str(LEGACY_CODE_DIR / "run_all_mineru.py"),
        str(run_root),
        "--mode",
        _configured_mineru_mode(),
        "--workers",
        str(MINERU_WORKERS),
    ]
    pdf_count = len(list((run_root / "factsheet").rglob("*.pdf")))
    # 下载阶段可能因旧 worker、进程重启或下载器异常只写入了“完成”状态，
    # 但没有实际 PDF。不要再把空目录交给 run_all_mineru.py，避免用户只看到
    # 无法定位的“MinerU 退出码 1”。
    if pdf_count == 0:
        reason = "下载阶段未产出可解析的 PDF"
        for file_key in pdf_keys:
            _append_scope_result(
                fund_results,
                existing,
                by_file.get(file_key, []),
                status="failed",
                reason=reason,
            )
        return AdapterResult(
            "failed",
            reason,
            {
                "fund_results": fund_results,
                "complete_fund_results": True,
                "returncode": None,
            },
        )
    stop_monitor, monitor = _monitor_file_progress(
        root=run_root / "markdown",
        pattern="*/**/manifest.json",
        stage="parse",
        total=pdf_count,
        cancel_event=cancel_event,
        event_callback=event_callback,
    )
    try:
        code, cancelled, timed_out = _run_fixed_process(
            command,
            cwd=PROJECT_ROOT,
            log_path=job_root / "worker.log",
            cancel_event=cancel_event,
            timeout=PARSE_TIMEOUT_SECONDS,
        )
    finally:
        stop_monitor.set()
        monitor.join(timeout=2)
    if cancelled:
        return AdapterResult(
            "cancelled",
            "MinerU 解析已取消",
            {
                "fund_results": fund_results,
                "complete_fund_results": True,
            },
        )
    if timed_out:
        reason = "MinerU 解析超时"
        for file_key in pdf_keys:
            _append_scope_result(
                fund_results,
                existing,
                by_file.get(file_key, []),
                status="failed",
                reason=reason,
            )
        return AdapterResult(
            "failed",
            reason,
            {
                "fund_results": fund_results,
                "complete_fund_results": True,
            },
        )
    report_path = _latest_stage_report(
        run_root / "reports",
        "mineru_run_report_*.json",
    )
    if report_path is not None:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for item in report.get("results") or []:
            if item.get("status") != "failed":
                continue
            file_key = _fund_file_key(str(item.get("pdf") or ""))
            _append_scope_result(
                fund_results,
                existing,
                by_file.get(file_key, []),
                status="failed",
                reason=str(item.get("error") or "MinerU 解析失败"),
            )
    elif code != 0:
        reason = f"MinerU 退出码 {code}"
        for file_key in pdf_keys:
            _append_scope_result(
                fund_results,
                existing,
                by_file.get(file_key, []),
                status="failed",
                reason=reason,
            )
    details = {
        "fund_results": fund_results,
        "complete_fund_results": True,
    }
    if code != 0:
        return AdapterResult(
            "failed",
            _stage_failure_message("MinerU ", code, fund_results),
            {"returncode": code, **details},
        )
    manifests = list((run_root / "markdown").rglob("manifest.json"))
    return AdapterResult(
        "success",
        f"MinerU 生成 {len(manifests)} 个缓存",
        {"manifests": len(manifests), **details},
        artifact_count=len(manifests),
    )


def run_structure_stage(
    session: Session,
    job: Job,
    *,
    job_root: Path,
    cancel_event: threading.Event,
    event_callback: Callable[[str, dict], None],
) -> AdapterResult:
    """调用现有结构化总入口；参数 session/job/job_root/cancel/event 为任务上下文。"""

    run_root = _job_input_root(job, job_root)
    by_file, pdf_keys = _scope_file_map(session, job, run_root)
    fund_results, existing = _missing_pdf_results(by_file, pdf_keys)
    scopes = [
        scope
        for items in by_file.values()
        for scope in items
    ]
    manager_map_path = _write_manager_map(
        run_root,
        _manager_map_for_scopes(session, scopes),
    )
    classifications = dict(
        session.execute(
            select(
                FundClassification.fund_id,
                FundClassification.level_1,
            ).where(
                FundClassification.fund_id.in_(
                    {scope.fund_id for scope in scopes}
                )
            )
        ).all()
    )
    for scope in scopes:
        category = classifications.get(scope.fund_id) or "未分类"
        if category in STRUCTURED_CATEGORIES:
            continue
        _append_scope_result(
            fund_results,
            existing,
            [scope],
            status="skipped",
            reason=f"资产类别“{category}”暂不支持结构化",
        )
    model_policy = dict(
        (getattr(job, "scope_definition", None) or {}).get("model_policy") or {}
    )
    structured_provider = str(model_policy.get("structured_provider") or "").strip().lower()
    if structured_provider not in {"deepseek", "qwen", "local"}:
        structured_provider = ""
    local_model = str(model_policy.get("local_model") or "qwen3:4b").strip()
    if structured_provider == "local":
        # Ollama 本地模型受内存约束，只允许有界并发；任务模型策略已在
        # jobs.py 校验为 1 或 2，读取时再做一次防御性收敛。
        try:
            local_workers = int(model_policy.get("local_workers", 1) or 1)
        except (TypeError, ValueError):
            local_workers = 1
        model_workers = max(1, min(2, local_workers))
    else:
        model_workers = MODEL_WORKERS
    command = [
        sys.executable,
        str(LEGACY_CODE_DIR / "run_all_structured_data.py"),
        str(run_root),
        "--source",
        "mineru",
        "--workers",
        str(model_workers),
        "--skip-existing",
    ]
    if structured_provider == "local":
        # 本地路由同时通过 CLI 和环境变量传递，避免长驻 Worker 或子进程
        # 未重新加载环境时回退到 config.ini 中的 DeepSeek 默认值。
        command.extend(
            [
                "--primary-model",
                local_model,
                "--fallback-model",
                local_model,
                "--vision-model",
                "",
            ]
        )
    pdf_count = len(list((run_root / "factsheet").rglob("*.pdf")))
    stop_monitor, monitor = _monitor_file_progress(
        root=run_root / "structure_data" / "_work",
        pattern="*/per_pdf/*.json",
        stage="structure",
        total=pdf_count,
        cancel_event=cancel_event,
        event_callback=event_callback,
        exclude_suffix=".meta.json",
    )
    try:
        structure_env = {
            "FACTSHEET_MANAGER_MAP": str(manager_map_path),
        } if manager_map_path else {}
        if structured_provider:
            structure_env["FACTSHEET_STRUCTURED_PROVIDER"] = structured_provider
        if structured_provider == "local":
            structure_env.update({
                "FACTSHEET_PRIMARY_MODEL": local_model,
                "FACTSHEET_FALLBACK_MODEL": local_model,
                "FACTSHEET_VISION_MODEL": "",
                "FACTSHEET_MODEL_WORKERS": str(model_workers),
                "FACTSHEET_MODEL_CONCURRENCY": str(model_workers),
                "FACTSHEET_STRUCTURED_THINKING": "false",
            })
        write_log(
            job_root / "worker.log",
            "结构化模型路由："
            f"provider={structured_provider or 'global'}, "
            f"primary={local_model if structured_provider == 'local' else 'config.ini'}, "
            f"workers={model_workers}",
        )
        code, cancelled, timed_out = _run_fixed_process(
            command,
            cwd=PROJECT_ROOT,
            log_path=job_root / "worker.log",
            cancel_event=cancel_event,
            timeout=STRUCTURE_TIMEOUT_SECONDS,
            extra_env=structure_env or None,
        )
    finally:
        stop_monitor.set()
        monitor.join(timeout=2)
    if cancelled:
        return AdapterResult(
            "cancelled",
            "结构化已取消",
            {
                "fund_results": fund_results,
                "complete_fund_results": True,
            },
        )
    if timed_out:
        reason = "结构化超时"
        for file_key in pdf_keys:
            _append_scope_result(
                fund_results,
                existing,
                by_file.get(file_key, []),
                status="failed",
                reason=reason,
            )
        return AdapterResult(
            "failed",
            reason,
            {
                "fund_results": fund_results,
                "complete_fund_results": True,
            },
        )
    work_root = run_root / "structure_data" / "_work"
    report_path = _latest_stage_report(
        work_root,
        "structured_run_summary_*.json",
    )
    if report_path is not None:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for item in report.get("failures") or []:
            file_key = _fund_file_key(str(item.get("pdf") or ""))
            _append_scope_result(
                fund_results,
                existing,
                by_file.get(file_key, []),
                status="failed",
                reason=str(item.get("error") or "结构化抽取失败"),
            )
        for item in report.get("skipped_unsupported") or []:
            category = str(item.get("category") or "")
            for scope in scopes:
                if classifications.get(scope.fund_id) != category:
                    continue
                _append_scope_result(
                    fund_results,
                    existing,
                    [scope],
                    status="skipped",
                    reason=f"资产类别“{category}”暂不支持结构化",
                )
    elif code != 0:
        reason = f"结构化退出码 {code}"
        for file_key in pdf_keys:
            _append_scope_result(
                fund_results,
                existing,
                by_file.get(file_key, []),
                status="failed",
                reason=reason,
            )
    details = {
        "fund_results": fund_results,
        "complete_fund_results": True,
    }
    if code != 0:
        return AdapterResult(
            "failed",
            _stage_failure_message("结构化", code, fund_results),
            {"returncode": code, **details},
        )
    supplementary = (
        (getattr(job, "scope_definition", None) or {})
        .get("document_policy", {})
        .get("supplementary_source")
    )
    if isinstance(supplementary, dict):
        target_scope = scopes[0] if len(scopes) == 1 else None
        target_fund = session.get(Fund, target_scope.fund_id) if target_scope else None
        target_keys = {
            _fund_file_key(target_scope.fund_name_snapshot if target_scope else ""),
            _fund_file_key(target_fund.name_en if target_fund else ""),
            str(target_scope.fund_code_snapshot if target_scope else "").casefold(),
        }
        observed: list[str] = []
        matched = False
        for combined_path in sorted(work_root.glob("*/combined/*_combined.json")):
            try:
                payload = json.loads(combined_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for record in payload.get("records") or []:
                name = str(record.get("fund_name") or record.get("fund_name_en") or "")
                code_value = str(record.get("fund_code") or "").casefold()
                observed.append(name or code_value or "未命名")
                if code_value and code_value in target_keys:
                    matched = True
                name_key = _fund_file_key(name)
                if name_key in target_keys or any(
                    len(name_key) >= 6
                    and len(target_key) >= 6
                    and (name_key in target_key or target_key in name_key)
                    for target_key in target_keys
                ):
                    matched = True
        if not matched:
            reason = f"输入文件与目标基金不匹配：目标 {target_scope.fund_code_snapshot if target_scope else '—'}，识别为 {'、'.join(observed[:3]) or '未识别基金'}"
            _append_scope_result(
                fund_results,
                existing,
                scopes,
                status="failed",
                reason=reason,
            )
            return AdapterResult(
                "failed",
                "补充来源与目标基金不匹配",
                {"returncode": 0, "fund_results": fund_results, "complete_fund_results": True, "mismatch": True, "observed_names": observed},
            )
    summary_files = list((run_root / "structure_data" / "_work").glob(
        "structured_run_summary_*.json"
    ))
    return AdapterResult(
        "success",
        f"结构化生成 {len(summary_files)} 个批次汇总",
        {"summaries": len(summary_files), **details},
        artifact_count=len(summary_files),
    )
