# 资本集团（Capital Group）月报适配器接入记录

日期：2026-11-25

## 范围

- `Sheet2` 中 `管理人=资本集团` 共 29 只基金，伞为 `Capital International基金`。
- 新增工作表“资本集团”，正式 I/J 列保持空白，备份：`爬虫list.xlsx.capitalgroup-before-20261125.bak`。
- “欧洲机会基金”当前已从 Capital Group 香港官方基金目录中消失，疑似已清算；不将其映射到其他欧洲策略。

## 官方目录与数据流

官方入口是 [Capital Group 香港 Fund Centre](https://www.capitalgroup.com/intermediaries/hk/en/investments/fund-centre.html)。页面内嵌 `fund-data` JSON，包含基金名称、基金 acronym、份额类别、币种、ISIN 及 `hasFactsheet` 标志。

适配器 `code/companies/capitalgroup/capitalgroup_fund_link.py` 的流程：

1. 请求香港 Fund Centre，解析内嵌目录，不依赖搜索引擎结果或相似名称。
2. 以英文基金名精确归一化匹配。
3. 在有 Factsheet 的份额中按 `HKD > USD > EUR > GBP > JPY` 选择。
4. I 列写入带 acronym 的基金中心页，例如 `fund-centre.CGGALU.html`。
5. J 列调用 `download_capitalgroup_factsheet.py`，使用 `https://docs.publifund.com/12_FACT/{ISIN}/en_HK` 下载香港 Factsheet。
6. 下载器验证 `%PDF-`，提取报告期后归档到统一月报目录；requests 被拦截时切换 Playwright 浏览器。

## 临时回填结果

使用 `/tmp/capitalgroup.xlsx` 验证 29 行：

| 结果 | 数量 |
|---|---:|
| OK | 28 |
| SKIP | 1 |
| FAIL | 0 |

SKIP 基金：`Capital Group European Opportunities (LUX)`，原因是香港官方当前目录未返回该基金，不能用其他欧洲基金代替。

已抽样实际下载 `Capital Group Global Allocation Fund (LUX)` 成功，归档文件为：

`data/2026-10-01/capitalgroup_Capital_Group_Global_Allocation_Fund_(LUX).pdf`

## 注册与验证

- `code/run_all_downloaders.py` 已注册 `资本集团 / Capital Group / Capital International` 别名。
- `platform/scripts/audit_monthly_coverage.py` 已注册“资本集团”。
- 当前适配器数量：48 个。
- 现有项目测试通过；正式工作簿 I/J 未回填，后续由统一任务按需写入。

## 下一家缺口最大的管理人

覆盖审计显示，资本集团接入后，下一家最大缺口为：

**路博迈、霸菱并列 29 只。**

按中文拼音顺序，建议下一家接入 **路博迈**。
