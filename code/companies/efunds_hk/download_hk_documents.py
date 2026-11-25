# -*- coding: utf-8 -*-
"""
易方达香港官网 (efunds.com.hk) —— 基金文件自动下载脚本

针对页面： https://www.efunds.com.hk/sc/products/<id>/documents/
该页面是服务端渲染的，"文件下载"区每种文档（月报、产品资料概要、基金说明书、
年度/中期财务报告、碳足迹信息等）放在一个 <select> 里，<option> 列出历史版本，
第一个 option 即最新版本，value 是 PDF 的相对路径。

用法：
    python download_hk_documents.py                  # 下载产品37的最新【月报】
    python download_hk_documents.py 37               # 指定产品 id
    python download_hk_documents.py 37 --type KeyFacts   # 下载指定类型的最新一期
    python download_hk_documents.py 37 --all         # 下载【月报】的所有历史版本
    python download_hk_documents.py 37 --all-types   # 下载每种文档的最新一期

文档类型 key（--type 的取值，对应页面里 Product.Documents.* ）：
    MonthlyFact      月报（默认）
    KeyFacts         产品资料概要
    Prospectus       基金说明书
    AnnualReport     年度财务报告
    SemiAnnualReport 中期财务报告
    Carbon           碳足迹信息
"""

import os
import re
import sys
import html

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path

# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
DEFAULT_PRODUCT_ID = "37"
DEFAULT_DOC_TYPE = "MonthlyFact"          # 月报
BASE_URL = "https://www.efunds.com.hk"
PAGE_URL_TMPL = BASE_URL + "/sc/products/{pid}/documents/"
COMPANY = "efunds_hk"
SAVE_DIR = company_data_dir(COMPANY)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
SESSION = requests.Session()
configure_session(SESSION)

# 每个文档块： <div class="tit">标题</div> ... <select i="Product.Documents.类型">选项</select>
BLOCK_RE = re.compile(
    r'<div class="tit">(?P<title>.*?)</div>.*?'
    r'<select i="Product\.Documents\.(?P<type>[^"]+)">(?P<options>.*?)</select>',
    re.S,
)
# 单个版本： <option value="/uploadfiles/.../xxx.pdf">2026-05-31</option>
OPTION_RE = re.compile(r'<option value="(?P<url>[^"]+?\.pdf)"[^>]*>(?P<date>[^<]*)</option>', re.I)


def fetch_page(pid):
    """抓取产品文件下载页 HTML。"""
    url = PAGE_URL_TMPL.format(pid=pid)
    resp = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def parse_documents(page_html):
    """
    解析所有文档块。返回 dict： {type_key: {"title": ..., "versions": [{date, url}, ...]}}
    versions 按页面顺序（最新在前）。
    """
    docs = {}
    for blk in BLOCK_RE.finditer(page_html):
        type_key = blk.group("type").strip()
        title = html.unescape(blk.group("title")).strip()
        versions = []
        for opt in OPTION_RE.finditer(blk.group("options")):
            url = opt.group("url").strip()
            if not url.startswith("http"):
                url = BASE_URL + url
            versions.append({"date": opt.group("date").strip(), "url": url})
        if versions:
            docs[type_key] = {"title": title, "versions": versions}
    return docs


def safe_filename(name):
    """把标题清理成合法文件名。"""
    name = re.sub(r"\s+", "", name)               # 去空格
    name = re.sub(r'[\\/:*?"<>|]', "_", name)     # 替换 Windows 非法字符
    return name


def local_filename(doc_title, date):
    """例如：易方达(香港)全球优质成长基金-月报_2026-05-31.pdf"""
    return f"{safe_filename(doc_title)}_{date}.pdf"


def download(url, dest_path):
    """下载 PDF 到 dest_path（原子写入），并校验响应确实是 PDF。"""
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as r:
        r.raise_for_status()
        tmp_path = dest_path + ".part"
        first_chunk = b""
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    if not first_chunk:
                        first_chunk = bytes(chunk[:8])
                    f.write(chunk)
        # 官网偶尔在 PDF URL 上返回 HTML 错误页/登录页。若不在下载阶段
        # 拦截，后续 MinerU 才会报“解析失败”，任务看起来却像是已下载。
        if not first_chunk.startswith(b"%PDF-"):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError(
                f"远程文档不是有效 PDF（首字节为 {first_chunk[:8]!r}）：{url}"
            )
        os.replace(tmp_path, dest_path)
    return os.path.getsize(dest_path)


def download_versions(doc, versions, save_dir):
    """下载某文档的若干版本，已存在则跳过。返回新增数量。"""
    new_count = 0
    for ver in versions:
        fname = local_filename(doc["title"], ver["date"])
        dest = dated_output_path(COMPANY, doc["title"], ver["date"])
        if os.path.exists(dest):
            # 旧版本曾把官网 HTML 错误页按 .pdf 保存；存在文件不能等价于
            # 有效产物，否则任务会永久跳过坏缓存。
            try:
                with open(dest, "rb") as existing:
                    valid_pdf = existing.read(5) == b"%PDF-"
            except OSError:
                valid_pdf = False
            if valid_pdf:
                print(f"    - 已存在，跳过: {fname}")
                continue
            print(f"    - 已存在但不是有效 PDF，重新下载: {fname}")
        print(f"    - 下载中: {ver['date']} -> {fname}")
        size = download(ver["url"], dest)
        print(f"      完成 ({size/1024:.1f} KB)")
        new_count += 1
    return new_count


def main():
    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("-")]
    pid = positional[0] if positional else DEFAULT_PRODUCT_ID

    all_types = "--all-types" in args
    all_versions = "--all" in args
    doc_type = DEFAULT_DOC_TYPE
    if "--type" in args:
        i = args.index("--type")
        if i + 1 < len(args):
            doc_type = args[i + 1]

    os.makedirs(SAVE_DIR, exist_ok=True)
    print(f"[*] 产品 id: {pid}")
    print(f"[*] 抓取页面: {PAGE_URL_TMPL.format(pid=pid)}")

    page_html = fetch_page(pid)
    docs = parse_documents(page_html)
    if not docs:
        print("[!] 没有解析到任何文档，页面结构可能已变化。")
        sys.exit(1)

    print(f"[*] 页面共发现 {len(docs)} 种文档: " + ", ".join(docs.keys()))

    total_new = 0
    if all_types:
        # 每种文档最新一期
        for key, doc in docs.items():
            print(f"[*] {doc['title']} ({key})")
            total_new += download_versions(doc, doc["versions"][:1], SAVE_DIR)
    else:
        if doc_type not in docs:
            print(f"[!] 页面上没有类型 '{doc_type}'，可选: {', '.join(docs.keys())}")
            sys.exit(1)
        doc = docs[doc_type]
        targets = doc["versions"] if all_versions else doc["versions"][:1]
        print(f"[*] {doc['title']} ({doc_type})，最新一期: {doc['versions'][0]['date']}")
        total_new += download_versions(doc, targets, SAVE_DIR)

    if total_new == 0:
        print("[*] 本地已是最新，无需下载。")
    else:
        print(f"[*] 本次新增下载 {total_new} 个文件，保存目录: {SAVE_DIR}")


if __name__ == "__main__":
    main()
