# 安联环球投资适配器接入记录

日期：2026-11-24  
范围：`安联` / `安联环球` 两种管理人标记，共 92 行基金

## 1. 按 SOP 完成的接入

安联香港官网实际分成两套公开目录：

1. ORSO 的 Allianz Global Investors Choice Fund；
2. Retail Funds 的 Allianz Global Investors Fund 等产品。

新适配器 `code/companies/allianz/allianz_fund_link.py` 同时读取两套目录 API，按英文基金核心名匹配，并按 HKD → USD → 其他币种挑选份额。I 列保存稳定的基金详情页，J 列使用 `download_allianz_factsheet.py`；不把带 Sitecore `hash/rev` 的随机 PDF 地址永久写入清单。

下载器每次打开详情页，提取当前 `data-url`/`data-file-value` PDF，校验 `%PDF-` 文件头，再调用项目标准路径归档，并从 PDF 首页识别报告月份。这样官网更新 Factsheet 后，下一次任务会重新发现新文件。

已注册：

- `code/run_all_downloaders.py`：`安联`、`安联环球`、`安联环球投资基金` → `allianz`；
- `platform/scripts/audit_monthly_coverage.py`：将 `安联`、`安联环球` 标记为已有适配器；
- `爬虫list.xlsx`：新增 `安联环球` 页表，按主数据复制 92 行，已插入柏瑞之前。

## 2. 验证结果

临时副本 `--backfill` 和正式 `run_all_downloaders.py --backfill --sheet 安联环球` 均完成目录匹配：

| 结果 | 数量 |
|---|---:|
| OK，可复现详情页入口 | 83 |
| SKIP，当前目录没有可验证详情页 | 9 |
| 合计 | 92 |

83 条已写入正式工作簿 I/J。抽样验证了 ORSO 港元流动基金和 Retail 环球人工智能基金，详情页均能发现并下载 PDF。

9 条当前仍缺少可验证月报入口：

- 安联欧元均衡基金
- 安联环球多元信贷基金
- 安联美元投资级别债券基金
- 安联新兴市场短存续期债券基金
- 安联变革世界基金
- 安联美国大型企业价值基金
- 安联环球信贷基金
- 安联粮食安全基金
- 安联环球多元资产均衡基金

这些行保留为空并记录细分缺口原因（如 `RENAME_NOT_RECONCILED`、`DOC_ONLY_NO_MONTHLY`、`CORPORATE_ACTION_DELISTED`、`FUND_CLOSED`），没有猜测随机 PDF。官网资料中部分产品仍出现在 KFS/招股书或公司行动文件中，但当前基金目录 API 未返回稳定详情页；`安联环球多元资产均衡基金` 另有清算公告，应按历史报告/清算流程处理，而不是伪造月报入口。

旧名 `Allianz GEM Equity High Dividend` 已通过官网 2026-09-22 改名为 `Allianz High Dividend Emerging Markets Equity` 的当前详情页映射成功。

## 2.1 缺口复核：为什么仍然没有入口

本次复核日期：2026-11-24。判定“可验证月报入口”的标准是：

1. 安联香港当前 ORSO/Retail 目录 API 能返回基金记录；
2. 记录能落到稳定详情页；
3. 详情页能在无人工登录、无临时会话的情况下发现 PDF，并由下载器校验 `%PDF-`。

因此，KFS、招股书、公司行动或 Dealing Guide 中出现基金名称，只能证明基金/历史子基金有官方资料，不能直接当作月报入口。

| 清单名称 | 英文/主数据线索 | 当前复核结论 | 根因分类 | 可使用的官方证据 |
|---|---|---|---|---|
| 安联欧元均衡基金 | Allianz Euro Balanced | 主数据改名表显示 BWO214 已于 2026-10-08 改为“安联动力多元资产欧洲均衡基金”；旧英文名未进入当前目录 | `RENAME_NOT_RECONCILED` | 2026-03 KFS 仍有 Allianz Euro Balanced，但不是月报页 |
| 安联环球多元信贷基金 | Allianz Global Diversified Credit | 仍在 2026-03 KFS/招股书中，但当前香港 Retail 目录不返回详情记录 | `DOC_ONLY_NO_MONTHLY` | KFS 有产品目标、币种和费用，但没有可复现 Factsheet URL |
| 安联美元投资级别债券基金 | Allianz US Investment Grade Credit | 在当前招股书中存在，Retail 目录没有可验证详情记录 | `DOC_ONLY_NO_MONTHLY` | Prospectus 有投资目标/限制；不能替代月报 |
| 安联新兴市场短存续期债券基金 | Allianz Emerging Markets Short Duration Bond | 2026-02 股东通知仍引用该子基金，Retail 目录没有可验证详情记录 | `DOC_ONLY_NO_MONTHLY` | 官方股东通知只说明投资目标变更，不提供月报入口 |
| 安联变革世界基金 | Allianz Positive Change | 仍出现在 2026-04/2025-11 Dealing Guide（USD Class AT、ISIN LU2211815571），但当前 Retail 目录没有详情记录 | `DISTRIBUTOR_DOC_ONLY` | Dealing Guide 是销售文件，不是 Factsheet |
| 安联美国大型企业价值基金 | Allianz US Large Cap Value | 在当前招股书及 2026-02 股东通知中存在，Retail 目录没有可验证详情记录 | `DOC_ONLY_NO_MONTHLY` | Prospectus/通知有产品描述，但没有当前月报页 |
| 安联环球信贷基金 | Allianz Global Credit | 当前招股书仍列出子基金，但 Retail 目录没有详情记录；旧资料还出现过 Global Credit SRI，不能直接映射 | `DOC_ONLY_NAME_AMBIGUOUS` | Prospectus/历史公司行动；名称与旧 SRI 版本存在歧义 |
| 安联粮食安全基金 | Allianz Food Security | 搜索引擎仍能看到 2026-09 详情页快照，但当前 Retail 搜索返回 `Listing: 0 Funds`；官网公司行动页列有 2026-09-01 merger notice | `CORPORATE_ACTION_DELISTED` | 当前目录已不再给可下载月报入口；不能用旧快照硬编码 |
| 安联环球多元资产均衡基金 | Allianz Global Multi Asset Balanced | 官方清算通知确定最后交易日为 2025-11-07，随后清算；不应继续追抓当前月报 | `FUND_CLOSED` | 官方清算通知给出 Final Dealing Day 和 Termination Date |

### 复核出的实现问题

这 9 条暴露的是“目录覆盖范围”而非 PDF 下载器的单一故障：

- 适配器只对两套当前目录 API 的 `FundList` 做匹配；官网搜索页/历史索引、KFS、招股书和 Dealing Guide 不会自动进入 `FundList`。
- 份额级详情页是动态页面，且当前站点对不存在或已下架 slug 会重定向到 Retail 列表页；此时页面没有 `data-url`/`data-file-value`，下载器正确地返回“未找到 Factsheet PDF”。
- 主数据保留历史基金名，而安联会改名、合并或清算；仅靠中文/英文精确匹配无法完成历史名到当前名的生命周期映射。
- 不能把 KFS、Prospectus、Corporate Action 或销售文件冒充“月报”。它们只能作为候选来源或生命周期证据，不能写入月报 `monthly_report_url`。

### 处置结论

本轮不写入 9 条猜测 URL，保持空缺是正确结果。后续应增加独立的 `fund_lifecycle`/`candidate_document` 记录：

1. `RENAME_NOT_RECONCILED`：先按改名表把基金代码和当前名称关联，再重新跑目录匹配；
2. `CORPORATE_ACTION_DELISTED`、`FUND_CLOSED`：保留最后一份中报/年报或公司行动证据，标记历史来源，不生成新的月报任务；
3. `DOC_ONLY_*`：把 KFS/招股书/Dealing Guide 作为候选资料入口，除非能在详情页发现并校验 PDF，否则不计入“月报覆盖率”；
4. 适配器审计应将 `NO_FUND_PAGE` 细分为上述原因，避免把“目录未发布月报”误报成“适配器故障”。

复核使用的官方页面：

- [安联香港 Retail Funds 当前目录](https://hk.allianzgi.com/en-hk/retail/products-solutions/retail-funds)（页面显示 420 只当前基金；对已下架名称查询会返回 `Listing: 0 Funds`）；
- [AGIF Product Key Facts](https://hk.allianzgi.com/documents/hk-agif-kfs-en)（包含 Allianz Euro Balanced、Allianz Global Diversified Credit 等产品资料）；
- [AGIF Prospectus](https://hk.allianzgi.com/documents/hk-agif-prospectus-en)（包含 Allianz US Investment Grade Credit、Allianz US Large Cap Value、Allianz Global Credit 等投资限制）；
- [2026-02 股东通知](https://hk.allianzgi.com/-/media/allianzgi/ap/HongKong/pdf/en/coporate-actions/2026-corporate-action-HK/20260209-AGIF-Shareholder-Notice-Eng)（引用 Emerging Markets Short Duration Bond、US Large Cap Value 等）；
- [AGIF Corporate Actions](https://hk.allianzgi.com/en-hk/retail/our-products/corporate-action/allianz-global-investors-fund)（列出 Food Security 合并通知及 Multi Asset Balanced 相关通知）；
- [Multi Asset Balanced 清算通知](https://hk.allianzgi.com/-/media/allianzgi/ap/HongKong/pdf/en/coporate-actions/2025-corporate-action-HK-EN/20250926-AGIF-Shareholder-Notice-en)（最后交易日 2025-11-07）；
- [Dealing Guide 2026-04-15](https://hk.allianzgi.com/documents/HK-Dealing-Guide-en-20260415)（可证明 Positive Change 仍出现在销售文件，但不等于有公开月报页）。

## 3. 数据库同步

使用当前项目 PostgreSQL 用户 `hu` 执行：

```bash
PYTHONPATH=platform/api \
PLATFORM_DATABASE_URL='postgresql+psycopg://hu@127.0.0.1:55432/fund_research' \
$FACTSHEET_VENV_ROOT/bin/python -m app.importers.cli \
  --hkex-workbook HKEX基金综合资料库_完整_20261008.xlsx \
  --crawler-workbook 爬虫list.xlsx \
  --no-activate \
  --report data/allianz_d1_import_20261124.json
```

D1 批次：`3eecabd4-182c-5276-a3e1-e312a1169050`。导入汇总：2479 funds、62 metrics、190 holdings、276 allocations、9 quality issues。

`fund_report_sources` 核对：`source_sheet='安联环球'` 共 92 条，其中 83 条有 `monthly_report_url` 和 `run_command`，9 条为空缺待后续补源；未切换当前发布快照。

## 4. 后续缺口处理

1. 对 9 条缺口先查改名表、基金详情页及香港证监会/IFP 的财报章节；确认文件首页基金名和报告期后再写入 `fallback_report_url`。
2. 对已清算/合并基金使用最后一份完整中报/年报，标记 `FUND_CLOSED`，不将 KFS 或公司行动公告冒充月报。
3. 运行 GUI 的“爬虫补全”验收，抽查不同产品线各 1 份 PDF；后续再对 83 份执行 MinerU/结构化任务。

