# 安盛（AXA IM）适配器接入记录

日期：2026-11-25

## 范围与官方入口

HKEX/`爬虫list.xlsx` 的“安盛”页共 21 行。AXA IM 香港站已并入 BNP Paribas AM，使用迁移后的官方入口：

- 基金目录：<https://www.bnpparibas-am.com/en-hk/fundexplorer/>
- 官方基金 API：<https://api.bnpparibas-am.com/push/fundsearchv2/IP_HK-FSE/ENG>
- 官方 DocFinder 目录：<https://api.bnpparibas-am.com/push/doclib/IP_HK-FSE?language=ENG>
- 迁移说明：<https://www.bnpparibas-am.com/en-hk/legal-transition/>

目录按 `AXA World Funds`/`AXA WF`、E.P.I.C 标点差异归一化。报告优先选 `DOC_FACTSHEET`；当前 AXA 产品在香港 DocFinder 多数没有该类型，适配器透明回退到最新官方 `DOC_REPS`（2026-06-30 半年报），运行命令保留 `semiannual` 类型，避免把半年报伪装成月报事实表。

## 实现与验证

- 新增 `code/companies/axa/axa_fund_link.py`、`download_axa_factsheet.py`、`tools/add_axa_sheet.py`。
- 新增“安盛”页表，正式工作簿 I/J 保持空白；备份为 `爬虫list.xlsx.axa-before-20261125.bak`。
- 临时副本 `/tmp/axa.xlsx` 回填：**17 OK、4 SKIP、0 FAIL**。
- 未匹配：`AXA World Funds - Global Strategic Income`、`AXA World Funds - US Growth`、`AXA World Funds - People & Planet Equity`、`Architas Flexible Bond`。当前官方 Fund Explorer/DocFinder 已不返回同名产品，保留缺口，未映射到相似产品。
- 抽样下载 `AXA World Funds - US High Yield Bonds` 的官方 `DOC_REPS` 成功，PDF 原文日期为 2026-06-30。

## 调度与边界

已在统一调度器和月报审计中注册“安盛”。安盛产品来源发生公司迁移、改名或下架时，适配器以官方当前目录为准；半年度 fallback 只作为官方周期性报告入口，后续可在 DocFinder 发布月度 factsheet 后自动优先切换。
