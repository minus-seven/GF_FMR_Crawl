#!/usr/bin/env python3
"""浦银国际 adapter with explicit empty catalog until official HK monthly URLs are verified."""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "浦银国际"
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="核验浦银国际官方月报入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" SPDBI', pages={})
if __name__ == "__main__": raise SystemExit(main())
