"""HKEX 综合资料库任务适配器。

旧版 ``HKEX_Fund_Crawl_v4`` 保持只读；本模块只负责把它包装为平台任务，
把输入参数转换为白名单命令，并把输出放入任务 staging/cache 区。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Callable

from app.core.config import get_settings


DEFAULT_REFERENCE = "HKEX基金综合资料库_完整_20261008.xlsx"
DEFAULT_SHEET = "主数据"
DEFAULT_RENAME_SHEET = "改名"
DEFAULT_RENAME_MODE = "不检测"
DEFAULT_UPDATE_MODE = "API（仅更新跨境理财通标签+最新规模）"
CRAWLER_PROGRESS_RE = re.compile(r"\[\s*(?P<processed>\d+)\s*/\s*(?P<total>\d+)\s*\]")


def dated_master_filename(reference: Path, update_date: datetime | None = None) -> str:
    """按参考文件模板生成带更新日期的缓存文件名；参数 reference/update_date 为参考路径和可选日期。"""

    stem = re.sub(r"_20\d{6}$", "", reference.stem)
    stamp = (update_date or datetime.now()).strftime("%Y%m%d")
    return f"{stem}_{stamp}{reference.suffix.lower()}"


def parse_crawler_progress(message: str) -> tuple[int, int] | None:
    """从旧爬虫日志提取基金处理进度；参数 message 为一行 stdout。"""

    match = CRAWLER_PROGRESS_RE.search(message)
    if match is None:
        return None
    processed = int(match.group("processed"))
    total = int(match.group("total"))
    if total <= 0:
        return None
    return max(0, min(processed, total)), total


@dataclass(frozen=True)
class MasterDatabaseConfig:
    """一次综合资料库运行的冻结参数。"""

    reference_file: str = DEFAULT_REFERENCE
    sheet_name: str = DEFAULT_SHEET
    rename_sheet_name: str = DEFAULT_RENAME_SHEET
    use_cache: bool = True
    rename_detection_mode: str = DEFAULT_RENAME_MODE
    update_data_mode: str = DEFAULT_UPDATE_MODE
    auto_import: bool = False


def config_from_job(job) -> MasterDatabaseConfig:
    """从 Job 的 document_policy 读取并规范化综合资料库参数。"""

    policy = (getattr(job, "scope_definition", None) or {}).get("document_policy") or {}
    raw = policy.get("master_database") if isinstance(policy, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    return MasterDatabaseConfig(
        reference_file=str(raw.get("reference_file") or DEFAULT_REFERENCE).strip(),
        sheet_name=str(raw.get("sheet_name") or DEFAULT_SHEET).strip(),
        rename_sheet_name=str(raw.get("rename_sheet_name") or "").strip(),
        use_cache=bool(raw.get("use_cache", True)),
        rename_detection_mode=str(raw.get("rename_detection_mode") or DEFAULT_RENAME_MODE).strip(),
        update_data_mode=str(raw.get("update_data_mode") or DEFAULT_UPDATE_MODE).strip(),
        auto_import=bool(raw.get("auto_import", False)),
    )


def _resolve_reference(reference_file: str, project_root: Path) -> Path:
    """解析参考工作簿，仅允许项目根目录或旧爬虫目录内的文件。"""

    settings = get_settings()
    path = Path(reference_file).expanduser()
    if not path.is_absolute():
        candidates = [
            project_root / path,
            settings.state_root / "inputs" / path,
            settings.data_root / path,
        ]
        path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    path = path.resolve()
    allowed_roots = [
        project_root.resolve(),
        settings.state_root.resolve(),
        settings.data_root.resolve(),
    ]
    if settings.hkex_crawler_root:
        allowed_roots.extend(
            [settings.hkex_crawler_root.resolve(), (settings.hkex_crawler_root / "doc").resolve()]
        )
    if not any(_is_relative_to(path, root) for root in allowed_roots):
        raise RuntimeError("参考文件必须位于 Web 项目或 HKEX 爬虫项目目录内")
    if path.suffix.lower() != ".xlsx" or not path.is_file():
        raise FileNotFoundError(f"参考工作簿不存在或不是 xlsx：{path}")
    return path


def _is_relative_to(path: Path, root: Path) -> bool:
    """判断 path 是否位于 root 下；参数 path/root 为待校验路径和边界。"""

    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _map_update_mode(value: str) -> str:
    """把 Web 数据更新标签映射为旧爬虫 CLI 值；参数 value 为 Web 标签。"""

    if value.startswith("详情页"):
        return "详情页API"
    return "API"


def _map_rename_mode(value: str) -> str:
    """把 Web 改名检测标签映射为旧爬虫 CLI 值；参数 value 为 Web 标签。"""

    if value.startswith("详情页"):
        return "详情页（较慢）"
    if value.startswith("API"):
        return "API（不稳定）"
    return "不检测"


def _terminate(process: subprocess.Popen) -> None:
    """终止受控旧爬虫进程树；参数 process 为待终止的子进程。"""

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


def run_master_database(
    job,
    *,
    job_root: Path,
    cancel_event: threading.Event,
    event_callback: Callable[[str, dict], None],
) -> tuple[str, str, dict]:
    """运行只读旧爬虫并把完整 xlsx 固化到任务缓存区。"""

    settings = get_settings()
    source_root = settings.hkex_crawler_root
    if source_root is None or not (source_root / "crawler.py").is_file():
        raise RuntimeError(
            "未找到 HKEX_Fund_Crawl_v4；请设置 HKEX_CRAWLER_ROOT 指向其目录"
        )
    config = config_from_job(job)
    reference = _resolve_reference(config.reference_file, settings.project_root)
    output_dir = job_root / "staging" / str(job.id) / "master_database"
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_root / "master_database.log"
    before = set(output_dir.glob("HKEX基金综合资料库_完整_*.xlsx"))

    # 旧项目的 caching.py 把 cache.db 固定放在源码目录。用任务运行时的
    # 极小 shim 把它重定向到 Web 自己的缓存区，避免触碰旧项目文件。
    runtime_dir = job_root / "master_runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    cache_db = settings.data_root / "hkex_master_cache" / "cache.db"
    cache_db.parent.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "caching.py").write_text(
        f'''# Web-owned cache shim for HKEX_Fund_Crawl_v4\n'''
        f'''import json, pickle, sqlite3, threading, time\n'''
        f'''from pathlib import Path\n'''
        f'''DB_PATH = r"{cache_db}"\n'''
        f'''_local = threading.local()\n'''
        f'''def _get_conn():\n'''
        f'''    if not hasattr(_local, "conn"):\n'''
        f'''        _local.conn = sqlite3.connect(DB_PATH, timeout=30)\n'''
        f'''        _local.conn.execute("CREATE TABLE IF NOT EXISTS cache (func TEXT NOT NULL, args TEXT NOT NULL, kwargs TEXT NOT NULL, val BLOB NOT NULL, cached_time INTEGER NOT NULL, PRIMARY KEY(func,args,kwargs))")\n'''
        f'''        _local.conn.commit()\n'''
        f'''    return _local.conn\n'''
        f'''def cached_run(func, args=(), kwargs=None, force_refresh=False, report_cache_use=False, cache_expire_time=0, store_if=None):\n'''
        f'''    kwargs = kwargs or {{}}\n'''
        f'''    conn = _get_conn(); now = int(time.time()); key = (func.__name__, json.dumps(args, ensure_ascii=False, sort_keys=True), json.dumps(kwargs, ensure_ascii=False, sort_keys=True))\n'''
        f'''    if not force_refresh:\n'''
        f'''        row = conn.execute("SELECT val,cached_time FROM cache WHERE func=? AND args=? AND kwargs=?", key).fetchone()\n'''
        f'''        if row is not None and (cache_expire_time <= 0 or now-row[1] <= cache_expire_time):\n'''
        f'''            value = pickle.loads(row[0]); return (True,value) if report_cache_use else value\n'''
        f'''    value = func(*args, **kwargs)\n'''
        f'''    if store_if is None or store_if(value):\n'''
        f'''        conn.execute("REPLACE INTO cache(func,args,kwargs,val,cached_time) VALUES(?,?,?,?,?)", (*key, pickle.dumps(value), now)); conn.commit()\n'''
        f'''    return (False,value) if report_cache_use else value\n''',
        encoding="utf-8",
    )
    bootstrap = runtime_dir / "run_hkex_crawler.py"
    bootstrap.write_text(
        "import sys\n"
        "import os\n"
        "from pathlib import Path\n"
        "runtime = Path(sys.argv[1]).resolve()\n"
        "source = Path(sys.argv[2]).resolve()\n"
        "for site in reversed([item for item in os.environ.get('HKEX_EXTRA_SITE_PACKAGES', '').split(os.pathsep) if item]):\n"
        "    sys.path.insert(0, site)\n"
        "sys.path.insert(0, str(runtime))\n"
        "sys.path.insert(1, str(source))\n"
        "sys.argv = ['crawler.py', *sys.argv[3:]]\n"
        "from crawler import main\n"
        "main()\n",
        encoding="utf-8",
    )
    legacy_python = source_root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    runner_python = str(legacy_python) if legacy_python.is_file() else sys.executable
    command = [
        runner_python,
        str(bootstrap),
        str(runtime_dir),
        str(source_root),
        "--reference",
        str(reference),
        "--sheet",
        config.sheet_name,
        "--output",
        str(output_dir),
        "--rename-detection-mode",
        _map_rename_mode(config.rename_detection_mode),
        "--update_data",
        _map_update_mode(config.update_data_mode),
    ]
    if config.rename_sheet_name:
        command.extend(["--rename-sheet", config.rename_sheet_name])
    if not config.use_cache:
        command.append("--no-cache")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(source_root) + os.pathsep + env.get("PYTHONPATH", "")
    current_site = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    env["HKEX_EXTRA_SITE_PACKAGES"] = str(current_site)
    last_progress: tuple[int, int] | None = None
    with log_path.open("a", encoding="utf-8") as log_handle:
        log_handle.write("\n$ " + " ".join(command) + "\n")
        log_handle.flush()
        process = subprocess.Popen(
            command,
            cwd=str(runtime_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=(os.name != "nt"),
        )
        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip()
            log_handle.write(text + "\n")
            log_handle.flush()
            if text:
                progress = parse_crawler_progress(text)
                if progress is not None:
                    last_progress = progress
                    processed, total = progress
                    # job.stage 供 API 的任务进度投影使用；保留 job.backfill
                    # 供 Run detail 事件控制台显示旧爬虫的原始日志。
                    event_callback(
                        "job.stage",
                        {
                            "stage": "download",
                            "status": "running",
                            "processed": processed,
                            "total": total,
                            "phase": "master_database",
                        },
                    )
                event_callback(
                    "job.backfill",
                    {
                        "stage": "master_database",
                        "message": text[-1000:],
                        **(
                            {
                                "processed": progress[0],
                                "total": progress[1],
                                "phase": "master_database",
                            }
                            if progress is not None
                            else {}
                        ),
                    },
                )
            if cancel_event.is_set():
                _terminate(process)
                break
        process.wait()

    if cancel_event.is_set():
        return "cancelled", "综合资料库任务已取消", {}
    if process.returncode != 0:
        raise RuntimeError(f"HKEX 综合资料库爬虫退出码 {process.returncode}，详见 master_database.log")

    candidates = [path for path in output_dir.glob("HKEX基金综合资料库_完整_*.xlsx") if path not in before]
    if not candidates:
        candidates = list(output_dir.glob("HKEX基金综合资料库_完整_*.xlsx"))
    if not candidates:
        raise RuntimeError("爬虫完成但没有生成 HKEX 综合资料库完整 xlsx")
    generated = max(candidates, key=lambda path: path.stat().st_mtime)
    stable = output_dir / "HKEX基金综合资料库_latest.xlsx"
    if generated.resolve() != stable.resolve():
        shutil.copy2(generated, stable)
    dated = output_dir / dated_master_filename(reference)
    if generated.resolve() != dated.resolve():
        shutil.copy2(generated, dated)
    digest = hashlib.sha256(stable.read_bytes()).hexdigest()
    details = {
        "master_artifact_path": stable.relative_to(job_root).as_posix(),
        "master_download_path": dated.relative_to(job_root).as_posix(),
        "master_generated_path": generated.relative_to(job_root).as_posix(),
        "master_sha256": digest,
        "master_size_bytes": stable.stat().st_size,
        "reference_file": str(reference),
        "use_cache": config.use_cache,
        "rename_detection_mode": config.rename_detection_mode,
        "update_data_mode": config.update_data_mode,
        "auto_import": config.auto_import,
        "complete_fund_results": True,
    }
    if last_progress is not None:
        details.update(
            {
                "processed": last_progress[0],
                "total": last_progress[1],
            }
        )
    return "success", f"综合资料库已缓存：{dated.name}", details

