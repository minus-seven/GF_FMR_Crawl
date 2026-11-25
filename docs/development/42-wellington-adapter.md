# 威灵顿适配器接入记录（2026-11-25）

## 范围与来源

- 工作簿范围：`爬虫list.xlsx` 中管理人“威灵顿”12 只基金。
- 官方香港文件入口：<https://www.wellington.com/en-hk/individual/fund-document-list>。
- 基金清单 JSON：`https://www.wellington.com/content/dam/wellington/content-fragments/en/funds/fundlisting-asset.fundListing.HK.individual.UCITS.en.contactId.json`。
- 文档查询接口：`https://docs.wellington.com/list/public/documents?query=FUND_ID=<ID>&languageCd=EN&recentMatch=true`。

文档接口返回多种文件类型。适配器只接受 `TEMPLATE_TYP=FS` 的 PDF，按 `asOfDate` 取最新文件，并在同一日期优先选择 `Retail-HK`，其次 `Institutional`、`MiFID`。KFS、派息记录、投资者通知和月度评论不作为月报入口。

## 实现

- `code/companies/wellington/wellington_fund_link.py`：动态读取基金 ID 和当前 FS Factsheet，按英文基金名去除 `Wellington` 前缀后精确匹配。
- `code/companies/wellington/download_wellington_factsheet.py`：校验 `%PDF-`，按 Factsheet 原文日期归档到 `data/.../factsheet/wellington/`。
- `tools/add_wellington_sheet.py`：从 Sheet2 建立“威灵顿”页表。

## 验证与落盘

- 临时回填：12/12 OK，0 SKIP，0 FAIL。
- 26-11-25 对 12 条 PDF 直链逐条请求，12/12 返回 HTTP 200 且文件头为 PDF。
- 正式回填前备份：`爬虫list.xlsx.before-natixis-wellington-promote-20261125.bak`。

## 限制

威灵顿香港个人投资者页面的 KFS/半年度报告入口与全球月度 FS 文档由不同服务提供。本适配器明确选用公开文档接口中的月度 `FS`，不把 KFS 或半年度报告冒充月报；接口未来若撤下 FS，回填会保留缺口并等待下一轮目录更新。
