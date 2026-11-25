# -*- coding: utf-8 -*-
"""伞级年度/半年度报告「按子基金章节切分」通用工具。

适用于：官网只发布伞级报告（整本 PDF），但业务需要“一基金一文件”的场景
（已用于富达 FGIF、汇丰 GIF）。

提供：
  download_report(url, cache_dir)      下载并缓存伞级报告；
  parse_toc_entries(text)              解析目录：支持「名 + a - b」与「名 + 起始页」两种版式；
  resolve_entry(entries, name, ...)    按归一化名称/别名匹配子基金章节；
  extract_pages(src, start, end, dest) 按页码切出独立 PDF（页码＝PDF 物理页索引）；
  split_to_file(...)                   下载→解析→匹配→切分→落盘 的一站式入口。
"""

from __future__ import annotations

import os
import re
from typing import Any

import requests

try:
    import pymupdf
except ImportError:  # 兼容旧包名
    import fitz as pymupdf  # type: ignore


RANGE_RE = re.compile(r"^\s*(\d{1,4})\s*[-–—]\s*(\d{1,4})\s*$")
PAGE_RE = re.compile(r"^\s*(\d{1,4})\s*$")


def download_report(url: str, cache_dir: str, *, filename: str | None = None,
                    headers: dict[str, str] | None = None, timeout: int = 180) -> str:
    """下载伞级报告到缓存目录，返回本地路径（已存在则复用）。

    参数 url：报告地址；cache_dir：缓存目录；filename：缓存文件名（默认取 URL 末段）；
    headers：请求头；timeout：超时秒数。
    """
    os.makedirs(cache_dir, exist_ok=True)
    name = filename or (os.path.basename(url.split("?")[0]) or "report.pdf")
    cache_path = os.path.join(cache_dir, name)
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 100_000:
        return cache_path
    tmp = cache_path + ".part"
    with requests.get(url, headers=headers or {}, timeout=timeout, stream=True) as response:
        response.raise_for_status()
        with open(tmp, "wb") as handle:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if chunk:
                    handle.write(chunk)
    os.replace(tmp, cache_path)
    return cache_path


def parse_toc_entries(text: str, *, single_page_until_next: bool = True) -> dict[str, tuple[int, int]]:
    """解析目录文本，返回 {子基金名: (起始页, 结束页)}。

    参数 text：目录页文本；single_page_until_next：仅给出起始页时，用下一条的起始页-1 作为结束页。
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    ordered: list[tuple[str, int, int | None]] = []
    for index in range(len(lines) - 1):
        name, value = lines[index], lines[index + 1]
        tone = RANGE_RE.match(value)
        if tone:
            ordered.append((name, int(tone.group(1)), int(tone.group(2))))
            continue
        single = PAGE_RE.match(value)
        if single:
            ordered.append((name, int(single.group(1)), None))
    entries: dict[str, tuple[int, int]] = {}
    for position, (name, start, end) in enumerate(ordered):
        if end is None:
            if single_page_until_next and position + 1 < len(ordered):
                end = max(start, ordered[position + 1][1] - 1)
            else:
                end = start
        entries[name] = (start, end)
    return entries


def normalize_name(value: str) -> str:
    """名称归一化：小写、去品牌/引号/括号/空格，仅保留字母数字与中文。

    参数 value：原始名称；返回可直接比较的键。
    """
    text = str(value or "").lower()
    text = re.sub(r"\(formerly[^)]*\)", "", text)
    for token in ("hsbc global investment funds", "fidelity global investment fund", "富达", "hsbc"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", text)


def resolve_entry(entries: dict[str, tuple[int, int]], name: str, *,
                  aliases: dict[str, str] | None = None,
                  normalizer=normalize_name) -> tuple[int, int, str] | None:
    """按归一化名称查子基金章节，返回 (起始页, 结束页, 目录原名)；找不到返回 None。

    参数 entries：parse_toc_entries 的结果；name：待查名称；aliases：别名表；
    normalizer：归一化函数（默认 normalize_name）。
    """
    raw = str(name or "").strip()
    if aliases and raw in aliases:
        raw = aliases[raw]
    key = normalizer(raw)
    index: dict[str, tuple[str, tuple[int, int]]] = {}
    for entry, span in entries.items():
        index.setdefault(normalizer(entry), (entry, span))
    if key in index:
        entry, span = index[key]
        return (*span, entry)
    for entry_key, (entry, span) in index.items():
        if key and (key in entry_key or entry_key in key):
            return (*span, entry)
    return None


def find_contiguous_section(
    report_path: str,
    name: str,
    *,
    aliases: tuple[str, ...] | list[str] = (),
    normalizer=normalize_name,
    heading_keywords: tuple[str, ...] | list[str] = (),
) -> tuple[int, int, str] | None:
    """在无法解析目录的财报中，按连续出现的基金名定位章节。

    参数 report_path：财报 PDF；name：子基金名；aliases：可用于匹配的短名/中英文别名；
    normalizer：页面文本和名称共用的归一化函数；heading_keywords：用于同长度候选排序的
    章节标题关键词。返回物理页索引 (起始页, 结束页, 命中名称)，找不到时返回 None。
    """
    targets: list[tuple[str, str]] = []
    for candidate in (name, *aliases):
        raw = str(candidate or "").strip()
        compact = normalizer(raw)
        if len(compact) >= 6 and all(compact != existing for _raw, existing in targets):
            targets.append((raw, compact))
    if not targets:
        return None
    headings = [normalizer(value) for value in heading_keywords if value]
    runs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    document = pymupdf.open(report_path)
    try:
        for page_index in range(document.page_count):
            page_text = document[page_index].get_text()
            page_key = normalizer(page_text)
            matched = next(
                ((raw, compact) for raw, compact in targets if compact in page_key),
                None,
            )
            if matched:
                if current and current["end"] + 1 == page_index:
                    current["end"] = page_index
                    current["heading"] = current["heading"] or any(
                        keyword in page_key for keyword in headings
                    )
                else:
                    current = {
                        "start": page_index,
                        "end": page_index,
                        "name": matched[0],
                        "target_length": len(matched[1]),
                        "heading": any(keyword in page_key for keyword in headings),
                    }
                    runs.append(current)
            else:
                current = None
    finally:
        document.close()
    if not runs:
        return None
    # 清算说明、市场风险表等也会反复出现基金名；有明确章节标题时优先使用标题页，
    # 避免把跨两页的说明表误判为基金正文。单页命中还必须是章节标题页。
    if headings:
        usable_runs = [item for item in runs if item["heading"]]
    else:
        usable_runs = [
            item for item in runs if item["end"] > item["start"] or item["heading"]
        ]
    if not usable_runs:
        return None
    best = max(
        usable_runs,
        key=lambda item: (
            item["end"] - item["start"] + 1,
            int(item["heading"]),
            item["target_length"],
        ),
    )
    return best["start"], best["end"], best["name"]


def extract_pages(report_path: str, start: int, end: int, dest_path: str) -> int:
    """把报告的 [start, end] 页（含端点，页码为 PDF 物理页索引）切分写入 dest_path。

    参数 report_path：源报告；start/end：起止页；dest_path：输出 PDF；返回输出页数。
    """
    source = pymupdf.open(report_path)
    try:
        first = max(0, start)
        last = min(end, source.page_count - 1)
        output = pymupdf.open()
        try:
            output.insert_pdf(source, from_page=first, to_page=last)
            output.save(dest_path)
        finally:
            output.close()
        return last - first + 1
    finally:
        source.close()


def split_to_file(report_path: str, entries: dict[str, tuple[int, int]], name: str,
                  dest_path: str, *, aliases: dict[str, str] | None = None,
                  normalizer=normalize_name) -> tuple[int, str]:
    """按子基金名切分报告并写入 dest_path，返回 (页数, 目录原名)。

    参数 report_path：报告路径；entries：目录；name：子基金名；dest_path：输出文件；
    aliases/normalizer：名称匹配配置。
    """
    section = resolve_entry(entries, name, aliases=aliases, normalizer=normalizer)
    if not section:
        raise KeyError(f"报告中未找到子基金：{name}")
    start, end, matched = section
    pages = extract_pages(report_path, start, end, dest_path)
    return pages, matched
