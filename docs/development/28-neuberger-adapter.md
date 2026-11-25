# 路博迈（Neuberger Berman）月报适配器接入记录

日期：2026-11-25

## 范围与官方入口

- `Sheet2` 中 `管理人=路博迈` 共 29 只，伞为“路博迈投资基金”。
- 路博迈官网已更名为 Neuberger，但香港资料仍使用 Neuberger Berman/Neuberger Berman Asia 相关名称；产品页位于 [Neuberger UCITS Funds](https://www.nb.com/products/ucits-funds)。官方产品页明确提供 Fund Literature 和 Factsheet 月报，例如 [Strategic Income Fund](https://www.nb.com/products/ucits-funds/strategic-income-fund)、[Global Equity Megatrends Fund](https://www.nb.com/products/ucits-funds/global-equity-megatrends-fund) 和 [Asia Responsible Transition Bond Fund](https://www.nb.com/products/ucits-funds/asia-responsible-transition-bond-fund)。
- 香港覆盖文件确认该产品体系属于 Neuberger Berman Investment Funds plc，香港授权产品需使用相应香港资料，不以美国 Mutual Fund/CEF 页面替代。

## 实现

- 适配器：`code/companies/neuberger/neuberger_fund_link.py`
- 下载器：`code/companies/neuberger/download_neuberger_factsheet.py`
- 页表工具：`tools/add_neuberger_sheet.py`
- 工作表：新增“路博迈”29 行，正式 I/J 保持空白，备份为 `爬虫list.xlsx.neuberger-before-20261125.bak`。
- 调度注册：`code/run_all_downloaders.py` 增加“路博迈 / Neuberger / Neuberger Berman”别名；覆盖审计增加“路博迈”。

适配器按英文名精确映射官方 UCITS 产品页 slug；J 列运行时打开产品页，读取动态 Fund Literature 中最新英文 Factsheet 链接，再通过 requests/Playwright 下载，验证 `%PDF-` 后按报告期归档。未将美国同名 Mutual Fund、CEF 或其他地区资料混入香港月报通道。

## 临时回填结果

在 `/tmp/neuberger.xlsx` 上运行 `--backfill`：

| 结果 | 数量 |
|---|---:|
| OK | 29 |
| SKIP | 0 |
| FAIL | 0 |

本轮 29 只均能对应到官方 UCITS 产品页；例如 Emerging Market Debt 的官方页面实际使用单数 slug `emerging-market-debt-hard-currency-fund`、`emerging-market-debt-local-currency-fund` 和 `emerging-market-debt-blend-fund`，已按官方路径修正。

当前执行环境对 `nb.com` 页面出现 HTTP 429/Vercel 检查，抽样下载未能在本机完成 PDF 归档；下载器已保留真实浏览器路径，并会在原生 Terminal/launchd/Windows worker 中打开产品页后寻找最新 Factsheet。不能把 429/检查页当作 PDF 入库。

## 审计后的下一家

更新月报覆盖审计后，路博迈缺口已从候选列表移除；下一家缺口最大为：

**霸菱（29 只）**。
