"""Shared deterministic import helpers."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
import uuid
from typing import Any


IMPORT_NAMESPACE = uuid.UUID("ba5c0c46-0880-47ce-bb7f-b77f67f8f5bb")


def stable_uuid(*parts: object) -> uuid.UUID:
    """根据业务键生成可重复 UUID；参数 parts：参与生成键的稳定字段。"""

    value = "\x1f".join(str(part or "") for part in parts)
    return uuid.uuid5(IMPORT_NAMESPACE, value)


def normalize_name(value: object) -> str:
    """规范化基金名称用于匹配；参数 value：原始名称或代码。"""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in text if character.isalnum())


def normalize_text(value: object) -> str:
    """清理单元格文本；参数 value：原始单元格值。"""

    if value is None:
        return ""
    return str(value).strip()


def sha256_file(path: Path) -> str:
    """计算文件 SHA-256；参数 path：待计算文件路径。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(payload: Any) -> str:
    """计算 JSON 内容的规范化 SHA-256；参数 payload：可序列化对象。"""

    value = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_report_month(value: object) -> date | None:
    """把结构化报告期转换为日期；有日时保留日，否则使用当月最后一日。

    月报结构化结果常只返回 ``YYYY-MM``。将其落成月初会把趋势图伪装成
    ``YYYY-MM-01``，而报告事实通常代表该月最后一个估值日；因此缺少日时
    统一使用自然月月末。原文已经给出具体日时仍严格保留原日。
    """

    text = normalize_text(value)
    match = re.search(
        r"(?P<year>20\d{2})\D{0,4}(?P<month>1[0-2]|0?[1-9])"
        r"(?:\D{0,4}(?P<day>[0-3]?\d))?",
        text,
    )
    if not match:
        return None
    day = int(match.group("day") or calendar.monthrange(
        int(match.group("year")), int(match.group("month"))
    )[1])
    try:
        return date(int(match.group("year")), int(match.group("month")), day)
    except ValueError:
        return None


def infer_batch_category(path: Path) -> str:
    """从结构化合并文件名推断分类；参数 path：combined JSON 路径。"""

    name = path.name
    marker = "_基金提取结果_combined.json"
    if name.endswith(marker):
        return name[: -len(marker)]
    return path.parent.parent.name


def relative_path(path: Path, project_root: Path) -> str:
    """将受管文件路径转换为项目相对路径；参数 path/project_root 为路径。"""

    configured_data_root = str(os.environ.get("FACTSHEET_DATA_ROOT") or "").strip()
    if configured_data_root:
        data_root = Path(configured_data_root).expanduser().resolve()
        try:
            return (Path("data") / path.resolve().relative_to(data_root)).as_posix()
        except ValueError:
            pass
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间；参数为空。"""

    return datetime.now(timezone.utc)
