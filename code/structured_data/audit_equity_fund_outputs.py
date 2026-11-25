#!/usr/bin/env python
"""Audit extracted equity fund JSON/Excel-friendly outputs against PDF text."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    from structured_data.extract_equity_fund import _normalize_report_month
    from structured_data.extract_fixed_income import (
        _compute_fund_size_hkd_100m,
        _extract_pdf_text,
        _parse_fund_size_from_text,
    )
except ImportError:  # pragma: no cover
    from extract_equity_fund import _normalize_report_month
    from extract_fixed_income import _compute_fund_size_hkd_100m, _extract_pdf_text, _parse_fund_size_from_text


MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parents[1]
DEFAULT_RUN_ID = "20260717143244"
DEFAULT_PDF_DIR = PROJECT_ROOT / "data" / DEFAULT_RUN_ID / "权益"
DEFAULT_JSON_DIR = PROJECT_ROOT / "data" / "_structured_equity" / DEFAULT_RUN_ID / "per_pdf"
DEFAULT_OUT = PROJECT_ROOT / "data" / "_structured_equity" / DEFAULT_RUN_ID / "audit" / "equity_fund_audit.csv"


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?[0-9][0-9,]*(?:\.[0-9]+)?", str(value))
    return float(match.group(0).replace(",", "")) if match else None


def _close(a: Any, b: Any, tol: float = 0.03) -> bool:
    left = _num(a)
    right = _num(b)
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= tol


def _compact(text: Any) -> str:
    return re.sub(r"[\s,，。%、%()（）\-_/]+", "", str(text or "")).lower()


def _weight_patterns(value: Any) -> list[str]:
    number = _num(value)
    if number is None:
        return []
    patterns = {f"{number:.2f}", f"{number:.1f}", f"{number:g}"}
    return [pattern.rstrip("0").rstrip(".") if "." in pattern else pattern for pattern in patterns]


def _text_has_weight(text: str, value: Any) -> bool:
    compact = _compact(text)
    return any(pattern.replace(".", "") in compact for pattern in _weight_patterns(value))


def _name_tokens(item: dict[str, Any], keys: list[str]) -> list[str]:
    tokens: list[str] = []
    for key in keys:
        value = item.get(key)
        if not value:
            continue
        text = str(value)
        tokens.append(text)
        if re.search(r"[\u4e00-\u9fff]", text) and len(text) >= 4:
            tokens.append(text[:4])
        for part in re.split(r"[\s,，()（）/\\-]+", text):
            if len(part) >= 4:
                tokens.append(part)
    return list(dict.fromkeys(tokens))


def _text_has_name(text: str, tokens: list[str]) -> bool:
    compact = _compact(text)
    for token in tokens:
        compact_token = _compact(token)
        if compact_token and compact_token in compact:
            return True
    return False


def _item_status(text: str, item: dict[str, Any], name_keys: list[str]) -> str:
    has_weight = _text_has_weight(text, item.get("weight_percent"))
    has_name = _text_has_name(text, _name_tokens(item, name_keys))
    if has_name and has_weight:
        return "match"
    if has_weight:
        return "weight_only"
    if has_name:
        return "name_only"
    return "not_found"


def _resolve_pdf_path(pdf_dir: Path, json_path: Path, meta: dict[str, Any], fund_name: str) -> Path:
    source_file = ((meta.get("_meta") or {}).get("source_file") or "").strip()
    candidates: list[Path] = []
    if source_file:
        candidates.append(Path(source_file))
    candidates.append(pdf_dir / f"{json_path.stem.replace('.equity_fund', '')}.pdf")
    candidates.append(pdf_dir / f"{fund_name}.pdf")
    return next((path for path in candidates if path.exists()), candidates[0])


def _field_rows(data: dict[str, Any], text: str, pdf_path: Path, json_path: Path, meta: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_report_date = (((meta.get("_meta") or {}).get("raw_model_output") or {}).get("report_date") or data.get("report_date"))
    report_candidate = _normalize_report_month(text, raw_report_date)
    size_candidate = _compute_fund_size_hkd_100m(_parse_fund_size_from_text(text))
    checks = [
        ("report_date", data.get("report_date"), report_candidate),
        ("fund_size_hkd_100m", data.get("fund_size_hkd_100m"), size_candidate),
    ]
    for field, extracted, candidate in checks:
        if candidate in (None, ""):
            status = "no_text_candidate"
        elif extracted in (None, ""):
            status = "missing"
        else:
            status = "match" if (extracted == candidate if field == "report_date" else _close(extracted, candidate)) else "diff"
        rows.append({
            "fund_name": data.get("fund_name"),
            "section": "field",
            "field": field,
            "item_name": "",
            "weight_percent": "",
            "extracted": extracted,
            "pdf_text_candidate": candidate,
            "status": status,
            "source_json": str(json_path),
            "source_pdf": str(pdf_path),
            "validation_issues": "; ".join((meta.get("_meta") or {}).get("validation_issues") or []),
        })
    return rows


def _list_rows(data: dict[str, Any], text: str, pdf_path: Path, json_path: Path, meta: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sections = [
        ("top_holdings", ["ticker", "name"], "name"),
        ("geographic_allocation", ["region"], "region"),
        ("sector_allocation", ["sector"], "sector"),
    ]
    for section, token_keys, display_key in sections:
        items = data.get(section) if isinstance(data.get(section), list) else []
        if not items:
            rows.append({
                "fund_name": data.get("fund_name"),
                "section": section,
                "field": "list",
                "item_name": "",
                "weight_percent": "",
                "extracted": "[]",
                "pdf_text_candidate": "",
                "status": "missing_list",
                "source_json": str(json_path),
                "source_pdf": str(pdf_path),
                "validation_issues": "; ".join((meta.get("_meta") or {}).get("validation_issues") or []),
            })
            continue
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            status = _item_status(text, item, token_keys) if text else "no_pdf_text"
            rows.append({
                "fund_name": data.get("fund_name"),
                "section": section,
                "field": f"{section}[{index}]",
                "item_name": item.get(display_key) or item.get("ticker") or "",
                "weight_percent": item.get("weight_percent"),
                "extracted": json.dumps(item, ensure_ascii=False),
                "pdf_text_candidate": "",
                "status": status,
                "source_json": str(json_path),
                "source_pdf": str(pdf_path),
                "validation_issues": "; ".join((meta.get("_meta") or {}).get("validation_issues") or []),
            })
    return rows


def audit(pdf_dir: Path, json_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for json_path in sorted(json_dir.glob("*.equity_fund.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        fund_name = str(data.get("fund_name") or json_path.stem.replace(".equity_fund", ""))
        meta_path = json_path.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        pdf_path = _resolve_pdf_path(pdf_dir, json_path, meta, fund_name)
        text = _extract_pdf_text(pdf_path) if pdf_path.exists() else ""
        rows.extend(_field_rows(data, text, pdf_path, json_path, meta))
        rows.extend(_list_rows(data, text, pdf_path, json_path, meta))
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = list(rows[0].keys()) if rows else ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit equity fund extraction outputs against PDF text.")
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--json-dir", type=Path, default=DEFAULT_JSON_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    rows = audit(args.pdf_dir, args.json_dir)
    write_csv(args.out, rows)
    summary: dict[str, int] = {}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    print(json.dumps({"rows": len(rows), "summary": summary, "out": str(args.out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
