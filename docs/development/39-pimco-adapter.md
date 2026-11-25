# 品浩（PIMCO）适配器接入记录

日期：2026-11-25

## 范围与官方入口

HKEX/`爬虫list.xlsx` 的“品浩”页共 19 行。使用品浩香港官方 Product Finder：

- 产品检索：<https://www.pimco.com/hk/en/product-finder>
- 前端配置的官方 API：<https://fundexp-ui.pimco.com/fund-explorer-api>
- 基金目录接口：`/api/dashboard/fundDetails`（请求头 `CountryCode=HK`、`UserRole=FI`、`LangCode=EN`）
- 文档接口：`/api/dashboard/{CUSIP}/documents?isPvit=false`

适配器按产品核心名去掉 `PIMCO` 前缀，优先探测 HKD/M Retail/E Class 份额，选择最新标题含 `HK` 的 `Fact Sheets` 文档；下载器使用官方 `fund-ui.pimco.com` 文档内容 URL。

## 实现与验证

- 新增 `code/companies/pimco/pimco_fund_link.py`、`download_pimco_factsheet.py`、`tools/add_pimco_sheet.py`。
- 新增“品浩”页表，正式工作簿 I/J 保持空白；备份为 `爬虫list.xlsx.pimco-before-20261125.bak`。
- 临时副本 `/tmp/pimco.xlsx` 回填：**18 OK、1 SKIP、0 FAIL**。
- 未匹配：`PIMCO Asia Bond Fund`。当前香港 Product Finder 未返回该产品，未用“Asia Strategic Interest Bond Fund”等相近名称替代。
- 抽样下载 `PIMCO Income Fund` 官方 Fact Sheet 成功，PDF 校验通过并归档为 2026-11 月。

## 调度与边界

已在统一调度器和月报审计中注册品浩。Product Finder 的访客角色/请求头是官方 API 必需条件；若接口返回空目录，应报告角色或网络问题，不把网页 HTML 当作 PDF。产品份额和文档日期变更时，运行时重新查询，不固化随机文档 ID 之外的假设。
