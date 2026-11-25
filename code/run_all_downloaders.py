# -*- coding: utf-8 -*-
"""
Run fund document downloaders from the crawler list and continue after failures.

Batch output layout:
  data/<run id>/factsheet/<category from crawler list>/<fund name from crawler list>.pdf

Each crawler-list row runs in its own subprocess. A failure in one fund is
recorded in the JSON report and does not stop later funds unless --fail-fast is
used.

Usage:
  python run_all_downloaders.py
  python run_all_downloaders.py --timeout 600 --retries 2
  python run_all_downloaders.py --only 汇丰
  python run_all_downloaders.py --exclude ubs --exclude fidelity_tw
  python run_all_downloaders.py --list
"""

import argparse
import concurrent.futures
import fnmatch
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
from datetime import datetime

from common_paths import safe_component


CODE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(
    os.environ.get("FACTSHEET_PROJECT_ROOT") or os.path.join(CODE_DIR, "..")
)
COMPANIES_DIR = os.path.join(CODE_DIR, "companies")
STATE_ROOT = os.path.abspath(
    os.path.expanduser(
        os.path.expandvars(
            os.environ.get("FACTSHEET_STATE_ROOT") or os.path.join(PROJECT_ROOT, "..", "..", "fund-research-state")
        )
    )
)
CRAWLER_LIST_PATH = os.path.join(STATE_ROOT, "inputs", "爬虫list.xlsx")
if os.path.isfile(os.path.join(PROJECT_ROOT, "爬虫list.xlsx")):
    CRAWLER_LIST_PATH = os.path.join(PROJECT_ROOT, "爬虫list.xlsx")
if os.environ.get("FACTSHEET_CRAWLER_WORKBOOK"):
    CRAWLER_LIST_PATH = os.path.abspath(os.path.expanduser(os.path.expandvars(os.environ["FACTSHEET_CRAWLER_WORKBOOK"])))
DEFAULT_TIMEOUT = 300
DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY = 5
DEFAULT_WORKERS = 5
OUTPUT_TAIL_CHARS = 6000
DISCOVERY_PATTERNS = (
    "download_*_factsheet.py",
    "download_*_documents.py",
    "download_*_reports.py",
    "download_monthly_report.py",
    "download_periodic_report.py",
)
REQUIRED_COLUMNS = ("基金", "大类", "运行方式")
OPTIONAL_COLUMNS = ("管理人", "小类", "月报链接", "备选链接(内地站)")
RETRYABLE_FAILURE_CATEGORIES = {"timeout", "network", "runner_exception"}
FRIENDLY_FAILURE_MESSAGES = {
    "timeout": "网站响应超时",
    "network": "网络连接失败",
    "forbidden": "网站暂时拒绝访问",
    "not_found": "下载地址不存在",
    "missing_document": "网站上没有找到对应月报",
    "invalid_download": "下载内容不是有效 PDF",
    "source_website_changed": "基金公司网站结构可能已改变",
    "runner_exception": "下载程序运行异常",
    "config_error": "下载配置不完整，需要人工处理",
}
OUTPUT_FILE_RE = re.compile(r"([A-Za-z]:\\[^\r\n]+?\.(?:pdf|json|xlsx|csv))", re.I)
RETRYABLE_FAILURE_TEXT = (
    "readtimeout",
    "connecttimeout",
    "connectionerror",
    "proxyerror",
    "temporarily unavailable",
    "connection reset",
    "remote end closed connection",
    "sslerror",
    "ssl eof",
    "unexpected_eof",
    "unexpected eof",
    "eof occurred in violation of protocol",
    "max retries exceeded",
    "net::err_timed_out",
    "net::err_connection",
    "timeout",
    "timed out",
)


def now_stamp():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def default_report_path(run_id):
    run_id = safe_component(run_id)
    return os.path.join(
        os.environ.get("FACTSHEET_DATA_ROOT") or os.path.join(PROJECT_ROOT, "data"),
        run_id,
        "reports",
        f"download_run_report_{run_id}.json",
    )


def tail_text(value, limit=OUTPUT_TAIL_CHARS):
    value = value or ""
    if len(value) <= limit:
        return value
    return value[-limit:]


def discover_scripts():
    scripts = []
    for dirpath, _, filenames in os.walk(COMPANIES_DIR):
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel_path = os.path.relpath(path, CODE_DIR)
            rel_path = rel_path.replace(os.sep, "/")
            if any(fnmatch.fnmatch(name, pattern) for pattern in DISCOVERY_PATTERNS):
                scripts.append(rel_path)
    scripts.sort()
    return scripts


def script_lookup():
    lookup = {}
    for rel_path in discover_scripts():
        lookup[os.path.basename(rel_path).lower()] = rel_path
        lookup[rel_path.lower()] = rel_path
    return lookup


def normalize_cell(value):
    return str(value or "").strip()


_PROGRESS_PRINT_LOCK = threading.Lock()


class ConsoleProgress:
    """Render concurrent downloader progress on one terminal line."""

    def __init__(self, total, stream=None, bar_width=20):
        self.total = max(0, int(total))
        self.stream = stream or sys.stdout
        self.bar_width = max(10, int(bar_width))
        self.completed = 0
        self.succeeded = 0
        self.failed = 0
        self.active = {}
        self.last_width = 0
        self.activity_tick = 0
        self.closed = False
        isatty = getattr(self.stream, "isatty", None)
        self.interactive = bool(isatty and isatty()) or os.environ.get("FACTSHEET_PROGRESS_PIPE") == "1"

    @staticmethod
    def _short_name(value, limit=16):
        value = str(value or "").strip()
        if len(value) <= limit:
            return value
        return value[: max(1, limit - 1)] + "…"

    @staticmethod
    def _active_label(value, limit=24):
        """渲染“正在”标签：保留“（第 x/y 次尝试）/（等待重试 Ns）”等后缀。"""
        text = str(value or "").strip()
        if "（" in text:
            base, _, note = text.partition("（")
            suffix = "（" + note
            keep = max(6, limit - min(len(suffix), 12))
            return ConsoleProgress._short_name(base, keep) + suffix
        return ConsoleProgress._short_name(text, limit)

    def _line(self):
        ratio = self.completed / self.total if self.total else 1
        filled = min(self.bar_width, int(ratio * self.bar_width))
        bar = "█" * filled + "░" * (self.bar_width - filled)
        pulse = "|/-\\"[self.activity_tick % 4]
        active_names = list(self.active.values())
        if active_names:
            visible = "、".join(self._active_label(name) for name in active_names[:2])
            if len(active_names) > 2:
                visible += f" 等{len(active_names)}项"
            active_note = f"  正在：{visible}"
        else:
            active_note = ""
        return (
            f"下载进度 {pulse} [{bar}] {self.completed}/{self.total}"
            f"  成功 {self.succeeded}  失败 {self.failed}{active_note}"
        )

    @staticmethod
    def _display_width(value):
        return sum(
            2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1
            for character in value
        )

    def _render(self, final=False):
        if not self.interactive and not final:
            return
        line = self._line()
        line_width = self._display_width(line)
        padded = line + " " * max(0, self.last_width - line_width)
        self.stream.write("\r" + padded)
        if final:
            self.stream.write("\n")
        self.stream.flush()
        self.last_width = line_width

    def __call__(self, payload):
        with _PROGRESS_PRINT_LOCK:
            if self.closed:
                return
            self.activity_tick += 1
            event = payload.get("event")
            row_number = payload.get("row_number")
            if event == "started":
                self.active[row_number] = payload.get("fund_name") or "未命名基金"
            elif event == "progress":
                # 让进度行体现“第几次尝试 / 等待重试”，而不是一直显示同一个基金名
                phase = payload.get("phase")
                fund = payload.get("fund_name") or self.active.get(row_number) or "未命名基金"
                if phase == "attempt_started" and int(payload.get("attempt") or 1) > 1:
                    self.active[row_number] = (
                        f"{fund}（第 {payload.get('attempt')}/{payload.get('max_attempts')} 次尝试）"
                    )
                elif phase == "retry_wait":
                    self.active[row_number] = (
                        f"{fund}（等待重试 {payload.get('retry_delay_seconds')}s）"
                    )
            elif event == "finished":
                self.active.pop(row_number, None)
                self.completed += 1
                result = payload.get("result") or {}
                if result.get("status") == "failed":
                    self.failed += 1
                else:
                    self.succeeded += 1
            self._render()

    def finish(self):
        with _PROGRESS_PRINT_LOCK:
            if self.closed:
                return
            self.closed = True
            self._render(final=True)


def load_workbook_rows(path, sheet_names=None):
    """读取爬虫list.xlsx的一个或多个工作表并解析为下载任务列表。

    参数 path：xlsx 文件路径；sheet_names：None 表示只处理第一个工作表，
    "all" 表示处理全部工作表（自动跳过缺少必需列或为空的说明页），
    其他值表示按名称处理指定工作表（缺失必需列时报错）。
    """
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("openpyxl is required to read 爬虫list.xlsx. Run: pip install -r requirements.txt") from exc

    if not os.path.exists(path):
        raise FileNotFoundError(f"Crawler list not found: {path}")

    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    all_mode = sheet_names == "all"
    if sheet_names == "all":
        sheet_names = list(workbook.sheetnames)
    elif sheet_names is None:
        sheet_names = [workbook.sheetnames[0]]
    else:
        missing_sheets = [name for name in sheet_names if name not in workbook.sheetnames]
        if missing_sheets:
            raise ValueError(
                f"爬虫list.xlsx 中不存在工作表: {', '.join(missing_sheets)}；"
                f"可用: {', '.join(workbook.sheetnames)}"
            )

    parsed = []
    lookup = script_lookup()
    global_row = 1  # 跨工作表递增的全局行号，保证报告与重试合并时唯一
    for sheet_name in sheet_names:
        sheet = workbook[sheet_name]
        rows = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows)
        except StopIteration:
            if all_mode:
                continue
            raise ValueError(f"工作表 {sheet_name} 为空：{path}")

        headers = {normalize_cell(value): index for index, value in enumerate(header_row)}
        missing = [name for name in REQUIRED_COLUMNS if name not in headers]
        if missing:
            if all_mode:
                print(f"[!] “所有”模式下跳过非爬虫列表工作表：{sheet_name}（缺少必需列）")
                continue
            raise ValueError(f"工作表 {sheet_name} 缺少必需列: {', '.join(missing)}")

        for excel_row_number, row in enumerate(rows, start=2):
            global_row += 1
            record = {}
            for name in REQUIRED_COLUMNS + OPTIONAL_COLUMNS:
                index = headers.get(name)
                record[name] = normalize_cell(row[index]) if index is not None and index < len(row) else ""

            if not any(record.values()):
                continue

            fund_col, category_col, run_command_col = REQUIRED_COLUMNS
            manager_col, subcategory_col, source_url_col, fallback_note_col = OPTIONAL_COLUMNS
            config_error = ""
            script = ""
            args = []
            if not record[run_command_col]:
                config_error = f"Row {global_row} (工作表 {sheet_name}) missing {run_command_col} for fund: {record.get(fund_col) or '(empty fund name)'}"
            else:
                try:
                    script, args = parse_run_command(record[run_command_col], lookup, global_row)
                except ValueError as exc:
                    config_error = str(exc)
            parsed.append({
                "row_number": global_row,
                "excel_row": excel_row_number,
                "sheet": sheet_name,
                "fund_name": record[fund_col],
                "manager": record[manager_col],
                "category": record[category_col],
                "subcategory": record[subcategory_col],
                "source_url": record[source_url_col],
                "fallback_note": record[fallback_note_col],
                "run_command": record[run_command_col],
                "script": script,
                "args": args,
                "config_error": config_error,
            })
    if not parsed:
        if sheet_names == "all":
            raise ValueError(f"Crawler list is empty: {path}")
        raise ValueError(f"工作表 {', '.join(sheet_names)} 没有可用的爬虫列表行：{path}")
    return parsed


def parse_run_command(command_text, lookup, row_number):
    try:
        parts = shlex.split(command_text, posix=True)
    except ValueError as exc:
        raise ValueError(f"Row {row_number} has invalid 运行方式 quoting: {command_text}") from exc

    if not parts:
        raise ValueError(f"Row {row_number} has empty 运行方式")

    executable = os.path.basename(parts[0]).lower()
    if executable in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        parts = parts[1:]
    if not parts:
        raise ValueError(f"Row {row_number} 运行方式 does not include a script: {command_text}")

    script_token = parts[0].replace("\\", "/")
    script_key = os.path.basename(script_token).lower()
    rel_path = lookup.get(script_token.lower()) or lookup.get(script_key)
    if not rel_path:
        known = ", ".join(sorted({os.path.basename(path) for path in lookup.values()}))
        raise ValueError(f"Row {row_number} references unknown downloader script '{parts[0]}'. Known scripts: {known}")
    return rel_path, parts[1:]


def matches_any(value, patterns):
    if not patterns:
        return False
    lowered = value.lower()
    for pattern in patterns:
        if fnmatch.fnmatch(lowered, pattern.lower()) or pattern.lower() in lowered:
            return True
    return False


def target_search_text(target):
    values = [
        target.get("script", ""),
        target.get("fund_name", ""),
        target.get("manager", ""),
        target.get("category", ""),
        target.get("subcategory", ""),
        target.get("run_command", ""),
    ]
    return " ".join(values)


def selected_targets(only_patterns, exclude_patterns, crawler_list_path, sheet_names=None):
    """按筛选条件选择下载任务。

    参数 only_patterns/exclude_patterns：包含/排除匹配；crawler_list_path：xlsx 路径；
    参数 sheet_names：None 或 "all" 或工作表名列表（见 load_workbook_rows）。
    """
    targets = load_workbook_rows(crawler_list_path, sheet_names)
    # Rows without a run command are intentionally manual and are not download
    # failures.  The current operating index has one such fund, so the automatic
    # batch contains 61 runnable rows.
    targets = [target for target in targets if target.get("run_command")]
    if only_patterns:
        targets = [target for target in targets if matches_any(target_search_text(target), only_patterns)]
    if exclude_patterns:
        targets = [target for target in targets if not matches_any(target_search_text(target), exclude_patterns)]
    return targets


def classify_failure(returncode, stdout, stderr):
    combined = f"{stdout}\n{stderr}"
    lowered = combined.lower()
    if returncode == 0:
        return "ok"
    if "invaliddownloaderror" in lowered or "downloaded content is not a pdf" in lowered:
        return "invalid_download"
    if "missingdocumenterror" in lowered or ("no " in lowered and " matched" in lowered):
        return "missing_document"
    if "sourcewebsiteerror" in lowered or "website shape" in lowered:
        return "source_website_changed"
    if "not found" in lowered:
        return "not_found"
    if "403" in combined or "Forbidden" in combined:
        return "forbidden"
    if "Timeout" in combined or "timed out" in combined or "ERR_TIMED_OUT" in combined:
        return "timeout"
    if (
        "ConnectionError" in combined
        or "WinError 10013" in combined
        or "ProxyError" in combined
        or "SSLError" in combined
        or "UNEXPECTED_EOF" in combined
        or "Max retries exceeded" in combined
    ):
        return "network"
    if "Traceback (most recent call last)" in combined:
        return "unexpected_exception"
    return "nonzero_exit"


def extract_output_file(stdout, stderr):
    combined = f"{stdout}\n{stderr}"
    candidates = []
    data_root = os.path.join(PROJECT_ROOT, "data").lower()
    for match in OUTPUT_FILE_RE.finditer(combined):
        path = match.group(1).strip().rstrip(").,;")
        if path.lower().startswith(data_root):
            candidates.append(path)
    pdfs = [path for path in candidates if path.lower().endswith(".pdf")]
    return (pdfs or candidates or [""])[-1]


def report_month_from_output_file(path):
    return ""


def infer_success_status(stdout, stderr, output_file):
    combined = f"{stdout}\n{stderr}".lower()
    if "existing" in combined or "skipped" in combined or "已存在" in combined or "跳过" in combined:
        return "existing"
    if output_file:
        return "downloaded"
    return "skipped"


def expected_output_file_for_run(run_id, category, fund_name, output_root=None):
    return os.path.join(
        output_root or os.path.join(PROJECT_ROOT, "data"),
        safe_component(run_id),
        "factsheet",
        safe_component(category),
        f"{safe_component(fund_name)}.pdf",
    )


def extract_exception_summary(stdout, stderr):
    combined = f"{stdout}\n{stderr}".strip()
    if not combined:
        return ""
    lines = [line.strip() for line in combined.splitlines() if line.strip()]
    if not lines:
        return ""

    prefixes = (
        "FactsheetError:",
        "MissingDocumentError:",
        "InvalidDownloadError:",
        "SourceWebsiteError:",
        "RuntimeError:",
        "ValueError:",
        "KeyError:",
        "IndexError:",
        "TypeError:",
        "requests.",
        "playwright.",
    )
    for line in reversed(lines):
        if line.startswith(prefixes):
            return line
    for line in reversed(lines):
        if "Error:" in line or "Exception:" in line:
            return line
    return lines[-1]


def emit_progress(progress_callback, **payload):
    if progress_callback is None:
        return
    try:
        progress_callback(payload)
    except Exception:
        # Progress reporting must never interrupt a downloader process.
        pass


def terminate_process_tree(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except Exception:
            pass
    if proc.poll() is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def run_target(
    target,
    timeout,
    run_id,
    cancel_event=None,
    progress_callback=None,
    attempt=1,
    max_attempts=1,
    extra_env=None,
):
    started = time.time()
    if target.get("config_error"):
        return {
            "row_number": target.get("row_number"),
            "fund_name": target.get("fund_name"),
            "manager": target.get("manager"),
            "category": target.get("category"),
            "subcategory": target.get("subcategory"),
            "source_url": target.get("source_url"),
            "run_command": target.get("run_command"),
            "script": target.get("script", ""),
            "args": target.get("args", []),
            "command": [],
            "output_category": target.get("category") or target.get("manager") or "未分类",
            "output_fund": target.get("fund_name") or "",
            "status": "failed",
            "failure_category": "config_error",
            "output_file": "",
            "report_month": "",
            "returncode": None,
            "duration_seconds": round(time.time() - started, 2),
            "exception_summary": target["config_error"],
            "stdout_tail": "",
            "stderr_tail": "",
        }

    script_name = target["script"]
    script_path = os.path.join(CODE_DIR, *script_name.split("/"))
    worker_exe = str(os.environ.get("FACTSHEET_WORKER_EXE") or "").strip()
    if worker_exe:
        command = [worker_exe, "--worker-script", f"code/{script_name}"] + target.get("args", [])
    else:
        command = [sys.executable, script_path] + target.get("args", [])
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["PYTHONPATH"] = CODE_DIR + os.pathsep + env.get("PYTHONPATH", "")
    env["FACTSHEET_RUN_ID"] = run_id
    env["FACTSHEET_OUTPUT_CATEGORY"] = target.get("category") or target.get("manager") or "未分类"
    env["FACTSHEET_OUTPUT_FUND"] = target.get("fund_name") or os.path.splitext(os.path.basename(script_name))[0]
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})

    base_result = {
        "row_number": target.get("row_number"),
        "fund_name": target.get("fund_name"),
        "manager": target.get("manager"),
        "category": target.get("category"),
        "subcategory": target.get("subcategory"),
        "source_url": target.get("source_url"),
        "run_command": target.get("run_command"),
        "script": script_name,
        "args": target.get("args", []),
        "command": command,
        "output_category": env["FACTSHEET_OUTPUT_CATEGORY"],
        "output_fund": env["FACTSHEET_OUTPUT_FUND"],
        "expected_output_file": expected_output_file_for_run(
            run_id,
            env["FACTSHEET_OUTPUT_CATEGORY"],
            env["FACTSHEET_OUTPUT_FUND"],
            env.get("FACTSHEET_OUTPUT_ROOT"),
        ),
    }

    try:
        popen_kwargs = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **popen_kwargs,
        )
        last_progress_second = -1
        stdout = ""
        stderr = ""
        while True:
            elapsed = int(time.time() - started)
            if elapsed != last_progress_second:
                emit_progress(
                    progress_callback,
                    phase="running",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    elapsed_seconds=elapsed,
                    timeout_seconds=timeout,
                )
                last_progress_second = elapsed
            if cancel_event is not None and cancel_event.is_set():
                terminate_process_tree(proc)
                stdout, stderr = proc.communicate()
                duration = round(time.time() - started, 2)
                return {
                    **base_result,
                    "status": "cancelled",
                    "failure_category": "cancelled",
                    "output_file": "",
                    "report_month": "",
                    "returncode": None,
                    "duration_seconds": duration,
                    "exception_summary": "Cancelled by user.",
                    "stdout_tail": tail_text(stdout),
                    "stderr_tail": tail_text(stderr),
                }
            if time.time() - started > timeout:
                terminate_process_tree(proc)
                stdout, stderr = proc.communicate()
                raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
            try:
                stdout, stderr = proc.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
        duration = round(time.time() - started, 2)
        output_file = extract_output_file(stdout, stderr)
        if not output_file and os.path.exists(base_result["expected_output_file"]):
            output_file = base_result["expected_output_file"]
        status = infer_success_status(stdout, stderr, output_file) if proc.returncode == 0 else "failed"
        category = classify_failure(proc.returncode, stdout, stderr)
        return {
            **base_result,
            "status": status,
            "failure_category": category,
            "output_file": output_file,
            "report_month": report_month_from_output_file(output_file),
            "returncode": proc.returncode,
            "duration_seconds": duration,
            "exception_summary": extract_exception_summary(stdout, stderr),
            "stdout_tail": tail_text(stdout),
            "stderr_tail": tail_text(stderr),
        }
    except subprocess.TimeoutExpired as exc:
        duration = round(time.time() - started, 2)
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
        return {
            **base_result,
            "status": "failed",
            "failure_category": "timeout",
            "output_file": "",
            "report_month": "",
            "returncode": None,
            "duration_seconds": duration,
            "exception_summary": f"Timed out after {timeout} seconds",
            "stdout_tail": tail_text(stdout),
            "stderr_tail": tail_text(stderr),
        }
    except Exception as exc:
        duration = round(time.time() - started, 2)
        return {
            **base_result,
            "status": "failed",
            "failure_category": "runner_exception",
            "output_file": "",
            "report_month": "",
            "returncode": None,
            "duration_seconds": duration,
            "exception_summary": f"{type(exc).__name__}: {exc}",
            "stdout_tail": "",
            "stderr_tail": traceback.format_exc(),
        }



def is_retryable_result(result):
    if result.get("status") != "failed":
        return False
    if result.get("failure_category") in RETRYABLE_FAILURE_CATEGORIES:
        return True

    combined = "\n".join([
        str(result.get("failure_category") or ""),
        str(result.get("exception_summary") or ""),
        str(result.get("stdout_tail") or ""),
        str(result.get("stderr_tail") or ""),
    ]).lower()
    return any(token in combined for token in RETRYABLE_FAILURE_TEXT)


def retry_history_item(result):
    return {
        "attempt": result.get("attempt"),
        "status": result.get("status"),
        "failure_category": result.get("failure_category"),
        "returncode": result.get("returncode"),
        "duration_seconds": result.get("duration_seconds"),
        "exception_summary": result.get("exception_summary"),
    }


def run_target_with_retries(
    target,
    timeout,
    run_id,
    retries,
    retry_delay,
    cancel_event=None,
    progress_callback=None,
    extra_env=None,
):
    attempts = []
    max_attempts = retries + 1
    for attempt in range(1, max_attempts + 1):
        attempt_env = dict(extra_env or {})
        if attempt_env.get("FACTSHEET_NETWORK_AUTO") == "1" and attempt > 1:
            # First try is direct. On retry, alternate system network settings
            # and direct mode so the same build works on home and corporate PCs.
            attempt_env["FACTSHEET_TRUST_ENV"] = "1" if attempt % 2 == 0 else "0"
        if attempt > 1:
            mode_note = ""
            if attempt_env.get("FACTSHEET_NETWORK_AUTO") == "1":
                mode_note = (
                    "（网络模式：系统代理）"
                    if attempt_env.get("FACTSHEET_TRUST_ENV") == "1"
                    else "（网络模式：直连）"
                )
            print(
                f"[RETRY] {target.get('fund_name')}：开始第 {attempt}/{max_attempts} 次尝试{mode_note}"
            )
        emit_progress(
            progress_callback,
            phase="attempt_started",
            attempt=attempt,
            max_attempts=max_attempts,
            elapsed_seconds=0,
            timeout_seconds=timeout,
            network_mode="system" if attempt_env.get("FACTSHEET_TRUST_ENV") == "1" else "direct",
        )
        if cancel_event is not None and cancel_event.is_set():
            result = {
                "row_number": target.get("row_number"),
                "fund_name": target.get("fund_name"),
                "manager": target.get("manager"),
                "category": target.get("category"),
                "subcategory": target.get("subcategory"),
                "source_url": target.get("source_url"),
                "run_command": target.get("run_command"),
                "script": target.get("script", ""),
                "args": target.get("args", []),
                "command": [],
                "output_category": target.get("category") or target.get("manager") or "未分类",
                "output_fund": target.get("fund_name") or "",
                "status": "cancelled",
                "failure_category": "cancelled",
                "output_file": "",
                "report_month": "",
                "returncode": None,
                "duration_seconds": 0,
                "exception_summary": "Cancelled before start.",
                "stdout_tail": "",
                "stderr_tail": "",
            }
        else:
            result = run_target(
                target,
                timeout,
                run_id,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
                attempt=attempt,
                max_attempts=max_attempts,
                extra_env=attempt_env,
            )
        result["attempt"] = attempt
        result["max_attempts"] = max_attempts
        attempts.append(result)

        if result["status"] == "cancelled":
            break
        if result["status"] != "failed":
            break
        if attempt >= max_attempts or not is_retryable_result(result):
            break

        emit_progress(
            progress_callback,
            phase="retry_wait",
            attempt=attempt,
            max_attempts=max_attempts,
            elapsed_seconds=result.get("duration_seconds", 0),
            timeout_seconds=timeout,
            retry_delay_seconds=retry_delay,
            failure_category=result.get("failure_category", ""),
            exception_summary=result.get("exception_summary", ""),
        )
        print(
            f"[RETRY] {target.get('fund_name')}：第 {attempt}/{max_attempts} 次尝试失败"
            f"（{friendly_failure_message(result)}），{retry_delay}s 后重试"
        )
        detail = str(result.get("exception_summary") or "").strip().replace("\n", " ")
        if detail:
            print(f"         原因：{detail[:180]}")
        for remaining in range(max(0, retry_delay), 0, -1):
            if cancel_event is not None and cancel_event.is_set():
                break
            if remaining <= 3:
                print(f"[RETRY] {target.get('fund_name')}：{remaining}s 后重试...")
            for _ in range(5):
                if cancel_event is not None and cancel_event.is_set():
                    break
                time.sleep(0.2)
        if cancel_event is not None and cancel_event.is_set():
            break

    final = attempts[-1]
    final["attempt_count"] = len(attempts)
    final["retried"] = len(attempts) > 1
    final["retry_history"] = [retry_history_item(item) for item in attempts[:-1]]
    if final["status"] in {"downloaded", "existing", "skipped"} and len(attempts) > 1:
        final["failure_category"] = "ok_after_retry"
        final["exception_summary"] = f"Succeeded on attempt {len(attempts)} after retryable failure."
        print(
            f"[RETRY] {final.get('fund_name')}：第 {len(attempts)}/{max_attempts} 次尝试成功"
        )
    return final


def company_key(target):
    """Rows for the same manager share one serial lane."""
    value = target.get("manager") or target.get("script") or target.get("fund_name")
    normalized = re.sub(r"\s+", "", str(value or "")).casefold()
    return normalized or f"row-{target.get('row_number')}"


def runner_exception_result(target, exc):
    return {
        "row_number": target.get("row_number"),
        "fund_name": target.get("fund_name"),
        "manager": target.get("manager"),
        "category": target.get("category"),
        "subcategory": target.get("subcategory"),
        "source_url": target.get("source_url"),
        "run_command": target.get("run_command"),
        "script": target.get("script", ""),
        "args": target.get("args", []),
        "command": [],
        "output_category": target.get("category") or target.get("manager") or "未分类",
        "output_fund": target.get("fund_name") or "",
        "status": "failed",
        "failure_category": "runner_exception",
        "output_file": "",
        "report_month": "",
        "returncode": None,
        "duration_seconds": 0,
        "exception_summary": f"{type(exc).__name__}: {exc}",
        "stdout_tail": "",
        "stderr_tail": "",
        "attempt_count": 0,
        "max_attempts": 0,
        "retried": False,
        "retry_history": [],
    }


def run_targets_parallel(
    targets,
    timeout,
    run_id,
    retries,
    retry_delay,
    workers=DEFAULT_WORKERS,
    fail_fast=False,
    runner=run_target_with_retries,
    progress_emitter=None,
):
    """Run different managers concurrently while serializing each manager."""
    workers = max(1, min(int(workers), DEFAULT_WORKERS))
    total = len(targets)
    console_progress = None
    if progress_emitter is None:
        console_progress = ConsoleProgress(total)
        progress_emitter = console_progress
    pending = list(enumerate(targets, start=1))
    active = {}
    active_companies = set()
    results_by_index = {}
    cancel_event = threading.Event()

    for queue_index, target in pending:
        progress_emitter(
            {
                "event": "queued",
                "index": queue_index,
                "total": total,
                "row_number": target["row_number"],
                "fund_name": target["fund_name"],
                "manager": target.get("manager") or "",
                "category": target["category"],
                "source_url": target.get("source_url") or "",
                "workers": workers,
            }
        )

    def run_one(queue_index, target):
        def progress_callback(payload):
            progress_emitter(
                {
                    "event": "progress",
                    "index": queue_index,
                    "total": total,
                    "row_number": target["row_number"],
                    "fund_name": target["fund_name"],
                    "manager": target.get("manager") or "",
                    "category": target["category"],
                    "source_url": target.get("source_url") or "",
                    "workers": workers,
                    **payload,
                }
            )

        return runner(
            target,
            timeout,
            run_id,
            retries,
            retry_delay,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )

    completed_count = 0
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="fund-download"
    ) as executor:
        try:
            while pending or active:
                while pending and len(active) < workers and not cancel_event.is_set():
                    selected = next(
                        (
                            position
                            for position, (_, target) in enumerate(pending)
                            if company_key(target) not in active_companies
                        ),
                        None,
                    )
                    if selected is None:
                        break
                    queue_index, target = pending.pop(selected)
                    key = company_key(target)
                    progress_emitter(
                        {
                            "event": "started",
                            "index": queue_index,
                            "total": total,
                            "row_number": target["row_number"],
                            "fund_name": target["fund_name"],
                            "manager": target.get("manager") or "",
                            "category": target["category"],
                            "source_url": target.get("source_url") or "",
                            "workers": workers,
                            "active_count": len(active) + 1,
                        }
                    )
                    future = executor.submit(run_one, queue_index, target)
                    active[future] = (queue_index, target, key)
                    active_companies.add(key)

                if not active:
                    break
                done, _ = concurrent.futures.wait(
                    active, timeout=0.25,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    queue_index, target, key = active.pop(future)
                    active_companies.discard(key)
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        result = runner_exception_result(target, exc)
                    results_by_index[queue_index] = result
                    completed_count += 1
                    progress_emitter(
                        {
                            "event": "finished",
                            "index": completed_count,
                            "queue_index": queue_index,
                            "total": total,
                            "row_number": target["row_number"],
                            "fund_name": target["fund_name"],
                            "manager": target.get("manager") or "",
                            "category": target["category"],
                            "source_url": target.get("source_url") or "",
                            "workers": workers,
                            "active_count": len(active),
                            "result": result,
                        }
                    )
                    if result["status"] == "failed":
                        if fail_fast:
                            cancel_event.set()
                            pending.clear()
        except KeyboardInterrupt:
            cancel_event.set()
            pending.clear()
            concurrent.futures.wait(active, timeout=15)
            raise
        finally:
            if console_progress is not None:
                console_progress.finish()

    return [results_by_index[index] for index in sorted(results_by_index)]


def write_report(results, report_path, run_id, crawler_list_path, backfilled=0):
    """写出下载报告 JSON。

    参数 results：下载结果项；report_path：报告路径；run_id：批次号；
    参数 crawler_list_path：爬虫表路径；backfilled：本轮补全成功的行数（0 表示无补全）。
    """
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "project_root": PROJECT_ROOT,
        "code_dir": CODE_DIR,
        "crawler_list_path": crawler_list_path,
        "data_dir": os.path.join(PROJECT_ROOT, "data", safe_component(run_id), "factsheet"),
        "total": len(results),
        # 只有真正下载/已存在的行算成功，跳过与失败单独统计
        "ok": sum(
            1
            for item in results
            if item["status"] not in ("failed", "skipped", "cancelled")
        ),
        "downloaded": sum(1 for item in results if item["status"] == "downloaded"),
        "existing": sum(1 for item in results if item["status"] == "existing"),
        "backfilled": int(backfilled or 0),
        "skipped": sum(1 for item in results if item["status"] == "skipped"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "retried": sum(1 for item in results if item.get("retried")),
        "ok_after_retry": sum(1 for item in results if item.get("status") != "failed" and item.get("retried")),
    }
    payload = {"summary": summary, "results": results}
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return summary


def friendly_failure_message(item):
    category = str(item.get("failure_category") or "")
    return FRIENDLY_FAILURE_MESSAGES.get(category) or str(
        item.get("exception_summary") or "未知错误"
    )


def print_download_result(results, *, retry_round=0, backfilled=0):
    """打印下载结果汇总；参数 backfilled：补全成功的行数（>0 时在汇总中显示“回填”）。"""
    failures = [item for item in results if item["status"] == "failed"]
    skipped = [item for item in results if item["status"] == "skipped"]
    total = len(results)
    success = total - len(failures) - len(skipped)
    title = "下载重试结果" if retry_round else "下载完成"
    prefix = f"回填 {int(backfilled)} 个｜" if backfilled else ""
    print("")
    print("=" * 68)
    if skipped:
        print(
            f"  {title}：{prefix}成功 {success} 个｜跳过 {len(skipped)} 个"
            f"｜失败 {len(failures)} 个｜共 {total} 个"
        )
    else:
        print(
            f"  {title}：{prefix}成功 {success} 个"
            f"｜失败 {len(failures)} 个｜共 {total} 个"
        )
    print("=" * 68)
    if skipped:
        print("  跳过项目：")
        for index, item in enumerate(skipped, start=1):
            print(
                f"  {index:>2}. [{item.get('category') or '未分类'}] "
                f"{item.get('fund_name') or '未命名基金'}"
            )
            print(f"      原因：{item.get('exception_summary') or '跳过'}")
    if not failures:
        print("  所有自动下载项目均已完成。")
        return

    print("  失败项目：")
    for index, item in enumerate(failures, start=1):
        print(
            f"  {index:>2}. [{item.get('category') or '未分类'}] "
            f"{item.get('fund_name') or '未命名基金'}"
        )
        print(f"      原因：{friendly_failure_message(item)}")


def print_unified_progress(summary):
    """输出整轮（含补全与跳过）的汇总进度行，供 GUI 进度栏对齐最终统计。

    参数 summary：write_report 生成的结果汇总（含 ok/skipped/failed）。
    """
    succeeded = int(summary.get("ok", 0) or 0)
    skipped = int(summary.get("skipped", 0) or 0)
    failed = int(summary.get("failed", 0) or 0)
    backfilled = int(summary.get("backfilled", 0) or 0)
    total = succeeded + skipped + failed
    bar = "█" * 20
    backfill_note = f"  回填 {backfilled}" if backfilled else ""
    extra = f"  跳过 {skipped}" if skipped else ""
    print(
        f"下载进度 - [{bar}] {total}/{total}{backfill_note}"
        f"  成功 {succeeded}{extra}  失败 {failed}"
    )


def retryable_failed_targets(targets, results):
    target_by_row = {target.get("row_number"): target for target in targets}
    retry_targets = []
    for result in results:
        if result.get("status") != "failed":
            continue
        target = target_by_row.get(result.get("row_number"))
        if target and target.get("script") and not target.get("config_error"):
            retry_targets.append(target)
    return retry_targets


def merge_retry_results(results, retry_results, retry_round):
    current_by_row = {item.get("row_number"): item for item in results}
    for retry_result in retry_results:
        row_number = retry_result.get("row_number")
        previous = current_by_row.get(row_number) or {}
        history = list(previous.get("interactive_retry_history") or [])
        history.append(retry_history_item(previous))
        retry_result["interactive_retry_history"] = history
        retry_result["interactive_retry_round"] = retry_round
        retry_result["total_attempt_count"] = int(
            previous.get("total_attempt_count")
            or previous.get("attempt_count")
            or 0
        ) + int(retry_result.get("attempt_count") or 0)
        retry_result["retried"] = True
        current_by_row[row_number] = retry_result
    return [
        current_by_row.get(item.get("row_number"), item)
        for item in results
    ]


def ask_retry_failed(count, input_func=input):
    while True:
        try:
            answer = input_func(
                f"\n以上 {count} 个失败项目是否立即重试？"
                "输入 Y 重试，输入 N 结束："
            )
        except (EOFError, KeyboardInterrupt):
            print("\n已结束，不再重试。")
            return False
        normalized = str(answer or "").strip().lower()
        if normalized in {"y", "yes", "是"}:
            return True
        if normalized in {"n", "no", "否", ""}:
            return False
        print("请输入 Y 或 N。")


# 链接补全脚本的统一文件命名模式：code/companies/<公司>/*_fund_link.py
BACKFILL_SCRIPT_PATTERN = "*_fund_link.py"
# 管理人/工作表中文名 到 code/companies 目录名的映射（可继续扩充）
BACKFILL_COMPANY_ALIASES = {
    "思博瑞资管": "allspring",
    "allspring": "allspring",
    "Allspring": "allspring",
    "摩根斯坦利": "morganstanley",
    "morganstanley": "morganstanley",
    "Morgan Stanley": "morganstanley",
    "工银资管": "icbcamg",
    "icbcamg": "icbcamg",
    "ICBC Asset Management": "icbcamg",
    "未来": "mirae",
    "mirae": "mirae",
    "Mirae Asset": "mirae",
    "野村": "nomura",
    "nomura": "nomura",
    "Nomura": "nomura",
    "Muzinich": "muzinich",
    "中信": "citic",
    "CITIC": "citic",
    "友邦": "aia",
    "AIA": "aia",
    "英卓": "mg",
    "M&G": "mg",
    "中国人寿富兰克林": "chinalifefranklin",
    "动量环球": "momentum",
    "Momentum": "momentum",
    "圣詹姆斯广场": "stjamesplace",
    "浦银国际": "puyin",
    "SPDBI": "puyin",
    "华安": "huaan",
    "HuaAn": "huaan",
    "工银瑞信": "icbcubs",
    "ICBC UBS": "icbcubs",
    "三井住友": "smdam",
    "SMD-AM": "smdam",
    "中邮国际": "chinapost",
    "China Post Global": "chinapost",
    "交银国际": "bocom",
    "BOCOM International": "bocom",
    "尚渤投资": "thornburg",
    "Thornburg": "thornburg",
    "招商永隆": "cmbwinglung",
    "CMB Wing Lung": "cmbwinglung",
    "凯基": "kgi",
    "KGI": "kgi",
    "建信": "ccbprincipal",
    "CCB Principal": "ccbprincipal",
    "招证资管": "cmsam",
    "招商证券资管": "cmsam",
    "CMSAM": "cmsam",
    "瑞士盈丰": "newcapital",
    "New Capital": "newcapital",
    "资产管理集团": "assetmg",
    "Asset Management Group": "assetmg",
    "铭基环球": "matthews",
    "Matthews Asia": "matthews",
    "中欧": "zo",
    "ZO": "zo",
    "信达": "ciam",
    "CIAM": "ciam",
    "信银资本": "cncb",
    "CNCB Capital": "cncb",
    "光大": "everbright",
    "Everbright": "everbright",
    "荷宝": "robeco",
    "robeco": "robeco",
    "Robeco": "robeco",
    "贝莱德": "blackrock",
    "blackrock": "blackrock",
    "柏瑞": "pinebridge",
    "pinebridge": "pinebridge",
    "富达": "fidelity",
    "fidelity": "fidelity",
    "东方汇理": "amundi",
    "amundi": "amundi",
    "博时": "bosera",
    "博时基金(国际)有限公司": "bosera",
    "博时基金（国际）有限公司": "bosera",
    "Bosera Asset Management (International) Co., Limited": "bosera",
    "bosera": "bosera",
    "大成": "dcfund",
    "dcfund": "dcfund",
    "安联": "allianz",
    "安联环球": "allianz",
    "安联环球投资": "allianz",
    "安联环球投资基金": "allianz",
    "allianz": "allianz",
    "allianzgi": "allianz",
    "东亚联丰": "buim",
    "buim": "buim",
    "富国": "fullgoal",
    "fullgoal": "fullgoal",
    "富兰克林邓普顿": "franklin",
    "franklin": "franklin",
    "Franklin Templeton": "franklin",
    "施罗德": "schroder",
    "schroder": "schroder",
    "Schroders": "schroder",
    "景顺": "invesco",
    "invesco": "invesco",
    "Invesco Hong Kong Limited": "invesco",
    "联博资产": "alliancebernstein",
    "alliancebernstein": "alliancebernstein",
    "AllianceBernstein": "alliancebernstein",
    "中银保诚": "bocip",
    "bocip": "bocip",
    "BOCI-Prudential": "bocip",
    "BOCI Prudential": "bocip",
    "法巴": "bnp",
    "bnp": "bnp",
    "BNP Paribas": "bnp",
    "BNP Paribas Asset Management": "bnp",
    "宏利": "manulife",
    "manulife": "manulife",
    "Manulife": "manulife",
    "Manulife Investment Management": "manulife",
    "安本标准": "aberdeen",
    "aberdeen": "aberdeen",
    "Aberdeen": "aberdeen",
    "abrdn": "aberdeen",
    "Aberdeen Investments": "aberdeen",
    "恒生": "hangseng",
    "hangseng": "hangseng",
    "Hang Seng": "hangseng",
    "Hang Seng Investment Management": "hangseng",
    "普信": "troweprice",
    "troweprice": "troweprice",
    "T. Rowe Price": "troweprice",
    "T Rowe Price": "troweprice",
    "T. Rowe Price Hong Kong": "troweprice",
    "晋达": "ninetyone",
    "ninetyone": "ninetyone",
    "Ninety One": "ninetyone",
    "Ninety One Hong Kong": "ninetyone",
    "法盛资本": "natixis",
    "natixis": "natixis",
    "Natixis Investment Managers": "natixis",
    "法盛": "natixis",
    "威灵顿": "wellington",
    "wellington": "wellington",
    "Wellington": "wellington",
    "Wellington Management": "wellington",
    "资本集团": "capitalgroup",
    "capitalgroup": "capitalgroup",
    "Capital Group": "capitalgroup",
    "Capital International": "capitalgroup",
    "路博迈": "neuberger",
    "neuberger": "neuberger",
    "Neuberger": "neuberger",
    "Neuberger Berman": "neuberger",
    "Neuberger Berman Asia": "neuberger",
    "霸菱": "barings",
    "barings": "barings",
    "Barings": "barings",
    "Baring": "barings",
    "Baring Asset Management": "barings",
    "保诚瀚亚": "eastspring",
    "eastspring": "eastspring",
    "Eastspring Investments": "eastspring",
    "瑞士百达": "pictet",
    "pictet": "pictet",
    "Pictet": "pictet",
    "首源投资": "firstsentier_global",
    "firstsentier_global": "firstsentier_global",
    "First Sentier Investors Global": "firstsentier_global",
    "骏利亨德森": "janushenderson",
    "janushenderson": "janushenderson",
    "Janus Henderson": "janushenderson",
    "Janus Henderson Investors": "janushenderson",
    "纽约梅隆": "bny",
    "bny": "bny",
    "BNY Mellon": "bny",
    "BNY Investments": "bny",
    "美富信": "mfs",
    "mfs": "mfs",
    "MFS": "mfs",
    "MFS Investment Management": "mfs",
    "高盛": "goldman",
    "goldman": "goldman",
    "Goldman Sachs": "goldman",
    "Goldman Sachs Asset Management": "goldman",
    "信安": "principal",
    "principal": "principal",
    "Principal": "principal",
    "Principal Asset Management": "principal",
    "安盛": "axa",
    "axa": "axa",
    "AXA IM": "axa",
    "AXA Investment Managers": "axa",
    "品浩": "pimco",
    "pimco": "pimco",
    "PIMCO": "pimco",
    "木星资管": "jupiter",
    "jupiter": "jupiter",
    "Jupiter Asset Management": "jupiter",
    "Jupiter Asset Management (Hong Kong) Limited": "jupiter",
    "高腾": "gaoteng",
    "gaoteng": "gaoteng",
    "海通": "gthtam",
    "gthtam": "gthtam",
    "华夏": "chinaamc",
    "华夏基金(香港)有限公司": "chinaamc",
    "华夏基金（香港）有限公司": "chinaamc",
    "China Asset Management (Hong Kong) Limited": "chinaamc",
    "chinaamc": "chinaamc",
    "汇丰": "hsbc",
    "hsbc": "hsbc",
    "惠理": "valuepartners",
    "valuepartners": "valuepartners",
    "汇添富": "99fund",
    "99fund": "99fund",
    "嘉实": "harvest",
    "harvest": "harvest",
    "建银国际": "ccbintl",
    "ccbintl": "ccbintl",
    "首域盈信": "firstsentier",
    "firstsentier": "firstsentier",
    "摩根": "jpmorgan",
    "摩根大通": "jpmorgan",
    "jpmorgan": "jpmorgan",
    "南方": "csop",
    "南方东英": "csop",
    "csop": "csop",
    "瑞银": "ubs",
    "ubs": "ubs",
    "睿远": "foresight",
    "foresight": "foresight",
    "太平": "taiping",
    "太平资管": "taiping",
    "taiping": "taiping",
    "泰康": "taikang",
    "taikang": "taikang",
    "兴证": "xyzq",
    "兴证国际": "xyzq",
    "xyzq": "xyzq",
    "易方达": "efunds_hk",
    "efunds": "efunds_hk",
    "efunds_hk": "efunds_hk",
    "efundshk": "efunds_hk",
    "招银国际": "cmbi",
    "cmbi": "cmbi",
    "中银香港": "bochk",
    "bochk": "bochk",
    "中金": "cicc",
    "cicc": "cicc",
    "华泰": "huatai",
    "huatai": "huatai",
    "招商": "cmsam",
    "招证资管": "cmsam",
    "招商证券资产管理": "cmsam",
    "招商证券资产管理（香港）有限公司": "cmsam",
    "cmsam": "cmsam",
    "广发国际": "gfintl",
    "广发国际资产管理有限公司": "gfintl",
    "GF International Investment Management Limited": "gfintl",
    "gfintl": "gfintl",
    "农银国际": "abci",
    "abci": "abci",
    "农银汇理": "abc_ca",
    "abc_ca": "abc_ca",
    "安本": "aberdeenbasic",
    "aberdeenbasic": "aberdeenbasic",
    "广发基金": "gffund",
    "gffund": "gffund",
    "建银": "ccbintlbasic",
    "ccbintlbasic": "ccbintlbasic",
    "德意志资管": "dws",
    "dws": "dws",
    "平安资管": "pinganhk",
    "中国平安资产管理(香港)有限公司": "pinganhk",
    "中国平安资产管理（香港）有限公司": "pinganhk",
    "Ping An of China Asset Management (Hong Kong) Co Ltd": "pinganhk",
    "pinganhk": "pinganhk",
    "国泰君安": "gtjai",
    "国泰君安资产管理(亚洲)有限公司": "gtjai",
    "国泰君安资产管理（亚洲）有限公司": "gtjai",
    "Guotai Junan Assets (Asia) Limited": "gtjai",
    "gtjai": "gtjai",
    "东方资管": "orientam",
    "东方资产管理": "orientam",
    "东方资产管理(香港)有限公司": "orientam",
    "东方资产管理（香港）有限公司": "orientam",
    "Orient Asset Management (Hong Kong) Limited": "orientam",
    "orientam": "orientam",
    "民银资本": "cmbccapital", "cmbccapital": "cmbccapital",
    "永明": "sunlife", "sunlife": "sunlife",
    "申万宏源": "shenwan", "shenwan": "shenwan",
    "罗安贝": "lordabbett", "lordabbett": "lordabbett",
    "英仕曼": "maninvest", "maninvest": "maninvest",
    "融通基金": "rongtong", "rongtong": "rongtong",
    "誉信": "ram", "ram": "ram",
    "兴证国际": "xyzqintl", "xyzqintl": "xyzqintl",
    "华富建业": "quam", "quam": "quam",
    "国信": "guosen", "guosen": "guosen",
    "国投证券": "sdic", "sdic": "sdic",
    "国泰": "guotai", "guotai": "guotai",
    "国泰全球": "guotaiglobal", "guotaiglobal": "guotaiglobal",
    "国海富兰克林": "sealand", "sealand": "sealand",
    "小飞": "microbit", "microbit": "microbit",
    "工银": "icbcglobal", "icbcglobal": "icbcglobal",
    "巴克莱": "barclays", "barclays": "barclays",
    "弘收投资": "incomepartners", "incomepartners": "incomepartners",
    "日兴资产": "amova", "amova": "amova",
    "日兴资管": "amova_hk", "amova_hk": "amova_hk",
    "时富金融": "cash", "cash": "cash",
    "民银": "cmbc", "cmbc": "cmbc",
    "永丰金": "sinopac", "sinopac": "sinopac",
    "法盛": "loomissayles", "loomissayles": "loomissayles",
    "简坤": "gptx", "gptx": "gptx",
    "融通国际": "rongtongglobal", "rongtongglobal": "rongtongglobal",
    "资威": "icapital", "icapital": "icapital",
    "辉立资本": "phillip", "phillip": "phillip",
    "银河": "galaxy", "galaxy": "galaxy",
    "长盛基金": "changsheng", "changsheng": "changsheng",
    "鹏格斯资本": "cfund", "cfund": "cfund",
}
BACKFILL_SUBPROCESS_TIMEOUT = 14400  # 链接补全脚本单次最长 4 小时
# 一个页表可挂多个补全脚本（按顺序执行，先认领先回填）；未列出的页表走别名自动匹配
BACKFILL_SHEET_SCRIPTS = {
    "富达": [
        "companies/fidelity_hk/fidelity_fund_link.py",
        "companies/fidelity_tw/fidelity_tw_fund_link.py",
    ],
    "首源投资": [
        "companies/firstsentier_global/firstsentier_global_fund_link.py",
    ],
}


def _matches_target_filters(target, only_patterns, exclude_patterns):
    """判断目标行是否通过 --only/--exclude 过滤。"""
    text = target_search_text(target)
    if only_patterns and not matches_any(text, only_patterns):
        return False
    if exclude_patterns and matches_any(text, exclude_patterns):
        return False
    return True


def target_needs_backfill(target):
    """判断一行是否缺少月报链接或运行方式（更细的公司判断由补全脚本 probe 提供）。"""
    source_url = str(target.get("source_url") or "").strip()
    run_command = str(target.get("run_command") or "").strip()
    if not source_url or not run_command:
        return True
    return False


def discover_fund_link_scripts():
    """扫描 code/companies 下所有 *_fund_link.py，返回相对 code 的路径列表。"""
    scripts = []
    for dirpath, _, filenames in os.walk(COMPANIES_DIR):
        for name in sorted(filenames):
            if not fnmatch.fnmatch(name, BACKFILL_SCRIPT_PATTERN):
                continue
            rel_path = os.path.relpath(os.path.join(dirpath, name), CODE_DIR)
            scripts.append(rel_path.replace(os.sep, "/"))
    return sorted(scripts)


def _write_backfill_cells(excel_path, entries):
    """把补全脚本输出的 (sheet, excel_row) -> (I月报链接, J运行方式) 写入爬虫表。

    参数 entries：元素为 (工作表名, Excel行号, 月报链接, 运行方式) 的列表。
    """
    if not entries:
        return
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("openpyxl is required to write backfill results.") from exc
    workbook = openpyxl.load_workbook(excel_path)
    changed_sheets = set()
    try:
        for sheet_name, excel_row, link, run_command in entries:
            if sheet_name not in workbook.sheetnames:
                continue
            sheet = workbook[sheet_name]
            header = [
                str(cell.value).strip() if cell.value is not None else ""
                for cell in sheet[1]
            ]
            link_col = header.index("月报链接") if "月报链接" in header else None
            run_col = header.index("运行方式") if "运行方式" in header else None
            if link_col is None and run_col is None:
                continue
            if link and link_col is not None:
                sheet.cell(excel_row, link_col + 1).value = link
            if run_command and run_col is not None:
                sheet.cell(excel_row, run_col + 1).value = run_command
            changed_sheets.add(sheet_name)
        if changed_sheets:
            workbook.save(excel_path)
    except PermissionError as exc:
        raise RuntimeError(
            f"爬虫表写入失败，请先关闭 Excel 中的该文件再重试：{excel_path}"
        ) from exc
    finally:
        workbook.close()


def fund_link_script_for_company(manager, sheet_name, scripts):
    """为公司匹配链接补全脚本；没有匹配返回 None。"""
    aliases = set()
    for key in (manager, sheet_name):
        key = str(key or "").strip()
        aliases.add(key.lower().replace(" ", ""))
        aliases.add(BACKFILL_COMPANY_ALIASES.get(key, "").lower())
    aliases.discard("")
    for rel_path in scripts:
        parts = rel_path.split("/")
        folder = parts[1] if len(parts) >= 2 else ""
        folder_key = folder.lower().replace("_", "").replace("-", "")
        # 别名同样去掉下划线/连字符后再比较（如 efunds_hk ↔ efundshk）
        if any(
            folder_key in alias.replace("_", "").replace("-", "")
            or alias.replace("_", "").replace("-", "") in folder_key
            for alias in aliases
        ):
            return rel_path
    return None


def fund_link_scripts_for_sheet(manager, sheet_name, scripts):
    """返回某页表要调用的补全脚本列表（支持一个页表多个公司脚本）。"""
    mapped = BACKFILL_SHEET_SCRIPTS.get(str(sheet_name or "").strip())
    if mapped:
        available = [rel for rel in mapped if rel in scripts]
        return available or list(mapped)
    single = fund_link_script_for_company(manager, sheet_name, scripts)
    return [single] if single else []


def _print_backfill_survey(incomplete_rows):
    """按公司分组输出摸查日志：哪些公司新增多少只待补全基金。"""
    groups = {}
    for row in incomplete_rows:
        company = str(row.get("manager") or row.get("sheet") or "未知公司").strip()
        groups.setdefault(company, []).append(row)
    for company, rows in groups.items():
        names = sorted(
            {str(row.get("fund_name") or "") for row in rows if row.get("fund_name")}
        )
        print(
            f"[*] 摸查：公司[{company}] 新增待补全基金 {len(rows)} 行"
            f"（去重 {len(names)} 个基金名）"
        )
        for index in range(0, len(names), 8):
            print(f"    - {'、'.join(names[index:index + 8])}")


def _run_backfill_pipeline(args, crawler_list_path, sheet_names):
    """下载 → 摸查/回填 → 同批次补跑 的三段流程主函数。"""
    raw_rows = load_workbook_rows(crawler_list_path, sheet_names)
    filtered = [
        row for row in raw_rows if _matches_target_filters(row, args.only, args.exclude)
    ]
    runnable_rows = [row for row in filtered if row.get("run_command")]
    incomplete_rows = [row for row in filtered if target_needs_backfill(row)]
    # 各公司“是否需要补全”的额外判断由补全脚本 --probe 提供（例如 J 只有脚本名缺参数）
    scripts = discover_fund_link_scripts()
    sheet_script_map = {}
    probed_keys = set()
    if not args.list and scripts:
        sheet_rows_map = {}
        for row in filtered:
            sheet_rows_map.setdefault(row["sheet"], []).append(row)
        for sheet_name, sheet_rows in sheet_rows_map.items():
            manager = str(sheet_rows[0].get("manager") or "").strip()
            script_list = fund_link_scripts_for_sheet(manager, sheet_name, scripts)
            if not script_list:
                continue
            sheet_script_map[sheet_name] = script_list
            if len(script_list) > 1:
                # 多脚本页表（如富达 HK+TW）：不做事先 probe，按顺序补全即可
                continue
            script_rel = script_list[0]
            script_path = os.path.join(CODE_DIR, *script_rel.split("/"))
            try:
                probe_proc = subprocess.run(
                    [
                        sys.executable,
                        script_path,
                        "--probe",
                        "--excel",
                        crawler_list_path,
                        "--sheet",
                        sheet_name,
                    ],
                    cwd=PROJECT_ROOT,
                    env=os.environ.copy(),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=BACKFILL_SUBPROCESS_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                print(f"[!] 工作表[{sheet_name}] 补全探测超时，按基础缺失规则处理")
                continue
            for line in (probe_proc.stdout or "").splitlines():
                parts = line.split("\t")
                if len(parts) >= 1 and parts[0].strip().isdigit():
                    probed_keys.add((sheet_name, int(parts[0].strip())))
        incomplete_rows = [
            row
            for row in filtered
            if target_needs_backfill(row)
            or (row.get("sheet"), row.get("excel_row")) in probed_keys
        ]
    phase1_rows = [
        row
        for row in runnable_rows
        if not target_needs_backfill(row)
        and (row.get("sheet"), row.get("excel_row")) not in probed_keys
    ]

    if args.list:
        for row in runnable_rows + incomplete_rows:
            args_text = " ".join(row.get("args", []))
            marker = "[缺I/J]" if target_needs_backfill(row) else ""
            print(
                f"row {row['row_number']}: {marker} {row['category']} / "
                f"{row['fund_name']} -> {row['script']} {args_text}".rstrip()
            )
        return 0
    if not phase1_rows and not incomplete_rows:
        print("[FAILED] No crawler-list rows selected.")
        return 2

    run_id = safe_component(args.run_id) if args.run_id else now_stamp()
    print(f"[*] Run ID: {run_id}")
    print(f"[*] 阶段1 可下载基金行数：{len(phase1_rows)}；待补全基金行数：{len(incomplete_rows)}")
    print(f"[*] PDF output: {os.path.join(PROJECT_ROOT, 'data', run_id, 'factsheet')}")

    results = []
    backfill_failed = False
    backfilled_count = 0
    if phase1_rows:
        print(f"[*] 阶段1：先下载已有月报链接/运行方式的基金（{len(phase1_rows)} 行）")
        results = run_targets_parallel(
            phase1_rows,
            args.timeout,
            run_id,
            args.retries,
            args.retry_delay,
            workers=args.workers,
            fail_fast=args.fail_fast,
        )

    phase2_rows = []
    skipped_items = []  # 补全阶段未收录/跳过基金，汇总进下载报告与 UI 结果明细
    stop_requested = False
    if incomplete_rows:
        _print_backfill_survey(incomplete_rows)
        completed_keys = set()
        skipped_keys = set()
        sheet_fail_reasons = {}
        backfill_entries = []  # (工作表, Excel行号, I月报链接, J运行方式)

        def _on_sigterm(_signum, _frame):
            """GUI 停止时把信号转成收尾请求，避免直接退出丢失已补全数据。"""
            nonlocal stop_requested
            stop_requested = True

        signal.signal(signal.SIGTERM, _on_sigterm)
        row_by_key = {(row["sheet"], row["excel_row"]): row for row in incomplete_rows}

        def _skip_item(source_row, reason):
            """把未成功补全的基金转成报告项，供 UI 结果明细汇总。"""
            return {
                **{key: source_row[key] for key in (
                    "row_number",
                    "fund_name",
                    "manager",
                    "category",
                    "subcategory",
                    "source_url",
                    "run_command",
                )},
                "status": "skipped",
                "output_file": "",
                "exception_summary": reason,
            }

        by_sheet = {}
        for row in incomplete_rows:
            by_sheet.setdefault(row["sheet"], []).append(row)
        def _run_script(sheet_name, script_rel, sheet_rows):
            """执行一个补全脚本：流式输出、逐行落盘并收集结果。"""
            nonlocal backfill_failed
            script_path = os.path.join(CODE_DIR, *script_rel.split("/"))
            command = [
                sys.executable,
                script_path,
                "--backfill",
                "--excel",
                crawler_list_path,
                "--sheet",
                sheet_name,
            ]
            print(
                f"[*] 补全：调用 {script_rel} 回填工作表[{sheet_name}]"
                f"（{len(sheet_rows)} 行待补全）"
            )
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    env=os.environ.copy(),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            except OSError as exc:
                backfill_failed = True
                sheet_fail_reasons[sheet_name] = f"补全脚本启动失败：{exc}"
                print(f"[ERROR] 无法启动补全脚本 {script_rel}：{exc}")
                return
            timed_out = False
            try:
                while True:
                    raw_line = proc.stdout.readline()
                    if not raw_line:
                        break
                    line = raw_line.rstrip("\n").rstrip("\r")
                    if not line.strip():
                        continue
                    print(line, flush=True)
                    if line.startswith("OK\t"):
                        parts = line.split("\t")
                        if len(parts) >= 6:
                            row_number = int(parts[1].strip())
                            url = parts[4].strip()
                            run_command = parts[5].strip()
                            if url.startswith("http://") or url.startswith("https://"):
                                completed_keys.add((sheet_name, row_number))
                                backfill_entries.append(
                                    (sheet_name, row_number, url, run_command)
                                )
                                # 每回填一行立即落盘，GUI 中途停止也不丢已补全数据
                                try:
                                    _write_backfill_cells(
                                        crawler_list_path,
                                        [(sheet_name, row_number, url, run_command)],
                                    )
                                except RuntimeError as exc:
                                    backfill_failed = True
                                    print(f"[ERROR] 增量写表失败：{exc}")
                    elif line.startswith("SKIP\t"):
                        parts = line.split("\t")
                        if len(parts) >= 6:
                            row_number = int(parts[1].strip())
                            reason = parts[5].strip() or "官网未收录，跳过"
                            source_row = row_by_key.get((sheet_name, row_number))
                            if source_row:
                                skipped_keys.add((sheet_name, row_number))
                                skipped_items.append(_skip_item(source_row, reason))
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.wait(timeout=BACKFILL_SUBPROCESS_TIMEOUT)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    try:
                        proc.kill()
                    except OSError:
                        pass
            if stop_requested:
                print(
                    "[*] 收到停止请求：已保存已补全行，正在收尾（跳过后续阶段）"
                )
                return
            if timed_out:
                backfill_failed = True
                sheet_fail_reasons[sheet_name] = "补全脚本执行超时"
                print(
                    f"[ERROR] 补全脚本 {script_rel} 超时，"
                    f"工作表[{sheet_name}] 未完成回填"
                )
            elif proc.returncode != 0:
                backfill_failed = True
                sheet_fail_reasons[sheet_name] = f"补全脚本退出码 {proc.returncode}"
                print(
                    f"[ERROR] 补全脚本退出码 {proc.returncode}，"
                    f"工作表[{sheet_name}] 未完成回填"
                )

        for sheet_name, sheet_rows in by_sheet.items():
            if stop_requested:
                break
            manager = str(sheet_rows[0].get("manager") or "").strip()
            script_list = sheet_script_map.get(sheet_name) or fund_link_scripts_for_sheet(
                manager, sheet_name, scripts
            )
            if not script_list:
                sheet_fail_reasons[sheet_name] = "该公司暂无链接补全脚本，仅摸查"
                names = "、".join(
                    sorted({str(r.get("fund_name") or "") for r in sheet_rows})
                )
                print(
                    f"[!] 工作表[{sheet_name}]（管理人[{manager or '未知'}]）暂无链接补全脚本，"
                    f"本次仅摸查 {len(sheet_rows)} 行并跳过：{names[:200]}"
                )
                continue
            for script_rel in script_list:
                if stop_requested:
                    break
                _run_script(sheet_name, script_rel, sheet_rows)

        for row in incomplete_rows:
            key = (row["sheet"], row["excel_row"])
            if key in completed_keys or key in skipped_keys:
                continue
            reason = sheet_fail_reasons.get(row["sheet"], "")
            if not reason:
                if stop_requested:
                    reason = "用户停止，未完成补全"
                elif len(sheet_script_map.get(row["sheet"]) or []) > 1:
                    reason = "HK/TW 官网均未匹配到月报，跳过"
                else:
                    reason = "补全脚本未返回该行结果"
            skipped_items.append(_skip_item(row, reason))
        backfilled_count = len(completed_keys)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        if completed_keys and not stop_requested:
            refreshed = load_workbook_rows(crawler_list_path, sheet_names)
            phase2_rows = [
                row
                for row in refreshed
                if row.get("run_command")
                and (row.get("sheet"), row.get("excel_row")) in completed_keys
                and _matches_target_filters(row, args.only, args.exclude)
            ]
            print(
                f"[+] 已回填 {len(completed_keys)} 行，"
                f"进入阶段2同批次补跑 {len(phase2_rows)} 行"
            )
            if phase2_rows:
                results.extend(
                    run_targets_parallel(
                        phase2_rows,
                        args.timeout,
                        run_id,
                        args.retries,
                        args.retry_delay,
                        workers=args.workers,
                        fail_fast=args.fail_fast,
                    )
                )
            else:
                print("[!] 补全后没有可补跑的行，跳过阶段2补跑")
        elif not stop_requested:
            print("[!] 本轮没有成功补全的行，跳过阶段2补跑")

    if args.report:
        report_path = args.report
        if not os.path.isabs(report_path):
            report_path = os.path.join(PROJECT_ROOT, report_path)
    else:
        report_path = default_report_path(run_id)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    if skipped_items:
        results.extend(skipped_items)
    summary = write_report(
        results, report_path, run_id, crawler_list_path, backfilled=backfilled_count
    )
    print_download_result(results, retry_round=0, backfilled=backfilled_count)
    # 补全流程包含“已有基金下载 + 补全 + 补全基金下载”三段，
    # 结束后用整轮汇总进度行覆盖 GUI 进度栏，避免只显示最后一段的 8/8。
    print_unified_progress(summary)
    print(f"[*] Data: {summary['data_dir']}")
    print(f"[*] Report: {report_path}")
    if stop_requested:
        print(
            "[*] 已停止：已把停止前补全的行写入爬虫表并生成报告，"
            "未处理的行将在下次运行时继续补全"
        )
        return 130
    if backfill_failed:
        print(
            "[FAILED] 补全阶段失败，本次没有可下载的补全结果，"
            "请查看上方 [ERROR] 日志后再重试"
        )
        return 3
    if summary["failed"] and not args.allow_failures:
        return 1
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Run fund document downloaders from 爬虫list.xlsx.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Seconds allowed per attempt for each fund row.")
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Retry count for transient network/timeout failures.")
    parser.add_argument("--retry-delay", type=int, default=DEFAULT_RETRY_DELAY, help="Seconds to wait between retries.")
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Concurrent downloader count (maximum 5); rows for the same manager stay serial.",
    )
    parser.add_argument("--only", action="append", default=[], help="Run only rows matching this substring/glob.")
    parser.add_argument("--exclude", action="append", default=[], help="Skip rows matching this substring/glob.")
    parser.add_argument("--list", action="store_true", help="List selected fund rows without running them.")
    parser.add_argument("--report", default="", help="JSON report path. Defaults to download_run_report_<run_id>.json.")
    parser.add_argument(
        "--run-id",
        default="",
        help="Stable output batch ID for rerunning into the same download directory.",
    )
    parser.add_argument("--crawler-list", default=CRAWLER_LIST_PATH, help="Path to 爬虫list.xlsx.")
    parser.add_argument(
        "--sheet",
        default="",
        help="处理的工作表名：默认第一个工作表；传“所有”处理全部工作表（自动跳过说明页）。",
    )
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failed fund row.")
    parser.add_argument("--allow-failures", action="store_true", help="Exit 0 even if some fund rows failed.")
    parser.add_argument(
        "--no-interactive-retry",
        action="store_true",
        help="Do not ask whether to retry failed downloads after the run.",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="下载有月报链接/运行方式的基金后，摸查并回填缺失行，再同批次重跑补全行。",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    crawler_list_path = args.crawler_list
    if not os.path.isabs(crawler_list_path):
        crawler_list_path = os.path.join(PROJECT_ROOT, crawler_list_path)

    try:
        sheet_arg = (args.sheet or "").strip()
        if sheet_arg == "所有":
            sheet_names = "all"
        elif sheet_arg:
            sheet_names = [sheet_arg]
        else:
            sheet_names = None
        if args.backfill:
            return _run_backfill_pipeline(args, crawler_list_path, sheet_names)
        targets = selected_targets(
            args.only, args.exclude, crawler_list_path, sheet_names=sheet_names
        )
    except Exception as exc:
        print(f"[FAILED] Cannot build run list: {type(exc).__name__}: {exc}")
        return 2

    if args.list:
        for target in targets:
            args_text = " ".join(target.get("args", []))
            print(f"row {target['row_number']}: {target['category']} / {target['fund_name']} -> {target['script']} {args_text}".rstrip())
        return 0

    if not targets:
        print("[FAILED] No crawler-list rows selected.")
        return 2

    run_id = safe_component(args.run_id) if args.run_id else now_stamp()
    print(f"[*] Run ID: {run_id}")
    print(f"[*] Selected fund rows: {len(targets)}")
    print(f"[*] Download concurrency: {max(1, min(args.workers, DEFAULT_WORKERS))} (same manager serial)")
    print(f"[*] PDF output: {os.path.join(PROJECT_ROOT, 'data', run_id, 'factsheet')}")

    results = run_targets_parallel(
        targets,
        args.timeout,
        run_id,
        args.retries,
        args.retry_delay,
        workers=args.workers,
        fail_fast=args.fail_fast,
    )

    if args.report:
        report_path = args.report
        if not os.path.isabs(report_path):
            report_path = os.path.join(PROJECT_ROOT, report_path)
    else:
        report_path = default_report_path(run_id)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    retry_round = 0
    interactive = (
        not args.no_interactive_retry
        and sys.stdin is not None
        and sys.stdin.isatty()
    )
    while True:
        summary = write_report(results, report_path, run_id, crawler_list_path)
        print_download_result(results, retry_round=retry_round)
        failed_targets = retryable_failed_targets(targets, results)
        if not failed_targets or not interactive:
            break
        if not ask_retry_failed(len(failed_targets)):
            break
        retry_round += 1
        print(f"\n[*] 开始第 {retry_round} 轮人工重试，只运行 {len(failed_targets)} 个失败项目。")
        retry_results = run_targets_parallel(
            failed_targets,
            args.timeout,
            run_id,
            args.retries,
            args.retry_delay,
            workers=args.workers,
            fail_fast=False,
        )
        results = merge_retry_results(results, retry_results, retry_round)

    # 普通下载也输出整轮汇总进度行，GUI 进度栏与最终统计保持一致
    print_unified_progress(summary)
    print(f"[*] Data: {summary['data_dir']}")
    print(f"[*] Report: {report_path}")

    if summary["failed"] and not args.allow_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
