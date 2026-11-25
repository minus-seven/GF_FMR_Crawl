# 纽约梅隆（BNY Mellon）月报适配器

日期：2026-11-25

## 范围与官方入口

- Sheet2 精确筛出管理人“纽约梅隆” **23** 行；新增同名页表，正式工作簿 I/J 保持为空。
- 官方入口：[BNY Mellon Hong Kong Funds Centre](https://www.bny.com/investments/hk/en/individual/funds-centre/)。页面动态指向 FE Precision Plus 的公开 FundOptions 目录，目录返回产品名与 CITI 代码。
- Factsheet 使用官方稳定直链 `https://digital.feprecisionplus.com/documents/bnymhkenindividual/en-gb/{CITI}/FS`，运行时不猜测历史 PDF。

## 实现

- `code/companies/bny/bny_fund_link.py`：读取 `DownloadTool/FundOptions` JSON 中的 `<option value=CITI>`，移除品牌和份额币种后缀做严格匹配；HKD、USD 等份额优先由目录顺序确定。官方目录未返回的 U.S. Treasury、U.S. Dollar Liquidity、Asian Income、Pan European Equity、Blockchain Innovation 五项明确 SKIP。
- `code/companies/bny/download_bny_factsheet.py`：下载直链并校验 `%PDF-`，用公共函数识别报告月份后归档。
- `tools/add_bny_sheet.py`：从 Sheet2 复制 23 行并保留样式，备份 `爬虫list.xlsx.bny-before-20261125.bak`；`tools/apply_adapter_backfill.py` 仅写 `/tmp` 临时副本。
- 统一调度器注册 `纽约梅隆`、`BNY Mellon`、`BNY Investments` 等别名；覆盖审计登记为适配器。

## 验证

- `/tmp/bny.xlsx` 临时回填：**OK 18｜SKIP 5｜FAIL 0**；`run_all_downloaders.py --sheet 纽约梅隆 --list` 输出 18 条可运行任务。
- 抽样 `BNY Mellon Global Credit Fund` 下载成功并归档：`data/2026-10-01/bny_BNY_Mellon_Global_Credit_Fund.pdf`。
- 适配器和调度器编译通过；正式工作簿新页 I/J 填充数为 0。

## 边界

目录只覆盖当前香港 Fund Centre 可验证产品；未命中的五只基金保留缺口，不用相近策略或旧 PDF 猜测替代。若 FE Precision Plus 参数变化，应优先更新目录参数，不把单月 PDF 作为永久入口。
