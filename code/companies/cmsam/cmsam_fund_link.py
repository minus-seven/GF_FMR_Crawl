#!/usr/bin/env python3
"""招证资管官方基金产品页适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "招证资管"
_PAGES = {
    "CMS Hong Kong Multi Income Fund": "https://cms.cmschina.com.hk/fund-product/hkmif?lang=chn",
    "CMS Money Market Fund": "https://www.cmschina.com.hk/AM/FundProduct?view=CMMF",
}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按招商证券资管官方基金产品页补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" CMSAM', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
