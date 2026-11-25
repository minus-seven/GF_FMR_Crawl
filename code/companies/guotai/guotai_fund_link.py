from common.batch_ifp_static import bind
SHEET_NAME = "国泰"
_fetch_catalog, _build_index, _resolve_single, _main = bind(SHEET_NAME)
if __name__ == "__main__": raise SystemExit(_main())
