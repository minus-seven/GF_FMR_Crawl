# 晋达（Ninety One）月报适配器接入记录

日期：2026-11-25

## 范围与身份确认

- 页表中 `管理人=晋达` 共 30 只，伞为“晋达环球策略基金”。
- “晋达”在香港公募资料中对应 Ninety One（原 Investec Asset Management），不是 Janus Henderson。
- 已新增工作表 `晋达`，从 `Sheet2` 复制 30 行；正式工作簿的 I/J（“月报链接”“运行方式”）保持空白，保留备份：`爬虫list.xlsx.ninetyone-before-20261125.bak`。

## 官方入口与实现

- 目录入口：[Ninety One Hong Kong Funds Literature](https://ninetyone.com/en/hong-kong/funds-literature)。香港目录页会经过 Cloudflare，不能把页面 HTML 当作稳定 API。
- 适配器：`code/companies/ninetyone/ninetyone_fund_link.py`。
- 下载器：`code/companies/ninetyone/download_ninetyone_factsheet.py`。
- 下载链路：先用 `requests` 直连官方 `/-/media/documents/factsheet/` PDF；遇到 401/403/429 时切换项目已有的 Playwright 真实浏览器下载；验证 `%PDF-` 后按报告期归档到统一月报目录。
- 调度注册：`code/run_all_downloaders.py` 增加“晋达 / Ninety One”别名；覆盖审计 `platform/scripts/audit_monthly_coverage.py` 增加“晋达”；新增工具 `tools/add_ninetyone_sheet.py`。

## 临时验证结果

使用 `/tmp/ninetyone.xlsx` 副本运行 `--backfill`，未触碰正式 I/J：

| 结果 | 数量 | 口径 |
|---|---:|---|
| OK | 12 | 官方检索结果已给出同名 Factsheet PDF，含 9 份香港 HK Factsheet 与 3 份官方 GSF Factsheet |
| SKIP | 18 | 只有产品页、Key Facts、Minimum Disclosure Document、其他地区 Factsheet，或尚未核验到同名香港月报 PDF |
| FAIL | 0 | 目录为静态人工核验清单，无运行时目录失败 |

OK 产品包括：Global Franchise、Asian Equity、Emerging Markets Equity、Emerging Markets Corporate Debt、US Dollar Money、Global Managed Income、Global Gold、Global Credit Income、Emerging Markets Local Currency Debt、All China Equity、European Equity、All China Bond。

SKIP 不做相近策略替换。例如 Global Equity 不替换为 Global Strategic Equity；Emerging Markets Local Currency Dynamic Debt 不替换为 Local Currency Debt；非 HK/SG 版本不自动冒充香港月报。后续若在 Ninety One 香港目录拿到单只月报 PDF，只需补 `CATALOG` 和对应缺口说明即可重新回填。

本机抽样下载时，Ninety One 媒体域对 requests 与当前 Playwright headless 环境均返回 HTTP 403；因此本轮的 OK 是“官方 PDF 地址已核验并可生成任务”，不是已在本机完成归档。正式 worker 若运行在具备原生浏览器权限的 Terminal/launchd/Windows 服务中，应优先复用浏览器渠道；若仍被拦截，需人工在浏览器下载后走补充来源流程，不能把 HTML 错误页归档为 PDF。

## 重要来源

已核验的官方 PDF 示例：

- [Global Franchise HK Factsheet](https://ninetyone.com/-/media/documents/factsheet/91-hk-gsf-global-franchise-fund-a-usd-factsheet-en.pdf)
- [Asian Equity HK Factsheet](https://ninetyone.com/-/media/documents/factsheet/91-hk-gsf-asian-equity-fund-a-usd-factsheet-en.pdf)
- [Emerging Markets Equity HK Factsheet](https://ninetyone.com/-/media/documents/factsheet/91-hk-gsf-emerging-markets-equity-fund-a-usd-factsheet-en.pdf)
- [Emerging Markets Corporate Debt HK Factsheet](https://ninetyone.com/-/media/documents/factsheet/91-hk-gsf-emerging-markets-corporate-debt-fund-a-usd-factsheet-en.pdf)
- [US Dollar Money HK Factsheet](https://ninetyone.com/-/media/documents/factsheet/91-hk-gsf-us-dollar-money-fund-a-usd-factsheet-en.pdf)
- [Global Managed Income HK Factsheet](https://ninetyone.com/-/media/documents/factsheet/91-hk-gsf-global-managed-income-fund-a-usd-factsheet-en.pdf)
- [Global Gold HK Factsheet](https://ninetyone.com/-/media/documents/factsheet/91-hk-gsf-global-gold-fund-a-usd-factsheet-en.pdf)
- [Global Credit Income HK Factsheet](https://ninetyone.com/-/media/documents/factsheet/91-hk-gsf-global-credit-income-fund-a-usd-factsheet-en.pdf)
- [Emerging Markets Local Currency Debt HK Factsheet](https://ninetyone.com/-/media/documents/factsheet/91-hk-gsf-emerging-markets-local-currency-debt-fund-a-usd-factsheet-en.pdf)
- [All China Bond official Factsheet](https://ninetyone.com/-/media/documents/factsheet/91-gsf-all-china-bond-fund-a-usd-factsheet-en.pdf)

## 审计后的下一步

重新运行月报覆盖审计后，晋达正式页表因按项目约定保留空白，仍显示 30 只待回填。除晋达外，当前最大缺口为 **资本集团、路博迈、霸菱并列 29 只**；若按拼音顺序，下一家建议接入资本集团。
