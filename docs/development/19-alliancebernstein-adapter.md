# 联博资产香港月报适配器

## 范围与官方入口

按 `爬虫list.xlsx` Sheet2 的 `管理人=联博资产` 精确筛选，新增“联博资产”页表 54 行，插在景顺之后。联博香港官网的 Fund Finder 使用官方 `webapi.alliancebernstein.com` 接口：

- API：`https://webapi.alliancebernstein.com/v2/funds/hk/en/investor/product-docs`；
- 基金页：`https://www.abfunds.com.hk/hk/en/investor/funds.html`；
- 文档类型 `productdocdetails-doctype-fact-sheet` 才视为单只基金月报；`fact-sheet-book` 是通用合订文档，不回填到单只基金。

接口按股份类别重复返回记录，适配器按 `fundNumber` 去重，按英文基金名匹配；去除官网的 `AB`/`AB (HK)`、`Portfolio`、`Fund` 前缀/尾缀。伞名含“联博(香港)单位信托基金系列”时优先匹配 `AB (HK)` 产品。

## 实现

- `code/companies/alliancebernstein/alliancebernstein_fund_link.py`：读取官方 product-docs 目录，选择最新单只 Fact Sheet，输出稳定基金页和当前 PDF 地址。
- `code/companies/alliancebernstein/download_alliancebernstein_factsheet.py`：校验 `%PDF-`，按报告期归档到标准月报目录；直链失效时再从官方 product-docs 目录重新找同一 `fundNumber` 的 Fact Sheet。
- `code/run_all_downloaders.py`：注册 `联博资产`、`alliancebernstein`、`AllianceBernstein` 别名。
- `platform/scripts/audit_monthly_coverage.py`：登记“联博资产”已接入。

## 缺口验证

2026-11-25 临时回填：**OK 45｜SKIP 9｜FAIL 0**。

| 缺口类别 | 数量 | 基金 |
|---|---:|---|
| 当前官方目录未找到基金页 | 2 | 亚洲高收益基金、欧洲增长基金 |
| 基金页存在但没有单只 Fact Sheet | 7 | 环球优越收益基金、美国高收益基金、新兴市场公司债券基金、环球全方位债券基金、跨领域可持续基金、可持续收益基金、新兴市场当地货币债券基金 |

上述 7 只页面有通用 `Fact Sheet Book` 或监管资料，但不能把合订资料当成单只基金月报；后续如需替代资料，应按 SOP 查历史资料或 HKEX IFP 财报正文。

## 验证

- 适配器、下载器、统一调度器和覆盖审计均已 `py_compile` 通过。
- 单只“环球高收益基金”官方 PDF 抽样下载成功，归档为 2026-10 月报。
- 统一调度器在 `/tmp/ab-cli.xlsx` 临时副本完成回填→补跑抽样，2 行下载成功；正式页表 I/J 未写入。
- 正式页表备份：`爬虫list.xlsx.alliancebernstein-before-20261125.bak`。

## 限制

联博 API 当前返回的是英文 Fund Finder 产品页和英文 Fact Sheet；若业务需要繁体中文版本，应在同一基金页文档列表中另行选择中文文档，不把监管 Product Key Facts 误当月报。
