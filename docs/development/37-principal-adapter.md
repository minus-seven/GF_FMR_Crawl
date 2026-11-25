# 信安（Principal）适配器接入记录

日期：2026-11-25

## 范围与官方入口

HKEX/`爬虫list.xlsx` 的“信安”页共 21 行。本轮使用信安香港官方 `mutual-funds` API：

- 目录：<https://www.principal.com.hk/api/mutual-funds>
- 基金资料入口：<https://www.principal.com.hk/fund-literature>
- 示例官方 Fact Sheet：<https://www.principal.com.hk/sites/default/files/general-files/Fund_Fact_Sheet_PLS_HKEF_E.pdf>

适配器读取 API 返回的多个 share class，按产品核心名去除品牌/份额后缀，优先选择 HKD、再选择 USD 的 `Fact-Sheet` 文件；未出现在当前 API 的历史/已下架产品不猜测链接。

## 实现与验证

- 新增 `code/companies/principal/principal_fund_link.py`、`download_principal_factsheet.py`、`tools/add_principal_sheet.py`。
- 新增“信安”页表，正式工作簿 I/J 保持空白；备份为 `爬虫list.xlsx.principal-before-20261125.bak`。
- 临时副本 `/tmp/principal.xlsx` 回填：**18 OK、3 SKIP、0 FAIL**。
- 未匹配：`Asian High Yield Fund`、`Principal Pension Bond Fund`、`Global Listed Infrastructure Fund`。官方 API 当前无同名 Fact Sheet，保留缺口。
- 抽样下载 `Principal Hong Kong Equity Fund` 成功；修正通用日期解析后，PDF 原文 `As of 30/9/2026` 归档为 `data/2026-09-01/`。

## 调度与边界

已在 `code/run_all_downloaders.py` 注册信安中英文别名，在月报覆盖审计登记为适配器。运行时仍重新下载官方静态 Fact Sheet 并校验 `%PDF-`；API 产品下架或改名时输出可复核缺口，不写入猜测地址。
