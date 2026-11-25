#!/usr/bin/env python3
"""Export the current published snapshot used by the money-market case workbook."""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "case" / "case_inputs.json"
SCALE_DATE = "2026-08-31"


def connect() -> psycopg.Connection:
    url = os.environ.get("PLATFORM_DATABASE_URL")
    if url:
        return psycopg.connect(url, row_factory=dict_row)
    return psycopg.connect(
        host="127.0.0.1",
        port=55432,
        dbname="fund_research",
        user=os.environ.get("USER", "hu"),
        row_factory=dict_row,
    )


def default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(type(value))


def main() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select * from analytics_readonly.current_snapshot")
        snapshot = cur.fetchone()
        snapshot_id = snapshot["snapshot_id"]

        cur.execute(
            """
            with scale as (
                select distinct on (s.fund_id)
                       s.fund_id, s.fund_size
                from public.fund_scale_history s
                where s.source_batch_id = %s and s.as_of_date = %s
                order by s.fund_id, s.fund_size desc
            )
            select distinct
                   f.id::text as fund_id, f.fund_code, f.name_cn, f.name_en,
                   f.base_currency, f.sfc_authorized_at,
                   m.short_name as manager_short, m.name_cn as manager_name,
                   m.ownership_type, scale.fund_size
            from public.funds f
            join public.managers m on m.id = f.manager_id
            join scale on scale.fund_id = f.id
            join public.fund_classifications c
              on c.fund_id = f.id and c.level_1 = '货币'
            where m.ownership_type = '中资'
            order by scale.fund_size desc
            """,
            (snapshot_id, SCALE_DATE),
        )
        ranked = cur.fetchall()
        for index, row in enumerate(ranked, start=1):
            row["rank"] = index

        guangfa = next((r for r in ranked if r["fund_code"] == "BTE502"), None)
        top_n = 15
        if guangfa and guangfa["rank"] > top_n:
            top_n = ((guangfa["rank"] + 4) // 5) * 5
        funds = ranked[:top_n]
        codes = [f["fund_code"] for f in funds]

        cur.execute(
            """
            select fm.fund_id::text as fund_id, f.fund_code, fm.metric_name,
                   fm.report_period, fm.numeric_value, fm.text_value, fm.unit,
                   fm.confidence, fm.review_status, fm.source_page,
                   fm.metadata_json, fm.document_version_id::text as dv_id,
                   dv.file_path, dv.source_url, d.document_type
            from public.fund_metrics fm
            join public.funds f on f.id = fm.fund_id
            join public.document_versions dv on dv.id = fm.document_version_id
            join public.documents d on d.id = dv.document_id
            where f.fund_code = any(%s) and fm.batch_id = %s
            order by f.fund_code, fm.metric_name, fm.report_period desc
            """,
            (codes, snapshot_id),
        )
        metrics = cur.fetchall()

        cur.execute(
            """
            select fh.fund_id::text as fund_id, f.fund_code, fh.report_period,
                   fh.security_name, fh.ticker, fh.holding_type, fh.weight,
                   fh.market_value, fh.source_page,
                   fh.document_version_id::text as dv_id, dv.file_path,
                   d.document_type
            from public.fund_holdings fh
            join public.funds f on f.id = fh.fund_id
            join public.document_versions dv on dv.id = fh.document_version_id
            join public.documents d on d.id = dv.document_id
            where f.fund_code = any(%s) and fh.batch_id = %s
            order by f.fund_code, fh.report_period desc, fh.weight desc nulls last
            """,
            (codes, snapshot_id),
        )
        holdings = cur.fetchall()

        cur.execute(
            """
            select fa.fund_id::text as fund_id, f.fund_code, fa.report_period,
                   fa.dimension, fa.category, fa.weight, fa.unit, fa.source_page,
                   fa.document_version_id::text as dv_id, dv.file_path,
                   d.document_type
            from public.fund_allocations fa
            join public.funds f on f.id = fa.fund_id
            join public.document_versions dv on dv.id = fa.document_version_id
            join public.documents d on d.id = dv.document_id
            where f.fund_code = any(%s) and fa.batch_id = %s
            order by f.fund_code, fa.report_period desc, fa.dimension,
                     fa.weight desc nulls last
            """,
            (codes, snapshot_id),
        )
        allocations = cur.fetchall()

        cur.execute(
            """
            with scale as (
                select distinct on (s.fund_id)
                       s.fund_id, s.fund_size
                from public.fund_scale_history s
                where s.source_batch_id = %s and s.as_of_date = %s
                order by s.fund_id, s.fund_size desc
            )
            select f.base_currency, count(distinct f.id) as fund_count,
                   sum(scale.fund_size) as total_size
            from public.funds f
            join public.managers m on m.id = f.manager_id
            join scale on scale.fund_id = f.id
            join public.fund_classifications c
              on c.fund_id = f.id and c.level_1 = '货币'
            where m.ownership_type = '中资'
            group by f.base_currency
            order by total_size desc
            """,
            (snapshot_id, SCALE_DATE),
        )
        market = cur.fetchall()

    payload = {
        "snapshot": snapshot,
        "scale_date": SCALE_DATE,
        "top_n": top_n,
        "funds": funds,
        "metrics": metrics,
        "holdings": holdings,
        "allocations": allocations,
        "market_capacity": market,
    }
    OUT.write_text(json.dumps(payload, default=default, ensure_ascii=False, indent=1))
    print(
        json.dumps(
            {
                "out": str(OUT),
                "snapshot": snapshot_id,
                "top_n": top_n,
                "funds": len(funds),
                "metrics": len(metrics),
                "holdings": len(holdings),
                "allocations": len(allocations),
                "market_rows": len(market),
            },
            default=default,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
