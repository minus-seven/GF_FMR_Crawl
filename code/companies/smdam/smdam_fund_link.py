#!/usr/bin/env python3
"""三井住友 DS Asset Management 香港 Fund Centre 适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "三井住友"
_INDEX = "https://www.smd-am.hk/en/fund-centre"
_PAGES = {name: _INDEX for name in ("DSBI Japan Equity Small Cap Absolute Value", "SMD-AM Japan Equity High Conviction Fund", "SMD-AM Japan Mid Small Cap Value", "SMD-AM China A Shares Fund")}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按 SMD-AM 官方 Fund Centre 补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" SMDAM', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
