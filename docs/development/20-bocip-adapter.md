# 中银保诚香港月报适配器

## 范围与官方入口

按 `爬虫list.xlsx` Sheet2 的 `管理人=中银保诚` 精确筛选，共 53 行；新增页表“中银保诚”，插在“联博资产”之后。正式工作簿 I/J 仍保持空白，遵循新公司接入 SOP，CLI 验证全部使用副本。

官方入口：

- [中银保诚基金价格/文件目录](https://www.boci-pru.com.hk/en/ut-prices)
- [官方 utListing JSON 接口](https://www.boci-pru.com.hk/en/api/UTFund/utListing)
- 基金页模板：`https://www.boci-pru.com.hk/en/ut/ut-prices/<utFundCode>`
- Factsheet 路径来自接口的 `pdf.fs` 字段，当前示例：[BOCHK HK Dollar Money Market Fund Factsheet](https://www.boci-pru.com.hk/Doc/Unit_Trust_Factsheet_E/BOC-HKDMMF_FS_E.pdf)

接口返回 123 个份额记录。一个基金的不同币种/类别通常共用同一份基金级 Factsheet；适配器先按基金代码去掉份额后缀聚合，再按港元、美元优先选择一个可追溯基金页。官网没有 `pdf.fs` 时，不把 KFS、年报、半年报、通用文件或仅价格数据当作月报。

## 实现

- `code/companies/bocip/bocip_fund_link.py`：读取官方 JSON，按英文核心名和明确品牌迁移别名匹配页表；输出 I=基金详情页、J=可执行下载命令。对未收录和无单只 Factsheet 的记录分别输出 `NO_CURRENT_FUND`、`NO_FACTSHEET`。
- `code/companies/bocip/download_bocip_factsheet.py`：下载并校验 `%PDF-`，直链失效时刷新官方目录按 `utFundCode` 重取；用公共 `extract_report_month_from_pdf` 识别报告期并归档到标准月报目录。
- `code/run_all_downloaders.py`：注册 `中银保诚`、`bocip`、`BOCI-Prudential`、`BOCI Prudential` 别名。
- `platform/scripts/audit_monthly_coverage.py`：登记“中银保诚”已接入，缺口不再计为未适配器。

## 缺口验证（2026-11-25）

临时副本全量回填：**OK 44｜SKIP 9｜FAIL 0**。

| 缺口类别 | 数量 | 说明 |
|---|---:|---|
| 当前官方目录未找到可验证基金页 | 6 | 中银保诚亚洲股票、BOCI-Prudential Provident Fund Schemes Investment Fund、中银保诚欧洲股票、中银保诚港元债券、中银保诚香港平稳退休、中银保诚 MSCI 环球 ESG 指数 |
| 基金页存在但没有单只 Factsheet | 3 | 中银保诚富时强积金中国 A 股指数、中银保诚中华 MSCI MPF 金龙指数、中银保诚亚洲优质股票 |

缺口是基于当前官网公开目录的可验证能力缺失，不代表基金一定不存在；后续如要追历史月报，应另查旧站、基金管理人存档或 HKEX IFP 财报正文，但不应把其它报告类型写入月报入口。

## 验证

- `bocip_fund_link.py`、下载器、统一调度器和审计脚本均 `py_compile` 通过。
- 中银香港港元货币市场基金官方 Factsheet 抽样下载成功，临时归档为 2026-10-01 期。
- 统一调度器在 `/tmp/bocip-cli.xlsx` 临时副本完成回填→补跑：选定基金下载成功；正式工作簿 I/J 未写入。
- 正式页表备份：`爬虫list.xlsx.bocip-before-20261125.bak`。

## 限制与后续

当前接口返回的 Factsheet 是英文文件名/英文页面入口，基金级文档可能覆盖多个份额类别；本适配器不伪造类别级月报。需要补齐 9 条缺口时，应按 SOP 对历史入口进行可复核检索，并保留“当前官方目录缺失”的证据。
