# 首源投资（First Sentier / FSSA / Stewart / RQI）适配器接入记录

日期：2026-11-25

## 官方入口与实现

- 香港文学页：<https://www.firstsentierinvestors.com/hk/en/retail/performance/literature.html>；该页统一承载 FSSA、First Sentier、Stewart Investors 与 RQI 品牌的 Monthly Factsheets。
- 新增 `code/companies/firstsentier_global/firstsentier_global_fund_link.py` 和 `download_firstsentier_global_factsheet.py`，将工作簿英文名作为文学页关键词，避免把同名不同品牌产品混淆。
- 新增“首源投资”工作表 27 行，备份为 `爬虫list.xlsx.firstsentier-global-before-20261125.bak`；正式 I/J 保持为空。原有“首域盈信”单基金适配器不改动。

## 回归结果

在 `/tmp/three-adapters.xlsx` 上运行：**OK 27｜SKIP 0｜FAIL 0**。FSSA、First Sentier、Stewart Investors、RQI 四类名称均生成官方文学页与关键词下载命令。

