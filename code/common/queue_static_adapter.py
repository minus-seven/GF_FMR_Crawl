"""Second-queue static adapter helpers."""
from __future__ import annotations
from typing import Any
from common.static_adapter import build_static_index, name_key, resolve_static

def catalog_from_pages(pages: dict[str, str], *noise: str) -> list[dict[str, Any]]:
    """根据官方页面映射生成目录；参数 pages 为基金名到 URL，noise 为品牌噪声词。"""
    return [{"fund_name": fund, "key": name_key(fund, *noise), "url": url} for fund, url in pages.items()]

def build_queue_adapter(*, sheet_name: str, description: str, downloader: str, pages: dict[str, str], noise: tuple[str, ...] = ()):
    """构造 SOP 适配器函数；参数为页表名、描述、下载命令、官方页面映射和噪声词。"""
    def _fetch_catalog() -> list[dict[str, Any]]:
        return catalog_from_pages(pages, *noise)
    def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        return build_static_index(catalog)
    def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
        return resolve_static(row, index, downloader)
    def _main(argv=None) -> int:
        from common.fund_link_backfill import cli_main
        return cli_main(argv, description=description, sheet_name=sheet_name, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)
    return _fetch_catalog, _build_index, _resolve_single, _main
