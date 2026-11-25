"""Ordered, executable download sources for a crawler report row."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import shlex
from typing import Any, Mapping


DOWNLOAD_CANDIDATES_KEY = "download_candidates"
PINEBRIDGE_PAGE_HOST = "www.metlife.com"
PINEBRIDGE_DOWNLOADER = "download_pinebridge_factsheet.py"
HTTP_URL_RE = re.compile(r"https?://[^\s\"']+", re.I)


@dataclass(frozen=True)
class DownloadSource:
    """一条可执行下载来源；参数描述顺序、稳定性、页面和命令。"""

    rank: int
    kind: str
    stability: str
    source_url: str
    run_command: str
    reason: str


def _command_url(command: str) -> str:
    """提取运行方式中的首个 HTTP URL；参数 command 为下载命令。"""

    match = HTTP_URL_RE.search(str(command or ""))
    return match.group(0) if match else ""


def _normalize_candidate(item: Mapping[str, Any], fallback_rank: int) -> DownloadSource | None:
    """校验一条已持久化候选；参数 item/fallback_rank 为记录和默认顺序。"""

    command = str(item.get("run_command") or "").strip()
    source_url = str(item.get("source_url") or "").strip()
    if not command:
        return None
    try:
        rank = int(item.get("rank") or fallback_rank)
    except (TypeError, ValueError):
        rank = fallback_rank
    return DownloadSource(
        rank=max(1, rank),
        kind=str(item.get("kind") or "registered"),
        stability=str(item.get("stability") or "high"),
        source_url=source_url,
        run_command=command,
        reason=str(item.get("reason") or ""),
    )


def infer_download_sources(
    *,
    manager_name: str,
    monthly_report_url: str | None,
    fallback_report_url: str | None,
    run_command: str | None,
) -> list[DownloadSource]:
    """从 I/J 推导有序可执行来源；参数均来自 crawler 来源记录。"""

    command = str(run_command or "").strip()
    if not command:
        return []
    registered_url = str(monthly_report_url or "").strip()
    direct_url = _command_url(command)
    primary_url = direct_url or registered_url
    primary_kind = "direct_pdf" if direct_url.lower().endswith(".pdf") else "registered"
    sources = [
        DownloadSource(
            rank=1,
            kind=primary_kind,
            stability="high",
            source_url=primary_url,
            run_command=command,
            reason=(
                "官方静态 PDF 直链，无需经过基金页免责声明"
                if primary_kind == "direct_pdf"
                else "当前登记的官方下载命令"
            ),
        )
    ]

    manager = str(manager_name or "").strip().lower()
    if (
        manager == "柏瑞"
        and PINEBRIDGE_DOWNLOADER in command
        and PINEBRIDGE_PAGE_HOST in registered_url
    ):
        page_command = (
            f'python {PINEBRIDGE_DOWNLOADER} "{registered_url}"'
        )
        sources.append(
            DownloadSource(
                rank=2,
                kind="fund_page",
                stability="fallback",
                source_url=registered_url,
                run_command=page_command,
                reason="PDF UUID 轮换或直链失效时，由官方基金页重新发现最新月报",
            )
        )

    fallback_url = str(fallback_report_url or "").strip()
    if (
        fallback_url
        and registered_url
        and registered_url in command
        and fallback_url not in {item.source_url for item in sources}
    ):
        sources.append(
            DownloadSource(
                rank=len(sources) + 1,
                kind="registered_fallback",
                stability="fallback",
                source_url=fallback_url,
                run_command=command.replace(registered_url, fallback_url),
                reason="主站不可用时切换到已登记的官方备选站",
            )
        )

    deduplicated: list[DownloadSource] = []
    seen_commands: set[str] = set()
    for item in sorted(sources, key=lambda candidate: candidate.rank):
        if item.run_command in seen_commands:
            continue
        seen_commands.add(item.run_command)
        deduplicated.append(item)
    return deduplicated


def sources_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> list[DownloadSource]:
    """读取数据库中持久化的候选列表；参数 metadata 为来源 metadata_json。"""

    raw = (metadata or {}).get(DOWNLOAD_CANDIDATES_KEY)
    if not isinstance(raw, list):
        return []
    result = [
        candidate
        for index, item in enumerate(raw, start=1)
        if isinstance(item, Mapping)
        if (candidate := _normalize_candidate(item, index)) is not None
    ]
    return sorted(result, key=lambda candidate: candidate.rank)


def sources_for_source(source: Any) -> list[DownloadSource]:
    """读取来源候选，旧记录无 metadata 时按 I/J 动态推导；参数 source 为 ORM。"""

    persisted = sources_from_metadata(getattr(source, "metadata_json", None))
    if persisted:
        return persisted
    return infer_download_sources(
        manager_name=str(getattr(source, "manager_name_source", "") or ""),
        monthly_report_url=getattr(source, "monthly_report_url", None),
        fallback_report_url=getattr(source, "fallback_report_url", None),
        run_command=getattr(source, "run_command", None),
    )


def serialize_download_sources(sources: list[DownloadSource]) -> list[dict[str, Any]]:
    """把候选转为可写入 JSONB 的字典列表；参数 sources 为有序下载来源。"""

    return [asdict(item) for item in sources]
