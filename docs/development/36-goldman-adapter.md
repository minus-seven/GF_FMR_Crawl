# 高盛（Goldman Sachs Asset Management）月报适配器

日期：2026-11-25

## 范围与官方入口

- Sheet2 精确筛出管理人“高盛” **23** 行；新增页表，正式 I/J 保持为空。
- 官方入口：[Hong Kong Fund Finder](https://am.gs.com/en-hk/individual/funds) 与[官方文件库](https://am.gs.com/en-hk/institutions/documents?selectedTab=fundsDocuments)。文件库公开提供 `POST https://am.gs.com/api/documents` 搜索接口。
- 适配器只接受 `documentCategory_s=fundDocuments`、`documentType_s=monthlyFundUpdate`、`language_s=en`，并优先 `country_s` 含 `hk` 或 audience 含 `individual` 的官方 `publicURL`。

## 实现

- `code/companies/goldman/goldman_fund_link.py`：按工作簿英文基金名逐项查询官方文档接口，去掉®后作为查询参数（接口将商标字符视作非法请求），本地匹配仍保留商标和 CORE 语义；按 `displayDateTime_t` 选择最新报告。
- `code/companies/goldman/download_goldman_factsheet.py`：下载 `publicURL` 并校验 `%PDF-`，识别报告月份后归档；不拼接 Lex UUID 或猜测文件名。
- `tools/add_goldman_sheet.py`：从 Sheet2 复制 23 行，备份 `爬虫list.xlsx.goldman-before-20261125.bak`；临时回填由 `tools/apply_adapter_backfill.py` 完成。
- 统一调度器注册 `高盛`、`Goldman Sachs`、`Goldman Sachs Asset Management` 等别名；覆盖审计登记为适配器。

## 验证

- `/tmp/goldman.xlsx` 临时回填：**OK 23｜SKIP 0｜FAIL 0**；统一调度器列出 23 条任务。
- 抽样 `Global CORE Equity Portfolio` 下载成功并归档：`data/2026-11-01/goldman_Goldman_Sachs_Global_CORE_Equity_Portfolio.pdf`。
- 适配器和调度器编译通过；正式工作簿新页 I/J 填充数为 0。

## 边界

高盛文档接口默认按搜索词返回最多 50 条记录；适配器通过基金标题、语言、报告类型和地区四重条件过滤，避免把其他国家、其他份额或公告当成香港月报。接口限流或暂时 500 时，公共 CLI 会重试并记录为可重试失败。
