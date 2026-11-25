# 景顺香港月报适配器

## 范围与官方入口

按 `爬虫list.xlsx` Sheet2 的 `管理人=景顺` 精确筛选，新增“景顺”页表 71 行。景顺香港官网使用两套公开目录：

- 互惠基金：`https://dng-api.invesco.com/product/search`，`accountType=MUTUAL-FUND`；
- 景顺特选退休基金：同一接口，`accountType=ORSO`。

两套目录均使用 `countryCode=HK`、`language=zh_hk`、`contentType=Product`、`shareClassStatus=open`、`q=*:*`，接口要求使用 `invesco-client/1.0` 和 Invesco Referer；当前返回 309 个互惠基金开放份额和 14 个 ORSO 开放份额。

## 数据流

- `code/companies/invesco/invesco_fund_link.py` 合并两套目录，按英文核心名匹配；处理 Fund/Fd、Equity/Eq、Corporate/Corp、Investment Grade/Invest Grd 等官网缩写。
- 伞优先选择目录：`景顺特选退休基金` 只取 ORSO，`景顺卢森堡基金系列`/`景顺信托基金系列`优先互惠基金；同目录按港元→美元→其他币种选份额。
- I 列写公开基金详情页；Solr 的 `/content/invesco/...` 内部路径转换为公开 `/hk/...` 路径。
- `code/companies/invesco/download_invesco_factsheet.py` 每次读取详情页 `data-model-json` 的 `documentsSections`，选择 `displayName=factsheet`，下载 Morningstar/景顺官方 PDF，校验 `%PDF-` 后归档。

## 缺口验证

2026-11-24 临时副本 `--backfill`：**OK 45｜SKIP 26｜FAIL 0**。

| 类别 | 数量 | 说明 |
|---|---:|---|
| 当前目录未找到开放份额 | 18 | 主要为景顺集成投资基金、信托基金系列的旧基金/到期基金，以及 feeder；不使用相似基金替代 |
| 有开放 ORSO 份额但公开详情页 404 | 7 | 平衡、增长、策略增长、港元货币市场、资本稳定、环球债券、平稳增长；官网目录存在，但没有可复现的公开详情页/当前 Factsheet 入口 |
| 详情页无 Factsheet | 1 | 景顺黄金机遇基金；详情页存在但未返回可验证 Factsheet |

剩余 26 只基金：国际债券、中港、亚洲、港元债券、欧洲、北美洲、景顺欧洲大陆小型企业、日本、港元储备、景顺新兴市场精选股票、2021 到期亚洲债券 I–VI、2022 到期亚洲债券 IX、景顺环球高评级企业债券联接，以及上述 7 只 ORSO 基金和景顺黄金机遇基金。

这些 SKIP 是当前公开月报通道缺口，不等同于基金法律状态已终止。后续可按 SOP 继续查景顺香港旧资料页、`invesco.com.hk` 历史资料、HKEX IFP 财报正文；不能把 2020 年历史 Factsheet、年报或相似基金链接伪装成当前月报。

## 验证

- 适配器与下载器 `py_compile` 通过。
- 单只普通互惠基金“景顺亚洲动力基金”返回公开详情页、ISIN `LU3321037924` 和可执行 J 命令。
- 该基金最新 Factsheet 下载成功，归档样本为 2026-10 PDF。
- 正式页表 I/J 保持空白；全量回填只在 CLI 临时验证阶段执行。

## 限制

景顺的 ORSO 目录虽然公开列出开放份额，但当前详情页路由返回 404；官网搜索仍能找到历史 ORSO Factsheet/中期报告，不能据此猜测当前月报 URL。正式 Worker 运行时遇到 5xx/限流由公共补全逻辑重试。
