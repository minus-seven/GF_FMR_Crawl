# 法盛资本适配器接入记录（2026-11-25）

## 范围与来源

- 工作簿范围：`爬虫list.xlsx` 中管理人“法盛资本”14 只基金。
- 官方香港基金目录：<https://www.im.natixis.com/en-hk/funds>。
- 目录 API：`https://api-esb.im.natixis.com/adobe-aem-fund-explorer/api/fundExplorer?jurisdiction=hong%20kong&urlLanguageCode=en-hk`。
- 单份额详情 API：`https://api-esb.im.natixis.com/adobe-aem-fund-explorer/api/fundDetail?isin=<ISIN>&jurisdiction=hong%20kong&urlLanguageCode=en-hk`。

详情 API 的 `featuredDocuments` 中只接受 `label=Factsheet` 且 `damUrl` 为 PDF 的记录；因此不会把 KFS、招股书或法律通知误写成月报入口。默认份额优先使用官方 `defaultClass`，没有默认份额才使用首个有效 ISIN。

## 实现

- `code/companies/natixis/natixis_fund_link.py`：动态读取目录和详情 API，按归一化英文名精确匹配，输出详情页 I 列和当前 Factsheet PDF 的 J 列下载命令。
- `code/companies/natixis/download_natixis_factsheet.py`：校验 `%PDF-` 文件头，按报告日期归档到 `data/.../factsheet/natixis/`。
- `tools/add_natixis_sheet.py`：从 Sheet2 建立“法盛资本”页表。

## 验证与落盘

- 临时回填：14/14 OK，0 SKIP，0 FAIL。
- 26-11-25 对 14 条 PDF 直链逐条请求，14/14 返回 HTTP 200 且文件头为 PDF。
- 正式回填前备份：`爬虫list.xlsx.before-natixis-wellington-promote-20261125.bak`。

## 限制

该适配器写入的是 Natixis 香港官方当前 Factsheet。若产品在目录中被撤销、改名或详情 API 不再返回 Factsheet，下一次运行会输出明确缺口，不会猜测其他地区或同策略产品的链接。
