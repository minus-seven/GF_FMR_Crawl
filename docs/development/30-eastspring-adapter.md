# 保诚瀚亚（Eastspring Investments）适配器接入记录

日期：2026-11-25

## 官方入口与实现

- 香港官方下载页：<https://www.eastspring.com/hk/funds/downloads>。
- 页面通过 `GetFunds_DownloadsData_HK` 官方接口返回基金名称、产品页、ISIN 和 Factsheet 链接；适配器读取该接口，不依赖旧 I/J。
- 新增 `code/companies/eastspring/eastspring_fund_link.py` 和 `download_eastspring_factsheet.py`，按英文核心名匹配并优先选择 A 类份额。
- 新增“保诚瀚亚”工作表 27 行，备份为 `爬虫list.xlsx.eastspring-before-20261125.bak`；正式 I/J 保持为空。

## 回归结果

在 `/tmp/three-adapters.xlsx` 上运行：**OK 27｜SKIP 0｜FAIL 0**。所有 27 行均取得 Eastspring 香港产品页与官方 Factsheet PDF 直链。

## 口径

工作簿中的基金中文名为“瀚亚投资”，管理人字段为“保诚瀚亚”；以管理人字段作为页表和调度器归属，英文名称作为官方目录主键。

