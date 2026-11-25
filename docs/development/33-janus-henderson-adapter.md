# 骏利亨德森（Janus Henderson）月报适配器

日期：2026-11-25

## 范围与官方入口

- Sheet2 中管理人“骏利亨德森”共 24 行；新增同名页表，正式工作簿 I/J 保持为空。
- 香港官方产品入口：[Fund Finder](https://www.janushenderson.com/en-hk/investor/product/)。产品目录通过公开 API `jhenderson/v1/product_filters/get_tab_data?content_name=filterEMEAPerformance&page_id=137597` 读取。
- 香港官方文件入口：[Documents](https://www.janushenderson.com/en-hk/investor/documents/)，当前 8 页分页，英文 Factsheet 行包含产品名、日期、语言和 `documents.janushenderson.com` 文档直链。

## 实现

- `code/companies/janushenderson/janushenderson_fund_link.py`：读取产品 API，按归一化英文核心名精确匹配；兼容工作簿 `Janus Henderson Horizon Fund - ...` 与官网省略 `Fund -` 的命名差异；I 列写产品页、J 列调用下载器。
- `code/companies/janushenderson/download_janushenderson_factsheet.py`：扫描 1–8 页 Documents，限定 `Factsheet + EN`，按报告日期倒序并优先 HKD/HSGD、USD 份额，下载后校验 `%PDF-`，用公共路径函数识别报告月份并归档。
- `tools/add_janushenderson_sheet.py`：从 Sheet2 复制 24 行并保留样式，生成备份 `爬虫list.xlsx.janushenderson-before-20261125.bak`，不写正式 I/J。
- `code/run_all_downloaders.py` 与 `platform/scripts/audit_monthly_coverage.py`：注册中文名、英文名和别名 `janushenderson`。

## 验证

- 临时副本 `/tmp/janushenderson.xlsx` 全量 `--backfill`：**OK 24｜SKIP 0｜FAIL 0**；正式工作簿 I/J 未写入。
- 实际下载抽样：`Janus Henderson Balanced Fund` 选择 `Janus Henderson Balanced Fund A5m HSGD Factsheet`（2026-10-31），PDF 校验和归档成功：`data/2026-10-01/janushenderson_Janus_Henderson_Balanced_Fund.pdf`。
- 本适配器不把 API 目录中的额外产品 `Janus Henderson Horizon Global Fusion Equity Fund` 强行加入页表；仅处理 HKEX 爬虫清单的 24 行。

## 已知边界

产品 API 和 Documents 页面是动态官方入口，后续若分页数超过 8 页需同步调整 `_pages()`；若某报告没有 HKD/USD Factsheet，下载器会退回最新可用英文份额，而不会把 KFS、年报或股东通知冒充月报。
