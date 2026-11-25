# 法巴香港月报适配器

## 范围与官方入口

按 `爬虫list.xlsx` Sheet2 的 `管理人=法巴` 精确筛选，共 39 行；新增页表“法巴”，插在“中银保诚”之后。正式工作簿 I/J 仍为空，所有 CLI 端到端验证使用临时副本。

官方入口：

- [BNP Paribas Asset Management Hong Kong Fund Explorer](https://www.bnpparibas-am.com/en-hk/fundexplorer/)
- [Fundsearch API](https://api.bnpparibas-am.com/push/fundsearchv2/IP_HK-FSE/HKG)
- [Doclib API](https://api.bnpparibas-am.com/push/doclib/IP_HK-FSE?language=ENG)

Fundsearch 返回基金级默认份额、ISIN、资产类别和 `compartment_id`。适配器按基金名称归一化匹配，并生成官方 Fundsheet 页面；Doclib 只选择同一 `compartment_id` 最新日期的 `DOC_FACTSHEET`。这能保持月报入口与单只基金对应，不把 KFS、FP、REPA/REPS、年报或合订报告伪装成月报。

## 实现

- `code/companies/bnp/bnp_fund_link.py`：读取 Fundsearch / Doclib，去除 `BNP PARIBAS FUNDS`、`BNP PARIBAS A FUND` 前缀后进行名称匹配；输出 I=基金详情页、J=可执行下载命令。未命中和无单只 Factsheet 分别输出 `NO_CURRENT_FUND`、`NO_FACTSHEET`。
- `code/companies/bnp/download_bnp_factsheet.py`：下载并校验 `%PDF-`，直链失败时按 `compartment_id` 刷新 Doclib；提取报告月份后调用公共归档函数写入标准月报目录。
- `code/run_all_downloaders.py`：注册 `法巴`、`bnp`、`BNP Paribas`、`BNP Paribas Asset Management` 别名。
- `platform/scripts/audit_monthly_coverage.py`：登记法巴已接入，审计时不再把其缺口计入“无适配器”。

## 缺口验证（2026-11-25）

临时副本全量回填结果：**OK 35｜SKIP 4｜FAIL 0**。

| 缺口类别 | 数量 | 基金 | 原因 |
|---|---:|---|---|
| 当前目录未找到基金页 | 3 | 法巴可持续亚洲城市债券基金、法巴俄罗斯股票基金、法巴新兴欧洲股票基金 | 当前香港 Fundsearch 没有可验证的基金记录 |
| 基金页有目录但无单只 Factsheet | 1 | 法巴可持续食品基金（SMaRT Food） | Doclib 当前没有对应 `DOC_FACTSHEET` |

这些是当前公开目录的能力缺失，不证明历史基金不存在。若要追补，应另查历史官网归档、管理人提供的旧月报或 HKEX IFP 财报正文；历史资料不能直接写入当前月报入口。

## 端到端验证

- `bnp_fund_link.py`、下载器、统一调度器和审计脚本已 `py_compile` 通过。
- 法巴水资源基金（Aqua）官方 Factsheet 下载成功，临时文件为 `data/bnp_sop_sample/factsheet/权益/法巴水资源基金.pdf`；基金页使用 [Aqua Fundsheet](https://www.bnpparibas-am.com/en-hk/fundsheet/equity/BNP%20PARIBAS%20FUNDS%20AQUA%20%5BCLASSIC%2C%20C%5D-LU1165135440/)，文档直链来自 [Docfinder PDF](https://docfinder.bnpparibas-am.com/api/files/e2fe0ddb-9eb6-463a-8ae5-0edea4b5c178/512)。
- 临时统一调度器报告 `/tmp/bnp-sop-report.json`：`backfilled=35`、`downloaded=1`、`failed=0`；正式页表 I/J 填充数为 0。

## 限制与后续

接口当前返回的是法巴香港公开目录和最新单只 Factsheet；缺少当前入口的历史/终止基金需要人工提供可追溯 PDF 或另建历史月报来源。基金级 Factsheet 可能覆盖多个份额类别，适配器不伪造类别级重复文件。后续执行 GUI“爬虫补全”时，使用本适配器输出的 I/J 命令回写正式页表。
