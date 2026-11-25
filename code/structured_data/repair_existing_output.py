#!/usr/bin/env python
"""对已有 per-PDF 结构化结果执行不调用模型的原文补正。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    from structured_data.extract_fixed_income import _extract_pdf_text, _finalize
except ImportError:  # pragma: no cover - standalone invocation
    from extract_fixed_income import _extract_pdf_text, _finalize  # type: ignore


def _load_text(meta: dict[str, Any], pdf_path: Path) -> str:
    """加载已有 MinerU Markdown，缺失时回退 PDF 原生文字层；参数为 meta 和 PDF 路径。"""

    mineru = meta.get("_meta") if isinstance(meta.get("_meta"), dict) else {}
    mineru_info = mineru.get("mineru") if isinstance(mineru.get("mineru"), dict) else {}
    markdown_path = Path(str(mineru_info.get("markdown_path") or ""))
    if markdown_path.is_file():
        markdown = markdown_path.read_text(encoding="utf-8", errors="ignore")
        return markdown + "\n" + (_extract_pdf_text(pdf_path) if pdf_path.is_file() else "")
    return _extract_pdf_text(pdf_path) if pdf_path.is_file() else ""


def repair_one(path: Path, *, write: bool, category: str) -> dict[str, Any]:
    """补正一份 JSON 及对应 meta；参数 path/write/category 为文件、是否落盘和业务类别。"""

    data = json.loads(path.read_text(encoding="utf-8"))
    meta_path = path.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {"status": "review", "data": data, "_meta": {}}
    meta_info = meta.get("_meta") if isinstance(meta.get("_meta"), dict) else {}
    source_file = Path(str(meta_info.get("source_file") or ""))
    text = _load_text(meta, source_file)
    corrected = _finalize(data, source_file, text, category=category) if source_file.is_file() else data
    audit = (corrected.get("disclosure_info") or {}).get("correction_audit", {})
    meta["data"] = corrected
    meta_info["repair"] = {
        "repaired_at": datetime.now(timezone.utc).isoformat(),
        "source_text": "mineru_markdown_plus_pdf_text" if text else "no_source_text",
        "correction_audit": audit,
    }
    meta["_meta"] = meta_info
    if write:
        path.write_text(json.dumps(corrected, ensure_ascii=False, indent=2), encoding="utf-8")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "path": str(path),
        "write": write,
        "report_date": corrected.get("report_date"),
        "investment_objective": bool(corrected.get("investment_objective")),
        "portfolio_ytm_percent": corrected.get("portfolio_ytm_percent"),
        "portfolio_ytm_note": bool(corrected.get("portfolio_ytm_note")),
        "top_holdings": len(corrected.get("top_holdings") or []),
        "correction_count": audit.get("correction_count", 0),
    }


def main() -> int:
    """解析补正命令行参数并执行；参数为空时使用 sys.argv。"""

    parser = argparse.ArgumentParser(description="Repair existing structured output with local PDF evidence.")
    parser.add_argument("json", nargs="+", type=Path, help="one or more *.money_market.json files")
    parser.add_argument("--category", default="money")
    parser.add_argument("--write", action="store_true", help="write corrected JSON/meta in place")
    args = parser.parse_args()
    results = [repair_one(path.resolve(), write=args.write, category=args.category) for path in args.json]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

