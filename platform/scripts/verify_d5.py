"""Run deterministic D5 integration, recovery evidence and safety checks."""

from __future__ import annotations

import argparse
import configparser
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

import httpx
from sqlalchemy import func, select

from app.core.config import get_settings, resolve_managed_path
from app.db.session import create_session_factory
from app.models import (
    DatasetBatch,
    Fund,
    FundAllocation,
    FundHolding,
    FundMetric,
    FundReportSource,
    Job,
    JobEvent,
    JobFundScope,
    PublicationState,
    QualityIssue,
)


def parse_args() -> argparse.Namespace:
    """解析 D5 核验参数；参数为空。"""

    parser = argparse.ArgumentParser(description="核验 D5 集成、安全与文件清单。")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        help="可选；写入验收报告和当前批次文件 manifest。",
    )
    return parser.parse_args()


def _api_get(
    client: httpx.Client,
    path: str,
    *,
    params: dict | None = None,
) -> dict:
    """请求本机 API；参数 client/path/params 为请求上下文。"""

    response = client.get(path, params=params)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(f"{path}: {payload['error']}")
    return payload


def _configured_secrets(settings) -> list[str]:
    """读取需要防止泄漏的配置值；参数 project_root 为项目根目录。"""

    parser = configparser.ConfigParser()
    config_override = str(os.environ.get("FACTSHEET_CONFIG") or "").strip()
    config_path = Path(config_override).expanduser() if config_override else settings.state_root / "config.ini"
    if not config_path.is_file() and (settings.project_root / "config.ini").is_file():
        config_path = settings.project_root / "config.ini"
    parser.read(config_path, encoding="utf-8")
    values = []
    for section, key in (("api", "qwen_api_key"), ("api", "mineru_api_token")):
        value = parser.get(section, key, fallback="").strip()
        if len(value) >= 8:
            values.append(value)
    return values


def _secret_scan(settings) -> dict:
    """扫描日志、前端产物和 manifest；参数 project_root 为项目根目录。"""

    secrets = _configured_secrets(settings)
    candidates: list[Path] = []
    candidates.extend(settings.state_root.joinpath("platform-runtime").glob("*.log"))
    candidates.extend(settings.data_root.joinpath("jobs").glob("*/worker.log"))
    candidates.extend(settings.data_root.joinpath("batches").glob("*/manifest.json"))
    dist = settings.project_root / "platform" / "web" / "dist"
    if dist.exists():
        candidates.extend(
            path
            for path in dist.rglob("*")
            if path.is_file() and path.suffix.lower() in {".html", ".js", ".css"}
        )
    findings = []
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(secret in text for secret in secrets):
            findings.append(str(path))
            continue
        if re.search(
            r"(?:qwen_api_key|mineru_api_token)\s*[:=]\s*['\"]?[A-Za-z0-9._-]{12,}",
            text,
            flags=re.IGNORECASE,
        ):
            findings.append(str(path))
    return {
        "files_scanned": len(candidates),
        "findings": findings,
        "passed": not findings,
    }


def _verify_manifest(
    project_root: Path,
    batch: DatasetBatch,
) -> dict:
    """校验当前批次 manifest 和文件哈希；参数 project_root/batch。"""

    if not batch.manifest_path:
        return {"passed": False, "error": "manifest path is empty", "files": []}
    path = resolve_managed_path(get_settings(), batch.manifest_path)
    if not path.is_file():
        return {"passed": False, "error": "manifest file is missing", "files": []}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    files = []
    failures = []
    for item in manifest.get("files") or []:
        artifact = path.parent / item["path"]
        if not artifact.is_file():
            failures.append(f"missing:{item['path']}")
            continue
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            failures.append(f"hash:{item['path']}")
        files.append(
            {
                "path": item["path"],
                "sha256": digest,
                "size_bytes": artifact.stat().st_size,
            }
        )
    return {
        "passed": not failures,
        "manifest_path": path.relative_to(project_root).as_posix(),
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "file_count": len(files),
        "files": files,
        "failures": failures,
    }


def main() -> int:
    """执行 D5 核验并可选写入证据；参数为空。"""

    args = parse_args()
    settings = get_settings()
    project_root = settings.project_root
    factory = create_session_factory(args.database_url)
    with factory() as session:
        state = session.get(PublicationState, 1)
        batch = session.get(DatasetBatch, state.current_batch_id) if state else None
        counts = {
            "funds": int(session.scalar(select(func.count()).select_from(Fund)) or 0),
            "report_sources": int(
                session.scalar(select(func.count()).select_from(FundReportSource))
                or 0
            ),
            "matched_report_sources": int(
                session.scalar(
                    select(func.count())
                    .select_from(FundReportSource)
                    .where(FundReportSource.match_status == "matched")
                )
                or 0
            ),
        }
        if batch:
            counts.update(
                {
                    "metrics": int(
                        session.scalar(
                            select(func.count())
                            .select_from(FundMetric)
                            .where(FundMetric.batch_id == batch.id)
                        )
                        or 0
                    ),
                    "holdings": int(
                        session.scalar(
                            select(func.count())
                            .select_from(FundHolding)
                            .where(FundHolding.batch_id == batch.id)
                        )
                        or 0
                    ),
                    "allocations": int(
                        session.scalar(
                            select(func.count())
                            .select_from(FundAllocation)
                            .where(FundAllocation.batch_id == batch.id)
                        )
                        or 0
                    ),
                    "open_quality_issues": int(
                        session.scalar(
                            select(func.count())
                            .select_from(QualityIssue)
                            .where(
                                QualityIssue.batch_id == batch.id,
                                QualityIssue.status == "open",
                            )
                        )
                        or 0
                    ),
                }
            )
        latest_job = session.scalar(
            select(Job)
            .where(Job.status == "completed")
            .order_by(Job.finished_at.desc())
            .limit(1)
        )
        job_events = 0
        failed_scopes = 0
        if latest_job:
            job_events = int(
                session.scalar(
                    select(func.count())
                    .select_from(JobEvent)
                    .where(JobEvent.job_id == latest_job.id)
                )
                or 0
            )
            failed_scopes = int(
                session.scalar(
                    select(func.count())
                    .select_from(JobFundScope)
                    .where(
                        JobFundScope.job_id == latest_job.id,
                        JobFundScope.result_status != "success",
                    )
                )
                or 0
            )

    failures = []
    if counts["funds"] != 2479:
        failures.append(f"funds expected 2479, got {counts['funds']}")
    if counts["report_sources"] != 775 or counts["matched_report_sources"] != 775:
        failures.append("crawler source count mismatch")
    if batch is None or batch.status != "published":
        failures.append("current batch is not published")
    if latest_job is None or latest_job.batch_id != (batch.id if batch else None):
        failures.append("latest completed job is not the current snapshot job")
    if job_events < 4 or failed_scopes:
        failures.append("latest job events or scope results are incomplete")

    with httpx.Client(base_url=args.api_url, timeout=15) as client:
        health = _api_get(client, "/api/health")
        snapshot = _api_get(client, "/api/snapshots/current")
        fund_search = _api_get(
            client,
            "/api/funds",
            params={"q": "AGD276", "page_size": 20},
        )
        detail = _api_get(client, "/api/funds/AGD276")
        quality = _api_get(
            client,
            "/api/quality/issues",
            params={"page_size": 100},
        )
        scope_options = _api_get(client, "/api/jobs/scope-options")
        charts = _api_get(
            client,
            "/api/analytics/perspectives",
            params={"scope_fund_codes": ["AGD276"], "period": "latest"},
        )
    current_id = str(batch.id) if batch else None
    if health["data"].get("status") != "ok":
        failures.append("API health is not ok")
    if snapshot["data"].get("snapshot_id") != current_id:
        failures.append("API snapshot does not match database")
    if fund_search["data"].get("total") != 1:
        failures.append("AGD276 search did not return one fund")
    if not detail["data"].get("metrics"):
        failures.append("AGD276 has no metrics")
    if quality["data"].get("total", 0) < 1:
        failures.append("quality issues are not queryable")
    options = scope_options["data"]
    if (
        options.get("total_funds") != 2479
        or not options.get("managers")
        or not options.get("categories")
    ):
        failures.append("batch scope options are incomplete")
    chart_views = charts["data"].get("views") or []
    if charts["data"].get("scope_count") != 1 or len(chart_views) != 2:
        failures.append("ECharts scope is inconsistent")
    if any(view.get("snapshot_id") != current_id for view in chart_views):
        failures.append("ECharts snapshot mismatch")
    if any(not view.get("dataset") for view in chart_views):
        failures.append("ECharts dataset is empty")

    manifest = (
        _verify_manifest(project_root, batch)
        if batch
        else {"passed": False, "files": []}
    )
    if not manifest["passed"]:
        failures.append("current batch file manifest failed")
    security = _secret_scan(settings)
    if not security["passed"]:
        failures.append("plaintext secrets found")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": not failures,
        "failures": failures,
        "database": {
            "snapshot_id": current_id,
            "batch_status": batch.status if batch else None,
            "counts": counts,
        },
        "latest_job": {
            "job_id": str(latest_job.id) if latest_job else None,
            "status": latest_job.status if latest_job else None,
            "outcome": latest_job.outcome if latest_job else None,
            "stages": latest_job.requested_stages if latest_job else [],
            "events": job_events,
            "failed_scopes": failed_scopes,
        },
        "api": {
            "health": health["data"],
            "fund_search_total": fund_search["data"]["total"],
            "detail_metric_count": len(detail["data"].get("metrics") or []),
            "quality_total": quality["data"]["total"],
            "scope_options": {
                "total_funds": options["total_funds"],
                "manager_count": len(options["managers"]),
                "categories": [
                    item["category"] for item in options["categories"]
                ],
            },
            "chart_scope_count": charts["data"].get("scope_count"),
            "chart_views": [view["view_id"] for view in chart_views],
        },
        "manifest": {key: value for key, value in manifest.items() if key != "files"},
        "security": security,
    }
    if args.evidence_dir:
        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = args.evidence_dir / "file_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "snapshot_id": current_id,
                    "manifest_path": manifest.get("manifest_path"),
                    "manifest_sha256": manifest.get("manifest_sha256"),
                    "files": manifest.get("files") or [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (args.evidence_dir / "d5_acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report["evidence"] = {
            "acceptance": str(args.evidence_dir / "d5_acceptance.json"),
            "file_manifest": str(manifest_path),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        print("[FAILED] " + "; ".join(failures))
        return 2
    print("[OK] D5 verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
